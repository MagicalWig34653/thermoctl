"""Zustandswechsel der Sonnenabsenkung im Schaltprotokoll.

`domain/solar_setback_log.py` leitet Beginn, Betragsänderung und Ende aus aufeinander-
folgenden `shadow_decision`-Zeilen einer Zone ab; `list_commands` mischt sie als
`entry_kind == "absenkung"` ein. Getestet wird gegen echte Zeilen in der echten
Datenbank (SQLite und MariaDB), weil die Wechsel per `LAG()` in der Datenbank bestimmt
werden -- ein Test gegen eine Python-Nachbildung würde die Abfrage gar nicht erreichen.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from tests.helpers import create_device, create_device_command, create_zone
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain.device_commands import ENTRY_KIND_SETBACK, list_commands
from thermoctl.domain.solar_setback_log import (
    SetbackTransition,
    setback_transitions,
    setback_zone_names,
)

T0 = datetime(2026, 10, 9, 6, 0)
SCHEDULED = Decimal("20.5")
K2 = Decimal("2.0")
K15 = Decimal("1.5")


def _row(
    session: Session,
    zone: Zone,
    at: datetime,
    k: Decimal | None,
    *,
    scheduled: Decimal | None = SCHEDULED,
) -> ShadowDecision:
    """Eine Schattenzeile; wirksamer Sollwert = Zeitplan - Absenkung, wie im Betrieb."""
    effective = None if scheduled is None else scheduled - (k or Decimal(0))
    row = ShadowDecision(
        decided_at=at,
        zone_id=zone.id,
        setpoint_c=effective,
        scheduled_setpoint_c=scheduled,
        solar_setback_k=k,
        setpoint_reason="Zeitplan",
        would_heat=False,
        outcome_code="aus",
        reason="Sollwert ist erreicht.",
    )
    session.add(row)
    session.flush()
    return row


def _series(
    session: Session, zone: Zone, ks: list[Decimal | None], *, start: datetime = T0,
    step: timedelta = timedelta(minutes=1),
) -> list[ShadowDecision]:
    return [_row(session, zone, start + step * i, k) for i, k in enumerate(ks)]


def _transitions(
    session: Session,
    zone_name: str | None = None,
    *,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    fetch: int = 100,
) -> list[SetbackTransition]:
    return setback_transitions(
        session, zone_name=zone_name, from_at=from_at, to_at=to_at, fetch=fetch
    )


def test_begin_change_and_end_each_produce_exactly_one_entry(session: Session) -> None:
    zone = create_zone(session, "sonnenzone")
    _series(session, zone, [None, None, K2, K2, K2, K15, K15, None, None])

    result = _transitions(session, zone.name)

    # Neueste zuerst: Ende (Index 7), Änderung (Index 5), Beginn (Index 2).
    assert [entry.kind for entry in result] == ["ende", "aenderung", "beginn"]
    assert [entry.decided_at for entry in result] == [
        T0 + timedelta(minutes=7),
        T0 + timedelta(minutes=5),
        T0 + timedelta(minutes=2),
    ]
    assert [entry.text for entry in result] == [
        "Sonnenabsenkung beendet: wirksam wieder 20,5 °C",
        "Sonnenabsenkung geändert auf -1,5 K (vorher -2,0 K): Zeitplan 20,5 °C, wirksam 19,0 °C",
        "Sonnenabsenkung -2,0 K: Zeitplan 20,5 °C, wirksam 18,5 °C",
    ]


def test_a_zone_without_any_setback_produces_no_entry(session: Session) -> None:
    zone = create_zone(session, "schattenzone")
    _series(session, zone, [None] * 5)

    assert _transitions(session, zone.name) == []
    assert list_commands(session, zone_name=zone.name) == []


def test_a_zone_without_any_shadow_rows_produces_no_entry(session: Session) -> None:
    zone = create_zone(session, "ohne-daten-zone")

    assert _transitions(session, zone.name) == []
    assert _transitions(session, "gibt-es-nicht") == []


def test_an_unchanged_setback_is_reported_once_not_once_per_cycle(session: Session) -> None:
    zone = create_zone(session, "dauerzone")
    _series(session, zone, [K2] * 30)

    result = _transitions(session, zone.name)

    assert [entry.kind for entry in result] == ["beginn"]


def test_a_changed_schedule_setpoint_with_the_same_amount_is_no_change(session: Session) -> None:
    zone = create_zone(session, "zeitplansprungzone")
    _row(session, zone, T0, K2, scheduled=Decimal("20.5"))
    _row(session, zone, T0 + timedelta(minutes=1), K2, scheduled=Decimal("21.0"))

    assert [entry.kind for entry in _transitions(session, zone.name)] == ["beginn"]


def test_the_first_row_of_the_window_compares_against_the_row_before_it(
    session: Session,
) -> None:
    """Ohne die Vorzeile wäre die erste Zeile im Fenster ein falscher Beginn."""
    zone = create_zone(session, "fensterzone")
    _series(session, zone, [None, K2, K2, K2, K2, None])

    # Fenster beginnt mitten in der laufenden Absenkung (Index 3).
    result = _transitions(session, zone.name, from_at=T0 + timedelta(minutes=3))

    assert [entry.kind for entry in result] == ["ende"]


def test_a_window_starting_exactly_on_a_change_reports_it_with_the_previous_amount(
    session: Session,
) -> None:
    zone = create_zone(session, "aenderungsfensterzone")
    _series(session, zone, [K2, K2, K15, K15])

    result = _transitions(session, zone.name, from_at=T0 + timedelta(minutes=2))

    assert [entry.kind for entry in result] == ["aenderung"]
    assert result[0].previous_k == K2
    assert result[0].setback_k == K15


def test_the_end_row_is_found_even_when_the_window_starts_right_on_it(
    session: Session,
) -> None:
    """Die letzte Zeile mit Absenkung liegt vor dem Fenster, das Ende darin."""
    zone = create_zone(session, "endfensterzone")
    _series(session, zone, [K2, K2, None, None])

    result = _transitions(session, zone.name, from_at=T0 + timedelta(minutes=2))

    assert [entry.kind for entry in result] == ["ende"]


def test_the_upper_bound_is_inclusive_and_cuts_off_later_changes(session: Session) -> None:
    zone = create_zone(session, "obergrenzenzone")
    _series(session, zone, [None, K2, K2, None])

    until_begin = _transitions(session, zone.name, to_at=T0 + timedelta(minutes=1))
    until_end = _transitions(session, zone.name, to_at=T0 + timedelta(minutes=3))

    assert [entry.kind for entry in until_begin] == ["beginn"]
    assert [entry.kind for entry in until_end] == ["ende", "beginn"]


def test_rows_without_a_schedule_setpoint_are_no_phantom_end(session: Session) -> None:
    """Die synthetische Rückkehr-Markierung (und Zeilen aus der Zeit vor der Migration)
    haben keinen `scheduled_setpoint_c` und sagen nichts über die Absenkung."""
    zone = create_zone(session, "markierungszone")
    _row(session, zone, T0, K2)
    _row(session, zone, T0 + timedelta(minutes=1), None, scheduled=None)
    _row(session, zone, T0 + timedelta(minutes=2), K2)

    assert [entry.kind for entry in _transitions(session, zone.name)] == ["beginn"]


def test_a_setback_row_without_schedule_setpoint_does_not_break_the_window_lookup(
    session: Session,
) -> None:
    """Widersprüchliche Handarbeit in der Tabelle: der einzige Beleg für den Beginn
    (`solar_setback_k` gesetzt) hat keinen Zeitplan-Sollwert. Die Vorzeile ist dann nicht
    zu finden; die erste vollständige Zeile gilt als Beginn statt als Absturz."""
    zone = create_zone(session, "luckenzone")
    _row(session, zone, T0, K2, scheduled=None)
    _row(session, zone, T0 + timedelta(hours=2), K2)

    result = _transitions(session, zone.name, from_at=T0 + timedelta(hours=1))

    assert [entry.kind for entry in result] == ["beginn"]


def test_zones_do_not_leak_into_each_other(session: Session) -> None:
    sonnig = create_zone(session, "sonnig")
    schattig = create_zone(session, "schattig")
    _series(session, sonnig, [None, K2, K2, None])
    _series(session, schattig, [None, None, None, None])
    # Dieselben Zeitstempel in der anderen Zone: kein Wechsel ohne eigene Vorzeile.
    result_all = _transitions(session)
    result_sonnig = _transitions(session, "sonnig")
    result_schattig = _transitions(session, "schattig")

    assert [(entry.zone_name, entry.kind) for entry in result_all] == [
        ("sonnig", "ende"),
        ("sonnig", "beginn"),
    ]
    assert len(result_sonnig) == 2
    assert result_schattig == []


def test_interleaved_zones_compare_only_against_their_own_previous_row(
    session: Session,
) -> None:
    """Zwei Zonen im Wechsel: hätte der Vergleich die falsche Vorzeile, gäbe es
    Beginn/Ende bei jedem Zeilenpaar."""
    a = create_zone(session, "wechsel-a")
    b = create_zone(session, "wechsel-b")
    for minute in range(6):
        _row(session, a, T0 + timedelta(minutes=minute), K2)
        _row(session, b, T0 + timedelta(minutes=minute), None)

    result = _transitions(session)

    assert [(entry.zone_name, entry.kind) for entry in result] == [("wechsel-a", "beginn")]


def test_the_newest_entries_are_returned_when_fetch_is_smaller_than_the_history(
    session: Session,
) -> None:
    """Abschnittsweises Lesen vom Neuesten her: über Wochen verteilte Wechsel, nur die
    neuesten drei verlangt -- dasselbe Ergebnis wie beim vollständigen Lesen."""
    zone = create_zone(session, "langzone")
    # Stündliche Zeilen über 40 Tage, die Absenkung wechselt täglich ein und aus.
    ks = [K2 if (hour // 24) % 2 == 0 and 6 <= hour % 24 < 12 else None for hour in range(40 * 24)]
    _series(session, zone, ks, step=timedelta(hours=1))

    complete = _transitions(session, zone.name, fetch=500)
    newest = _transitions(session, zone.name, fetch=3)

    assert newest == complete[:3]
    # Zwanzig Tage mit Absenkung: abwechselnd Beginn und Ende, nirgends zweimal dasselbe.
    assert [entry.kind for entry in reversed(complete)] == ["beginn", "ende"] * 20


def test_a_long_setback_across_section_boundaries_has_no_false_begin(session: Session) -> None:
    """Die Abschnitte (7, 14, ... Tage) enden mitten in der Absenkung; an keiner Grenze
    darf ein weiterer Beginn entstehen."""
    zone = create_zone(session, "grenzzone")
    ks: list[Decimal | None] = [None] + [K2] * (30 * 24) + [None]
    _series(session, zone, ks, step=timedelta(hours=1))

    result = _transitions(session, zone.name, fetch=500)

    assert [entry.kind for entry in result] == ["ende", "beginn"]


def test_a_window_inside_a_long_setback_reports_nothing(session: Session) -> None:
    zone = create_zone(session, "mittendrinzone")
    ks: list[Decimal | None] = [None] + [K2] * (20 * 24) + [None]
    _series(session, zone, ks, step=timedelta(hours=1))

    result = _transitions(
        session,
        zone.name,
        from_at=T0 + timedelta(days=5),
        to_at=T0 + timedelta(days=15),
    )

    assert result == []


# --- Einbindung in list_commands -------------------------------------------------


def test_list_commands_marks_the_entries_as_absenkung_and_merges_them_by_time(
    session: Session,
) -> None:
    zone = create_zone(session, "protokollzone-sonne")
    geraet = create_device(session, "protokollgeraet-sonne")
    _series(session, zone, [None, K2, K2, None])
    create_device_command(session, zone, geraet, at=T0 + timedelta(minutes=2))

    result = list_commands(session, zone_name=zone.name)

    assert [entry.entry_kind for entry in result] == ["absenkung", "befehl", "absenkung"]
    ende, _befehl, beginn = result
    assert ende.sent_at == T0 + timedelta(minutes=3)
    assert ende.zone_name == zone.name
    assert ende.source == "sonnenabsenkung"
    assert ende.command == "sonnenabsenkung"
    assert ende.outcome == "absenkung_ende"
    assert ende.reason == "Sonnenabsenkung beendet: wirksam wieder 20,5 °C"
    assert ende.entry_kind == ENTRY_KIND_SETBACK
    assert ende.simulated is False
    assert beginn.outcome == "absenkung_beginn"
    assert beginn.reason == "Sonnenabsenkung -2,0 K: Zeitplan 20,5 °C, wirksam 18,5 °C"


def test_an_outcome_filter_excludes_the_absenkung_entries(session: Session) -> None:
    zone = create_zone(session, "ergebnisfilterzone-sonne")
    geraet = create_device(session, "ergebnisfiltergeraet-sonne")
    _series(session, zone, [None, K2, None])
    create_device_command(session, zone, geraet, outcome_code="executed")

    result = list_commands(session, zone_name=zone.name, outcome="executed")

    assert [entry.entry_kind for entry in result] == ["befehl"]


def test_the_zone_filter_keeps_other_zones_absenkung_entries_out(session: Session) -> None:
    sonnig = create_zone(session, "gefiltert-sonnig")
    andere = create_zone(session, "gefiltert-andere")
    _series(session, sonnig, [None, K2, None])
    _series(session, andere, [None, K15, None])

    result = list_commands(session, zone_name="gefiltert-andere")

    assert {entry.zone_name for entry in result} == {"gefiltert-andere"}
    assert len(result) == 2


def test_offset_and_limit_paginate_over_the_merged_absenkung_entries(session: Session) -> None:
    zone = create_zone(session, "seitenzone-sonne")
    _series(session, zone, [None, K2, K15, None])  # beginn, aenderung, ende

    first = list_commands(session, zone_name=zone.name, limit=2, offset=0)
    second = list_commands(session, zone_name=zone.name, limit=2, offset=2)

    assert [entry.outcome for entry in first] == ["absenkung_ende", "absenkung_aenderung"]
    assert [entry.outcome for entry in second] == ["absenkung_beginn"]


def test_date_bounds_given_as_aware_values_are_honoured(session: Session) -> None:
    zone = create_zone(session, "zeitzone-sonne")
    _series(session, zone, [None, K2, K2, None])

    result = list_commands(
        session,
        zone_name=zone.name,
        from_at=(T0 + timedelta(minutes=3)).replace(tzinfo=UTC),
    )

    assert [entry.outcome for entry in result] == ["absenkung_ende"]


def test_the_zone_filter_also_matches_the_display_name(session: Session) -> None:
    """Das Schaltprotokoll nennt Zonen in seiner Auswahl beim Anzeigenamen (so steht er in
    `device_command.zone_name`); wählt man ihn, müssen auch die Absenkungen kommen."""
    zone = create_zone(session, "anzeigename-zone")
    other = create_zone(session, "anzeigename-andere")
    _series(session, zone, [None, K2, None])
    _series(session, other, [None, K15, None])

    by_display = _transitions(session, zone.display_name)
    by_name = _transitions(session, zone.name)

    assert {entry.zone_name for entry in by_display} == {zone.name}
    assert [e.decision_id for e in by_display] == [e.decision_id for e in by_name]
    assert len(by_display) == 2


def test_setback_zone_names_lists_only_zones_with_a_setback(session: Session) -> None:
    with_setback = create_zone(session, "namen-mit")
    without = create_zone(session, "namen-ohne")
    _series(session, with_setback, [None, K2, None])
    _series(session, without, [None, None])

    assert setback_zone_names(session) == [with_setback.display_name]
