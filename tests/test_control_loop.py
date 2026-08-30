"""Tests fuer die reine Regelentscheidung `thermoctl.domain.regelung.entscheiden`.

Diese Aufgabe wird an ihren Tests gemessen (Auftragstext Aufgabe 6): jede der sechs Regeln
einzeln, ihre Rangfolge gegeneinander, die Grenzfaelle exakt auf der Schwelle, und
ausdruecklich der Defekt des Altsystems, den `thermoctl` nicht wiederholen darf.
"""

from decimal import Decimal

from thermoctl.domain.control_loop import (
    GRUND_CODE_AUS,
    GRUND_CODE_HEIZEN,
    REASON_CODE_BLOCKED_MINIMUM_DURATION,
    REASON_CODE_FROST_SENSOR_FAILURE,
    REASON_CODE_NO_SOURCE,
    REASON_CODE_UNCHANGED,
    REASON_CODE_WINDOW_OPEN,
    Lage,
    entscheiden,
)
from thermoctl.domain.zone_settings import ControlParameters


def _parameter(
    *,
    hysteresis_k: Decimal = Decimal("0.5"),
    min_on_seconds: int = 300,
    min_off_seconds: int = 300,
    sensor_timeout_seconds: int = 600,
    temperature_offset_k: Decimal = Decimal("0.0"),
    window_resume_delay_seconds: int = 300,
) -> ControlParameters:
    return ControlParameters(
        hysteresis_k=hysteresis_k,
        min_on_seconds=min_on_seconds,
        min_off_seconds=min_off_seconds,
        sensor_timeout_seconds=sensor_timeout_seconds,
        temperature_offset_k=temperature_offset_k,
        window_resume_delay_seconds=window_resume_delay_seconds,
    )


def _lage(
    *,
    ist_c: Decimal | None = Decimal("20.0"),
    soll_c: Decimal = Decimal("21.0"),
    soll_grund: str = "Zeitplan: Modus Tag ab 06:00",
    frost_c: Decimal = Decimal("16.0"),
    operating_mode: str = "auto",
    heizt_gerade: bool = False,
    seit_s: int | None = 1000,
    window_open: bool = False,
    window_closed_for_s: int | None = 1000,
    sensor_status: str = "ok",
    parameter: ControlParameters | None = None,
) -> Lage:
    return Lage(
        ist_c=ist_c,
        soll_c=soll_c,
        soll_grund=soll_grund,
        frost_c=frost_c,
        operating_mode=operating_mode,
        heizt_gerade=heizt_gerade,
        seit_s=seit_s,
        window_open=window_open,
        window_closed_for_s=window_closed_for_s,
        sensor_status=sensor_status,
        parameter=parameter or _parameter(),
    )


# ---------------------------------------------------------------------------
# Regel 1 — Sensorausfall
# ---------------------------------------------------------------------------


def test_rule1_a_stale_sensor_controls_to_frost_protection() -> None:
    """Ein veralteter Messwert traegt keine normale Heizentscheidung mehr — aber Frostschutz.

    Dauerhaft abschalten waere die gefaehrlichere Antwort: Genau so friert im Januar eine
    Leitung ein. Stattdessen gilt der Frostschutz-Sollwert; er liegt tief genug, dass die
    Anlage auf einem falschen Wert hoechstens auf ein unbedenkliches Niveau heizt.
    """
    e = entscheiden(
        _lage(sensor_status="veraltet", ist_c=Decimal("10.0"), soll_c=Decimal("21.0"),
              frost_c=Decimal("16.0"))
    )
    assert e.heizen is True
    assert e.grund_code == REASON_CODE_FROST_SENSOR_FAILURE
    assert "16.0" in e.grund and "Frostschutz" in e.grund


def test_rule1_a_stale_sensor_does_not_heat_to_the_normal_setpoint() -> None:
    """Der eigentliche Sollwert gilt bei ausgefallenem Sensor ausdruecklich nicht mehr."""
    e = entscheiden(
        _lage(sensor_status="veraltet", ist_c=Decimal("18.0"), soll_c=Decimal("21.0"),
              frost_c=Decimal("16.0"))
    )
    # Auf 21 °C wuerde bei 18 °C geheizt; auf den Frostschutzwert von 16 °C nicht.
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_FROST_SENSOR_FAILURE


