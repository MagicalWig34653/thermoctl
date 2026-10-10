"""Das Glossar: Loader, Prüfregeln, Bindung der Texte an den Code und Docs-Fassung.

Drei Arten von Tests, aus drei Gründen:

* **Loader** -- jede Prüfregel von `parse_glossary` hat einen Fall, der sie auslöst, und
  einen, der sie nicht auslöst. Ein Validator, dessen Fehlerweg nie ausgeführt wird,
  prüft nichts.
* **Bindung an den Code** -- ein Glossartext, der einen Vorgabewert oder eine Schwelle
  nennt, ist eine Behauptung über den Code. Ändert jemand den Wert dort, muss dieser
  Test fehlschlagen, statt dass der Text still falsch wird.
* **Docs** -- `docs/glossar.md` ist erzeugt; der Test schlägt fehl, wenn jemand die
  JSON-Quelle ändert und das Skript zu laufen vergisst (oder die Markdown-Datei von
  Hand verändert).
"""

import json
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column

from thermoctl.db.models.operations import Setting
from thermoctl.domain import glossary
from thermoctl.domain.glossary import (
    AREAS,
    MAX_EXPLANATION_LENGTH,
    MAX_SHORT_LENGTH,
    Glossary,
    GlossaryError,
    default_glossary,
    load_glossary,
    parse_glossary,
    render_markdown,
)
from thermoctl.domain.solar_setback import SUNSHINE_THRESHOLD_W_M2
from thermoctl.domain.temperature_source_health import ECHO_INDEPENDENCE_DELAY

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "thermoctl" / "web" / "templates"


def _entry(entry_id: str = "alpha", **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": entry_id,
        "term": entry_id.capitalize(),
        "short": "Kurz.",
        "explanation": "Lang.",
        "area": "Regelung",
    }
    base.update(changes)
    return base


def _data(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "entries": list(entries)}


# --------------------------------------------------------------------------------------
# Loader: der gültige Weg
# --------------------------------------------------------------------------------------


def test_a_minimal_glossary_parses_with_defaults_for_the_optional_fields() -> None:
    parsed = parse_glossary(_data(_entry()))
    (entry,) = parsed.entries
    assert (entry.id, entry.term, entry.area) == ("alpha", "Alpha", "Regelung")
    assert entry.synonyms == ()
    assert entry.see_also == ()


def test_fields_are_stripped_of_surrounding_whitespace() -> None:
    parsed = parse_glossary(_data(_entry(term="  Alpha  ", synonyms=["  a  "])))
    assert parsed.entries[0].term == "Alpha"
    assert parsed.entries[0].synonyms == ("a",)


def test_entries_are_sorted_the_german_way_and_umlauts_count_as_their_base_letter() -> None:
    """ä, ö, ü, ß ordnen sich ein, statt hinter Z zu landen; die Klammer zählt nicht."""
    parsed = parse_glossary(
        _data(
            _entry("zeta", term="Zeta"),
            _entry("uebel", term="Übel"),
            _entry("ofen", term="Ofen"),
            _entry("ass", term="Äpfel"),
            _entry("strasse", term="Straße"),
            _entry("klammer", term="(Beta) Klammer"),
            _entry("anfang", term="Anfang"),
        )
    )
    assert [entry.term for entry in parsed.entries] == [
        "Anfang",
        "Äpfel",
        "(Beta) Klammer",
        "Ofen",
        "Straße",
        "Übel",
        "Zeta",
    ]
    assert parsed.letters() == ("A", "B", "O", "S", "U", "Z")


def test_grouped_returns_each_letter_with_its_entries_in_order() -> None:
    parsed = parse_glossary(
        _data(_entry("bb", term="Bb"), _entry("ab", term="Ab"), _entry("aa", term="Aa"))
    )
    assert [(letter, [e.id for e in entries]) for letter, entries in parsed.grouped()] == [
        ("A", ["aa", "ab"]),
        ("B", ["bb"]),
    ]


def test_see_also_may_point_at_an_existing_entry() -> None:
    parsed = parse_glossary(_data(_entry("a", see_also=["b"]), _entry("b")))
    assert parsed.get("a").see_also == ("b",)


