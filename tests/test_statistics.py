"""Heizzeiten aus dem Schattenprotokoll.

Die Zahl auf der Statistikseite ist nur so viel wert wie die Rechnung dahinter. Eine
Heizung, die laut Statistik acht Stunden lief, weil der Dienst acht Stunden stand, waere
schlimmer als gar keine Statistik.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from tests.helpers import create_zone
from thermoctl.db.models.state import ShadowDecision
from thermoctl.domain.statistics import as_duration, heizzeiten

BEGINN = datetime(2026, 8, 24, 6, 0)


def _protokoll(session: Session, zone_id: int, pattern: list[tuple[int, bool]]) -> None:
    """`muster` ist eine Folge aus (Minuten seit BEGINN, wuerde heizen)."""
    from decimal import Decimal

    for versatz, heizt in pattern:
        session.add(
            ShadowDecision(
                decided_at=BEGINN + timedelta(minutes=versatz),
                zone_id=zone_id,
                temperature_c=Decimal("20.0"),
                setpoint_c=Decimal("21.0"),
                setpoint_reason="Zeitplan",
                would_heat=heizt,
                previous_would_heat=None,
                outcome_code="ok",
                reason="Test",
            )
        )
    session.flush()


def test_dauer_kommt_aus_den_abstaenden(session: Session) -> None:
    """Drei Messpunkte im Minutentakt, die ersten beiden heizend: zwei Minuten."""
    zone = create_zone(session, "statistikzone")
    _protokoll(session, zone.id, [(0, True), (1, True), (2, False)])

    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(hours=1), cycle_seconds=60
    )
    assert result[zone.id].seconds_gesamt == 120


def test_der_letzte_messpunkt_zaehlt_nicht_ins_unendliche(session: Session) -> None:
    """Nach dem letzten Punkt weiss niemand, wie es weiterging -- er darf nichts
    beitragen."""
    zone = create_zone(session, "letzterpunkt")
    _protokoll(session, zone.id, [(0, True)])
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(hours=1), cycle_seconds=60
    )
    assert result[zone.id].seconds_gesamt == 0


def test_eine_luecke_wird_gekappt(session: Session) -> None:
    """Der Dienst stand acht Stunden. Diese Zeit als Heizzeit zu zaehlen waere frei
    erfunden -- die Anlage hat nichts gemeldet."""
    zone = create_zone(session, "luecke")
    _protokoll(session, zone.id, [(0, True), (480, True), (481, False)])
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(days=1), cycle_seconds=60
    )
    # 3 Minuten Kappung fuer die Luecke, dazu die eine echte Minute danach.
    assert result[zone.id].seconds_gesamt == 180 + 60


def test_ohne_kappung_waere_es_ein_ganzer_arbeitstag(session: Session) -> None:
    """Gegenprobe zur Kappung: Sie ist der Unterschied zwischen vier Minuten und acht
    Stunden. Ohne sie waere der Test oben auch von einer Fassung erfuellt, die schlicht
    jeden Abstand addiert."""
    zone = create_zone(session, "ohnekappung")
    _protokoll(session, zone.id, [(0, True), (480, True), (481, False)])
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(days=1), cycle_seconds=100000
    )
    assert result[zone.id].seconds_gesamt == 480 * 60 + 60


def test_ein_langsamerer_zyklus_zaehlt_richtig(session: Session) -> None:
    """Die Zyklusdauer ist einstellbar. Ein Zaehler "Zeilen mal Zyklus" laege falsch,
    sobald sie einmal anders stand -- die Abstaende liegen richtig."""
    zone = create_zone(session, "langsam")
    _protokoll(session, zone.id, [(0, True), (5, True), (10, False)])
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(hours=1), cycle_seconds=300
    )
    assert result[zone.id].seconds_gesamt == 600


def test_tage_werden_getrennt(session: Session) -> None:
    zone = create_zone(session, "tagesgrenze")
    _protokoll(
        session,
        zone.id,
        [(0, True), (1, False), (60 * 24, True), (60 * 24 + 1, False)],
    )
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(days=2), cycle_seconds=60
    )
    by_day = {t.day: t.seconds for t in result[zone.id].days}
    assert by_day[BEGINN.date()] == 60
    assert by_day[(BEGINN + timedelta(days=1)).date()] == 60


def test_zone_ohne_protokoll_kommt_mit_nullen_vor(session: Session) -> None:
    """Sonst faellt eine Zone aus der Liste, sobald sie nie geheizt hat -- und man
    haelt sie fuer geloescht statt fuer kalt."""
    zone = create_zone(session, "stille-zone")
    result = heizzeiten(
        session, [zone.id], BEGINN, BEGINN + timedelta(days=2), cycle_seconds=60
    )
    assert zone.id in result
    assert len(result[zone.id].days) == 3
    assert result[zone.id].seconds_gesamt == 0


def test_dauer_in_worten() -> None:
    assert as_duration(0) == "–"
    assert as_duration(59) == "1m"
    assert as_duration(35 * 60) == "35m"
    assert as_duration(4 * 3600 + 5 * 60) == "4h 05m"
