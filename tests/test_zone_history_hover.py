"""Verlauf: Daten für die Anzeige am Mauszeiger und die Seite „Verläufe aller Zonen".

Grundsatz wie beim Diagramm selbst: Die Daten enthalten nur, was protokollierte Zeilen belegen.
Wo keine Zeile liegt, liegt auch kein Eintrag -- die Anzeige sagt dort "keine Daten".
"""

import html as html_module
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_shadow_decision, create_zone
from thermoctl.domain import zone_history_chart as chart_module
from thermoctl.domain.zone_history_chart import Sample, build_chart
from thermoctl.web import templates
from thermoctl.web.navigation import NAVIGATION_ITEMS

BERLIN = "Europe/Berlin"


def _sample(
    at: datetime,
    temperature: float | None = 19.5,
    effective: float | None = 20.5,
    scheduled: float | None = 20.5,
    heat: bool = False,
    solar_k: float | None = 0.0,
) -> Sample:
    return Sample(at, temperature, effective, scheduled, heat, solar_k)


def _payload(
    samples: list[Sample], start: datetime, end: datetime, tz: str | None = BERLIN
) -> dict:
    chart = build_chart(samples, start, end, tz)
    return chart_module.hover_payload(chart, tz)  # type: ignore[return-value]


def _cells(payload: dict) -> list[tuple[int, int, int, int, int, int, int]]:
    """Dieselbe Auflösung wie `parse` in zone_history.js: absolute Sekunden je Stück."""
    flat = payload["c"]
    cells = []
    at = 0
    for i in range(0, len(flat), 7):
        first = at + flat[i]
        end = first + flat[i + 1]
        at = end
        mean = flat[i + 3]
        cells.append(
            (first, end, flat[i + 2], mean, mean + flat[i + 4], mean + flat[i + 5], flat[i + 6])
        )
    return cells


def _covered(payload: dict) -> list[tuple[int, int]]:
    """Zeitspannen, für die es Zeilen gibt: aneinanderstoßende Stücke zusammengefasst."""
    spans: list[tuple[int, int]] = []
    for first, end, *_ in _cells(payload):
        if spans and spans[-1][1] == first:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((first, end))
    return spans


def _runs(entries: list[list]) -> list[list]:
    """Läufe wie in der Anzeige ausgebreitet: ein Eintrag je Stück."""
    spread: list[list] = []
    for run in entries:
        spread += [run] * run[0]
    return spread


# --- Datenformat ---------------------------------------------------------------------


def test_a_gap_leaves_no_cell_and_so_no_value_to_show() -> None:
    start = datetime(2026, 10, 9, 4)
    before = [_sample(start + timedelta(minutes=i)) for i in range(5)]
    after = [_sample(start + timedelta(minutes=40 + i)) for i in range(5)]
    payload = _payload(before + after, start, start + timedelta(hours=24))
    # Zwei Spannen, dazwischen 36 Minuten, in denen es keinen Eintrag gibt: dort zeigt die
    # Anzeige "keine Daten", sie behauptet weder den letzten noch einen mittleren Wert. Eine
    # Spanne endet 90 Sekunden nach ihrer letzten Zeile (Zyklus), nicht erst bei der nächsten.
    assert _covered(payload) == [(0, 4 * 60 + 90), (40 * 60, 44 * 60 + 90)]


def test_the_time_after_the_last_row_belongs_to_no_cell() -> None:
    start = datetime(2026, 10, 9, 4)
    samples = [_sample(start + timedelta(minutes=i)) for i in range(10)]
    payload = _payload(samples, start, start + timedelta(hours=24))
    ((first, end),) = _covered(payload)
    assert first == 0 and end == 9 * 60 + 90
    assert payload["e"] == 24 * 3600
    assert end < payload["e"]


def test_rows_from_before_the_recording_are_unknown_not_zero() -> None:
    start = datetime(2026, 10, 9, 4)
    old = [_sample(start + timedelta(minutes=i), scheduled=None, solar_k=None) for i in range(3)]
    new = [_sample(start + timedelta(minutes=3 + i), solar_k=2.0, effective=18.5) for i in range(3)]
    payload = _payload(old + new, start, start + timedelta(hours=24))
    scheduled = _runs(payload["sc"])
    solar = _runs(payload["so"])
    # Das erste Stück (nur Altzeilen) kennt weder den Sollwert vor der Absenkung noch die
    # Absenkung -- weder
    # "gleich dem wirksamen Soll" noch "keine Absenkung".
    assert scheduled[0][1] is None and solar[0][1] is None
    # Die Stücke danach tragen belegte Werte: Sollwert vor der Absenkung 20,5 und Absenkung 2,0 K.
    assert scheduled[-1][1:3] == [205, 205] and solar[-1][1:3] == [20, 20]
    assert all(run[1] is not None for run in solar[1:])