def test_get_returns_the_entry_and_an_unknown_id_is_an_error_not_none() -> None:
    parsed = parse_glossary(_data(_entry()))
    assert parsed.get("alpha").term == "Alpha"
    with pytest.raises(KeyError, match="nirgends"):
        parsed.get("nirgends")


def test_search_text_contains_term_synonyms_and_texts_in_lower_case() -> None:
    parsed = parse_glossary(
        _data(_entry(term="Hysterese", synonyms=["Schalthysterese"], short="Spielraum"))
    )
    text = parsed.entries[0].search_text
    assert text == text.casefold()
    for part in ("hysterese", "schalthysterese", "spielraum", "lang."):
        assert part in text


def test_ids_returns_all_ids() -> None:
    parsed = parse_glossary(_data(_entry("a"), _entry("b")))
    assert parsed.ids() == frozenset({"a", "b"})


# --------------------------------------------------------------------------------------
# Loader: die Fehlerwege
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ([], "Objekt"),
        ({"entries": [_entry()]}, "Version"),
        ({"version": 2, "entries": [_entry()]}, "Version"),
        # In Python ist True == 1 und 1.0 == 1; in JSON sind es aber weder die Zahl 1
        # noch eine Version. Der Vergleich allein ließe beides als Version 1 durch.
        ({"version": True, "entries": [_entry()]}, "Version"),
        ({"version": 1.0, "entries": [_entry()]}, "Version"),
        ({"version": 1}, "nicht leere Liste"),
        ({"version": 1, "entries": []}, "nicht leere Liste"),
        ({"version": 1, "entries": "x"}, "nicht leere Liste"),
        (_data("kein Objekt"), "muss ein Objekt sein"),
    ],
)
def test_structural_errors_are_rejected(data: object, fragment: str) -> None:
    with pytest.raises(GlossaryError, match=fragment):
        parse_glossary(data)


@pytest.mark.parametrize("field", ["id", "term", "short", "explanation", "area"])
def test_every_required_field_must_be_present_and_not_empty(field: str) -> None:
    missing = _entry()
    del missing[field]
    with pytest.raises(GlossaryError, match=f"Pflichtfeld „{field}“"):
        parse_glossary(_data(missing))
    with pytest.raises(GlossaryError, match=f"Pflichtfeld „{field}“"):
        parse_glossary(_data(_entry(**{field: "   "})))
    with pytest.raises(GlossaryError, match=f"Pflichtfeld „{field}“"):
        parse_glossary(_data(_entry(**{field: 7})))


def test_the_error_names_the_offending_entry() -> None:
    with pytest.raises(GlossaryError, match="Eintrag „kaputt“"):
        parse_glossary(_data(_entry("kaputt", short="")))
    with pytest.raises(GlossaryError, match="Eintrag Nr. 1"):
        parse_glossary(_data({"term": "Ohne Kennung"}))


def test_unknown_fields_are_rejected_so_a_typo_does_not_silently_lose_data() -> None:
    with pytest.raises(GlossaryError, match="unbekannte Felder synonyme"):
        parse_glossary(_data(_entry(synonyme=["x"])))


@pytest.mark.parametrize("bad", ["Alpha", "a b", "ä", "a_b", "-a", "a-", "a--b", ""])
def test_an_id_that_is_unfit_as_an_anchor_is_rejected(bad: str) -> None:
    with pytest.raises(GlossaryError):
        parse_glossary(_data(_entry(bad)))


def test_an_unknown_area_is_rejected() -> None:
    with pytest.raises(GlossaryError, match="Bereich „Wetter“ ist unbekannt"):
        parse_glossary(_data(_entry(area="Wetter")))


def test_all_four_areas_are_accepted() -> None:
    for area in AREAS:
        parse_glossary(_data(_entry(area=area)))
    assert AREAS == ("Regelung", "Sensorik", "Betrieb", "Oberfläche")


def test_short_and_explanation_have_an_upper_bound() -> None:
    parse_glossary(_data(_entry(short="x" * MAX_SHORT_LENGTH)))
    with pytest.raises(GlossaryError, match="zu lang"):
        parse_glossary(_data(_entry(short="x" * (MAX_SHORT_LENGTH + 1))))
    parse_glossary(_data(_entry(explanation="x" * MAX_EXPLANATION_LENGTH)))
    with pytest.raises(GlossaryError, match="zu lang"):
        parse_glossary(_data(_entry(explanation="x" * (MAX_EXPLANATION_LENGTH + 1))))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(GlossaryError, match="Kennung „alpha“ kommt mehrfach vor"):
        parse_glossary(_data(_entry("alpha"), _entry("alpha", term="Anders")))