def test_rule1_no_source_does_not_heat() -> None:
    """Ohne jeden Ist-Wert gibt es nichts, woran zu regeln waere — dann bleibt nur aus."""
    e = entscheiden(_lage(sensor_status="keine_quelle", ist_c=None))
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_NO_SOURCE


def test_rule1_a_stale_sensor_without_a_value_does_not_heat() -> None:
    """Veraltet UND ohne Wert: auch der Frostschutz braucht etwas, woran er sich misst."""
    e = entscheiden(_lage(sensor_status="veraltet", ist_c=None))
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_NO_SOURCE


def test_rule1_safety_net_status_ok_without_a_measured_value() -> None:
    """Vertragsverletzung des Aufrufers (status 'ok', aber kein Ist-Wert) fuehrt nicht zum
    Absturz, sondern zur selben sicheren Antwort wie 'keine_quelle'."""
    e = entscheiden(_lage(sensor_status="ok", ist_c=None))
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_NO_SOURCE


# ---------------------------------------------------------------------------
# Regel 2 — Betriebsart 'off'
# ---------------------------------------------------------------------------


def test_rule2_off_runs_through_the_normal_rule() -> None:
    """'off' heisst nicht stromlos: Der Aufrufer hat soll_c bereits auf den Frostschutzwert
    aufgeloest (aufgeloester_sollwert), und ab hier gilt schlicht die normale Hysterese
    darauf — bei ausreichend kaltem Ist-Wert wird auch im Zustand 'off' geheizt."""
    e = entscheiden(
        _lage(
            operating_mode="off",
            soll_c=Decimal("16.0"),
            soll_grund="Betriebsart Aus — Frostschutz",
            ist_c=Decimal("10.0"),
            heizt_gerade=False,
        )
    )
    assert e.heizen is True
    assert e.grund_code == GRUND_CODE_HEIZEN


def test_regel2_off_schaltet_auch_wieder_aus() -> None:
    e = entscheiden(
        _lage(
            operating_mode="off",
            soll_c=Decimal("16.0"),
            ist_c=Decimal("18.0"),
            heizt_gerade=True,
        )
    )
    assert e.heizen is False
    assert e.grund_code == GRUND_CODE_AUS


# ---------------------------------------------------------------------------
# Regel 3 — Fenster offen
# ---------------------------------------------------------------------------


def test_rule3_an_open_window_does_not_heat_despite_a_cold_room() -> None:
    e = entscheiden(_lage(window_open=True, ist_c=Decimal("5.0"), soll_c=Decimal("21.0")))
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_WINDOW_OPEN


# ---------------------------------------------------------------------------
# Regel 4 — Wiederanlaufverzoegerung
# ---------------------------------------------------------------------------