def test_a_cell_with_only_old_rows_is_marked_unknown_everywhere() -> None:
    start = datetime(2026, 10, 9, 4)
    old = [_sample(start + timedelta(minutes=i), scheduled=None, solar_k=None) for i in range(3)]
    payload = _payload(old, start, start + timedelta(hours=24))
    assert _runs(payload["sc"]) == [[1, None, None, 1]]
    assert _runs(payload["so"]) == [[1, None, None, 1]]
    assert _runs(payload["ef"]) == [[1, 205, 205, 0]]


def test_a_dense_period_gives_mean_minimum_and_maximum_per_section() -> None:
    start = datetime(2026, 10, 2, 4)
    temperatures = [19.0, 20.0, 21.0, 20.0]
    samples = [
        _sample(start + timedelta(minutes=i), temperature=t) for i, t in enumerate(temperatures)
    ]
    payload = _payload(samples, start, start + timedelta(days=7))
    assert payload["b"] == "17 Minuten"
    ((first, end, count, mean, low, high, heat),) = _cells(payload)
    assert (count, mean, low, high, heat) == (4, 200, 190, 210, 0)
    assert first == 0 and end == 3 * 60 + 90


def test_the_heat_share_is_weighted_by_seconds_and_never_rounds_to_all_or_nothing() -> None:
    start = datetime(2026, 10, 9, 4)
    # eine Sekunde Anforderung vor neunzig Sekunden ohne: ein Anteil, nicht "nein"
    samples = [_sample(start, heat=True), _sample(start + timedelta(seconds=1), heat=False)]
    payload = _payload(
        samples + [_sample(start + timedelta(seconds=91))], start, start + timedelta(hours=1)
    )
    assert _cells(payload)[0][6] == 1
    always = _payload(
        [_sample(start + timedelta(minutes=i), heat=True) for i in range(3)],
        start,
        start + timedelta(hours=1),
    )
    never = _payload(
        [_sample(start + timedelta(minutes=i)) for i in range(3)], start, start + timedelta(hours=1)
    )
    assert _cells(always)[0][6] == 100 and _cells(never)[0][6] == 0


def test_a_row_without_temperature_still_covers_time_but_shows_no_measurement() -> None:
    start = datetime(2026, 10, 9, 4)
    payload = _payload([_sample(start, temperature=None)], start, start + timedelta(hours=24))
    ((first, end, count, mean, low, high, _heat),) = _cells(payload)
    assert (count, mean, low, high) == (0, 0, 0, 0)
    assert end > first


def test_the_payload_carries_the_start_as_utc_and_the_configured_zone_across_a_clock_change() -> (
    None
):
    start = datetime(2026, 10, 24, 20)  # UTC; die Umstellung auf Winterzeit liegt im Fenster
    end = start + timedelta(hours=24)
    payload = _payload([_sample(start + timedelta(hours=3))], start, end)
    assert payload["tz"] == BERLIN
    assert payload["s"] == int(start.replace(tzinfo=UTC).timestamp())
    assert payload["e"] == 24 * 3600
    assert _payload([_sample(start)], start, end, None)["tz"] == "UTC"


def test_the_payload_stays_compact_for_a_busy_week() -> None:
    start = datetime(2026, 10, 2, 4)
    samples = [
        _sample(start + timedelta(seconds=60 * i), temperature=20 + (i % 7) / 10)
        for i in range(60 * 24 * 7)
    ]
    text = json.dumps(_payload(samples, start, start + timedelta(days=7)), separators=(",", ":"))
    assert len(text) < 20_000


def test_the_attribute_form_of_the_json_cannot_leave_its_quotes() -> None:
    from thermoctl.web.zone_views import _attribute_json

    nasty = {"b": "x' onmouseover='alert(1)", "tz": "a&b<c>d", "c": [1, 2]}
    text = str(_attribute_json(nasty))
    for char in "'&<>":
        assert char not in text
    assert json.loads(text) == nasty


# --- Seiten --------------------------------------------------------------------------


def _fill(session: Session, zone, minutes: int = 30) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(minutes):
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=minutes - index)
        row.temperature_c = Decimal("19.5")
        row.setpoint_c = Decimal("18.5")
        row.scheduled_setpoint_c = Decimal("20.5")
        row.solar_setback_k = Decimal("2.0")
    session.flush()


def _tile_ids(html: str) -> list[int]:
    return [int(value) for value in re.findall(r'id="zone-history-(\d+)"', html)]


