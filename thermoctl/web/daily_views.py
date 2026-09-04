from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.db.base import utcnow
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.control import settings as control_settings
from thermoctl.domain.modes import DomainError, update_setpoints
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import (
    cancel_override,
    create_override,
    end_of_next_switch,
    resolved_setpoint,
    temperature_for_mode,
)
from thermoctl.domain.zone_settings import (
    ControlParameters,
    ParameterOutOfRange,
    control_parameters,
    pi_eligibility,
    save_control_parameters,
    validate_pi_parameters,
    validate_valve_protection,
)
from thermoctl.web.forms import FormError, form_again
from thermoctl.web.urls import prefixed

# `include_in_schema=False`: the OpenAPI description is the contract of the REST
# interface. These routes deliver HTML for humans, and in the interface under
# /docs there would otherwise be a form route next to every real endpoint whose
# 'Try it out' triggers a real change.
router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)

FELDER = (
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "temperature_offset_k",
    "window_resume_delay_seconds",
    "solar_setback_max_k",
    "valve_protection_interval_days",
    "valve_protection_duration_minutes",
)
GANZZAHLEN = frozenset(FELDER) - {"hysteresis_k", "temperature_offset_k", "solar_setback_max_k"}

# PI (Beta), specification section 6/7. Handled like `valve_protection_interval_days`
# above -- parsed through `_check_parameters` (so an empty field means "unchanged", not
# "inherited": PI is a non-nullable, non-inherited zone field) -- and like
# `valve_protection_enabled` for the switch itself, which is its own checkbox outside
# `_check_parameters`.
PI_FELDER = (
    "pi_gain_per_k",
    "pi_integral_time_minutes",
    "pi_min_on_seconds",
    "pi_min_off_seconds",
)
GANZZAHLEN = GANZZAHLEN | (frozenset(PI_FELDER) - {"pi_gain_per_k"})

# Not part of `FELDER`: unlike the fields above, an empty value here does not mean
# "inherited from the global default" -- there is no meaningful plant-wide default for
# how much sun a particular room gets. It is its own, always-present, non-nullable
# zone value (default 0 -- off), so it gets its own bounds check instead of going
# through `_check_parameters`.
SOLAR_GAIN_FACTOR_LIMITS = (Decimal("0"), Decimal("1"))


def _zone_or_404(session: Session, principal: Principal, zone_id: int, permission: str) -> Zone:
    zone = next((z for z in visible_zones(session, principal, permission) if z.id == zone_id), None)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone nicht gefunden")
    return zone


def _parameter_page(
    request: Request,
    session: Session,
    zone: Zone,
    effective: ControlParameters,
    values: dict[str, str],
    errors: FormError | None = None,
) -> Response:
    # Shown *before* the switch can be offered (specification section 6): whether
    # this zone's current device assignment and control cycle even qualify for PI,
    # independent of whatever the form's own (possibly rejected) PI values say.
    eligibility = pi_eligibility(
        session,
        zone,
        pi_min_on_seconds=effective.pi_min_on_seconds,
        pi_min_off_seconds=effective.pi_min_off_seconds,
    )
    return form_again(
        request,
        "parameter.html",
        values,
        errors,
        zone=zone,
        effective=effective,
        pi_eligibility=eligibility,
        assumed_lifetime_operations=control_settings(session).assumed_relay_lifetime_operations,
    )


