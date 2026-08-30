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

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import ZoneSetpoint
from thermoctl.domain.remote_control import RemoteControlError, boost, set_setpoint
from thermoctl.domain.schedule import create_override, resolved_setpoint

# Ein Montag, 08:00 UTC. Die Einstellungen stehen auf UTC, also ist das auch Ortszeit.
MONDAY_EIGHT = datetime(2026, 8, 31, 8, 0)


def _zone_with_plan(session: Session) -> tuple[object, object, object]:
    """Eine Zone mit Tag ab 06:00 und Nacht ab 22:00 -- und Sollwerten fuer beide."""
    # Zeitzone UTC, damit im Test die Ortszeit des Plans und die UTC der Ergebnisse
    # dieselbe Zahl sind. Die Umrechnung selbst pruefen die Zeitplantests.
    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "planzone")
    day = create_mode(session, "tag")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=day.id),
            SchedulePoint(
                zone_id=zone.id, weekday=1, minute_of_day=1320, setpoint_mode_id=night.id
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=day.id, temperature_c=Decimal("21.0")
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    return zone, day, night


def test_der_sollwert_verstellt_den_modus_der_gerade_gilt(session: Session) -> None:
    zone, day, night = _zone_with_plan(session)

    set_setpoint(session, zone, Decimal("22.5"), MONDAY_EIGHT, source="system")

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == day.id
        )
    ) == Decimal("22.5")
    # Gegenprobe: Der andere Modus bleibt, wie er war -- verstellt wurde genau einer.
    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == night.id
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
    zone, _, _ = _zone_with_plan(session)
    create_override(
        session, zone, Decimal("19.0"), None, user_id=None, source="system"
    )

    set_setpoint(session, zone, Decimal("23.0"), MONDAY_EIGHT, source="system")

    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("23.0")


def test_boost_zieht_die_naechste_schaltung_vor(session: Session) -> None:
    """Ab sofort gilt, was als Naechstes kaeme -- bis genau zu dem Zeitpunkt."""
    zone, _, night = _zone_with_plan(session)

    result = boost(session, zone, MONDAY_EIGHT, source="system")

    assert result.mode_code == "nacht"
    assert result.temperature == Decimal("18.0")
    # 22:00 desselben Tages: der Punkt, der planmaessig als Naechstes gekommen waere.
    assert result.bis == datetime(2026, 8, 31, 22, 0)
    assert resolved_setpoint(session, zone, MONDAY_EIGHT).temperature_c == Decimal("18.0")


def test_nach_dem_boost_uebernimmt_der_zeitplan_von_selbst(session: Session) -> None:
    """Die Gegenprobe: Es bleibt nichts stehen, das jemand aufraeumen muesste."""
    zone, _, _ = _zone_with_plan(session)
    boost(session, zone, MONDAY_EIGHT, source="system")

    danach = datetime(2026, 8, 31, 22, 30)
    # Ab 22:00 gilt ohnehin Nacht -- der Grund muss aber wieder der Zeitplan sein und
    # nicht die Uebersteuerung, sonst haette sie ihn ueberdauert.
    assert "Zeitplan" in resolved_setpoint(session, zone, danach).grund


def test_boost_ohne_zeitplan_sagt_warum(session: Session) -> None:
    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "planlos")
    with pytest.raises(RemoteControlError, match="keinen Zeitplan"):
        boost(session, zone, MONDAY_EIGHT, source="system")


def test_boost_ohne_hinterlegte_temperatur_sagt_warum(session: Session) -> None:
    """Ein Modus ohne Sollwert in dieser Zone: Es gibt nichts vorzuziehen."""
    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "temperaturlos")
    day = create_mode(session, "tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=day.id)
    )
    session.flush()
    with pytest.raises(RemoteControlError, match="keine Temperatur"):
        boost(session, zone, MONDAY_EIGHT, source="system")


def test_boost_ohne_einstellungen_sagt_warum(session: Session) -> None:
    zone = create_zone(session, "unfertig")
    with pytest.raises(RemoteControlError, match="unvollständig"):
        boost(session, zone, MONDAY_EIGHT, source="system")
