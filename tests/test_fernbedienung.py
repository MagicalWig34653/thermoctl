"""Was ein Drehregler von aussen bewirkt.

Zwei Entscheidungen stehen hier auf dem Pruefstand: dass der Thermostat den *Modus*
verstellt und nicht "jetzt gerade", und dass Boost die naechste Schaltung vorzieht statt
auf einen geratenen Wert zu heizen.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import einstellungen_anlegen, modus_anlegen, quelle, zone_anlegen
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.fernbedienung import Fernbedienungsfehler, boost, sollwert_setzen
from thermoctl.domain.schedule import aufgeloester_sollwert, uebersteuerung_anlegen

# Ein Montag, 08:00 UTC. Die Einstellungen stehen auf UTC, also ist das auch Ortszeit.
MONTAG_ACHT = datetime(2026, 8, 31, 8, 0)


def _zone_mit_plan(session: Session) -> tuple[object, object, object]:
    """Eine Zone mit Tag ab 06:00 und Nacht ab 22:00 -- und Sollwerten fuer beide."""
    # Zeitzone UTC, damit im Test die Ortszeit des Plans und die UTC der Ergebnisse
    # dieselbe Zahl sind. Die Umrechnung selbst pruefen die Zeitplantests.
    einstellungen_anlegen(session).timezone = "UTC"
    quelle(session, "system")
    zone = zone_anlegen(session, "planzone")
    tag = modus_anlegen(session, "tag")
    nacht = modus_anlegen(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=tag.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=nacht.id
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=tag.id, temperature_c=Decimal("21.0")
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=nacht.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    return zone, tag, nacht


def test_der_sollwert_verstellt_den_modus_der_gerade_gilt(session: Session) -> None:
    zone, tag, nacht = _zone_mit_plan(session)

    sollwert_setzen(session, zone, Decimal("22.5"), MONTAG_ACHT, quelle="system")

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == tag.id
        )
    ) == Decimal("22.5")
    # Gegenprobe: Der andere Modus bleibt, wie er war -- verstellt wurde genau einer.
    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == nacht.id
        )
    ) == Decimal("18.0")
    # Und es entsteht keine Uebersteuerung, die nach dem naechsten Punkt verfiele.
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()


def test_bei_laufender_uebersteuerung_wird_diese_verstellt(session: Session) -> None:
    """Dann gibt es keinen Modus, den man verstellen koennte.

    Ohne diesen Fall spraenge der Regler beim naechsten Zustandsbericht auf den Wert der
    Uebersteuerung zurueck und saehe aus, als habe er den Befehl verschluckt.
    """
    zone, _, _ = _zone_mit_plan(session)
    uebersteuerung_anlegen(
        session, zone, Decimal("19.0"), None, user_id=None, quelle="system"
    )

    sollwert_setzen(session, zone, Decimal("23.0"), MONTAG_ACHT, quelle="system")

    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("23.0")


def test_boost_zieht_die_naechste_schaltung_vor(session: Session) -> None:
    """Ab sofort gilt, was als Naechstes kaeme -- bis genau zu dem Zeitpunkt."""
    zone, _, nacht = _zone_mit_plan(session)

    ergebnis = boost(session, zone, MONTAG_ACHT, quelle="system")

    assert ergebnis.modus_code == "nacht"
    assert ergebnis.temperatur == Decimal("18.0")
    # 22:00 desselben Tages: der Punkt, der planmaessig als Naechstes gekommen waere.
    assert ergebnis.bis == datetime(2026, 8, 31, 22, 0)
    assert aufgeloester_sollwert(session, zone, MONTAG_ACHT).temperature_c == Decimal("18.0")


def test_nach_dem_boost_uebernimmt_der_zeitplan_von_selbst(session: Session) -> None:
    """Die Gegenprobe: Es bleibt nichts stehen, das jemand aufraeumen muesste."""
    zone, _, _ = _zone_mit_plan(session)
    boost(session, zone, MONTAG_ACHT, quelle="system")

    danach = datetime(2026, 8, 31, 22, 30)
    # Ab 22:00 gilt ohnehin Nacht -- der Grund muss aber wieder der Zeitplan sein und
    # nicht die Uebersteuerung, sonst haette sie ihn ueberdauert.
    assert "Zeitplan" in aufgeloester_sollwert(session, zone, danach).grund


def test_boost_ohne_zeitplan_sagt_warum(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "planlos")
    with pytest.raises(Fernbedienungsfehler, match="keinen Zeitplan"):
        boost(session, zone, MONTAG_ACHT, quelle="system")


def test_boost_ohne_hinterlegte_temperatur_sagt_warum(session: Session) -> None:
    """Ein Modus ohne Sollwert in dieser Zone: Es gibt nichts vorzuziehen."""
    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "temperaturlos")
    tag = modus_anlegen(session, "tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=tag.id)
    )
    session.flush()
    with pytest.raises(Fernbedienungsfehler, match="keine Temperatur"):
        boost(session, zone, MONTAG_ACHT, quelle="system")


def test_boost_ohne_einstellungen_sagt_warum(session: Session) -> None:
    zone = zone_anlegen(session, "unfertig")
    with pytest.raises(Fernbedienungsfehler, match="unvollständig"):
        boost(session, zone, MONTAG_ACHT, quelle="system")
