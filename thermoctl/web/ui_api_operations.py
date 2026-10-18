"""Regelung, Betrieb und Einstellungen für die Svelte-Oberfläche (`/ui-api`).

Dünner Adapter (Grundsatz 6 und 7): Jeder Endpunkt ruft dieselben Domänen- und
Servicefunktionen wie zuvor die HTML-Oberfläche (Raumtechnik, Betrieb, Einstellungen,
Schnittstellen). Hier steht keine Fachregel.
Geprüft wird ausschließlich in der Domäne; dieser Adapter übersetzt deren Fehler in
`{detail, code[, field]}`.

Rechte wie in der früheren HTML-Oberfläche, in jedem Endpunkt neu geprüft:

- Raumtechnik: Zone sichtbar (`zone.read`, sonst dasselbe 404 wie unbekannt), dann
  `zone.manage` (403). Ausgleichswerte der Ersatzquelle zusätzlich `device.manage`.
- Betrieb lesen und Einstellungen lesen: `zone.read` anlagenweit. Freigabe setzen:
  `control.arm`. Einstellungen schreiben, Schnittstellen, Benachrichtigungen:
  `setting.manage`.
- Allein das Recht entscheidet; das Gruppenprofil sperrt keine dieser Seiten.

**Die drei Sperren bleiben getrennt**, wie auf der Betriebsseite: gespeicherte Freigabe
(`control_armed`), MQTT-Sendefreigabe (`sending_allowed`, beim Prozessstart gebaut) und die
Rolle im Verbund (`cluster_standby`). `PUT /operation/armed` ruft nur `domain.control.arm`
und fasst `app.state.sending_allowed` nie an -- die Freigabe wirkt erst nach einem Neustart.

Geheimnisse verlassen diesen Adapter nie im Klartext: Passwörter und Tokens der
Schnittstellen kommen aus der Domäne bereits als "hinterlegt"/"nicht hinterlegt"; die
Webhook-Adresse (kann ein Geheimnis im Pfad tragen) wird auf Schema und Rechner gekürzt.
Schreibwege für solche Werte gibt es nicht -- sie stehen in der Umgebung.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.sensor_failure import SensorFailureProfile
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission, visible_zones
from thermoctl.domain.control import (
    GANZZAHLIG,
    LABELS,
    LIMITS,
    WINDOW_ALARM_GANZZAHLIG,
    WINDOW_ALARM_LABELS,
    WINDOW_ALARM_LIMITS,
    WINDOW_TEMP_DROP_GANZZAHLIG,
    WINDOW_TEMP_DROP_LABELS,
    WINDOW_TEMP_DROP_LIMITS,
    ControlError,
    arm,
    save_sensor_failure_backup_offset,
    save_sensor_failure_plant_defaults,
    save_settings,
    save_solar_location,
    save_window_alarm_settings,
    save_window_temp_drop_settings,
    settings,
)
from thermoctl.domain.device_assignment import CapabilityMissing
from thermoctl.domain.interfaces import homebridge_zone_configs, overview
from thermoctl.domain.outdoor import outdoor_reading, set_outdoor_temperature_source
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.sensor_failure_policy import (
    CurvePoint,
    PolicyError,
    ProfileValues,
    effective_default_profile,
    list_backup_offset_candidates,
)
from thermoctl.domain.sensor_failure_policy import read_defaults as read_sensor_failure_defaults
from thermoctl.domain.statistics import pi_switching_model
from thermoctl.domain.zone_settings import (
    BY_NAME,
    ControlParameters,
    ParameterOutOfRange,
    control_parameters,
    pi_eligibility,
    save_control_parameters,
    save_sensor_failure_parameters,
    sensor_failure_parameters,
    set_window_temp_drop_detection,
    validate_pi_parameters,
    validate_valve_protection,
)
from thermoctl.domain.zones import latest_decisions_by_zone
from thermoctl.integrations import notification
from thermoctl.services import cluster
from thermoctl.services.emergency_state import zone_emergency_view
from thermoctl.web.forms import FormError
from thermoctl.web.shared.control import PI_FALLBACK_LABELS, TEST_COOLDOWN
from thermoctl.web.shared.zone_parameters import (
    FELDER,
    PI_FELDER,
    check_parameters,
    check_solar_gain_factor,
)
from thermoctl.web.ui_api_rooms import (
    ActuationOut,
    RoomApiError,
    _actuation,
    _dec,
    _iso,
    _RoomsRoute,
    _settings_or_error,
    _visible_zone,
)

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

Sess = Annotated[Session, Depends(get_session)]
Prin = Annotated[Principal, Depends(current_principal)]

#: Zahlen kommen als Text oder ganze Zahl; die Domäne prüft den Inhalt.
Number = str | int | None


# -- Rechte -----------------------------------------------------------------------------


def _forbidden() -> RoomApiError:
    return RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _need(principal: Principal, permission: str) -> None:
    """Anlagenweites Recht (wie `authz.require` in den Views)."""
    if not has_permission(principal, permission):
        raise _forbidden()


def _need_zone_manage(session: Session, principal: Principal, zone_id: int) -> Zone:
    zone = _visible_zone(session, principal, zone_id)
    if not has_permission(principal, "zone.manage", zone.id):
        raise _forbidden()
    return zone


def _text(value: Number) -> str:
    return "" if value is None else str(value).strip()


def _num(value: object) -> str:
    return format(value, "f") if isinstance(value, Decimal) else str(value)


# -- Raumtechnik: Ausgabe -----------------------------------------------------------------


class ParameterLimitOut(BaseModel):
    minimum: str
    maximum: str
    step: str
    unit: str | None
    label: str


class ParameterOut(BaseModel):
    """Eigener Wert und wirksamer Wert getrennt: `own` ist `null`, wenn die Zone erbt."""

    own: str | None
    effective: str
    inherited: bool
    source: Literal["zone", "plant", "default"]
    limit: ParameterLimitOut


class PiOut(BaseModel):
    enabled: bool
    gain_per_k: str
    integral_time_minutes: int
    min_on_seconds: int
    min_off_seconds: int
    eligible: bool
    eligibility_reason: str
    limits: dict[str, ParameterLimitOut]


class ValveProtectionOut(BaseModel):
    enabled: bool
    interval_days: int
    duration_minutes: int


class ProfileOptionOut(BaseModel):
    id: int
    name: str


class ZoneSensorFailureOut(BaseModel):
    own_enabled: bool
    own_profile_id: int | None
    own_emergency_setpoint_c: str | None
    effective_profile_id: int
    effective_profile_name: str
    profile_source: str
    effective_emergency_setpoint_c: str
    setpoint_source: str
    profiles: list[ProfileOptionOut]


class BackupOffsetOut(BaseModel):
    assignment_id: int
    device_name: str
    offset_k: str


class SwitchingModelRowOut(BaseModel):
    id: str
    per_hour: int
    per_day: int
    per_year: int


class PiSwitchingModelOut(BaseModel):
    """Modellrechnung zum Verschleißhinweis an der PI-Regelung; der Server rechnet."""

    rows: list[SwitchingModelRowOut]
    worst_case_per_year: int
    assumed_lifetime_operations: int
    worst_case_ratio: str


class ParametersOut(BaseModel):
    zone_id: int
    name: str
    control: dict[str, ParameterOut]
    solar_gain_factor: str
    valve_protection: ValveProtectionOut
    pi: PiOut
    window_temp_drop_detection_enabled: bool
    sensor_failure: ZoneSensorFailureOut
    #: `null`, wenn dieser Principal die Ausgleichswerte nicht sehen darf (`device.manage`).
    backup_offsets: list[BackupOffsetOut] | None
    assumed_relay_lifetime_operations: int
    pi_switching_model: PiSwitchingModelOut


# -- Raumtechnik: Eingabe -----------------------------------------------------------------


class BackupOffsetIn(BaseModel):
    assignment_id: int
    offset_k: Number = None


class ParametersIn(BaseModel):
    """Nur gesendete Felder ändern etwas; ein fehlendes Feld bleibt unverändert.

    Abweichung zum früheren HTML-Formular, das immer alles sendete: Dort bedeutet ein leeres Feld
    "erben" und ein fehlender Haken "aus". Hier ist `null` bei einem erbbaren Feld
    "erben", ein fehlendes Feld "unverändert" -- ein Teil-Aufruf kann so nie still
    etwas ausschalten.
    """

    hysteresis_k: Number = None
    min_on_seconds: Number = None
    min_off_seconds: Number = None
    sensor_timeout_seconds: Number = None
    temperature_offset_k: Number = None
    window_resume_delay_seconds: Number = None
    solar_setback_max_k: Number = None
    solar_gain_factor: Number = None
    valve_protection_enabled: bool | None = None
    valve_protection_interval_days: Number = None
    valve_protection_duration_minutes: Number = None
    pi_enabled: bool | None = None
    pi_confirm: bool = False
    pi_gain_per_k: Number = None
    pi_integral_time_minutes: Number = None
    pi_min_on_seconds: Number = None
    pi_min_off_seconds: Number = None
    window_temp_drop_detection_enabled: bool | None = None
    sensor_failure_enabled: bool | None = None
    sensor_failure_profile_id: int | None = None
    sensor_failure_emergency_setpoint_c: Number = None
    backup_offsets: list[BackupOffsetIn] | None = None


# -- Raumtechnik: Hilfen ------------------------------------------------------------------


def _limit(name: str) -> ParameterLimitOut:
    description = BY_NAME[name]
    return ParameterLimitOut(
        minimum=_num(description.minimum),
        maximum=_num(description.maximum),
        step=_num(description.step),
        unit=description.unit,
        label=description.label,
    )


def _parameters(session: Session, principal: Principal, zone: Zone) -> ParametersOut:
    effective: ControlParameters = control_parameters(session, zone)
    control: dict[str, ParameterOut] = {}
    for name in FELDER[:7]:
        own = getattr(zone, name)
        source: Literal["zone", "plant", "default"]
        if own is not None:
            source = "zone"
        elif name == "temperature_offset_k":
            source = "default"  # kein Anlagenwert; die Domäne erbt hier fest 0
        else:
            source = "plant"
        control[name] = ParameterOut(
            own=_num(own) if own is not None else None,
            effective=_num(getattr(effective, name)),
            inherited=own is None,
            source=source,
            limit=_limit(name),
        )
    eligibility = pi_eligibility(
        session,
        zone,
        pi_min_on_seconds=effective.pi_min_on_seconds,
        pi_min_off_seconds=effective.pi_min_off_seconds,
    )
    policy = sensor_failure_parameters(session, zone)
    profiles = session.scalars(
        select(SensorFailureProfile).order_by(SensorFailureProfile.name, SensorFailureProfile.id)
    ).all()
    offsets: list[BackupOffsetOut] | None = None
    if has_permission(principal, "device.manage", zone.id):
        offsets = [
            BackupOffsetOut(
                assignment_id=candidate.zone_device_id,
                device_name=candidate.device_name,
                offset_k=_num(candidate.offset_k),
            )
            for candidate in list_backup_offset_candidates(session, zone)
        ]
    lifetime = settings(session).assumed_relay_lifetime_operations
    model = pi_switching_model(lifetime)
    return ParametersOut(
        zone_id=zone.id,
        name=zone.display_name,
        control=control,
        solar_gain_factor=_num(zone.solar_gain_factor),
        valve_protection=ValveProtectionOut(
            enabled=zone.valve_protection_enabled,
            interval_days=zone.valve_protection_interval_days,
            duration_minutes=zone.valve_protection_duration_minutes,
        ),
        pi=PiOut(
            enabled=zone.pi_enabled,
            gain_per_k=_num(zone.pi_gain_per_k),
            integral_time_minutes=zone.pi_integral_time_minutes,
            min_on_seconds=zone.pi_min_on_seconds,
            min_off_seconds=zone.pi_min_off_seconds,
            eligible=eligibility.eligible,
            eligibility_reason=eligibility.reason,
            limits={name: _limit(name) for name in PI_FELDER},
        ),
        window_temp_drop_detection_enabled=zone.window_temp_drop_detection_enabled,
        sensor_failure=ZoneSensorFailureOut(
            own_enabled=zone.sensor_failure_enabled,
            own_profile_id=zone.sensor_failure_profile_id,
            own_emergency_setpoint_c=_dec(zone.sensor_failure_emergency_setpoint_c),
            effective_profile_id=policy.profile.id,
            effective_profile_name=policy.profile.values.name,
            profile_source=policy.profile_source,
            effective_emergency_setpoint_c=_num(policy.emergency_setpoint_c),
            setpoint_source=policy.setpoint_source,
            profiles=[ProfileOptionOut(id=row.id, name=row.name) for row in profiles],
        ),
        backup_offsets=offsets,
        assumed_relay_lifetime_operations=lifetime,
        pi_switching_model=PiSwitchingModelOut(
            rows=[
                SwitchingModelRowOut(
                    id=row.id, per_hour=row.per_hour, per_day=row.per_day, per_year=row.per_year
                )
                for row in model.rows
            ],
            worst_case_per_year=model.worst_case_per_year,
            assumed_lifetime_operations=model.assumed_lifetime_operations,
            worst_case_ratio=format(model.worst_case_ratio, "f"),
        ),
    )


def _parameter_field_for(message: str) -> str | None:
    """Ordnet eine Domänenmeldung ohne Feldangabe dem Eingabefeld zu.

    `validate_valve_protection` und `validate_pi_parameters` nennen kein Feld; die
    Meldung enthält aber die Bezeichnung (`BY_NAME[...].label`) bzw. "Ventilschutz-...".
    """
    if message.startswith("Ventilschutz-Abstand"):
        return "valve_protection_interval_days"
    if message.startswith("Ventilschutz"):
        return "valve_protection_duration_minutes"
    for name in PI_FELDER:
        if BY_NAME[name].label in message:
            return name
    if "PI-" in message:
        return "pi_enabled"
    return None


def _parameter_error(exc: ParameterOutOfRange) -> RoomApiError:
    return RoomApiError(422, str(exc), "validation", _parameter_field_for(str(exc)))


def _form_error(exc: FormError) -> RoomApiError:
    return RoomApiError(422, exc.notice, "validation", exc.field)


# -- Raumtechnik: Endpunkte ---------------------------------------------------------------


@router.get("/rooms/{zone_id}/parameters")
def show_parameters(zone_id: int, principal: Prin, session: Sess) -> ParametersOut:
    """Parameter einer Zone, mit Effektivwerten und Eigenwerten getrennt."""
    zone = _need_zone_manage(session, principal, zone_id)
    return _parameters(session, principal, zone)


@router.put("/rooms/{zone_id}/parameters")
def save_parameters(
    zone_id: int, body: ParametersIn, principal: Prin, session: Sess
) -> ParametersOut:
    """Parameter speichern: dieselben Prüfungen, dieselben Domänenrufe,
    dieselbe verschachtelte Transaktion; Fehler als `{detail, code: validation, field}`."""
    zone = _need_zone_manage(session, principal, zone_id)
    sent = body.model_fields_set
    may_manage_backup_offsets = has_permission(principal, "device.manage", zone.id)
    if body.backup_offsets is not None and not may_manage_backup_offsets:
        # Das Formular zeigt die Felder ohne `device.manage` gar nicht; ein JSON-Aufruf
        # würde sie sonst still ignorieren. Hier wird ausdrücklich verweigert.
        raise _forbidden()

    # Ausgangslage: der gespeicherte Stand. Was gesendet wurde, ersetzt ihn.
    values: dict[str, str] = {
        name: (str(getattr(zone, name)) if getattr(zone, name) is not None else "")
        for name in FELDER
    }
    for name in (*FELDER, *PI_FELDER):
        if name in sent:
            values[name] = _text(getattr(body, name))
    for name in PI_FELDER:
        values.setdefault(name, str(getattr(zone, name)))
    raw_solar_gain_factor = _text(body.solar_gain_factor) if "solar_gain_factor" in sent else ""
    valve_enabled = (
        zone.valve_protection_enabled
        if body.valve_protection_enabled is None
        else body.valve_protection_enabled
    )
    pi_enabled = zone.pi_enabled if body.pi_enabled is None else body.pi_enabled
    window_drop = (
        zone.window_temp_drop_detection_enabled
        if body.window_temp_drop_detection_enabled is None
        else body.window_temp_drop_detection_enabled
    )
    sensor_failure_enabled = (
        zone.sensor_failure_enabled
        if body.sensor_failure_enabled is None
        else body.sensor_failure_enabled
    )
    profile_id = (
        body.sensor_failure_profile_id
        if "sensor_failure_profile_id" in sent
        else zone.sensor_failure_profile_id
    )
    emergency_text = (
        _text(body.sensor_failure_emergency_setpoint_c)
        if "sensor_failure_emergency_setpoint_c" in sent
        else (
            str(zone.sensor_failure_emergency_setpoint_c)
            if zone.sensor_failure_emergency_setpoint_c is not None
            else ""
        )
    )

    candidates = list_backup_offset_candidates(session, zone)
    backup_texts: dict[int, str] = {}
    if body.backup_offsets is not None:
        known = {candidate.zone_device_id for candidate in candidates}
        for entry in body.backup_offsets:
            if entry.assignment_id not in known:
                raise RoomApiError(
                    422,
                    "Diese Zuordnung ist für den Ausgleichswert nicht vorgesehen.",
                    "validation",
                    f"backup_offset_{entry.assignment_id}",
                )
            backup_texts[entry.assignment_id] = _text(entry.offset_k)

    try:
        checked = check_parameters({name: values[name] for name in FELDER})
        checked["valve_protection_enabled"] = valve_enabled
        for name in ("valve_protection_interval_days", "valve_protection_duration_minutes"):
            if checked[name] is None:
                checked[name] = getattr(zone, name)
        solar_gain_factor = (
            zone.solar_gain_factor
            if not raw_solar_gain_factor
            else check_solar_gain_factor(raw_solar_gain_factor)
        )
        validate_valve_protection(checked)

        pi_checked = check_parameters({name: values[name] for name in PI_FELDER})
        for name in PI_FELDER:
            if pi_checked[name] is None:
                pi_checked[name] = getattr(zone, name)
        checked.update(pi_checked)
        # Die Bestätigung wird nur in dem Moment verlangt, in dem PI für diese Zone
        # tatsächlich eingeschaltet wird -- nicht bei jedem späteren Speichern.
        if pi_enabled and not zone.pi_enabled and not body.pi_confirm:
            raise FormError(
                "pi_confirm",
                "Bitte bestätigen, dass mehr Schaltspiele und eine falsche "
                "Parametrierung verstanden wurden, bevor PI (Beta) eingeschaltet wird.",
            )
        checked["pi_enabled"] = pi_enabled
        validate_pi_parameters(session, zone, checked)

        try:
            emergency_setpoint = (
                Decimal(emergency_text.replace(",", ".")) if emergency_text else None
            )
        except InvalidOperation as exc:
            raise FormError(
                "sensor_failure_emergency_setpoint_c", "Bitte eine gültige Zahl angeben."
            ) from exc
        backup_offsets: dict[int, Decimal | None] = {}
        for assignment_id, text in backup_texts.items():
            try:
                backup_offsets[assignment_id] = Decimal(text.replace(",", ".")) if text else None
            except InvalidOperation as exc:
                raise FormError(
                    f"backup_offset_{assignment_id}", "Bitte eine gültige Zahl angeben."
                ) from exc
    except FormError as exc:
        raise _form_error(exc) from exc
    except ParameterOutOfRange as exc:
        raise _parameter_error(exc) from exc

    # Zwei getrennt validierte Domänenaufrufe (Regelparameter, Notbetriebsprofil) in einer
    # Transaktion: scheitert der zweite, nimmt der Savepoint auch den ersten zurück.
    try:
        with session.begin_nested():
            zone.solar_gain_factor = solar_gain_factor
            save_control_parameters(
                session, zone, checked, user_id=principal.user_id, token_id=principal.token_id
            )
            set_window_temp_drop_detection(
                session,
                zone,
                window_drop,
                user_id=principal.user_id,
                token_id=principal.token_id,
            )
            save_sensor_failure_parameters(
                session,
                zone,
                enabled=sensor_failure_enabled,
                profile_id=profile_id,
                emergency_setpoint_c=emergency_setpoint,
                user_id=principal.user_id,
                token_id=principal.token_id,
            )
            for assignment_id, offset in backup_offsets.items():
                save_sensor_failure_backup_offset(
                    session,
                    assignment_id,
                    offset,
                    user_id=principal.user_id,
                    token_id=principal.token_id,
                )
    except PolicyError as exc:
        field_map = {
            "profile_id": "sensor_failure_profile_id",
            "emergency_setpoint_c": "sensor_failure_emergency_setpoint_c",
            "fixed_on_seconds": "sensor_failure_profile_id",
            "fixed_off_seconds": "sensor_failure_profile_id",
        }
        raise RoomApiError(
            422,
            exc.notice,
            "validation",
            field_map.get(exc.field, "sensor_failure_profile_id"),
        ) from exc
    except ParameterOutOfRange as exc:
        # Aus `save_control_parameters` (Festtakt des Notbetriebsprofils gegen die
        # neuen Mindestzeiten); die frühere Oberfläche fing das nicht, 500.
        raise _parameter_error(exc) from exc
    return _parameters(session, principal, zone)


# -- Betrieb: Ausgabe ---------------------------------------------------------------------


class ClusterOut(BaseModel):
    """Die dritte Sperre: die Rolle dieser Instanz im Aktiv-Bereitschafts-Verbund."""

    instance_id: str
    standby: bool
    takeover_timeout_seconds: int


class OperationActuationOut(BaseModel):
    """Die drei Sperren getrennt -- nie zu einem "scharf" zusammengezogen.

    `armed` ist die gespeicherte Freigabe, `sending_allowed` der beim Prozessstart gebaute
    MQTT-Riegel, `cluster_standby` die Rolle im Verbund. `live` ist nur dann wahr, wenn
    Freigabe **und** Sendefreigabe stehen; `dry_run` ist das Gegenteil davon (Schattenbetrieb:
    Entscheidungen werden protokolliert, nichts geht an Aktoren). `restart_required` meint:
    gespeichert freigegeben, aber der Prozess wurde seither nicht neu gestartet.
    """

    armed: bool
    sending_allowed: bool
    cluster_standby: bool
    cluster_state: Literal["leading", "standby", "determining"]
    determining_until: str | None
    live: bool
    dry_run: bool
    restart_required: bool
    state: Literal["dry_run", "armed_restart_missing", "live"]


class DecisionDetailOut(BaseModel):
    """Die letzte Regelentscheidung samt Begründung -- eine Entscheidung, kein Schaltzustand."""

    decided_at: str
    would_heat: bool
    previous_would_heat: bool | None
    outcome_code: str
    reason: str
    temperature_c: str | None
    setpoint_c: str | None
    setpoint_reason: str
    planned_setpoint_c: str | None
    solar_setback_k: str | None
    requested_controller: str
    effective_controller: str
    controller_fallback_reason: str | None
    controller_fallback_label: str | None


class EmergencyActuatorOut(BaseModel):
    device_id: int
    device_name: str
    kind: str
    phase: str | None
    phase_deadline_at: str | None
    on_seconds: int | None
    off_seconds: int | None
    cycle_source: str | None
    handover_status_text: str
    restore_status_text: str


class EmergencyComparisonOut(BaseModel):
    device_id: int
    device_name: str
    sample_count: int
    mean_deviation_k: str | None
    suggested_offset_text: str | None


class EmergencyDetailOut(BaseModel):
    stage: str
    stage_label: str
    failure_started_at: str | None
    active_source_device_name: str | None
    sensor_timeout_seconds: int | None
    recovery_sample_count: int
    recovery_samples: int | None
    emergency_setpoint_c: str | None
    outdoor_status_text: str | None
    outdoor_temperature_c: str | None
    actuators: list[EmergencyActuatorOut]
    comparisons: list[EmergencyComparisonOut]


class OperationRoomOut(BaseModel):
    id: int
    name: str
    temperature_c: str | None
    setpoint_c: str
    setpoint_reason: str
    decision: DecisionDetailOut | None
    emergency: EmergencyDetailOut


class OperationOut(BaseModel):
    actuation: OperationActuationOut
    cluster: ClusterOut
    may_arm: bool
    rooms: list[OperationRoomOut]


class ArmedOut(BaseModel):
    changed: bool
    actuation: OperationActuationOut


class ArmedIn(BaseModel):
    armed: bool
    reason: str = ""


# -- Betrieb: Hilfen ----------------------------------------------------------------------


def _operation_actuation(request: Request, session: Session, row: Any) -> OperationActuationOut:
    base: ActuationOut = _actuation(request, session, row)
    live = base.state == "live"
    return OperationActuationOut(
        armed=base.control_armed,
        sending_allowed=base.sending_allowed,
        cluster_standby=base.cluster_standby,
        cluster_state=base.cluster_state,
        determining_until=base.determining_until,
        live=live,
        dry_run=not live,
        restart_required=base.state == "armed_restart_missing",
        state=base.state,
    )


def _cluster(session: Session, row: Any) -> ClusterOut:
    return ClusterOut(
        instance_id=cluster.instance_id(),
        standby=not cluster.is_leader(session, holder=cluster.instance_id()),
        takeover_timeout_seconds=cluster.takeover_timeout_seconds(row),
    )


def _operation_rooms(
    session: Session, zones: list[Zone], now: datetime, row: Any
) -> list[OperationRoomOut]:
    states = {
        zone_id: state
        for zone_id, state in session.execute(
            select(ZoneState.zone_id, ZoneState)
            .join(SensorStatus, SensorStatus.id == ZoneState.sensor_status_id)
            .where(ZoneState.zone_id.in_([zone.id for zone in zones]))
        )
    }
    decisions = latest_decisions_by_zone(session, [zone.id for zone in zones])
    rooms: list[OperationRoomOut] = []
    for zone in zones:
        state = states.get(zone.id)
        setpoint = resolved_setpoint(session, zone, now)
        decision = decisions.get(zone.id)
        view = zone_emergency_view(session, zone, now, row)
        rooms.append(
            OperationRoomOut(
                id=zone.id,
                name=zone.display_name,
                temperature_c=_dec(state.temperature_c) if state is not None else None,
                setpoint_c=format(setpoint.temperature_c, "f"),
                setpoint_reason=setpoint.reason,
                decision=(
                    DecisionDetailOut(
                        decided_at=cast("str", _iso(decision.decided_at)),
                        would_heat=decision.would_heat,
                        previous_would_heat=decision.previous_would_heat,
                        outcome_code=decision.outcome_code,
                        reason=decision.reason,
                        temperature_c=_dec(decision.temperature_c),
                        setpoint_c=_dec(decision.setpoint_c),
                        setpoint_reason=decision.setpoint_reason,
                        planned_setpoint_c=_dec(decision.scheduled_setpoint_c),
                        solar_setback_k=_dec(decision.solar_setback_k),
                        requested_controller=decision.requested_controller,
                        effective_controller=decision.effective_controller,
                        controller_fallback_reason=decision.controller_fallback_reason,
                        controller_fallback_label=(
                            PI_FALLBACK_LABELS.get(
                                decision.controller_fallback_reason,
                                decision.controller_fallback_reason,
                            )
                            if decision.controller_fallback_reason is not None
                            else None
                        ),
                    )
                    if decision is not None
                    else None
                ),
                emergency=EmergencyDetailOut(
                    stage=view.stage,
                    stage_label=view.stage_label,
                    failure_started_at=_iso(view.failure_started_at),
                    active_source_device_name=view.active_source_device_name,
                    sensor_timeout_seconds=view.sensor_timeout_seconds,
                    recovery_sample_count=view.recovery_sample_count,
                    recovery_samples=view.recovery_samples,
                    emergency_setpoint_c=_dec(view.emergency_setpoint_c),
                    outdoor_status_text=view.outdoor_status_text,
                    outdoor_temperature_c=(
                        _dec(view.outdoor.temperature_c) if view.outdoor is not None else None
                    ),
                    actuators=[
                        EmergencyActuatorOut(
                            device_id=actuator.device_id,
                            device_name=actuator.device_name,
                            kind=actuator.kind,
                            phase=actuator.phase,
                            phase_deadline_at=_iso(actuator.phase_deadline_at),
                            on_seconds=actuator.on_seconds,
                            off_seconds=actuator.off_seconds,
                            cycle_source=actuator.cycle_source,
                            handover_status_text=actuator.handover_status_text,
                            restore_status_text=actuator.restore_status_text,
                        )
                        for actuator in view.actuators
                    ],
                    comparisons=[
                        EmergencyComparisonOut(
                            device_id=comparison.device_id,
                            device_name=comparison.device_name,
                            sample_count=comparison.sample_count,
                            mean_deviation_k=_dec(comparison.mean_deviation_k),
                            suggested_offset_text=comparison.suggested_offset_text,
                        )
                        for comparison in view.comparisons
                    ],
                ),
            )
        )
    return rooms


def _control_error(exc: ControlError) -> RoomApiError:
    return RoomApiError(422, exc.notice, "validation", exc.field or None)


# -- Betrieb: Endpunkte -------------------------------------------------------------------


@router.get("/operation")
def show_operation(request: Request, principal: Prin, session: Sess) -> OperationOut:
    """Betrieb lesen: wer die Anlage sehen darf, darf das lesen."""
    _need(principal, "zone.read")
    row = _settings_or_error(session)
    zones = visible_zones(session, principal, "zone.read")
    return OperationOut(
        actuation=_operation_actuation(request, session, row),
        cluster=_cluster(session, row),
        may_arm=has_permission(principal, "control.arm"),
        rooms=_operation_rooms(session, zones, utcnow(), row),
    )


@router.put("/operation/armed")
def set_armed(request: Request, body: ArmedIn, principal: Prin, session: Sess) -> ArmedOut:
    """Freigabe setzen: gleiche Domänenfunktion, gleiche Prüfung (Grund Pflicht
    beim Scharfschalten), gleicher Protokolleintrag.

    Setzt ausschließlich die **gespeicherte** Freigabe. Der MQTT-Startriegel
    (`app.state.sending_allowed`) wird hier weder gelesen noch geschrieben: Er entsteht
    beim Prozessstart, und eine Freigabe im laufenden Betrieb öffnet ihn nicht.
    """
    _need(principal, "control.arm")
    row = _settings_or_error(session)
    try:
        changed = arm(
            session,
            body.armed,
            reason=body.reason,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        raise _control_error(exc) from exc
    return ArmedOut(changed=changed, actuation=_operation_actuation(request, session, row))


# -- Einstellungen: Ausgabe ---------------------------------------------------------------


class NumberFieldOut(BaseModel):
    name: str
    label: str
    value: str
    minimum: str
    maximum: str
    integer: bool


class SolarOut(BaseModel):
    enabled: bool
    latitude: str | None
    longitude: str | None


class CurvePointModel(BaseModel):
    outdoor_c: str
    on_seconds: int
    off_seconds: int


class SensorFailureDefaultsOut(BaseModel):
    profile_id: int
    profile_name: str
    fixed_on_seconds: int
    fixed_off_seconds: int
    recovery_seconds: int
    recovery_samples: int
    warm_restart_hysteresis_k: str
    curve_points: list[CurvePointModel]
    emergency_setpoint_c: str


class DeviceOptionOut(BaseModel):
    id: int
    name: str


class OutdoorReadingOut(BaseModel):
    status: str
    temperature_c: str | None
    measured_at: str | None


class OutdoorOut(BaseModel):
    source_device_id: int | None
    source_device_name: str | None
    reading: OutdoorReadingOut
    devices: list[DeviceOptionOut]


class NotificationsOut(BaseModel):
    """Kanäle und Ergebnis des letzten Versuchs. Die Webhook-Adresse und das Token stehen
    nie hier -- nur, ob ein Webhook in der Umgebung gesetzt ist."""

    sensor_faults: bool
    bridge_faults: bool
    command_failures: bool
    stuck_sensor: bool
    window_alarm: bool
    tenant_reports: bool
    last_attempt_at: str | None
    last_ok: bool | None
    last_error: str | None
    webhook_configured: bool


class SettingsOut(BaseModel):
    may_edit: bool
    defaults: list[NumberFieldOut]
    timezone: str
    solar: SolarOut
    window_alarm: list[NumberFieldOut]
    window_temp_drop: list[NumberFieldOut]
    sensor_failure: SensorFailureDefaultsOut
    outdoor: OutdoorOut
    notifications: NotificationsOut


class NotificationTestOut(BaseModel):
    ok: bool
    status_code: int | None
    duration_seconds: float
    error: str | None


# -- Einstellungen: Eingabe ---------------------------------------------------------------


class DefaultsIn(BaseModel):
    """Alle Felder aus `domain.control.LIMITS` plus Zeitzone; was fehlt, meldet die Domäne
    ("Bitte einen Wert angeben") -- hier wird nichts aufgefüllt."""

    values: dict[str, str | int]
    timezone: str


class SolarLocationIn(BaseModel):
    enabled: bool
    latitude: str | None = None
    longitude: str | None = None


class ThresholdsIn(BaseModel):
    values: dict[str, str | int]


class SensorFailureDefaultsIn(BaseModel):
    fixed_on_seconds: Number
    fixed_off_seconds: Number
    recovery_seconds: Number
    recovery_samples: Number
    warm_restart_hysteresis_k: Number
    emergency_setpoint_c: Number
    curve_points: list[CurvePointModel] = []


class OutdoorSourceIn(BaseModel):
    device_id: int | None = None


class NotificationsIn(BaseModel):
    sensor_faults: bool
    bridge_faults: bool
    command_failures: bool
    stuck_sensor: bool
    window_alarm: bool
    tenant_reports: bool


# -- Einstellungen: Hilfen ----------------------------------------------------------------


def _number_fields(
    row: Any,
    limits: dict[str, tuple[Decimal, Decimal]],
    labels: dict[str, str],
    integers: frozenset[str],
) -> list[NumberFieldOut]:
    return [
        NumberFieldOut(
            name=name,
            label=labels[name],
            value=_num(getattr(row, name)),
            minimum=_num(lower),
            maximum=_num(upper),
            integer=name in integers,
        )
        for name, (lower, upper) in limits.items()
    ]


def _settings_out(session: Session, principal: Principal) -> SettingsOut:
    row = settings(session)
    profile = effective_default_profile(session)
    outdoor_source = (
        session.get(Device, row.outdoor_temperature_source_device_id)
        if row.outdoor_temperature_source_device_id is not None
        else None
    )
    reading = outdoor_reading(session, row, utcnow())
    return SettingsOut(
        may_edit=has_permission(principal, "setting.manage"),
        defaults=_number_fields(row, LIMITS, LABELS, GANZZAHLIG),
        timezone=row.timezone,
        solar=SolarOut(
            enabled=row.solar_forecast_enabled,
            latitude=_dec(row.solar_forecast_latitude),
            longitude=_dec(row.solar_forecast_longitude),
        ),
        window_alarm=_number_fields(
            row, WINDOW_ALARM_LIMITS, WINDOW_ALARM_LABELS, WINDOW_ALARM_GANZZAHLIG
        ),
        window_temp_drop=_number_fields(
            row, WINDOW_TEMP_DROP_LIMITS, WINDOW_TEMP_DROP_LABELS, WINDOW_TEMP_DROP_GANZZAHLIG
        ),
        sensor_failure=SensorFailureDefaultsOut(
            profile_id=profile.id,
            profile_name=profile.values.name,
            fixed_on_seconds=profile.values.fixed_on_seconds,
            fixed_off_seconds=profile.values.fixed_off_seconds,
            recovery_seconds=profile.values.recovery_seconds,
            recovery_samples=profile.values.recovery_samples,
            warm_restart_hysteresis_k=_num(profile.values.warm_restart_hysteresis_k),
            curve_points=[
                CurvePointModel(
                    outdoor_c=_num(point.outdoor_c),
                    on_seconds=point.on_seconds,
                    off_seconds=point.off_seconds,
                )
                for point in profile.values.curve_points
            ],
            emergency_setpoint_c=_num(read_sensor_failure_defaults(session).emergency_setpoint_c),
        ),
        outdoor=OutdoorOut(
            source_device_id=outdoor_source.id if outdoor_source is not None else None,
            source_device_name=(
                outdoor_source.display_name if outdoor_source is not None else None
            ),
            reading=OutdoorReadingOut(
                status=reading.status,
                temperature_c=_dec(reading.temperature_c),
                measured_at=_iso(reading.measured_at),
            ),
            devices=[
                DeviceOptionOut(id=device.id, name=device.display_name)
                for device in session.scalars(
                    select(Device).order_by(Device.display_name, Device.id)
                )
            ],
        ),
        notifications=NotificationsOut(
            sensor_faults=row.notify_sensor_faults,
            bridge_faults=row.notify_bridge_faults,
            command_failures=row.notify_command_failures,
            stuck_sensor=row.notify_stuck_sensor,
            window_alarm=row.notify_window_alarm,
            tenant_reports=row.notify_tenant_reports,
            last_attempt_at=_iso(row.notify_last_attempt_at),
            last_ok=row.notify_last_ok,
            last_error=row.notify_last_error,
            webhook_configured=get_settings().notify_webhook is not None,
        ),
    )


def _known_fields(values: dict[str, str | int], allowed: dict[str, Any]) -> dict[str, str]:
    """Ein unbekannter Schlüssel ist ein Fehler, kein stilles Ignorieren."""
    for name in values:
        if name not in allowed:
            raise RoomApiError(422, "Unbekanntes Feld.", "validation", name)
    return {name: str(value).strip() for name, value in values.items()}


# -- Einstellungen: Endpunkte -------------------------------------------------------------


@router.get("/settings")
def show_settings(principal: Prin, session: Sess) -> SettingsOut:
    """Einstellungen lesen: `zone.read` anlagenweit; `may_edit` sagt, ob der
    Principal speichern darf."""
    _need(principal, "zone.read")
    _settings_or_error(session)
    return _settings_out(session, principal)


@router.put("/settings/defaults")
def save_defaults(body: DefaultsIn, principal: Prin, session: Sess) -> SettingsOut:
    """Regelvorgaben und Zeitzone (`domain.control.save_settings`)."""
    _need(principal, "setting.manage")
    _settings_or_error(session)
    values = _known_fields(body.values, LIMITS)
    try:
        save_settings(
            session,
            values,
            body.timezone,
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        raise _control_error(exc) from exc
    return _settings_out(session, principal)


@router.put("/settings/solar-location")
def save_solar(body: SolarLocationIn, principal: Prin, session: Sess) -> SettingsOut:
    """Sonnenabsenkung ein/aus und Standort (`domain.control.save_solar_location`).

    Getrennt von den Regelvorgaben (wie REST): ein abgelehnter Standort lässt die
    Regelvorgaben nicht halb geschrieben zurück, weil sie gar nicht im selben Aufruf stehen.
    """
    _need(principal, "setting.manage")
    _settings_or_error(session)
    try:
        save_solar_location(
            session,
            enabled=body.enabled,
            latitude_text=body.latitude or "",
            longitude_text=body.longitude or "",
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except ControlError as exc:
        raise _control_error(exc) from exc
    return _settings_out(session, principal)


@router.put("/settings/window-alarm")
def save_window_alarm(body: ThresholdsIn, principal: Prin, session: Sess) -> SettingsOut:
    _need(principal, "setting.manage")
    _settings_or_error(session)
    values = _known_fields(body.values, WINDOW_ALARM_LIMITS)
    try:
        save_window_alarm_settings(
            session, values, user_id=principal.user_id, token_id=principal.token_id
        )
    except ControlError as exc:
        raise _control_error(exc) from exc
    return _settings_out(session, principal)


@router.put("/settings/window-temp-drop")
def save_window_temp_drop(body: ThresholdsIn, principal: Prin, session: Sess) -> SettingsOut:
    _need(principal, "setting.manage")
    _settings_or_error(session)
    values = _known_fields(body.values, WINDOW_TEMP_DROP_LIMITS)
    try:
        save_window_temp_drop_settings(
            session, values, user_id=principal.user_id, token_id=principal.token_id
        )
    except ControlError as exc:
        raise _control_error(exc) from exc
    return _settings_out(session, principal)


@router.put("/settings/sensor-failure")
def save_sensor_failure(
    body: SensorFailureDefaultsIn, principal: Prin, session: Sess
) -> SettingsOut:
    """Notbetrieb der Anlage: Profil, Kurve, Rückkehr und Notsollwert als **eine** Einheit
    (`domain.control.save_sensor_failure_plant_defaults`, prüft alles vor dem ersten Schreiben)."""
    _need(principal, "setting.manage")
    _settings_or_error(session)
    current = effective_default_profile(session)
    texts = {
        name: _text(getattr(body, name))
        for name in (
            "fixed_on_seconds",
            "fixed_off_seconds",
            "recovery_seconds",
            "recovery_samples",
            "warm_restart_hysteresis_k",
            "emergency_setpoint_c",
        )
    }
    integers: dict[str, int] = {}
    for name in ("fixed_on_seconds", "fixed_off_seconds", "recovery_seconds", "recovery_samples"):
        try:
            integers[name] = int(texts[name])
        except ValueError as exc:
            raise RoomApiError(
                422, "Bitte eine ganze Zahl angeben.", "validation", name
            ) from exc
    decimals: dict[str, Decimal] = {}
    for name in ("warm_restart_hysteresis_k", "emergency_setpoint_c"):
        try:
            decimals[name] = Decimal(texts[name].replace(",", "."))
        except InvalidOperation as exc:
            raise RoomApiError(422, "Bitte eine Zahl angeben.", "validation", name) from exc
    points: list[CurvePoint] = []
    for index, point in enumerate(body.curve_points):
        try:
            points.append(
                CurvePoint(
                    Decimal(point.outdoor_c.strip().replace(",", ".")),
                    point.on_seconds,
                    point.off_seconds,
                )
            )
        except InvalidOperation as exc:
            raise RoomApiError(
                422,
                "Kennlinie: bitte überall gültige Zahlen angeben.",
                "validation",
                f"curve_points[{index}].outdoor_c",
            ) from exc
    values = ProfileValues(
        name=current.values.name,
        fixed_on_seconds=integers["fixed_on_seconds"],
        fixed_off_seconds=integers["fixed_off_seconds"],
        recovery_seconds=integers["recovery_seconds"],
        recovery_samples=integers["recovery_samples"],
        warm_restart_hysteresis_k=decimals["warm_restart_hysteresis_k"],
        curve_points=tuple(points),
    )
    try:
        save_sensor_failure_plant_defaults(
            session,
            values,
            profile_id=current.id,
            emergency_setpoint_c=decimals["emergency_setpoint_c"],
            user_id=principal.user_id,
            token_id=principal.token_id,
        )
    except (ControlError, PolicyError) as exc:
        raise RoomApiError(422, exc.notice, "validation", exc.field or None) from exc
    return _settings_out(session, principal)


@router.put("/settings/outdoor-source")
def save_outdoor_source(body: OutdoorSourceIn, principal: Prin, session: Sess) -> SettingsOut:
    """Außentemperatur-Quelle setzen oder lösen (`domain.outdoor`)."""
    _need(principal, "setting.manage")
    row = _settings_or_error(session)
    device: Device | None = None
    if body.device_id is not None:
        device = session.get(Device, body.device_id)
        if device is None:
            raise RoomApiError(
                422, "Dieses Gerät ist nicht bekannt.", "validation", "device_id"
            )
    try:
        set_outdoor_temperature_source(session, row, device, actor_id=principal.user_id)
    except CapabilityMissing as exc:
        raise RoomApiError(422, exc.notice, "validation", "device_id") from exc
    return _settings_out(session, principal)


@router.put("/settings/notifications")
def save_notifications(body: NotificationsIn, principal: Prin, session: Sess) -> SettingsOut:
    """Welche der sechs Meldungsarten hinausgehen -- schreibt wie `save_notification_preferences`
    direkt auf die Einstellungszeile (dort gibt es keine Domänenfunktion) und protokolliert
    genauso. Webhook-Adresse und Token sind Umgebung und hier nicht schreibbar."""
    _need(principal, "setting.manage")
    row = _settings_or_error(session)
    row.notify_sensor_faults = body.sensor_faults
    row.notify_bridge_faults = body.bridge_faults
    row.notify_command_failures = body.command_failures
    row.notify_stuck_sensor = body.stuck_sensor
    row.notify_window_alarm = body.window_alarm
    row.notify_tenant_reports = body.tenant_reports
    audit.record(
        session,
        source="web",
        action="update",
        object_type="setting",
        object_id="1",
        summary="Einstellungen für Störungsmeldungen geändert",
        user_id=principal.user_id,
        token_id=principal.token_id,
    )
    return _settings_out(session, principal)


@router.post("/settings/notifications/test")
async def send_test_notification(principal: Prin, session: Sess) -> NotificationTestOut:
    """Testmeldung: echter Versand über den Webhook-Weg, mit
    derselben Sperrzeit. Ohne Webhook oder innerhalb der Sperrzeit wird nichts gesendet
    (409/429); ein fehlgeschlagener Versand ist eine Antwort (`ok: false`), kein Fehler."""
    _need(principal, "setting.manage")
    env_settings = get_settings()
    row = _settings_or_error(session)
    if env_settings.notify_webhook is None:
        raise RoomApiError(
            409, "Ohne hinterlegten Webhook gibt es nichts zu testen.", "no_webhook"
        )
    last_attempt = row.notify_last_attempt_at
    now = utcnow()
    if last_attempt is not None and now - last_attempt < TEST_COOLDOWN:
        raise RoomApiError(
            429, "Bitte kurz warten -- der letzte Versuch war gerade eben.", "cooldown"
        )
    result = await notification.send_test(env_settings)
    row.notify_last_attempt_at = now
    row.notify_last_ok = result.ok
    row.notify_last_error = None if result.ok else result.error
    audit.record(
        session, source="web",
        action="notification.test_sent" if result.ok else "notification.test_failed",
        object_type="setting", object_id="1",
        summary=(
            "Testmeldung versendet" if result.ok else "Testmeldung konnte nicht versendet werden"
        ),
        user_id=principal.user_id, token_id=principal.token_id,
        detail=None if result.ok else result.error,
    )
    return NotificationTestOut(
        ok=result.ok,
        status_code=result.status_code,
        duration_seconds=result.duration_seconds,
        error=result.error,
    )


# -- Schnittstellen -----------------------------------------------------------------------


class InterfaceDetailOut(BaseModel):
    name: str
    value: str
    source: str
    variable: str | None


class InterfaceMoreOut(BaseModel):
    label: str
    path: str


class InterfaceOut(BaseModel):
    key: str
    name: str
    purpose: str
    state: str
    finding: str
    details: list[InterfaceDetailOut]
    hint: str | None
    more: InterfaceMoreOut | None


class HomebridgeZoneOut(BaseModel):
    zone_id: int
    zone_name: str
    config_json: str


class InterfacesOut(BaseModel):
    interfaces: list[InterfaceOut]
    homebridge_zones: list[HomebridgeZoneOut]
    homebridge_broker_configured: bool


_WEBHOOK_VARIABLE = "THERMOCTL_NOTIFY_WEBHOOK"


def _masked(name: str | None, value: str) -> str:
    """Die Webhook-Adresse kann ein Geheimnis im Pfad oder in der Abfrage tragen
    (Zugangsadressen großer Dienste tun das); sie wird auf Schema und Rechner gekürzt.
    Alle anderen Werte kommen aus der Domäne bereits ohne Geheimnisse."""
    if name != _WEBHOOK_VARIABLE or value == "—":
        return value
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname:
        return "hinterlegt"
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{parts.hostname}{port}/…"


@router.get("/interfaces")
def show_interfaces(request: Request, principal: Prin, session: Sess) -> InterfacesOut:
    """Schnittstellen lesen: `setting.manage`; Zonennamen nur für sichtbare Zonen."""
    _need(principal, "setting.manage")
    env_settings = get_settings()
    items = overview(session, env_settings, getattr(request.app.state, "bridge_reachable", None))
    configs = homebridge_zone_configs(visible_zones(session, principal, "zone.read"), env_settings)
    return InterfacesOut(
        interfaces=[
            InterfaceOut(
                key=item.key,
                name=item.name,
                purpose=item.purpose,
                state=item.state,
                finding=item.finding,
                details=[
                    InterfaceDetailOut(
                        name=detail.name,
                        value=_masked(detail.variable, detail.value),
                        source=detail.source,
                        variable=detail.variable,
                    )
                    for detail in item.details
                ],
                hint=item.hint,
                more=(
                    InterfaceMoreOut(label=item.more[0], path=item.more[1])
                    if item.more is not None
                    else None
                ),
            )
            for item in items
        ],
        homebridge_zones=[
            HomebridgeZoneOut(
                zone_id=config.zone_id, zone_name=config.zone_name, config_json=config.config_json
            )
            for config in configs
        ],
        homebridge_broker_configured=env_settings.mqtt_host is not None,
    )
