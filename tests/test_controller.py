"""Bediengeraete: was ein Tastendruck an der Wand bewirkt.

Der Kern der Sache ist, dass hier **nichts geraten** wird. Wie ein Geraet seine Tasten
nennt, entscheidet Zigbee2MQTT je Modell; der Dienst zeichnet auf, was wirklich ankam,
und die Belegung steht in der Datenbank. Diese Tests pruefen beide Haelften: das Zuhoeren
und das Ausfuehren.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_mode,
    create_settings,
    create_zone,
    rolle,
    source,
)
from thermoctl.db.models.device import ControllerBinding, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability
from thermoctl.db.models.measurement import Measurement
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.controller import (
    DEFAULT_STEP_K,
    ControllerError,
    execute_aktion,
    gesehene_aktionen,
    set_binding,
)
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.services.ingest import process_message

MONDAY_EIGHT = datetime(2026, 8, 31, 8, 0)


def _commands(session: Session) -> None:
    """Die Nachschlagetabelle, die in der echten Anlage die Migration fuellt."""
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS

    for code, bezeichnung in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=bezeichnung))
    session.add(DeviceCapability(code="action", label="Tastendruck"))
    session.flush()


def _anlage(session: Session):
    """Eine Zone mit Plan und ein Bediengeraet daran."""
    create_settings(session).timezone = "UTC"
    source(session, "system")
    _commands(session)
    zone = create_zone(session, "wandzone")
    day = create_mode(session, "tag")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=day.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id
            ),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    device = create_device(session, "wandschalter")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    session.flush()
    return zone, device


def test_eine_belegte_taste_verstellt_den_geltenden_modus(session: Session) -> None:
    """Nicht als Uebersteuerung: Die waere nach dem naechsten Schaltpunkt weg, und der
    Raum kuehlte ohne Zutun wieder aus."""
    zone, device = _anlage(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    betroffen = execute_aktion(session, device, "single_plus", MONDAY_EIGHT)

    assert betroffen == [zone.name]
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_die_schrittweite_laesst_sich_je_taste_festlegen(session: Session) -> None:
    zone, device = _anlage(session)
    set_binding(session, device, "hold_minus", "setpoint_down", Decimal("2.0"))

    execute_aktion(session, device, "hold_minus", MONDAY_EIGHT)

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("19.0")
    # Gegenprobe: Ohne eigene Schrittweite gilt der Standard.
    set_binding(session, device, "single_minus", "setpoint_down")
    execute_aktion(session, device, "single_minus", MONDAY_EIGHT)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == (
        Decimal("19.0") - DEFAULT_STEP_K
    )


def test_boost_und_betriebsart_lassen_sich_auf_tasten_legen(session: Session) -> None:
    zone, device = _anlage(session)
    from tests.helpers import operating_mode

    operating_mode(session, "off")
    set_binding(session, device, "single_center", "boost")
    set_binding(session, device, "hold_center", "mode_off")

    execute_aktion(session, device, "single_center", MONDAY_EIGHT)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).grund.startswith("Uebersteuerung")

    execute_aktion(session, device, "hold_center", MONDAY_EIGHT)
    assert zone.operating_mode.code == "off"

    # Und zurueck -- sonst waere die Taste eine Einbahnstrasse.
    set_binding(session, device, "double_center", "mode_auto")
    execute_aktion(session, device, "double_center", MONDAY_EIGHT)
    assert zone.operating_mode.code == "auto"


def test_eine_unbelegte_taste_tut_nichts_und_ist_kein_fehler(session: Session) -> None:
    """Die meisten Geraete schicken mehr Aktionen, als jemand belegen will -- jedes
    Halten und jedes Loslassen. Eine Warnung je Druck waere Laerm."""
    zone, device = _anlage(session)
    vorher = resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c

    assert execute_aktion(session, device, "release_plus", MONDAY_EIGHT) == []
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == vorher


def test_ein_bediengeraet_ohne_zone_tut_nichts(session: Session) -> None:
    """Der haeufigste Grund, warum 'die Taste tut nichts' -- und deshalb im Protokoll."""
    create_settings(session)
    source(session, "system")
    _commands(session)
    device = create_device(session, "herrenloser-schalter")
    set_binding(session, device, "single_plus", "setpoint_up")

    assert execute_aktion(session, device, "single_plus", MONDAY_EIGHT) == []


def test_gesehene_aktionen_kommen_aus_dem_was_wirklich_ankam(session: Session) -> None:
    """Der Kern: Es wird nicht geraten, wie ein Modell seine Tasten nennt."""
    zone, device = _anlage(session)
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "button_1_single", "battery": 90}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONDAY_EIGHT,
    )

    aktionen = gesehene_aktionen(session, device)

    assert [a.aktion for a in aktionen] == ["button_1_single"]
    assert aktionen[0].command_code is None
    assert aktionen[0].last_seen is not None


def test_eine_belegte_taste_bleibt_sichtbar_ohne_frischen_druck(session: Session) -> None:
    """Sonst verschwaende eine funktionierende Belegung aus der Oberflaeche, sobald das
    Aufraeumen der Messwerte den letzten Druck geloescht hat."""
    _zone, device = _anlage(session)
    set_binding(session, device, "nie_wieder_gedrueckt", "boost")

    aktionen = gesehene_aktionen(session, device)

    assert [a.aktion for a in aktionen] == ["nie_wieder_gedrueckt"]
    assert aktionen[0].command_name == "Nächste Schaltung vorziehen"
    assert aktionen[0].last_seen is None


def test_ein_tastendruck_aus_einer_echten_nachricht_wirkt(session: Session) -> None:
    """Der ganze Weg: MQTT-Nachricht, Messwert, Ausfuehrung."""
    zone, device = _anlage(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "single_plus"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONDAY_EIGHT,
    )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_dieselbe_nachricht_zweimal_wirkt_nur_einmal(session: Session) -> None:
    """Eine behaltene Nachricht wird bei **jeder** Neuverbindung erneut zugestellt.

    Ohne diesen Schutz loeste ein Wackelkontakt in der Netzverbindung denselben
    Tastendruck immer wieder aus -- und ein Boost, den niemand gedrueckt hat, faellt
    erst auf, wenn es im Raum zu warm ist.
    """
    zone, device = _anlage(session)
    set_binding(session, device, "single_plus", "setpoint_up")
    payload = json.dumps(
        {"action": "single_plus", "last_seen": "2026-08-31T08:00:00Z"}
    ).encode()

    for _ in range(3):
        process_message(
            session,
            f"zigbee2mqtt/{device.external_id}",
            payload,
            basis="zigbee2mqtt",
            empfangen_am=MONDAY_EIGHT,
        )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("21.5")


def test_ein_spaeterer_druck_wirkt_wieder(session: Session) -> None:
    """Gegenprobe zum Schutz oben: Er darf nicht jede Wiederholung sperren.

    Wer zweimal auf 'waermer' drueckt, meint zwei Schritte -- sonst waere der Schutz
    schlimmer als das Problem.
    """
    zone, device = _anlage(session)
    set_binding(session, device, "single_plus", "setpoint_up")

    for versatz in (0, 1, 2):
        moment = MONDAY_EIGHT + timedelta(minutes=versatz)
        process_message(
            session,
            f"zigbee2mqtt/{device.external_id}",
            json.dumps(
                {"action": "single_plus", "last_seen": moment.isoformat() + "Z"}
            ).encode(),
            basis="zigbee2mqtt",
            empfangen_am=moment,
        )

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("22.5")


def test_jeder_tastendruck_wird_als_messwert_abgelegt(session: Session) -> None:
    """Er ist die Grundlage der Einrichtung -- und die Antwort auf 'kommt da ueberhaupt
    etwas an?'."""
    _zone, device = _anlage(session)
    process_message(
        session,
        f"zigbee2mqtt/{device.external_id}",
        json.dumps({"action": "double_center"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONDAY_EIGHT,
    )

    capability_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    values = session.scalars(
        select(Measurement.value_text).where(
            Measurement.device_id == device.id, Measurement.capability_id == capability_id
        )
    ).all()
    assert list(values) == ["double_center"]


def test_eine_belegung_laesst_sich_wieder_loeschen(session: Session) -> None:
    _zone, device = _anlage(session)
    set_binding(session, device, "single_plus", "setpoint_up")
    set_binding(session, device, "single_plus", "boost", Decimal("1.0"))

    assert session.scalars(select(ControllerBinding)).one().step_k == Decimal("1.0")

    set_binding(session, device, "single_plus", None)
    assert session.scalars(select(ControllerBinding)).all() == []
    # Und ein zweites Loeschen ist kein Fehler.
    set_binding(session, device, "single_plus", None)


def test_unbrauchbare_belegungen_werden_abgewiesen(session: Session) -> None:
    _zone, device = _anlage(session)
    with pytest.raises(ControllerError, match="gibt es nicht"):
        set_binding(session, device, "single_plus", "gibtsnicht")
    with pytest.raises(ControllerError, match="groesser als null"):
        set_binding(session, device, "single_plus", "setpoint_up", Decimal(0))
    with pytest.raises(ControllerError, match="Nachkommastelle"):
        set_binding(session, device, "single_plus", "setpoint_up", Decimal("0.25"))