def test_a_card_carries_parseable_hover_data_and_the_drawing_sizes(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "hoverkarte")
    _fill(session, zone)
    page = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history").text
    match = re.search(r"data-history-data='([^']*)'", page)
    assert match is not None
    payload = json.loads(html_module.unescape(match.group(1)))
    assert payload["v"] == 1 and payload["e"] == 24 * 3600 and _cells(payload)
    assert page.count("data-left=") == 4  # eine Maßangabe je Zeichnung
    assert "data-history-plot" in page
    # Nur eine Reihe Daten, nicht je Zeichnung: vier Zeichnungen, ein Datensatz.
    assert page.count("data-history-data=") == 1
    assert "<script" not in page


def test_a_card_without_data_has_no_hover_data(client_als, session: Session) -> None:
    zone = create_zone(session, "leerkarte")
    page = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history").text
    assert "data-history-data" not in page and "Keine Verlaufsdaten" in page


def test_the_card_on_the_zone_page_keeps_its_ids_and_the_tile_gets_its_own(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "kennungen")
    _fill(session, zone)
    user = client_als([("zone.manage", zone.id)])
    card = user.get(f"/zones/{zone.id}/history").text
    tile = user.get(f"/zones/{zone.id}/history?tile=1").text
    assert 'id="zone-history"' in card and 'aria-describedby="zone-history-summary"' in card
    assert f'id="zone-history-{zone.id}"' in tile
    assert f'aria-describedby="zone-history-{zone.id}-summary"' in tile
    assert f'id="zone-history-{zone.id}-summary"' in tile
    assert f'id="zone-history-{zone.id}-hatch-desktop"' not in tile  # nur bei unbekannter Absenkung
    # Die Kachel hat keine eigene Zeitraumwahl (die steht auf der Seite) und verlinkt die Zone.
    assert 'aria-label="Zeitraum"' not in tile
    assert f'<a href="/zones/{zone.id}">' in tile
    assert 'aria-label="Zeitraum"' in card


def test_hatch_patterns_get_one_id_per_tile(client_als, session: Session) -> None:
    zone = create_zone(session, "schraffur")
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(5):
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=5 - index)
        row.temperature_c = Decimal("19.5")
        row.setpoint_c = Decimal("20.5")  # Altzeile: weder Sollwert vor Absenkung noch Absenkung
    session.flush()
    tile = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history?tile=1").text
    assert f'id="zone-history-{zone.id}-hatch-desktop"' in tile
    assert f"url(#zone-history-{zone.id}-hatch-desktop)" in tile


def test_overview_lists_only_the_zones_each_user_may_manage(
    client_als, client: TestClient, session: Session
) -> None:
    mine = create_zone(session, "meinraum")
    theirs = create_zone(session, "fremderraum")
    first = client_als([("zone.manage", mine.id)]).get("/zones/history")
    assert first.status_code == 200
    assert _tile_ids(first.text) == [mine.id]
    assert "Fremderraum" not in first.text and "fremderraum" not in first.text
    second = client_als([("zone.manage", theirs.id)]).get("/zones/history")
    assert _tile_ids(second.text) == [theirs.id]
    assert "Meinraum" not in second.text
    both = client_als([("zone.manage", None)]).get("/zones/history")
    assert sorted(_tile_ids(both.text)) == sorted([mine.id, theirs.id])
    # Die Seite trägt keine Messdaten: die holt jede Kachel einzeln, und dort prüft die
    # Einzelkarte die Zone noch einmal.
    assert "data-history-data" not in first.text
    assert f'hx-get="/zones/{mine.id}/history?period=24h&amp;tile=1"' in first.text.replace(
        "&tile=1", "&amp;tile=1"
    )
    assert 'hx-trigger="revealed"' in first.text


def test_overview_answers_like_the_zone_list_without_the_right(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "nurlesen")
    user = client_als([("zone.read", zone.id)])
    overview = user.get("/zones/history")
    listing = user.get("/zones")
    assert overview.status_code == listing.status_code == 200
    assert _tile_ids(overview.text) == []
    assert "Noch keine sichtbaren Zonen vorhanden." in overview.text
    assert "Nurlesen" not in overview.text
    # Und die Verknüpfung dorthin steht dann auch nicht auf der Zonenliste.
    assert "/zones/history" not in listing.text
    nobody = client_als([]).get("/zones/history")
    assert nobody.status_code == 200 and _tile_ids(nobody.text) == []


def test_overview_tile_of_a_foreign_zone_is_refused_even_when_asked_for_directly(
    client_als, session: Session
) -> None:
    mine = create_zone(session, "kachelmein")
    theirs = create_zone(session, "kachelfremd")
    user = client_als([("zone.manage", mine.id)])
    assert user.get(f"/zones/{mine.id}/history?tile=1").status_code == 200
    refused = user.get(f"/zones/{theirs.id}/history?tile=1")
    assert refused.status_code == 404
    assert "kachelfremd" not in refused.text.lower()
    assert user.get(f"/zones/{theirs.id}/history?tile=1&period=ungültig").status_code == 404