def test_a_term_may_not_occur_twice_regardless_of_case() -> None:
    with pytest.raises(GlossaryError, match="auch bei „alpha“"):
        parse_glossary(_data(_entry("alpha", term="Eins"), _entry("beta", term="EINS")))


def test_a_synonym_may_not_equal_another_entrys_term_or_synonym() -> None:
    with pytest.raises(GlossaryError, match="auch bei „alpha“"):
        parse_glossary(_data(_entry("alpha"), _entry("beta", synonyms=["alpha"])))
    with pytest.raises(GlossaryError, match="auch bei „alpha“"):
        parse_glossary(
            _data(_entry("alpha", synonyms=["Gleich"]), _entry("beta", synonyms=["gleich"]))
        )


def test_a_synonym_may_not_repeat_the_entrys_own_term_or_itself() -> None:
    with pytest.raises(GlossaryError, match="doppelt"):
        parse_glossary(_data(_entry("alpha", synonyms=["Alpha"])))
    with pytest.raises(GlossaryError, match="doppelt"):
        parse_glossary(_data(_entry("alpha", synonyms=["x", "X"])))


@pytest.mark.parametrize("bad", ["text", [""], [3], ["  "]])
def test_synonyms_and_see_also_must_be_lists_of_non_empty_texts(bad: object) -> None:
    with pytest.raises(GlossaryError):
        parse_glossary(_data(_entry(synonyms=bad)))
    with pytest.raises(GlossaryError):
        parse_glossary(_data(_entry(see_also=bad)))


def test_see_also_must_point_at_an_existing_entry() -> None:
    with pytest.raises(GlossaryError, match="verweist auf „fehlt“, den es nicht gibt"):
        parse_glossary(_data(_entry("alpha", see_also=["fehlt"])))


def test_see_also_may_not_point_at_itself() -> None:
    with pytest.raises(GlossaryError, match="auf sich selbst"):
        parse_glossary(_data(_entry("alpha", see_also=["alpha"])))


def test_see_also_may_not_list_a_target_twice() -> None:
    with pytest.raises(GlossaryError, match="mehrfach"):
        parse_glossary(_data(_entry("a", see_also=["b", "b"]), _entry("b")))


def test_parsing_does_not_modify_its_input() -> None:
    data = _data(_entry("a", see_also=["b"]), _entry("b"))
    before = deepcopy(data)
    parse_glossary(data)
    assert data == before


# --------------------------------------------------------------------------------------
# Laden aus Datei
# --------------------------------------------------------------------------------------


def test_load_glossary_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(json.dumps(_data(_entry())), encoding="utf-8")
    assert load_glossary(path).get("alpha").term == "Alpha"


def test_load_glossary_reports_invalid_json_as_a_glossary_error(tmp_path: Path) -> None:
    path = tmp_path / "kaputt.json"
    path.write_text("{ nicht json", encoding="utf-8")
    with pytest.raises(GlossaryError, match="kein gültiges JSON"):
        load_glossary(path)