@router.get("/zones/{zone_id}/parameters")
async def show_parameter(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.manage")
    values = {
        name: str(getattr(zone, name)) if getattr(zone, name) is not None else "" for name in FELDER
    }
    values["solar_gain_factor"] = str(zone.solar_gain_factor)
    values["valve_protection_enabled"] = "yes" if zone.valve_protection_enabled else ""
    for name in PI_FELDER:
        values[name] = str(getattr(zone, name))
    values["pi_enabled"] = "yes" if zone.pi_enabled else ""
    values["pi_confirm"] = ""
    return _parameter_page(request, session, zone, control_parameters(session, zone), values)


def _check_parameters(values: dict[str, str]) -> dict[str, Decimal | int | None]:
    result: dict[str, Decimal | int | None] = {}
    for name, text in values.items():
        if not text:
            result[name] = None
            continue
        try:
            value: Decimal | int = (
                int(text) if name in GANZZAHLEN else Decimal(text.replace(",", "."))
            )
        except (ValueError, InvalidOperation) as exc:
            raise FormError(name, "Bitte eine gültige Zahl eingeben.") from exc
        if name != "temperature_offset_k" and value < 0:
            raise FormError(name, "Dieser Wert darf nicht negativ sein.")
        result[name] = value
    return result


def _check_solar_gain_factor(text: str) -> Decimal:
    """Always present, never empty (unlike the fields in `FELDER`) -- 0 is the
    documented off-state, not a missing value."""
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise FormError("solar_gain_factor", "Bitte eine gültige Zahl eingeben.") from exc
    lower, upper = SOLAR_GAIN_FACTOR_LIMITS
    if not lower <= value <= upper:
        raise FormError(
            "solar_gain_factor", f"Bitte einen Wert zwischen {lower} und {upper} angeben."
        )
    return value


@router.post("/zones/{zone_id}/parameters")
async def save_parameter(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "zone.manage")
    form = await request.form()
    values = {name: str(form.get(name, "")).strip() for name in FELDER}
    values["valve_protection_enabled"] = str(form.get("valve_protection_enabled", ""))
    raw_solar_gain_factor = str(form.get("solar_gain_factor", "")).strip()
    values["solar_gain_factor"] = raw_solar_gain_factor or str(zone.solar_gain_factor)
    for name in PI_FELDER:
        values[name] = str(form.get(name, "")).strip()
    values["pi_enabled"] = str(form.get("pi_enabled", ""))
    values["pi_confirm"] = str(form.get("pi_confirm", ""))
    try:
        checked = _check_parameters({name: values[name] for name in FELDER})
        checked["valve_protection_enabled"] = bool(values["valve_protection_enabled"])
        for name in (
            "valve_protection_interval_days",
            "valve_protection_duration_minutes",
        ):
            if checked[name] is None:
                checked[name] = getattr(zone, name)
        # Missing/empty leaves the zone's current value untouched -- unlike the
        # fields in `FELDER`, an empty `solar_gain_factor` cannot mean "inherit",
        # so there is nothing sensible left for it to mean except "unchanged".
        solar_gain_factor = (
            zone.solar_gain_factor
            if not raw_solar_gain_factor
            else _check_solar_gain_factor(raw_solar_gain_factor)
        )
        validate_valve_protection(checked)

        pi_checked = _check_parameters({name: values[name] for name in PI_FELDER})
        for name in PI_FELDER:
            if pi_checked[name] is None:
                pi_checked[name] = getattr(zone, name)
        checked.update(pi_checked)
        pi_enabled = bool(values["pi_enabled"])
        # The confirmation is only asked for the moment PI actually gets switched
        # on for this zone (specification section 8) -- not on every later save of
        # an already-enabled zone, and not when switching it back off.
        if pi_enabled and not zone.pi_enabled and not values["pi_confirm"]:
            raise FormError(
                "pi_confirm",
                "Bitte bestätigen, dass mehr Schaltspiele und eine falsche "
                "Parametrierung verstanden wurden, bevor PI (Beta) eingeschaltet wird.",
            )
        checked["pi_enabled"] = pi_enabled
        # Checked here, not just inside `save_control_parameters` below: that call
        # only runs once `zone.solar_gain_factor` is already set, and a rejected PI
        # value must not leave that already-committed the same way a bad valve-
        # protection value above must not, either (`validate_valve_protection`'s own
        # comment on `save_settings` for the same reasoning).
        validate_pi_parameters(session, zone, checked)
    except (FormError, ParameterOutOfRange) as exc:
        if isinstance(exc, ParameterOutOfRange):
            # Both `validate_valve_protection` and `validate_pi_parameters` raise
            # this same plain exception type without naming a field (like every
            # other multi-field domain check in this project) -- the message text
            # is the only way to route it back to roughly the right input.
            field = "pi_enabled" if "PI-" in str(exc) else "valve_protection_duration_minutes"
            exc = FormError(field, str(exc))
        return _parameter_page(
            request, session, zone, control_parameters(session, zone), values, exc
        )
    # Set before `save_control_parameters` runs: that call also writes the audit
    # entry, and its `object_type="zone_settings"` covers this field too -- a second,
    # near-identical entry right after it would only make the log harder to read.
    zone.solar_gain_factor = solar_gain_factor
    save_control_parameters(
        session, zone, checked, user_id=principal.user_id, token_id=principal.token_id
    )
    return RedirectResponse(
        prefixed(request, f"/zones/{zone.id}/parameters"), status.HTTP_303_SEE_OTHER
    )


@router.post("/zones/{zone_id}/override")
async def create_override_view(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "override.create")
    form = await request.form()
    temperature_text = str(form.get("temperature_c", "")).strip()
    kind = str(form.get("end", "permanent"))
    try:
        # Only the number itself now. The limit is checked by `uebersteuerung_anlegen`
        # further below -- it used to be here a second time too, with its own numbers,
        # and would have had to be kept in sync on the next change. This exact mistake
        # has already happened to the project once; the message comes from the domain.
        temperature = Decimal(temperature_text.replace(",", "."))
        if kind == "next_switch":
            end_at = end_of_next_switch(session, zone)
        elif kind == "duration":
            duration = int(str(form.get("duration_minutes", "")))
            if duration <= 0:
                raise ValueError
            end_at = utcnow() + timedelta(minutes=duration)
        elif kind == "permanent":
            end_at = None
        else:
            raise ValueError
    # Parentheses, even though Python 3.14 no longer requires them (PEP 758): without
    # them the line looks like the Python 2 form, which meant something different.
    except (InvalidOperation, ValueError):
        parameter = urlencode(
            {
                "override_errors": "Bitte Temperatur, Art und Dauer prüfen.",
                "zone_id": zone.id,
                "temperature_c": temperature_text,
                "end": kind,
                "duration_minutes": str(form.get("duration_minutes", "")),
            }
        )
        return RedirectResponse(prefixed(request, f"/?{parameter}"), status.HTTP_303_SEE_OTHER)
    try:
        create_override(
            session, zone, temperature, end_at,
            user_id=principal.user_id, token_id=principal.token_id,
        )
    except DomainError as exc:
        # The limit has lived in the domain since the final review, so it applies to
        # all three adapters. Here it is only displayed.
        parameter = urlencode(
            {
                "override_errors": exc.notice,
                "zone_id": zone.id,
                "temperature_c": temperature_text,
                "end": kind,
                "duration_minutes": str(form.get("duration_minutes", "")),
            }
        )
        return RedirectResponse(prefixed(request, f"/?{parameter}"), status.HTTP_303_SEE_OTHER)
    return RedirectResponse(prefixed(request, "/"), status.HTTP_303_SEE_OTHER)


@router.post("/zones/{zone_id}/override/cancel")
async def end_override(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    zone = _zone_or_404(session, principal, zone_id, "override.cancel")
    cancel_override(session, zone)
    return RedirectResponse(prefixed(request, "/"), status.HTTP_303_SEE_OTHER)


# One click on the start page's thermostat. A half step, because below that a room
# doesn't perceptibly change and you would otherwise click too often.
THERMOSTAT_STEP = Decimal("0.5")


@router.post("/zones/{zone_id}/thermostat")
async def adjust_thermostat(
    zone_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Adjusts the stored temperature of the mode currently in effect.

    This is explicitly **not** an override: whoever presses here changes the setpoint
    of the running mode permanently -- "day should be half a degree warmer", not "make
    it warmer just this once". Both are everyday requests, and doing both with the same
    control would be the surest way to hit the wrong one. That's why it shows next to
    it which mode is being adjusted.

    The step is calculated here against the *current* value, not in the browser: two
    clicks are then two steps, even if the page hasn't reloaded in between.
    """
    zone = _zone_or_404(session, principal, zone_id, "setpoint.write")
    form = await request.form()
    try:
        mode_id = int(str(form.get("mode_id", "")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kein Modus angegeben") from exc

    direction = str(form.get("direction", ""))
    if direction not in ("up", "down"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unbekannte Richtung")

    # The value the page shows -- not the stored row. The two are not the same: if a
    # zone has no own setpoint for frost protection, `aufgeloester_sollwert` shows the
    # fallback of 16 degrees. The thermostat used to look up the row, find none, and
    # respond with 404 -- on the page it looked as if nothing happened when pressed.
    # That is exactly the state of a freshly set-up plant where nobody has maintained
    # setpoints yet.
    current = temperature_for_mode(session, zone, mode_id)
    if current is None:
        shown = resolved_setpoint(session, zone, utcnow())
        if shown.mode_id == mode_id:
            current = shown.temperature_c
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Für diesen Modus gibt es keinen Sollwert")

    new = current + (THERMOSTAT_STEP if direction == "up" else -THERMOSTAT_STEP)
    try:
        update_setpoints(
            session, zone, {mode_id: new}, user_id=principal.user_id
        )
    except DomainError as exc:
        # Reached the limit. Not an error state, but the end of the road -- the
        # page simply shows the unchanged value afterward.
        parameter = urlencode({"thermostat_errors": exc.notice, "zone_id": zone.id})
        return RedirectResponse(prefixed(request, f"/?{parameter}"), status.HTTP_303_SEE_OTHER)
    return RedirectResponse(prefixed(request, "/"), status.HTTP_303_SEE_OTHER)
