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
from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.regelung import Lage, entscheiden
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.domain.stoerung import KEINE_QUELLE
from thermoctl.domain.zone_settings import regelparameter

log = logging.getLogger(__name__)

_FROSTSCHUTZ_STANDARD = Decimal("16.0")


def _frostschutz_sollwert(session: Session, zone: Zone, einstellungen: Setting) -> Decimal:
    """Der Frostschutz-Sollwert der Zone zum konfigurierten Frostschutz-Modus.

    Derselbe Rueckfall wie in `aufgeloester_sollwert()`: Fehlt der Zone ein eigener Wert
    fuer diesen Modus, gilt ein unverdaechtiger Standardwert statt eines Fehlers — eine
    fehlende Zeile in `zone_setpoint` darf die Regelung nicht zum Stehen bringen.
    """
    wert = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == einstellungen.frost_protection_mode_id,
        )
    )
    return wert if wert is not None else _FROSTSCHUTZ_STANDARD


def _vorheriger_zustand(
    session: Session, zone_id: int, jetzt: datetime
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
    for zustand, zeitpunkt in zeilen:
        if zustand != aktuell:
            break
        beginn = zeitpunkt
    return aktuell, int((jetzt - beginn).total_seconds()), aktuell


def _fensterlage(
    session: Session, zone: Zone, zustand: ZoneState | None, jetzt: datetime
) -> tuple[bool, int | None]:
    """Fensterzustand und Dauer seit dem letzten Schliessen aus der Historie."""
    if zustand is None or zustand.window_open is not False:
        return bool(zustand and zustand.window_open), None

    kontakt = session.scalar(select(DeviceCapability).where(DeviceCapability.code == "contact"))
    rolle = session.scalar(select(DeviceRole).where(DeviceRole.code == "window_contact"))
    if kontakt is None or rolle is None:
        return False, None
    geraete_ids = list(
        session.scalars(
            select(ZoneDevice.device_id).where(
                ZoneDevice.zone_id == zone.id,
                ZoneDevice.device_role_id == rolle.id,
            )
        )
    )
    zuletzt_geschlossen: datetime | None = None
    for geraet_id in geraete_ids:
        vorheriger_wert: str | None = None
        for wert, gemessen_am in session.execute(
            select(Measurement.value_text, Measurement.measured_at)
            .where(
                Measurement.device_id == geraet_id,
                Measurement.capability_id == kontakt.id,
                Measurement.value_text.in_(("true", "false")),
            )
            .order_by(Measurement.measured_at, Measurement.id)
        ):
            if wert == "true" and vorheriger_wert == "false":
                zuletzt_geschlossen = max(
                    zuletzt_geschlossen or gemessen_am,
                    gemessen_am,
                )
            vorheriger_wert = wert
    if zuletzt_geschlossen is None:
        return False, None
    return False, max(0, int((jetzt - zuletzt_geschlossen).total_seconds()))


def _zone_verarbeiten(session: Session, zone: Zone, jetzt: datetime) -> ShadowDecision:
    einstellungen = session.get(Setting, 1)
    assert einstellungen is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"

    zustand = session.get(ZoneState, zone.id)
    if zustand is None:
        ist_c = None
        sensor_status = KEINE_QUELLE
    else:
        ist_c = zustand.temperature_c
        sensor_status_zeile = session.get(SensorStatus, zustand.sensor_status_id)
        assert sensor_status_zeile is not None, "sensor_status-Zeile fehlt zur Referenz"
        sensor_status = sensor_status_zeile.code
    fenster_offen, fenster_zu_seit_s = _fensterlage(session, zone, zustand, jetzt)

    sollwert = aufgeloester_sollwert(session, zone, jetzt)
    frost_c = _frostschutz_sollwert(session, zone, einstellungen)
    parameter = regelparameter(session, zone)
    heizt_gerade, seit_s, previous_would_heat = _vorheriger_zustand(session, zone.id, jetzt)

    lage = Lage(
        ist_c=ist_c,
        soll_c=sollwert.temperature_c,
        soll_grund=sollwert.grund,
        frost_c=frost_c,
        betriebsart=zone.operating_mode.code,
        heizt_gerade=heizt_gerade,
        seit_s=seit_s,
        fenster_offen=fenster_offen,
        fenster_zu_seit_s=fenster_zu_seit_s,
        sensor_status=sensor_status,
        parameter=parameter,
    )
    entscheidung = entscheiden(lage)

    zeile = ShadowDecision(
        decided_at=jetzt,
        zone_id=zone.id,
        temperature_c=ist_c,
        setpoint_c=sollwert.temperature_c,
        setpoint_reason=sollwert.grund,
        would_heat=entscheidung.heizen,
        previous_would_heat=previous_would_heat,
        outcome_code=entscheidung.grund_code,
        reason=entscheidung.grund,
    )
    session.add(zeile)
    session.flush()
    return zeile


def zyklus(session: Session, jetzt: datetime) -> list[ShadowDecision]:
    """Ein Schattenzyklus ueber alle Zonen — schreibt, schaltet aber nichts.

    Eine Zone, deren Verarbeitung scheitert, haelt die uebrigen nicht auf: jede Zone laeuft
    in einem eigenen Savepoint, dessen Rollback bei einer Ausnahme nur ihre eigenen,
    unvollstaendigen Aenderungen zuruecknimmt — nicht die bereits erfolgreich verarbeiteten
    Zonen im selben Aufruf.
    """
    ergebnisse: list[ShadowDecision] = []
    for zone in session.scalars(select(Zone).order_by(Zone.id)):
        try:
            with session.begin_nested():
                zeile = _zone_verarbeiten(session, zone, jetzt)
        except Exception:
            log.exception(
                "Schattenzyklus fuer eine Zone gescheitert — uebrige Zonen laufen weiter",
                extra={"zone_id": zone.id},
            )
            continue
        ergebnisse.append(zeile)
    return ergebnisse
