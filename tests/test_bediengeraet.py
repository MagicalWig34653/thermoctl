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

from tests.hilfen import (
    einstellungen_anlegen,
    geraet_anlegen,
    modus_anlegen,
    quelle,
    rolle,
    zone_anlegen,
)
from thermoctl.db.models.device import ControllerBinding, ZoneDevice
from thermoctl.db.models.lookup import ControllerCommand, DeviceCapability
from thermoctl.db.models.messwert import Measurement
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.bediengeraet import (
    STANDARDSCHRITT_K,
    Bediengeraetefehler,
    aktion_ausfuehren,
    belegung_setzen,
    gesehene_aktionen,
)
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.services.ingest import nachricht_verarbeiten

MONTAG_ACHT = datetime(2026, 8, 31, 8, 0)


def _befehle(session: Session) -> None:
    """Die Nachschlagetabelle, die in der echten Anlage die Migration fuellt."""
    from thermoctl.db.models.lookup import CONTROLLER_COMMANDS

    for code, bezeichnung in CONTROLLER_COMMANDS:
        session.add(ControllerCommand(code=code, label=bezeichnung))
    session.add(DeviceCapability(code="action", label="Tastendruck"))
    session.flush()


def _anlage(session: Session):
    """Eine Zone mit Plan und ein Bediengeraet daran."""
    einstellungen_anlegen(session).timezone = "UTC"
    quelle(session, "system")
    _befehle(session)
    zone = zone_anlegen(session, "wandzone")
    tag = modus_anlegen(session, "tag")
    nacht = modus_anlegen(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=tag.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=nacht.id
            ),
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=tag.id, temperature_c=Decimal("21.0")),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=nacht.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    geraet = geraet_anlegen(session, "wandschalter")
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=geraet.id,
            device_role_id=rolle(session, "controller").id,
        )
    )
    session.flush()
    return zone, geraet


def test_eine_belegte_taste_verstellt_den_geltenden_modus(session: Session) -> None:
    """Nicht als Uebersteuerung: Die waere nach dem naechsten Schaltpunkt weg, und der
    Raum kuehlte ohne Zutun wieder aus."""
    zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")

    betroffen = aktion_ausfuehren(session, geraet, "single_plus", MONTAG_ACHT)

    assert betroffen == [zone.name]
    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("21.5")


def test_die_schrittweite_laesst_sich_je_taste_festlegen(session: Session) -> None:
    zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "hold_minus", "setpoint_down", Decimal("2.0"))

    aktion_ausfuehren(session, geraet, "hold_minus", MONTAG_ACHT)

    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("19.0")
    # Gegenprobe: Ohne eigene Schrittweite gilt der Standard.
    belegung_setzen(session, geraet, "single_minus", "setpoint_down")
    aktion_ausfuehren(session, geraet, "single_minus", MONTAG_ACHT)
    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == (
        Decimal("19.0") - STANDARDSCHRITT_K
    )


def test_boost_und_betriebsart_lassen_sich_auf_tasten_legen(session: Session) -> None:
    zone, geraet = _anlage(session)
    from tests.hilfen import betriebsart

    betriebsart(session, "off")
    belegung_setzen(session, geraet, "single_center", "boost")
    belegung_setzen(session, geraet, "hold_center", "mode_off")

    aktion_ausfuehren(session, geraet, "single_center", MONTAG_ACHT)
    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).grund.startswith("Uebersteuerung")

    aktion_ausfuehren(session, geraet, "hold_center", MONTAG_ACHT)
    assert zone.operating_mode.code == "off"

    # Und zurueck -- sonst waere die Taste eine Einbahnstrasse.
    belegung_setzen(session, geraet, "double_center", "mode_auto")
    aktion_ausfuehren(session, geraet, "double_center", MONTAG_ACHT)
    assert zone.operating_mode.code == "auto"


def test_eine_unbelegte_taste_tut_nichts_und_ist_kein_fehler(session: Session) -> None:
    """Die meisten Geraete schicken mehr Aktionen, als jemand belegen will -- jedes
    Halten und jedes Loslassen. Eine Warnung je Druck waere Laerm."""
    zone, geraet = _anlage(session)
    vorher = aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c

    assert aktion_ausfuehren(session, geraet, "release_plus", MONTAG_ACHT) == []
    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == vorher


def test_ein_bediengeraet_ohne_zone_tut_nichts(session: Session) -> None:
    """Der haeufigste Grund, warum 'die Taste tut nichts' -- und deshalb im Protokoll."""
    einstellungen_anlegen(session)
    quelle(session, "system")
    _befehle(session)
    geraet = geraet_anlegen(session, "herrenloser-schalter")
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")

    assert aktion_ausfuehren(session, geraet, "single_plus", MONTAG_ACHT) == []


