from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_zone, point, source, zone_with_schedule
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import SetpointMode
from thermoctl.domain.schedule import (
    cancel_override,
    create_override,
    current_point,
    next_point,
    resolved_setpoint,
)


def test_punkt_gilt_bis_zum_naechsten() -> None:
    points = [point(1, 360, "tag"), point(1, 1380, "nacht")]  # Mo 06:00 und 23:00
    monday_ten = datetime(2026, 8, 31, 10, 0)
    assert current_point(points, monday_ten).minute_of_day == 360


def test_vor_dem_ersten_punkt_gilt_der_letzte_der_woche() -> None:
    """Der Sonntagabend-Punkt wirkt bis Montagfrueh — die Woche ist ein Ring."""
    points = [point(1, 360, "tag"), point(7, 1320, "nacht")]  # Mo 06:00, So 22:00
    monday_three = datetime(2026, 8, 31, 3, 0)
    gilt = current_point(points, monday_three)
    assert gilt.weekday == 7 and gilt.minute_of_day == 1320


def test_ohne_punkte_gibt_es_keinen_geltenden() -> None:
    assert current_point([], datetime(2026, 8, 31, 10, 0)) is None


def test_punkt_genau_zur_schaltminute_gilt_bereits() -> None:
    points = [point(1, 360, "tag")]
    assert current_point(points, datetime(2026, 8, 31, 6, 0)) is not None


def test_naechster_punkt_liegt_in_der_zukunft() -> None:
    points = [point(1, 360, "tag"), point(1, 1380, "nacht")]
    next_one = next_point(points, datetime(2026, 8, 31, 10, 0))
    assert next_one == datetime(2026, 8, 31, 23, 0)


def test_ohne_zeitplan_gilt_der_frostschutz(session: Session) -> None:
    zone = zone_with_schedule(session, "leer", points=[], frost_protection=Decimal("16.0"))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")
    assert "Frostschutz" in result.grund


def test_betriebsart_aus_ergibt_frostschutz(session: Session) -> None:
    zone = zone_with_schedule(session, "aus", points=[(1, 360, "tag", Decimal("21.0"))],
                             operating_mode="off", frost_protection=Decimal("16.0"))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("16.0")


def test_uebersteuerung_schlaegt_den_zeitplan(session: Session) -> None:
    zone = zone_with_schedule(session, "ueber", points=[(1, 360, "tag", Decimal("21.0"))],
                             override=(Decimal("23.5"), None))
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("23.5")
    assert "Uebersteuerung" in result.grund


def test_abgelaufene_uebersteuerung_wirkt_nicht_mehr(session: Session) -> None:
    zone = zone_with_schedule(
        session, "abgelaufen", points=[(1, 360, "tag", Decimal("21.0"))],
        override=(Decimal("23.5"), datetime(2026, 8, 31, 9, 0)),
    )
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("21.0")


def test_grund_benennt_die_entscheidung(session: Session) -> None:
    """Grundsatz 5: nachvollziehbar, warum dieser Sollwert gilt."""
    zone = zone_with_schedule(session, "grund", points=[(1, 360, "tag", Decimal("21.0"))])
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert "Tag" in result.grund and "06:00" in result.grund


def test_naechster_punkt_ohne_punkte_gibt_es_nicht() -> None:
    assert next_point([], datetime(2026, 8, 31, 10, 0)) is None


def test_uebersteuerung_mit_unbekannter_quelle_schlaegt_fehl(session: Session) -> None:
    """Eine Uebersteuerung ohne Quelle waere eine, bei der niemand mehr sagen kann,
    worueber sie eingestellt wurde — das soll laut scheitern.

    Frueher stand hier fest die Quelle 'api', auch wenn die Uebersteuerung aus der
    Oberflaeche kam; der Test prueft deshalb jetzt die Ablehnung eines *unbekannten*
    Namens statt das Fehlen genau einer Lookup-Zeile.
    """
    zone = create_zone(session, "ohne-quelle")
    with pytest.raises(ValueError, match="rauchzeichen"):
        create_override(
            session, zone, Decimal("20.0"), None, source="rauchzeichen"
        )


def test_uebersteuerung_merkt_sich_den_adapter(session: Session) -> None:
    """Gegenprobe: Die drei Adapter muessen unterscheidbar bleiben, sonst beantwortet
    `zone_override.source_id` die Frage 'worueber wurde das eingestellt' fuer zwei von
    dreien falsch — genau der Zustand, den diese Aenderung behebt."""
    zone = create_zone(session, "adapterzone")
    aus_web = create_override(session, zone, Decimal("21.0"), None)
    aus_mcp = create_override(session, zone, Decimal("22.0"), None, source="mcp")
    assert aus_web.source_id != aus_mcp.source_id


def test_uebersteuerung_anlegen_legt_eine_neue_uebersteuerung_an(session: Session) -> None:
    zone = create_zone(session, "mit-quelle")
    source(session, "api")
    entry = create_override(
        session, zone, Decimal("22.5"), None, user_id=None, token_id=None
    )
    assert entry.zone_id == zone.id
    assert entry.temperature_c == Decimal("22.5")
    assert entry.id is not None


def test_uebersteuerung_aufheben_beendet_die_aktive(session: Session) -> None:
    zone = zone_with_schedule(
        session, "aufheben", points=[], override=(Decimal("23.0"), None)
    )
    entry = cancel_override(session, zone)
    assert entry is not None
    assert entry.cancelled_at is not None


def test_uebersteuerung_aufheben_ohne_aktive_gibt_none(session: Session) -> None:
    zone = create_zone(session, "keine-ueber")
    assert cancel_override(session, zone) is None


def test_uebersteuerung_auf_modus_ohne_feste_temperatur(session: Session) -> None:
    """Eine Uebersteuerung kann auf einen Modus statt eine feste Temperatur zeigen —
    der Sollwert kommt dann aus der Zonentemperatur fuer diesen Modus."""
    zone = zone_with_schedule(session, "modus-ueber", points=[(1, 360, "tag", Decimal("21.0"))])
    tag_mode = session.query(SetpointMode).filter_by(code="tag").one()
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            setpoint_mode_id=tag_mode.id,
            starts_at=datetime(2026, 8, 31, 0, 0),
            ends_at=None,
            source_id=source(session).id,
        )
    )
    session.flush()
    result = resolved_setpoint(session, zone, datetime(2026, 8, 31, 10, 0))
    assert result.temperature_c == Decimal("21.0")
    assert "Modus tag" in result.grund