def test_regel4_wiederanlaufverzoegerung_haelt_ab() -> None:
    """Fenster gerade erst wieder zu — die Anlage soll nicht sofort gegen den noch
    auskuehlenden Raum anheizen."""
    e = entscheiden(
        _lage(
            window_open=False,
            window_closed_for_s=100,
            parameter=_parameter(window_resume_delay_seconds=300),
            ist_c=Decimal("5.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is False
    assert "Wiederanlauf" in e.grund


def test_rule4_after_the_delay_expires_the_normal_rule_applies_again() -> None:
    e = entscheiden(
        _lage(
            window_open=False,
            window_closed_for_s=301,
            parameter=_parameter(window_resume_delay_seconds=300),
            ist_c=Decimal("5.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is True
    assert e.grund_code == GRUND_CODE_HEIZEN


def test_rule4_no_delay_without_a_known_closing_time() -> None:
    """`fenster_zu_seit_s is None` heisst 'kein anstehender Nachlauf' (das Fenster war seit
    Beginn der Aufzeichnung nie offen) — dann gibt es nichts abzuwarten."""
    e = entscheiden(
        _lage(
            window_open=False,
            window_closed_for_s=None,
            ist_c=Decimal("5.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is True


# ---------------------------------------------------------------------------
# Regel 5 — Mindestschaltdauer
# ---------------------------------------------------------------------------


def test_rule5_the_minimum_on_time_keeps_the_valve_open() -> None:
    """Obwohl die Hysterese laengst 'aus' verlangt, bleibt eine gerade erst begonnene
    Heizphase fuer min_on_seconds bestehen — Ventilschutz."""
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            seit_s=10,
            parameter=_parameter(min_on_seconds=300),
            ist_c=Decimal("30.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is True
    assert e.grund_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_rule5_the_minimum_off_time_keeps_the_valve_closed() -> None:
    e = entscheiden(
        _lage(
            heizt_gerade=False,
            seit_s=10,
            parameter=_parameter(min_off_seconds=300),
            ist_c=Decimal("5.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_rule5_an_unknown_elapsed_time_does_not_lift_the_block_artificially() -> None:
    """`seit_s is None` heisst 'Dauer des aktuellen Zustands unbekannt', typischerweise der
    erste Zyklus nach einem Neustart ohne Vorgeschichte. Eine Sperre auf eine unbekannte
    Dauer waere selbst willkuerlich; deshalb greift sie hier nicht, und die Hysterese
    entscheidet regulaer weiter — ein frisch gestarteter Dienst haengt nicht in einer nie
    begonnenen Frist fest."""
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            seit_s=None,
            parameter=_parameter(min_on_seconds=300),
            ist_c=Decimal("30.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is False
    assert e.grund_code == GRUND_CODE_AUS


# ---------------------------------------------------------------------------
# Regel 6 — Hysterese, inklusive Grenzfaelle
# ---------------------------------------------------------------------------


def test_rule6_switches_on_below_setpoint_minus_hysteresis() -> None:
    e = entscheiden(
        _lage(
            heizt_gerade=False,
            ist_c=Decimal("20.4"),
            soll_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heizen is True
    assert e.grund_code == GRUND_CODE_HEIZEN


def test_rule6_switches_off_above_setpoint_plus_hysteresis() -> None:
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            ist_c=Decimal("21.6"),
            soll_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heizen is False
    assert e.grund_code == GRUND_CODE_AUS


def test_rule6_exactly_on_the_switch_on_threshold_does_not_switch_on_yet() -> None:
    """ist == soll - h: exakt auf der Schwelle wird noch nicht eingeschaltet."""
    e = entscheiden(
        _lage(
            heizt_gerade=False,
            ist_c=Decimal("20.5"),
            soll_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_UNCHANGED


def test_rule6_exactly_on_the_switch_off_threshold_does_not_switch_off_yet() -> None:
    """ist == soll + h: exakt auf der Schwelle wird noch nicht ausgeschaltet."""
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            ist_c=Decimal("21.5"),
            soll_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert e.heizen is True
    assert e.grund_code == REASON_CODE_UNCHANGED


def test_rule6_inside_the_hysteresis_nothing_changes_in_either_direction() -> None:
    aus_bleibt_aus = entscheiden(
        _lage(heizt_gerade=False, ist_c=Decimal("21.0"), soll_c=Decimal("21.0"))
    )
    an_bleibt_an = entscheiden(
        _lage(heizt_gerade=True, ist_c=Decimal("21.0"), soll_c=Decimal("21.0"))
    )
    assert aus_bleibt_aus.heizen is False
    assert an_bleibt_an.heizen is True
    assert aus_bleibt_aus.grund_code == an_bleibt_an.grund_code == REASON_CODE_UNCHANGED


# ---------------------------------------------------------------------------
# Der Defekt des Altsystems
# ---------------------------------------------------------------------------


def test_the_legacy_defect_no_constant_toggling_at_the_setpoint() -> None:
    """Das Altsystem entscheidet `if ist < soll: an, sonst aus` — ohne Hysterese schaltet das
    Ventil bei ist == soll in jedem Zyklus um (an, aus, an, aus, ...), weil Gleichheit jedes
    Mal denselben Vergleich neu und ohne Gedaechtnis an den letzten Zustand auswertet. Dieser
    Test laesst mehrere Zyklen mit exakt `ist == soll` laufen und belegt, dass `thermoctl`
    dank Hysterese in jedem Zyklus beim zuletzt gewaehlten Zustand bleibt, ganz gleich, ob
    dieser Zustand mit 'an' oder mit 'aus' begonnen hat.
    """
    ist_c = Decimal("21.0")
    soll_c = Decimal("21.0")
    parameter = _parameter(hysteresis_k=Decimal("0.5"), min_on_seconds=0, min_off_seconds=0)

    for start_state in (False, True):
        heizt_gerade = start_state
        for cycle in range(5):
            e = entscheiden(
                _lage(
                    ist_c=ist_c,
                    soll_c=soll_c,
                    heizt_gerade=heizt_gerade,
                    seit_s=1000,
                    parameter=parameter,
                )
            )
            assert e.heizen == start_state, f"Zyklus {cycle}: unerwartet umgeschaltet"
            assert e.grund_code == REASON_CODE_UNCHANGED
            heizt_gerade = e.heizen


# ---------------------------------------------------------------------------
# Rangfolge gegeneinander — jedes Paar benachbarter Regeln
# ---------------------------------------------------------------------------


def test_precedence_sensor_failure_beats_the_resolved_setpoint() -> None:
    """Bei ausgefallenem Sensor gilt der Frostschutzwert, egal was der Zeitplan sagt."""
    e = entscheiden(
        _lage(sensor_status="veraltet", operating_mode="off", ist_c=Decimal("5.0"),
              soll_c=Decimal("21.0"), frost_c=Decimal("16.0"))
    )
    assert e.grund_code == REASON_CODE_FROST_SENSOR_FAILURE
    assert e.heizen is True
    assert "16.0" in e.grund


def test_precedence_an_open_window_beats_frost_protection_on_sensor_failure() -> None:
    """Ein offenes Fenster gewinnt auch gegen den Frostschutz.

    Das ist Absicht: Gegen ein offenes Fenster zu heizen hilft niemandem, und die Zone
    kuehlt in der Zeit nicht auf Frostniveau ab. Sobald das Fenster zu ist, greift der
    Frostschutz wieder.
    """
    e = entscheiden(
        _lage(sensor_status="veraltet", ist_c=Decimal("5.0"), window_open=True)
    )
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_WINDOW_OPEN


def test_precedence_sensor_failure_beats_an_open_window() -> None:
    """Besonders im Auftrag verlangt: Sensorausfall gewinnt auch gegen ein offenes Fenster."""
    e = entscheiden(
        _lage(sensor_status="keine_quelle", ist_c=None, window_open=True)
    )
    assert e.grund_code == REASON_CODE_NO_SOURCE


def test_precedence_operating_mode_off_loses_to_an_open_window() -> None:
    """'off' fuehrt lediglich zum Frostschutz-Sollwert; ein offenes Fenster gewinnt trotzdem
    gegen die daraus resultierende Heizabsicht der Hysterese."""
    e = entscheiden(
        _lage(
            operating_mode="off",
            soll_c=Decimal("16.0"),
            ist_c=Decimal("5.0"),  # weit unter Soll — Hysterese wuerde fuer 'an' stimmen
            window_open=True,
        )
    )
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_WINDOW_OPEN


def test_precedence_an_open_window_beats_the_restart_delay() -> None:
    """Widerspruechliche Eingabe (Fenster offen, aber auch eine 'zu seit'-Dauer gesetzt) —
    Regel 3 gewinnt unabhaengig davon, was Regel 4 dazu sagen wuerde."""
    e = entscheiden(
        _lage(
            window_open=True,
            window_closed_for_s=1,
            parameter=_parameter(window_resume_delay_seconds=300),
            ist_c=Decimal("5.0"),
        )
    )
    assert e.grund_code == REASON_CODE_WINDOW_OPEN


def test_precedence_the_restart_delay_beats_the_minimum_switch_time() -> None:
    """Fenster gerade erst zu UND der aktuelle (Aus-)Zustand gilt auch noch keine
    Mindestdauer — Regel 4 entscheidet mit ihrer eigenen Begruendung, nicht mit der aus
    Regel 5."""
    e = entscheiden(
        _lage(
            window_open=False,
            window_closed_for_s=10,
            parameter=_parameter(window_resume_delay_seconds=300, min_off_seconds=300),
            heizt_gerade=False,
            seit_s=10,
            ist_c=Decimal("5.0"),
        )
    )
    assert e.heizen is False
    assert e.grund_code == GRUND_CODE_AUS
    assert e.grund_code != REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_precedence_the_minimum_switch_time_beats_the_hysteresis() -> None:
    """Besonders im Auftrag verlangt (sinngemaess): eine noch laufende Mindestdauer haelt
    den Zustand fest, obwohl die Hysterese laengst etwas anderes verlangt."""
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            seit_s=5,
            parameter=_parameter(min_on_seconds=300, hysteresis_k=Decimal("0.5")),
            ist_c=Decimal("30.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.heizen is True
    assert e.grund_code == REASON_CODE_BLOCKED_MINIMUM_DURATION


def test_precedence_an_open_window_beats_the_minimum_switch_time() -> None:
    """Besonders im Auftrag verlangt: ein offenes Fenster gewinnt gegen eine laufende
    Mindestschaltdauer, die sonst den Heizzustand haette festhalten koennen."""
    e = entscheiden(
        _lage(
            window_open=True,
            heizt_gerade=True,
            seit_s=5,
            parameter=_parameter(min_on_seconds=300),
            ist_c=Decimal("30.0"),
        )
    )
    assert e.heizen is False
    assert e.grund_code == REASON_CODE_WINDOW_OPEN


# ---------------------------------------------------------------------------
# Grenzfall Mindestschaltdauer: die Sperre endet genau zum angegebenen Zeitpunkt
# ---------------------------------------------------------------------------


def test_exactly_at_the_minimum_duration_the_block_is_over() -> None:
    """seit_s == min_on_seconds: die Sperre ist zu diesem Zeitpunkt bereits vorbei
    (`<` statt `<=` in der Bedingung), die Hysterese entscheidet wieder regulaer."""
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            seit_s=300,
            parameter=_parameter(min_on_seconds=300, hysteresis_k=Decimal("0.5")),
            ist_c=Decimal("30.0"),
            soll_c=Decimal("21.0"),
        )
    )
    assert e.grund_code != REASON_CODE_BLOCKED_MINIMUM_DURATION
    assert e.heizen is False
    assert e.grund_code == GRUND_CODE_AUS


# ---------------------------------------------------------------------------
# Der Offset wirkt
# ---------------------------------------------------------------------------


def test_the_offset_changes_the_decision_when_it_reaches_past_the_hysteresis() -> None:
    """Derselbe rohe Ist-Wert fuehrt mit einem hinreichend grossen Offset zu einer anderen
    Entscheidung — die Kalibrierung wirkt vor der Regel, wie in Abschnitt 6 gefordert."""
    ohne_offset = entscheiden(
        _lage(
            ist_c=Decimal("20.0"),
            soll_c=Decimal("21.0"),
            heizt_gerade=False,
            parameter=_parameter(hysteresis_k=Decimal("0.5"), temperature_offset_k=Decimal("0.0")),
        )
    )
    mit_offset = entscheiden(
        _lage(
            ist_c=Decimal("20.0"),
            soll_c=Decimal("21.0"),
            heizt_gerade=False,
            parameter=_parameter(hysteresis_k=Decimal("0.5"), temperature_offset_k=Decimal("2.0")),
        )
    )
    assert ohne_offset.heizen is True
    assert mit_offset.heizen is False
    assert ohne_offset.grund_code != mit_offset.grund_code


# ---------------------------------------------------------------------------
# Die Begruendung enthaelt konkrete Zahlen, keine Schablone
# ---------------------------------------------------------------------------


def test_the_reason_carries_the_actual_numbers_of_the_hysteresis_decision() -> None:
    e = entscheiden(
        _lage(
            heizt_gerade=False,
            ist_c=Decimal("20.4"),
            soll_c=Decimal("21.0"),
            parameter=_parameter(hysteresis_k=Decimal("0.5")),
        )
    )
    assert "20.4" in e.grund
    assert "21.0" in e.grund
    assert "0.5" in e.grund


def test_the_reason_carries_the_actual_numbers_of_the_minimum_duration_decision() -> None:
    e = entscheiden(
        _lage(
            heizt_gerade=True,
            seit_s=42,
            parameter=_parameter(min_on_seconds=300),
            ist_c=Decimal("30.0"),
        )
    )
    assert "42" in e.grund
    assert "300" in e.grund


def test_the_reason_carries_the_actual_numbers_of_the_restart_delay() -> None:
    e = entscheiden(
        _lage(
            window_open=False,
            window_closed_for_s=17,
            parameter=_parameter(window_resume_delay_seconds=300),
            ist_c=Decimal("5.0"),
        )
    )
    assert "17" in e.grund
    assert "300" in e.grund
