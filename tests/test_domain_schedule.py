from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import punkt, quelle, zone_anlegen, zone_mit_zeitplan
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.zone import SetpointMode
from thermoctl.domain.schedule import (
    aufgeloester_sollwert,
    geltender_punkt,
    naechster_punkt,
    uebersteuerung_anlegen,
    uebersteuerung_aufheben,
)


def test_punkt_gilt_bis_zum_naechsten() -> None:
    punkte = [punkt(1, 360, "tag"), punkt(1, 1380, "nacht")]  # Mo 06:00 und 23:00
    montag_zehn_uhr = datetime(2026, 8, 31, 10, 0)
    assert geltender_punkt(punkte, montag_zehn_uhr).minute_of_day == 360


def test_vor_dem_ersten_punkt_gilt_der_letzte_der_woche() -> None:
    """Der Sonntagabend-Punkt wirkt bis Montagfrueh — die Woche ist ein Ring."""
    punkte = [punkt(1, 360, "tag"), punkt(7, 1320, "nacht")]  # Mo 06:00, So 22:00
    montag_drei_uhr = datetime(2026, 8, 31, 3, 0)
    gilt = geltender_punkt(punkte, montag_drei_uhr)
    assert gilt.weekday == 7 and gilt.minute_of_day == 1320


def test_ohne_punkte_gibt_es_keinen_geltenden() -> None:
    assert geltender_punkt([], datetime(2026, 8, 31, 10, 0)) is None


def test_punkt_genau_zur_schaltminute_gilt_bereits() -> None:
    punkte = [punkt(1, 360, "tag")]
    assert geltender_punkt(punkte, datetime(2026, 8, 31, 6, 0)) is not None


def test_naechster_punkt_liegt_in_der_zukunft() -> None:
    punkte = [punkt(1, 360, "tag"), punkt(1, 1380, "nacht")]
    naechster = naechster_punkt(punkte, datetime(2026, 8, 31, 10, 0))
    assert naechster == datetime(2026, 8, 31, 23, 0)


def test_ohne_zeitplan_gilt_der_frostschutz(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "leer", punkte=[], frostschutz=Decimal("16.0"))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("16.0")
    assert "Frostschutz" in ergebnis.grund


def test_betriebsart_aus_ergibt_frostschutz(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "aus", punkte=[(1, 360, "tag", Decimal("21.0"))],
                             betriebsart="off", frostschutz=Decimal("16.0"))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("16.0")


def test_uebersteuerung_schlaegt_den_zeitplan(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "ueber", punkte=[(1, 360, "tag", Decimal("21.0"))],
                             uebersteuerung=(Decimal("23.5"), None))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("23.5")
    assert "Uebersteuerung" in ergebnis.grund


def test_abgelaufene_uebersteuerung_wirkt_nicht_mehr(session: Session) -> None:
    zone = zone_mit_zeitplan(
        session, "abgelaufen", punkte=[(1, 360, "tag", Decimal("21.0"))],
        uebersteuerung=(Decimal("23.5"), datetime(2026, 8, 31, 9, 0)),
    )
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("21.0")


def test_grund_benennt_die_entscheidung(session: Session) -> None:
    """Grundsatz 5: nachvollziehbar, warum dieser Sollwert gilt."""
    zone = zone_mit_zeitplan(session, "grund", punkte=[(1, 360, "tag", Decimal("21.0"))])
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert "Tag" in ergebnis.grund and "06:00" in ergebnis.grund


def test_naechster_punkt_ohne_punkte_gibt_es_nicht() -> None:
    assert naechster_punkt([], datetime(2026, 8, 31, 10, 0)) is None


def test_uebersteuerung_anlegen_ohne_api_quelle_schlaegt_fehl(session: Session) -> None:
    """Fehlt die Lookup-Zeile fuer die Quelle 'api', ist die Einrichtung unvollstaendig —
    das soll laut und eindeutig scheitern statt eine Uebersteuerung ohne Quelle anzulegen."""
    zone = zone_anlegen(session, "ohne-quelle")
    with pytest.raises(RuntimeError, match="api"):
        uebersteuerung_anlegen(session, zone, Decimal("20.0"), None)


def test_uebersteuerung_anlegen_legt_eine_neue_uebersteuerung_an(session: Session) -> None:
    zone = zone_anlegen(session, "mit-quelle")
    quelle(session, "api")
    eintrag = uebersteuerung_anlegen(
        session, zone, Decimal("22.5"), None, user_id=None, token_id=None
    )
    assert eintrag.zone_id == zone.id
    assert eintrag.temperature_c == Decimal("22.5")
    assert eintrag.id is not None


def test_uebersteuerung_aufheben_beendet_die_aktive(session: Session) -> None:
    zone = zone_mit_zeitplan(
        session, "aufheben", punkte=[], uebersteuerung=(Decimal("23.0"), None)
    )
    eintrag = uebersteuerung_aufheben(session, zone)
    assert eintrag is not None
    assert eintrag.cancelled_at is not None


def test_uebersteuerung_aufheben_ohne_aktive_gibt_none(session: Session) -> None:
    zone = zone_anlegen(session, "keine-ueber")
    assert uebersteuerung_aufheben(session, zone) is None


def test_uebersteuerung_auf_modus_ohne_feste_temperatur(session: Session) -> None:
    """Eine Uebersteuerung kann auf einen Modus statt eine feste Temperatur zeigen —
    der Sollwert kommt dann aus der Zonentemperatur fuer diesen Modus."""
    zone = zone_mit_zeitplan(session, "modus-ueber", punkte=[(1, 360, "tag", Decimal("21.0"))])
    tag_modus = session.query(SetpointMode).filter_by(code="tag").one()
    session.add(
        ZoneOverride(
            zone_id=zone.id,
            setpoint_mode_id=tag_modus.id,
            starts_at=datetime(2026, 8, 31, 0, 0),
            ends_at=None,
            source_id=quelle(session).id,
        )
    )
    session.flush()
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("21.0")
    assert "Modus tag" in ergebnis.grund
