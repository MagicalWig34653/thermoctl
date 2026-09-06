"""The plant's operating state: armed or dry run, and the global defaults.

Two things live here that used to be operable nowhere at all:

* **Arming.** `setting.control_armed` used to be a field that nobody could set -- the
  dry run's bolt, but without a key. Whoever wanted to bring the plant into operation
  had to flip it in the database by hand. This is now an operator action with its own
  permission, its own audit entry, and a reason that gets recorded along with it.
* **The global defaults** that every zone inherits from unless it has set its own.

Both live in the domain and not in the web interface, so that REST and MCP use the same
check instead of each having their own, slightly different one.

**The second bolt is left untouched.** `MqttClient(schalten_erlaubt=...)` is set when the
client is built, not from here. Arming alone therefore still does not send anything -- it
only lifts the bolt the database holds. That is deliberate: a single wrong click must not
be able to set the heating in motion.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting


@dataclass
class ControlError(Exception):
    """An input to be rejected, not a fault of the service.

    Not frozen: Python attaches a traceback to an exception when it is raised, and a
    frozen dataclass refuses exactly that.
    """

    field: str
    notice: str

    def __str__(self) -> str:  # pragma: no cover - display, not logic
        return self.notice


# Lower bounds are not a matter of taste: a control cycle of zero seconds is an infinite
# loop, a minimum switch duration of zero seconds is exactly the legacy system's defect
# (cycling at the setpoint), and a hysteresis of zero kelvin is the same problem. Upper
# bounds keep out numbers that can only arise from a typo.
LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    "polling_interval_seconds": (Decimal(5), Decimal(3600)),
    "shadow_interval_seconds": (Decimal(5), Decimal(3600)),
    "default_hysteresis_k": (Decimal("0.1"), Decimal("5.0")),
    "default_min_on_seconds": (Decimal(30), Decimal(7200)),
    "default_min_off_seconds": (Decimal(30), Decimal(7200)),
    "default_sensor_timeout_seconds": (Decimal(60), Decimal(86400)),
    "default_window_resume_delay_seconds": (Decimal(0), Decimal(3600)),
    # An hour is the shortest span for which "unmoving" is still a meaningful claim
    # about a room's temperature at all; a week is long enough that anything longer
    # would stop being a diagnostic and start being silence.
    "stuck_reading_hours": (Decimal(1), Decimal(168)),
    "measurement_retention_days": (Decimal(1), Decimal(3650)),
    "shadow_decision_retention_days": (Decimal(1), Decimal(3650)),
    "session_lifetime_seconds": (Decimal(300), Decimal(31536000)),
    "default_solar_setback_max_k": (Decimal("0.0"), Decimal("10.0")),
    # A window of a day or more would predict "sun" far past the point where the
    # forecast is still meaningful; below one hour there is nothing left to look
    # ahead to.
    "solar_setback_lookahead_hours": (Decimal(1), Decimal(12)),
    # Order-of-magnitude sanity, not a manufacturer figure: relay datasheets for this
    # current class state electrical endurance from the low tens of thousands up to a
    # few million operations. 1,000 is implausibly fragile for anything sold as a
    # switching relay; 10,000,000 exceeds even generously rated general-purpose power
    # relays and is far more likely a missing-thousands-separator typo than a real
    # value.
    "assumed_relay_lifetime_operations": (Decimal(1_000), Decimal(10_000_000)),
}

# Deliberately its own dict, not folded into `LIMITS` above: `LIMITS` is shared with
# REST (`api/routes.py`) and MCP (`mcp/server.py`), and the project owner's explicit
# instruction is that anything new about the window alarm and the outdoor source
# stays out of those two adapters -- only the interface and Home Assistant get it.
# Folding these two fields into `LIMITS` would put them on `PUT /api/v1/control/
# defaults` and the matching MCP tool for free, which is exactly what must not
# happen. `check_number` below takes the bounds dict as a parameter for this
# reason -- one validation function, two independent sets of fields.
WINDOW_ALARM_LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    # Five minutes is close to the shortest a deliberate "Stoßlüften" is ever
    # recommended to last; four hours is long enough that anything shorter would
    # start flagging ordinary, brief airing as forgotten.
    "window_alarm_open_minutes": (Decimal(5), Decimal(240)),
    # -20°C is colder than this plant's climate ever reasonably gets; 15°C is above
    # any outdoor temperature an open window could meaningfully be called a frost
    # risk at.
    "window_alarm_outdoor_threshold_c": (Decimal("-20.0"), Decimal("15.0")),
}

WINDOW_ALARM_LABELS: dict[str, str] = {
    "window_alarm_open_minutes": "Fenster gilt als vergessen nach (Minuten)",
    "window_alarm_outdoor_threshold_c": "Fenster-Alarm unter Außentemperatur (°C)",
}

WINDOW_ALARM_GANZZAHLIG = frozenset({"window_alarm_open_minutes"})

LABELS: dict[str, str] = {
    "polling_interval_seconds": "Abfrageintervall (Sekunden)",
    "shadow_interval_seconds": "Regelzyklus (Sekunden)",
    "default_hysteresis_k": "Hysterese (K)",
    "default_min_on_seconds": "Mindest-Einschaltdauer (Sekunden)",
    "default_min_off_seconds": "Mindest-Ausschaltdauer (Sekunden)",
    "default_sensor_timeout_seconds": "Sensor gilt als ausgefallen nach (Sekunden)",
    "default_window_resume_delay_seconds": "Nachlauf nach Fensterschluss (Sekunden)",
    "stuck_reading_hours": "Messwert gilt als festhängend nach (Stunden)",
    "measurement_retention_days": "Messwerte aufbewahren (Tage)",
    "shadow_decision_retention_days": "Schattenentscheidungen aufbewahren (Tage)",
    "session_lifetime_seconds": "Sitzungsdauer (Sekunden)",
    "default_solar_setback_max_k": "Sonnenabsenkung, Obergrenze (K)",
    "solar_setback_lookahead_hours": "Sonnenabsenkung, Vorschau (Stunden)",
    "assumed_relay_lifetime_operations": "Angenommene Relais-Lebensdauer (Schaltspiele)",
}

# Which fields are integer-valued. The rest are decimal numbers.
GANZZAHLIG = frozenset(LIMITS) - {"default_hysteresis_k", "default_solar_setback_max_k"}


def settings(session: Session) -> Setting:
    row = session.get(Setting, 1)
    if row is None:  # pragma: no cover - only on incomplete setup
        raise ControlError("", "Die Einrichtung ist unvollständig.")
    return row


def check_number(
    field: str,
    input_value: str,
    *,
    limits: dict[str, tuple[Decimal, Decimal]] = LIMITS,
    ganzzahlig: frozenset[str] = GANZZAHLIG,
) -> Decimal:
    """Checks a single value against its bounds.

    Its own function, because the same check is called from the interface, from REST
    and from MCP -- and because a bound that lives in three places drifts in three
    places. `limits`/`ganzzahlig` default to the shared `LIMITS`/`GANZZAHLIG` those
    three adapters use; `save_window_alarm_settings` below passes its own, smaller
    pair instead, since those two fields must not appear in REST or MCP at all.
    """
    text = input_value.strip().replace(",", ".")
    if not text:
        raise ControlError(field, "Bitte einen Wert angeben.")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ControlError(field, "Bitte eine Zahl angeben.") from exc
    if field in ganzzahlig and value != value.to_integral_value():
        raise ControlError(field, "Bitte eine ganze Zahl angeben.")
    lower, upper = limits[field]
    if not (lower <= value <= upper):
        raise ControlError(
            field, f"Bitte einen Wert zwischen {_short(lower)} und {_short(upper)} angeben."
        )
    return value


def _short(value: Decimal) -> str:
    whole = value.to_integral_value()
    return str(int(whole)) if value == whole else str(value)


def save_settings(
    session: Session,
    values: dict[str, str],
    timezone_name: str,
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """Applies the global defaults after **all** values have been checked.

    Check first, then write: otherwise a rejected input in the third field would leave
    a half-applied setting behind -- the same bug that once left the setup itself
    half-created.
    """
    if not timezone_name.strip():
        raise ControlError("timezone", "Bitte eine Zeitzone angeben.")
    checked = {field: check_number(field, values.get(field, "")) for field in LIMITS}

    row = settings(session)
    row.timezone = timezone_name.strip()
    for field, value in checked.items():
        setattr(row, field, value if field not in GANZZAHLIG else int(value))
    audit.record(
        session,
        source=source,
        action="update",
        object_type="setting",
        object_id="1",
        summary="Globale Regelvorgaben geändert",
        user_id=user_id,
        token_id=token_id,
    )


def check_coordinate(field: str, eingabe: str, *, bound: Decimal) -> Decimal | None:
    """A latitude or longitude, or `None` if the field was left empty.

    Empty is a valid, meaningful value here -- unlike `check_number` above, where an
    empty global default would be a mistake. Without a location the solar setback
    feature stays off regardless of `solar_forecast_enabled` (CLAUDE.md principle 1:
    no coordinate belongs in the source as a fallback, so there is no default to fall
    back to -- "not configured" has to be a real, valid state).
    """
    text = eingabe.strip().replace(",", ".")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ControlError(field, "Bitte eine Zahl angeben.") from exc
    if not -bound <= value <= bound:
        raise ControlError(
            field, f"Bitte einen Wert zwischen -{bound} und {bound} angeben."
        )
    return value


def save_solar_location(
    session: Session,
    *,
    enabled: bool,
    latitude_text: str,
    longitude_text: str,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """The on/off switch and the location for the solar forecast.

    Kept apart from `save_settings`: those are bounded numbers with a global default,
    these are a location that has none, plus a switch. Checked together before either
    is written, for the same reason `save_settings` does -- a coordinate rejected in
    the second field must not leave the switch already flipped.
    """
    latitude = check_coordinate(
        "solar_forecast_latitude", latitude_text, bound=Decimal("90")
    )
    longitude = check_coordinate(
        "solar_forecast_longitude", longitude_text, bound=Decimal("180")
    )

    row = settings(session)
    row.solar_forecast_enabled = enabled
    row.solar_forecast_latitude = latitude
    row.solar_forecast_longitude = longitude
    audit.record(
        session,
        source=source,
        action="update",
        object_type="setting",
        object_id="1",
        summary="Standort und Schalter für die Sonnenprognose geändert",
        user_id=user_id,
        token_id=token_id,
    )


def save_window_alarm_settings(
    session: Session,
    values: dict[str, str],
    *,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> None:
    """The window alarm's two thresholds -- deliberately apart from `save_settings`.

    Not folded into `LIMITS`/`save_settings`: those two are shared with REST and
    MCP, and the project owner's explicit instruction is that nothing new about
    the window alarm reaches those two adapters -- only the interface. Using
    `WINDOW_ALARM_LIMITS` here instead keeps that boundary in the data, not just
    in a comment someone has to remember to honour.
    """
    checked = {
        field: check_number(
            field, values.get(field, ""),
            limits=WINDOW_ALARM_LIMITS, ganzzahlig=WINDOW_ALARM_GANZZAHLIG,
        )
        for field in WINDOW_ALARM_LIMITS
    }
    row = settings(session)
    for field, value in checked.items():
        setattr(row, field, value if field not in WINDOW_ALARM_GANZZAHLIG else int(value))
    audit.record(
        session,
        source=source,
        action="update",
        object_type="setting",
        object_id="1",
        summary="Schwellen des Fenster-Alarms geändert",
        user_id=user_id,
        token_id=token_id,
    )


def arm(
    session: Session,
    armed: bool,
    *,
    reason: str,
    user_id: int | None,
    token_id: int | None = None,
    source: str = "web",
) -> bool:
    """Flips the bolt the database holds. Returns whether anything actually changed.

    The reason is mandatory when arming and optional when disarming: whoever hands the
    plant over to be operated should be able to look up later what they relied on when
    doing so. Going back to the dry run, on the other hand, should never fail on a
    formality -- that is the path someone takes in a hurry.
    """
    if armed and not reason.strip():
        raise ControlError(
            "reason", "Bitte kurz festhalten, worauf sich das Scharfschalten stützt."
        )

    row = settings(session)
    if row.control_armed == armed:
        return False
    row.control_armed = armed
    audit.record(
        session,
        source=source,
        action="arm" if armed else "disarm",
        object_type="setting",
        object_id="1",
        summary=(
            "Regelung scharf geschaltet — Sollwertausgabe erst nach Neustart freigegeben"
            if armed
            else "Regelung in den Trockenlauf zurückgenommen"
        ),
        detail=reason.strip() or None,
        user_id=user_id,
        token_id=token_id,
    )
    return True