def test_overview_is_closed_to_tenants_and_anonymous_visitors(
    client_als, tenant_client, client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "mieterraum")
    anonymous = client.get("/zones/history", follow_redirects=False)
    assert anonymous.status_code in (303, 401) and "Mieterraum" not in anonymous.text
    tenant = tenant_client([("zone.manage", zone.id)])
    assert tenant.get("/zones/history").status_code == 403
    assert tenant.get(f"/zones/{zone.id}/history?tile=1").status_code == 403
    assert "Mieterraum" not in tenant.get("/zones/history").text


def test_overview_rejects_an_unknown_period_and_passes_the_valid_one_to_every_tile(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "zeitraeume")
    user = client_als([("zone.manage", zone.id)])
    for bad in ("ungültig", "", "24H", "1d", "x" * 5000, "../etc"):
        assert user.get("/zones/history", params={"period": bad}).status_code == 422, bad
    for good in ("24h", "3d", "7d"):
        page = user.get("/zones/history", params={"period": good}).text
        chosen = re.findall(r'btn btn-primary"\s+hx-get="[^"]*period=(\w+)"', page)
        assert chosen == [good] and f"period={good}&" in page
    assert user.get("/zones/history").status_code == 200  # Vorgabe: 24 Stunden


def test_overview_is_a_page_of_its_own_and_not_mistaken_for_a_zone(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "keinzoneid")
    page = client_als([("zone.manage", zone.id)]).get("/zones/history")
    assert page.status_code == 200 and "<h1" in page.text and "Verläufe aller Zonen" in page.text
    assert "<title>Verläufe aller Zonen" in page.text


def test_navigation_and_the_zone_list_point_to_the_overview(client_als, session: Session) -> None:
    zone = create_zone(session, "wegweiser")
    user = client_als([("zone.manage", zone.id)])
    nav = re.search(r'<nav class="tc-sidenav.*?</nav>', user.get("/").text, re.S)
    assert nav is not None and 'href="/zones/history"' in nav.group(0)
    assert 'href="/zones/history"' in user.get("/zones").text
    # Unter der Übersicht ist nur ihr eigener Eintrag die aktuelle Seite, nicht zugleich "Zonen".
    here = re.search(r'<nav class="tc-sidenav.*?</nav>', user.get("/zones/history").text, re.S)
    assert here is not None
    assert here.group(0).count('aria-current="page"') == 1
    assert re.search(r'href="/zones/history"\s+aria-current="page"', here.group(0))
    nothing = re.search(r'<nav class="tc-sidenav.*?</nav>', client_als([]).get("/").text, re.S)
    assert nothing is not None and "/zones/history" not in nothing.group(0)


def test_the_navigation_entry_is_bound_to_the_right_the_view_checks() -> None:
    (entry,) = [item for item in NAVIGATION_ITEMS if item.path == "/zones/history"]
    assert (entry.permission, entry.scope, entry.section) == ("zone.manage", "any_zone", "analysis")


def test_the_page_body_is_reusable_with_other_addresses_and_another_zone_filter() -> None:
    """Die Nahtstelle für eine spätere Mieter-Route: Körper ohne Rechte und feste Adressen."""

    class Room:
        id = 7
        display_name = "Mieterzimmer"

    body = templates.env.get_template("zones_history_body.html").render(
        zones=[Room()],
        period="3d",
        page_path="/wohnung/verlaeufe",
        tile_path="/wohnung/raeume/{zone_id}/verlauf",
        url_prefix="/ha",
    )
    assert 'hx-get="/ha/wohnung/verlaeufe?period=7d"' in body
    assert 'hx-get="/ha/wohnung/raeume/7/verlauf?period=3d&amp;tile=1"' in body or (
        'hx-get="/ha/wohnung/raeume/7/verlauf?period=3d&tile=1"' in body
    )
    assert "Mieterzimmer" in body and "/zones/" not in body


@pytest.mark.parametrize("name", ["desktop", "desktop-full", "mobile", "mobile-tall"])
def test_every_drawing_carries_its_own_plot_measures(
    client_als, session: Session, name: str
) -> None:
    zone = create_zone(session, f"masse-{name}")
    _fill(session, zone)
    page = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history").text
    layout = next(item for item in chart_module.LAYOUTS if item.name == name)
    expected = f'viewBox="0 0 {layout.width} {layout.height}"'
    svg = re.search(rf"<svg {re.escape(expected)}[^>]*>", page)
    assert svg is not None
    for key, value in (
        ("left", layout.left),
        ("right", layout.right),
        ("top", layout.top),
        ("bottom", layout.bottom),
        ("lane-bottom", layout.lane_bottom),
    ):
        assert f'data-{key}="{value}"' in svg.group(0)