def test_gesehene_aktionen_kommen_aus_dem_was_wirklich_ankam(session: Session) -> None:
    """Der Kern: Es wird nicht geraten, wie ein Modell seine Tasten nennt."""
    zone, geraet = _anlage(session)
    nachricht_verarbeiten(
        session,
        f"zigbee2mqtt/{geraet.external_id}",
        json.dumps({"action": "button_1_single", "battery": 90}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONTAG_ACHT,
    )

    aktionen = gesehene_aktionen(session, geraet)

    assert [a.aktion for a in aktionen] == ["button_1_single"]
    assert aktionen[0].befehl_code is None
    assert aktionen[0].zuletzt_gesehen is not None


def test_eine_belegte_taste_bleibt_sichtbar_ohne_frischen_druck(session: Session) -> None:
    """Sonst verschwaende eine funktionierende Belegung aus der Oberflaeche, sobald das
    Aufraeumen der Messwerte den letzten Druck geloescht hat."""
    _zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "nie_wieder_gedrueckt", "boost")

    aktionen = gesehene_aktionen(session, geraet)

    assert [a.aktion for a in aktionen] == ["nie_wieder_gedrueckt"]
    assert aktionen[0].befehl_name == "Nächste Schaltung vorziehen"
    assert aktionen[0].zuletzt_gesehen is None


def test_ein_tastendruck_aus_einer_echten_nachricht_wirkt(session: Session) -> None:
    """Der ganze Weg: MQTT-Nachricht, Messwert, Ausfuehrung."""
    zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")

    nachricht_verarbeiten(
        session,
        f"zigbee2mqtt/{geraet.external_id}",
        json.dumps({"action": "single_plus"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONTAG_ACHT,
    )

    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("21.5")


def test_dieselbe_nachricht_zweimal_wirkt_nur_einmal(session: Session) -> None:
    """Eine behaltene Nachricht wird bei **jeder** Neuverbindung erneut zugestellt.

    Ohne diesen Schutz loeste ein Wackelkontakt in der Netzverbindung denselben
    Tastendruck immer wieder aus -- und ein Boost, den niemand gedrueckt hat, faellt
    erst auf, wenn es im Raum zu warm ist.
    """
    zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")
    nutzlast = json.dumps(
        {"action": "single_plus", "last_seen": "2026-08-31T08:00:00Z"}
    ).encode()

    for _ in range(3):
        nachricht_verarbeiten(
            session,
            f"zigbee2mqtt/{geraet.external_id}",
            nutzlast,
            basis="zigbee2mqtt",
            empfangen_am=MONTAG_ACHT,
        )

    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("21.5")


def test_ein_spaeterer_druck_wirkt_wieder(session: Session) -> None:
    """Gegenprobe zum Schutz oben: Er darf nicht jede Wiederholung sperren.

    Wer zweimal auf 'waermer' drueckt, meint zwei Schritte -- sonst waere der Schutz
    schlimmer als das Problem.
    """
    zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")

    for versatz in (0, 1, 2):
        zeitpunkt = MONTAG_ACHT + timedelta(minutes=versatz)
        nachricht_verarbeiten(
            session,
            f"zigbee2mqtt/{geraet.external_id}",
            json.dumps(
                {"action": "single_plus", "last_seen": zeitpunkt.isoformat() + "Z"}
            ).encode(),
            basis="zigbee2mqtt",
            empfangen_am=zeitpunkt,
        )

    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("22.5")


def test_jeder_tastendruck_wird_als_messwert_abgelegt(session: Session) -> None:
    """Er ist die Grundlage der Einrichtung -- und die Antwort auf 'kommt da ueberhaupt
    etwas an?'."""
    _zone, geraet = _anlage(session)
    nachricht_verarbeiten(
        session,
        f"zigbee2mqtt/{geraet.external_id}",
        json.dumps({"action": "double_center"}).encode(),
        basis="zigbee2mqtt",
        empfangen_am=MONTAG_ACHT,
    )

    faehigkeit_id = session.scalar(
        select(DeviceCapability.id).where(DeviceCapability.code == "action")
    )
    werte = session.scalars(
        select(Measurement.value_text).where(
            Measurement.device_id == geraet.id, Measurement.capability_id == faehigkeit_id
        )
    ).all()
    assert list(werte) == ["double_center"]


def test_eine_belegung_laesst_sich_wieder_loeschen(session: Session) -> None:
    _zone, geraet = _anlage(session)
    belegung_setzen(session, geraet, "single_plus", "setpoint_up")
    belegung_setzen(session, geraet, "single_plus", "boost", Decimal("1.0"))

    assert session.scalars(select(ControllerBinding)).one().step_k == Decimal("1.0")

    belegung_setzen(session, geraet, "single_plus", None)
    assert session.scalars(select(ControllerBinding)).all() == []
    # Und ein zweites Loeschen ist kein Fehler.
    belegung_setzen(session, geraet, "single_plus", None)


def test_unbrauchbare_belegungen_werden_abgewiesen(session: Session) -> None:
    _zone, geraet = _anlage(session)
    with pytest.raises(Bediengeraetefehler, match="gibt es nicht"):
        belegung_setzen(session, geraet, "single_plus", "gibtsnicht")
    with pytest.raises(Bediengeraetefehler, match="groesser als null"):
        belegung_setzen(session, geraet, "single_plus", "setpoint_up", Decimal(0))
    with pytest.raises(Bediengeraetefehler, match="Nachkommastelle"):
        belegung_setzen(session, geraet, "single_plus", "setpoint_up", Decimal("0.25"))