def test_load_glossary_validates_what_it_reads(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(json.dumps(_data(_entry(area="Wetter"))), encoding="utf-8")
    with pytest.raises(GlossaryError, match="Bereich"):
        load_glossary(path)


# --------------------------------------------------------------------------------------
# Das ausgelieferte Glossar
# --------------------------------------------------------------------------------------

#: Begriffe, die die Oberfläche ohne Erklärung nicht verständlich macht. Fehlt einer,
#: ist das Glossar unvollständig -- die Liste kommt aus der Bestandsaufnahme der Vorlagen.
REQUIRED_IDS = {
    "hysterese",
    "pi-regelung",
    "mindestschaltdauer",
    "sonnenabsenkung",
    "ersatzquelle",
    "notbetrieb",
    "rueckkehrpruefung",
    "ausgleichswert",
    "festtakt",
    "aussenkennlinie",
    "ventilschutz",
    "frostschutz",
    "schattenbetrieb",
    "trockenlauf",
    "echo-regel",
    "scharfschalten",
    "uebersteuerung",
    "urlaub",
    "abwesenheit",
    "fensterkontakt",
    "fenster-alarm",
    "festhaengender-messwert",
    "sensor-timeout",
    "relaisverschleiss",
}


def test_the_shipped_glossary_loads_and_is_cached() -> None:
    first = default_glossary()
    assert isinstance(first, Glossary)
    assert default_glossary() is first


def test_the_shipped_glossary_contains_every_term_the_interface_needs() -> None:
    missing = REQUIRED_IDS - default_glossary().ids()
    assert not missing, f"Im Glossar fehlen: {sorted(missing)}"


def test_the_shipped_glossary_uses_every_area() -> None:
    used = {entry.area for entry in default_glossary().entries}
    assert used == set(AREAS)


def test_the_shipped_glossary_file_is_valid_json_with_utf8_umlauts() -> None:
    """Die Datei steht im Klartext mit Umlauten, nicht als \\uXXXX -- sie soll sich von
    Hand pflegen und im Diff lesen lassen."""
    raw = (ROOT / "thermoctl" / "data" / "glossar.json").read_text(encoding="utf-8")
    assert "\\u" not in raw
    assert "ä" in raw


def test_no_entry_leaks_a_real_plant_name_or_a_secret() -> None:
    """Grundsatz 1 und 2 für die Texte: keine Raum-, Geräte- oder Zugangsdaten."""
    text = (ROOT / "thermoctl" / "data" / "glossar.json").read_text(encoding="utf-8").casefold()
    for forbidden in ("passwort:", "password=", "secret=", "192.168.", "10.0.", "@gmail"):
        assert forbidden not in text


# --- Die Texte nennen Werte aus dem Code; ändert sich der Code, muss das hier auffallen --


def _column_default(name: str) -> Any:
    column = Setting.__table__.c[name]
    assert isinstance(column, Column)
    assert column.default is not None
    return column.default.arg


def _german(value: Decimal | int) -> str:
    """Eine Zahl so geschrieben wie im Glossar: ohne Nachkomma-Nullen, mit Komma."""
    return f"{Decimal(value).normalize():f}".replace(".", ",")


def test_the_sun_threshold_in_the_text_is_the_one_the_code_uses() -> None:
    text = default_glossary().get("sonnenabsenkung").explanation
    assert f"{SUNSHINE_THRESHOLD_W_M2} W/m²" in text
    assert "mindestens" in text, "Die Schwelle gilt inklusive (>=), nicht 'über'"


def test_the_sun_setback_defaults_in_the_text_are_the_factory_defaults() -> None:
    text = default_glossary().get("sonnenabsenkung").explanation
    hours = _column_default("solar_setback_lookahead_hours")
    maximum = _column_default("default_solar_setback_max_k")
    assert f"ab Werk {hours})" in text
    assert f"(ab Werk {_german(maximum)} K)" in text


def test_the_hysteresis_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("hysterese").explanation
    assert f"ab Werk {_german(_column_default('default_hysteresis_k'))} K" in text


def test_the_minimum_duration_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("mindestschaltdauer").explanation
    on = _column_default("default_min_on_seconds")
    off = _column_default("default_min_off_seconds")
    assert on == off, "Der Text sagt 'je 5 Minuten'; bei ungleichen Vorgaben falsch"
    assert f"je {on // 60} Minuten" in text


def test_the_sensor_timeout_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("sensor-timeout").explanation
    assert f"Ab Werk {_column_default('default_sensor_timeout_seconds') // 60} Minuten" in text


def test_the_control_cycle_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("regelzyklus").explanation
    assert f"ab Werk {_column_default('shadow_interval_seconds')} Sekunden" in text


def test_the_stuck_reading_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("festhaengender-messwert").explanation
    assert f"ab Werk {_column_default('stuck_reading_hours')})" in text


def test_the_resume_delay_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("fensterkontakt").explanation
    assert f"ab Werk {_column_default('default_window_resume_delay_seconds')} Sekunden" in text


def test_the_takeover_cycles_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("aktiv-bereitschafts-verbund").explanation
    assert f"ab Werk {_column_default('cluster_takeover_cycles')})" in text


def test_the_relay_lifetime_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("relaisverschleiss").explanation
    assumed = _column_default("assumed_relay_lifetime_operations")
    assert f"ab Werk {assumed:,}".replace(",", ".") in text


def test_the_shadow_retention_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("schattenbetrieb").explanation
    assert f"ab Werk {_column_default('shadow_decision_retention_days')} Tage" in text


def test_the_notsollwert_default_in_the_text_is_the_factory_default() -> None:
    text = default_glossary().get("notsollwert").explanation
    default = _column_default("sensor_failure_default_emergency_setpoint_c")
    assert f"ab Werk {_german(default)} °C" in text


def test_the_echo_delay_in_the_text_is_the_one_the_code_uses() -> None:
    text = default_glossary().get("echo-regel").explanation
    assert f"{int(ECHO_INDEPENDENCE_DELAY.total_seconds() // 60)} Minuten" in text


# --------------------------------------------------------------------------------------
# Verweise aus der Oberfläche
# --------------------------------------------------------------------------------------

_HELP_CALL = re.compile(r"glossar_hilfe\(\s*[\"']([^\"']+)[\"']\s*\)")
_GLOSSARY_HREF = re.compile(r"/glossar#([a-z0-9-]+)")


def _template_references() -> dict[str, set[str]]:
    """Template-Datei -> Kennungen, auf die sie per Hilfe-Symbol oder Anker verweist."""
    found: dict[str, set[str]] = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        ids = set(_HELP_CALL.findall(text)) | set(_GLOSSARY_HREF.findall(text))
        if ids:
            found[path.name] = ids
    return found


def test_every_anchor_referenced_from_the_interface_hits_an_existing_entry() -> None:
    references = _template_references()
    known = default_glossary().ids()
    dead = {
        template: sorted(ids - known) for template, ids in references.items() if ids - known
    }
    assert not dead, f"Verweise auf Glossareinträge, die es nicht gibt: {dead}"


def test_the_scanner_finds_the_help_icons_so_the_test_above_is_not_blind() -> None:
    """Zwischen fünf und acht Stellen sind vorgesehen (Auftrag); weniger hieße, dass
    der Scanner nichts mehr erkennt."""
    references = _template_references()
    places = sum(
        len(_HELP_CALL.findall((TEMPLATES / name).read_text("utf-8"))) for name in references
    )
    assert places >= 5, f"nur {places} Hilfe-Symbole gefunden: {references}"
    assert {"parameter.html", "settings.html", "control.html"} <= set(references)


def test_the_help_function_rejects_an_unknown_id_instead_of_linking_into_the_void() -> None:
    from thermoctl.web import glossary_help

    with pytest.raises(KeyError, match="gibt-es-nicht"):
        glossary_help({"url_prefix": ""}, "gibt-es-nicht")  # type: ignore[arg-type]


def test_the_help_function_builds_an_accessible_link_that_honours_the_url_prefix() -> None:
    from thermoctl.web import glossary_help

    html = glossary_help({"url_prefix": "/api/hassio_ingress/abc"}, "hysterese")  # type: ignore[arg-type]
    assert 'href="/api/hassio_ingress/abc/glossar#hysterese"' in html
    assert 'aria-label="Erklärung zu Hysterese im Glossar (öffnet in neuem Tab)"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_the_help_function_works_without_a_prefix() -> None:
    from thermoctl.web import glossary_help

    html = glossary_help({}, "pi-regelung")  # type: ignore[arg-type]
    assert 'href="/glossar#pi-regelung"' in html
    assert "PI-Regelung (Beta)" in html


def test_the_help_function_escapes_the_term(monkeypatch: pytest.MonkeyPatch) -> None:
    import thermoctl.web as web

    hostile = parse_glossary(_data(_entry("feind", term="<b>x</b>")))
    monkeypatch.setattr(web, "default_glossary", lambda: hostile)
    html = web.glossary_help({"url_prefix": ""}, "feind")  # type: ignore[arg-type]
    assert "<b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


# --------------------------------------------------------------------------------------
# Docs-Fassung
# --------------------------------------------------------------------------------------


def test_the_markdown_is_deterministic_and_ends_with_exactly_one_newline() -> None:
    first = render_markdown(default_glossary())
    assert first == render_markdown(default_glossary())
    assert first.endswith("\n") and not first.endswith("\n\n")


def test_the_markdown_for_a_small_glossary_has_the_documented_shape() -> None:
    small = parse_glossary(
        _data(
            _entry("ab", term="Ab", synonyms=["Abc"], see_also=["zz"], area="Betrieb"),
            _entry("zz", term="Zz"),
        )
    )
    text = render_markdown(small)
    assert text.startswith("# Glossar\n")
    assert glossary.MARKDOWN_NOTICE in text
    assert "[A](#a) · [Z](#z)" in text
    assert '<a id="ab"></a>\n### Ab\n\n*Betrieb* — Kurz.\n\nLang.\n\nAuch: Abc\n\n' in text
    assert "Siehe auch: [Zz](#zz)\n" in text
    assert text.index("## A") < text.index("### Ab") < text.index("## Z") < text.index("### Zz")


def test_the_markdown_has_one_anchor_and_one_heading_per_entry() -> None:
    text = render_markdown(default_glossary())
    for entry in default_glossary().entries:
        assert text.count(f'<a id="{entry.id}"></a>') == 1
        assert f"### {entry.term}\n" in text


def test_every_markdown_link_resolves_to_an_anchor_in_the_same_file() -> None:
    text = render_markdown(default_glossary())
    entry_anchors = set(re.findall(r'<a id="([^"]+)"></a>', text))
    # GitHub leitet die Kennung einer Überschrift "## A" selbst ab: kleingeschrieben.
    letter_anchors = {letter.lower() for letter in re.findall(r"^## (\w)$", text, re.MULTILINE)}
    targets = re.findall(r"\]\(#([^)]+)\)", text)
    assert len(targets) > len(default_glossary().letters()), "Es gibt kaum Verweise zu prüfen"
    for target in targets:
        assert target in entry_anchors | letter_anchors, target


def test_docs_glossar_md_matches_the_source() -> None:
    """Schlägt fehl, wenn glossar.json geändert wurde und docs/glossar.md nicht (oder
    umgekehrt). Abhilfe: `python -m tools.glossar_erzeugen`."""
    on_disk = (ROOT / "docs" / "glossar.md").read_text(encoding="utf-8")
    assert on_disk == render_markdown(default_glossary()), (
        "docs/glossar.md passt nicht zu thermoctl/data/glossar.json. "
        "Neu erzeugen: python -m tools.glossar_erzeugen"
    )


@pytest.mark.parametrize("document", ["README.md", "docs/bedienung.md"])
def test_the_glossary_is_linked_from_readme_and_the_user_guide(document: str) -> None:
    text = (ROOT / document).read_text(encoding="utf-8")
    assert "glossar.md" in text, f"{document} verweist nicht auf das Glossar"


# --------------------------------------------------------------------------------------
# Das Erzeugungsskript
# --------------------------------------------------------------------------------------


def _tool() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "glossar_erzeugen", ROOT / "tools" / "glossar_erzeugen.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_check_passes_for_the_committed_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert _tool().main(["--pruefen"]) == 0
    assert "aktuell" in capsys.readouterr().out


def test_the_tool_writes_the_file_and_then_reports_it_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _tool()
    target = tmp_path / "glossar.md"
    monkeypatch.setattr(tool, "ZIEL", target)
    assert tool.main([]) == 0
    assert target.read_text(encoding="utf-8") == render_markdown(default_glossary())
    assert "geschrieben" in capsys.readouterr().out
    assert tool.main([]) == 0
    assert "bereits aktuell" in capsys.readouterr().out


def test_the_tool_check_fails_for_a_stale_or_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _tool()
    target = tmp_path / "glossar.md"
    monkeypatch.setattr(tool, "ZIEL", target)
    assert tool.main(["--pruefen"]) == 1, "fehlende Datei"
    target.write_text("veraltet\n", encoding="utf-8")
    assert tool.main(["--pruefen"]) == 1, "veraltete Datei"
    assert "nicht aktuell" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "veraltet\n", "--pruefen darf nichts schreiben"


def test_the_tool_reports_a_broken_glossary_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _tool()
    target = tmp_path / "glossar.md"
    monkeypatch.setattr(tool, "ZIEL", target)

    def broken() -> Glossary:
        raise GlossaryError("kaputt")

    monkeypatch.setattr(tool, "default_glossary", broken)
    assert tool.main([]) == 2
    assert "kaputt" in capsys.readouterr().err
    assert not target.exists()
