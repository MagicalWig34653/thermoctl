"""Der Schattenlauf: je Zone die Lage zusammenstellen, entscheiden, protokollieren.

Trockenlauf (Abschnitt 1 der Spezifikation): dieses Modul schaltet nichts und
veroeffentlicht nichts. Es liest `zone_state` (von `ingest.zonenzustand_fortschreiben`
bereits fortgeschrieben), ruft `regelung.entscheiden()` auf und schreibt das Ergebnis als
`shadow_decision`-Zeile. Genau diese Zeilen sind spaeter die Vergleichsgrundlage gegen das
Altsystem (Teilprojekt 4).
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.device import ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability, DeviceRole, SensorStatus
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.control_loop import Lage, entscheiden
from thermoctl.domain.fault import NO_SOURCE
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.zone_settings import control_parameters

log = logging.getLogger(__name__)

_FROST_DEFAULT = Decimal("16.0")


def _frost_setpoint(session: Session, zone: Zone, settings: Setting) -> Decimal:
    """Der Frostschutz-Sollwert der Zone zum konfigurierten Frostschutz-Modus.

    Derselbe Rueckfall wie in `aufgeloester_sollwert()`: Fehlt der Zone ein eigener Wert
    fuer diesen Modus, gilt ein unverdaechtiger Standardwert statt eines Fehlers — eine
    fehlende Zeile in `zone_setpoint` darf die Regelung nicht zum Stehen bringen.
    """
    value = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == settings.frost_protection_mode_id,
        )
    )
    return value if value is not None else _FROST_DEFAULT


def _previous_state(
    session: Session, zone_id: int, now: datetime
) -> tuple[bool, int | None, bool | None]:
    """`heizt_gerade` und `seit_s` aus der Kette der eigenen bisherigen Entscheidungen.

    Im Schattenbetrieb schaltet nichts wirklich, also gibt es keinen echten Ventilzustand,
    an dem sich ablesen liesse, ob und seit wann gerade geheizt wird. Die einzige
    verfuegbare Wahrheit ist deshalb die eigene Entscheidungshistorie: `would_heat` der
    letzten Zeile gilt als aktueller Zustand, und `seit_s` ist die Zeit bis zur aeltesten
    Zeile, die noch denselben Wert traegt. Genau das macht das Protokoll spaeter mit dem
    Altsystem vergleichbar (Abschnitt 6 der Spezifikation) — und ist der Grund, warum die
    Mindestschaltdauer (Regel 5 in `regelung.entscheiden`) im Schattenbetrieb ueberhaupt
    etwas bewirkt: ohne diese Herleitung waere `seit_s` bei jedem Zyklus `None`.

    Liefert zusaetzlich das rohe `previous_would_heat` fuer die neue Zeile: `None`, wenn es
    noch gar keine Vorgeschichte gibt, sonst den zuletzt entschiedenen Wert.
    """
    zeilen = list(
        session.execute(
            select(ShadowDecision.would_heat, ShadowDecision.decided_at)
            .where(ShadowDecision.zone_id == zone_id)
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        )
    )
    if not zeilen:
        return False, None, None

    aktuell = zeilen[0].would_heat
    beginn = zeilen[0].decided_at
    for state, moment in zeilen:
        if state != aktuell:
            break
        beginn = moment
    return aktuell, int((now - beginn).total_seconds()), aktuell


def _window_situation(
    session: Session, zone: Zone, state: ZoneState | None, now: datetime
) -> tuple[bool, int | None]:
    """Fensterzustand und Dauer seit dem letzten Schliessen aus der Historie."""
    if state is None or state.window_open is not False:
        return bool(state and state.window_open), None

    contact = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    rolle = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    if contact is None or rolle is None:
        return False, None
    devices_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == rolle.id,
            )
        )
    )
    last_closed: datetime | None = None
    for device_id in devices_ids:
        previous_value: str | None = None
        for value, gemessen_am in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == device_id,
                Measurement.capability_id == contact.id,
                Measurement.value_text.in_(("true", "false")),
            )
            .order_by(Measurement.measured_at, Measurement.id)
        ):
            if value == "true" and previous_value == "false":
                last_closed = max(
                    last_closed or gemessen_am,
                    gemessen_am,
                )
            previous_value = value
    if last_closed is None:
        return False, None
    return False, max(0, int((now - last_closed).total_seconds()))


def _process_zone(session: Session, zone: Zone, now: datetime) -> ShadowDecision:
    settings = session.get(Setting, 1)
    assert settings is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"

    state = session.get(ZoneState, zone.id)
    if state is None:
        ist_c = None
        sensor_status = NO_SOURCE
    else:
        ist_c = state.temperature_c
        sensor_status_row = session.get(SensorStatus, state.sensor_status_id)
        assert sensor_status_row is not None, "sensor_status-Zeile fehlt zur Referenz"
        sensor_status = sensor_status_row.code
    window_open, window_closed_for_s = _window_situation(session, zone, state, now)

    setpoint = resolved_setpoint(session, zone, now)
    frost_c = _frost_setpoint(session, zone, settings)
    parameter = control_parameters(session, zone)
    heizt_gerade, seit_s, previous_would_heat = _previous_state(session, zone.id, now)

    lage = Lage(
        ist_c=ist_c,
        soll_c=setpoint.temperature_c,
        soll_grund=setpoint.grund,
        frost_c=frost_c,
        operating_mode=zone.operating_mode.code,
        heizt_gerade=heizt_gerade,
        seit_s=seit_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        sensor_status=sensor_status,
        parameter=parameter,
    )
    entscheidung = entscheiden(lage)

    zeile = ShadowDecision(
        decided_at=now,
        zone_id=zone.id,
        temperature_c=ist_c,
        setpoint_c=setpoint.temperature_c,
        setpoint_reason=setpoint.grund,
        would_heat=entscheidung.heizen,
        previous_would_heat=previous_would_heat,
        outcome_code=entscheidung.grund_code,
        reason=entscheidung.grund,
    )
    session.add(zeile)
    session.flush()
    return zeile


def cycle(session: Session, now: datetime) -> list[ShadowDecision]:
    """Ein Schattenzyklus ueber alle Zonen — schreibt, schaltet aber nichts.

    Eine Zone, deren Verarbeitung scheitert, haelt die uebrigen nicht auf: jede Zone laeuft
    in einem eigenen Savepoint, dessen Rollback bei einer Ausnahme nur ihre eigenen,
    unvollstaendigen Aenderungen zuruecknimmt — nicht die bereits erfolgreich verarbeiteten
    Zonen im selben Aufruf.
    """
    results: list[ShadowDecision] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        try:
            with session.begin_nested():
                zeile = _process_zone(session, zone, now)
        except Exception:
            log.exception(
                "Schattenzyklus fuer eine Zone gescheitert — uebrige Zonen laufen weiter",
                extra={"zone_id": zone.id},
            )
            continue
        results.append(zeile)
    return results
