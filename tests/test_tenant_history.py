"""Der Temperaturverlauf der Wohnungssicht (`/temperature-history`) -- und vor allem: wessen Räume.

Der Kern dieser Datei ist die Zonenisolation (Grundsatz 7). Ein Mieter sieht auf dieser Seite
ausschließlich Kacheln seiner eigenen Räume; ein fremder Raum taucht weder als Kachel, Adresse,
Hover-Datum, Zählung noch als Fehlermeldung auf, und die Kachel-Route beantwortet eine fremde
Zone wie eine nicht vorhandene. Geprüft wird, wie in `test_tenant_views.py`, auf Anzeigename
**und** Id im gesamten HTML.

Der zweite Teil sind die Texte: die Wohnungssicht spricht in Alltagssprache, ohne dass die Zahlen
oder die Zeichnung andere wären als in der Anlagensicht.
"""

import html as html_module
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_settings,
    create_shadow_decision,
    create_zone,
    user_with_permissions,
)
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.db.models.zone import Zone
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.domain.zone_history_chart import (
    DESKTOP,
    TECHNICAL,
    TENANT,
    Sample,
    build_chart,
    hover_payload,
    solar_bands_for,
    unknown_bands_for,
)
from thermoctl.web.navigation import NAVIGATION_ITEMS, visible_navigation

Client = Callable[[list[tuple[str, int | None]]], TestClient]

BERLIN = "Europe/Berlin"


def _room(session: Session, name: str, display: str, temperature: str) -> Zone:
    """Ein Raum mit 30 Minuten Verlauf, dessen Messwert nur ihm gehört."""
    zone = create_zone(session, name)
    zone.display_name = display
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(30):
        row = create_shadow_decision(session, zone)
        row.decided_at = now - timedelta(minutes=30 - index)
        row.temperature_c = Decimal(temperature)
        row.setpoint_c = Decimal("18.5")
        row.scheduled_setpoint_c = Decimal("20.5")
        row.solar_setback_k = Decimal("2.0")
    session.flush()
    return zone


def _wohnungen(session: Session) -> tuple[Zone, Zone, Zone]:
    """Drei Räume mit klar unterscheidbaren Messwerten (19,5 / 23,7 / 16,3 °C)."""
    create_settings(session)
    return (
        _room(session, "mein-wohnzimmer", "Wohnzimmer Nordblick", "19.5"),
        _room(session, "fremdes-schlafzimmer", "Nachbars Schlafzimmer", "23.7"),
        _room(session, "mein-bad", "Bad Seeblick", "16.3"),
    )


def _tile_ids(html: str) -> list[int]:
    return [int(value) for value in re.findall(r'id="zone-history-(\d+)"', html)]


def _hover(html: str) -> dict:
    match = re.search(r"data-history-data='([^']*)'", html)
    assert match is not None
    return json.loads(html_module.unescape(match.group(1)))


# -- Zonenisolation -----------------------------------------------------------------------


def test_each_tenant_sees_tiles_of_exactly_their_own_rooms(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, bath = _wohnungen(session)
    first = tenant_client([("zone.read", mine.id), ("zone.read", bath.id)]).get(
        "/temperature-history"
    )
    assert first.status_code == 200
    assert sorted(_tile_ids(first.text)) == sorted([mine.id, bath.id])
    second = tenant_client([("zone.read", theirs.id)]).get("/temperature-history")
    assert _tile_ids(second.text) == [theirs.id]
    # Der erste Mieter kennt den Raum des zweiten nicht -- weder Name noch Id noch Adresse.
    assert theirs.display_name not in first.text
    assert f"/temperature-history/{theirs.id}/tile" not in first.text
    assert f"zone-history-{theirs.id}" not in first.text
    # Und umgekehrt.
    for room in (mine, bath):
        assert room.display_name not in second.text
        assert f"/temperature-history/{room.id}/tile" not in second.text


def test_the_page_leaks_no_foreign_room_anywhere_in_the_markup(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    for path in ("/temperature-history", f"/temperature-history/{mine.id}/tile"):
        page = client.get(path).text
        assert theirs.display_name not in page
        assert theirs.name not in page
        assert f"zone-history-{theirs.id}" not in page
        assert f"/{theirs.id}/" not in page
        # Keine Zählung wie "2 von 3 Räumen", die die Existenz weiterer Räume verriete.
        assert not re.search(r"\bvon \d+ R", page)


def test_a_tenant_without_any_room_gets_an_empty_page_not_an_error(
    tenant_client: Client, session: Session
) -> None:
    _wohnungen(session)
    page = tenant_client([]).get("/temperature-history")
    assert page.status_code == 200
    assert _tile_ids(page.text) == []
    assert "noch kein Raum freigegeben" in page.text
    assert "Nordblick" not in page.text and "Nachbars" not in page.text


def test_the_tile_of_a_foreign_room_is_not_found_even_when_asked_for_directly(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    assert client.get(f"/temperature-history/{mine.id}/tile").status_code == 200
    refused = client.get(f"/temperature-history/{theirs.id}/tile")
    assert refused.status_code == 404
    for hidden in (theirs.display_name, theirs.name, "23,7", "237"):
        assert hidden not in refused.text
    # Auch mit gültigem, ungültigem und gar keinem Zeitraum: immer 404, nie 422 -- sonst verriete
    # der Unterschied, dass es den Raum gibt.
    for period in ("24h", "7d", "ungültig", "", "x" * 3000):
        answer = client.get(f"/temperature-history/{theirs.id}/tile", params={"period": period})
        assert answer.status_code == 404, period


def test_a_foreign_room_answers_exactly_like_one_that_does_not_exist(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    foreign = client.get(f"/temperature-history/{theirs.id}/tile", params={"period": "kaputt"})
    missing = client.get("/temperature-history/999999/tile", params={"period": "kaputt"})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.text == missing.text


def test_an_unparseable_room_id_is_not_found_like_everywhere_else_in_the_tenant_view(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    for bad in ("keine-zahl", "1.5", "-", "0x1"):
        assert client.get(f"/temperature-history/{bad}/tile").status_code == 404, bad


def test_a_right_for_a_different_permission_does_not_open_the_tile(
    tenant_client: Client, session: Session
) -> None:
    """Der Zonenfilter ist `zone.read` -- wer einen Raum nur bedienen darf, ihn aber nicht
    lesen darf, bekommt weder Kachel noch Platzhalter."""
    mine, _theirs, _bath = _wohnungen(session)
    client = tenant_client([("setpoint.write", mine.id), ("schedule.manage", mine.id)])
    assert _tile_ids(client.get("/temperature-history").text) == []
    assert client.get(f"/temperature-history/{mine.id}/tile").status_code == 404


def test_the_hover_data_of_a_tile_belongs_to_its_own_room_only(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id), ("zone.read", bath.id)])
    own = _hover(client.get(f"/temperature-history/{mine.id}/tile").text)
    other = _hover(client.get(f"/temperature-history/{bath.id}/tile").text)
    # Mittelwert in Zehntelgrad steht an vierter Stelle des ersten Stücks (Format: hover_payload).
    assert own["c"][3] == 195
    assert other["c"][3] == 163
    assert 237 not in own["c"] and 237 not in other["c"]


def test_the_tile_drawing_and_summary_carry_only_the_own_measurements(
    tenant_client: Client, session: Session
) -> None:
    mine, theirs, _bath = _wohnungen(session)
    page = tenant_client([("zone.read", mine.id)]).get(f"/temperature-history/{mine.id}/tile").text
    assert "19,5 °C" in page
    assert "23,7" not in page


# -- Profil und Anmeldung ----------------------------------------------------------------


def test_an_administrator_does_not_reach_the_tenant_history(
    client_als: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    admin = client_als([("zone.read", None), ("zone.manage", None)])
    page = admin.get("/temperature-history")
    assert page.status_code == 403
    tile = admin.get(f"/temperature-history/{mine.id}/tile")
    assert tile.status_code == 403
    assert mine.display_name not in page.text + tile.text
    forged = admin.get(f"/temperature-history/{mine.id}/tile", params={"period": "x"})
    assert forged.status_code == 403


def test_the_plant_history_routes_stay_closed_to_tenants(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id), ("zone.manage", mine.id)])
    assert client.get("/zones/history").status_code == 403
    assert client.get(f"/zones/{mine.id}/history").status_code == 403
    assert client.get(f"/zones/{mine.id}/history?tile=1").status_code == 403
    assert mine.display_name not in client.get("/zones/history").text


def test_without_login_neither_page_nor_tile_shows_anything(
    client: TestClient, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    for path in ("/temperature-history", f"/temperature-history/{mine.id}/tile"):
        answer = client.get(path, follow_redirects=False)
        assert answer.status_code in (303, 401), path
        assert mine.display_name not in answer.text


def test_the_history_page_is_a_page_of_the_tenant_view(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    page = tenant_client([("zone.read", mine.id)]).get("/temperature-history").text
    assert "tc-tbottomnav" in page and "tc-sidenav" not in page
    assert "<h1" in page and "Wie war die Temperatur?" in page
    assert "<title>Verlauf" in page
    # htmx führt Skripte in getauschtem Inhalt aus; die Seite selbst trägt keine.
    assert "<script" not in page.split("<main", 1)[1].split("</main>", 1)[0]


# -- Zeitraum -----------------------------------------------------------------------------


def test_an_unknown_period_is_refused_like_in_the_plant_view(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    for bad in ("ungültig", "", "24H", "1d", "x" * 5000, "../etc"):
        assert client.get("/temperature-history", params={"period": bad}).status_code == 422, bad
        answer = client.get(f"/temperature-history/{mine.id}/tile", params={"period": bad})
        assert answer.status_code == 422, bad


def test_the_chosen_period_is_marked_and_handed_to_every_tile(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id), ("zone.read", bath.id)])
    assert client.get("/temperature-history").status_code == 200  # Vorgabe: 24 Stunden
    for good, label in (("24h", "24 Stunden"), ("3d", "3 Tage"), ("7d", "7 Tage")):
        page = client.get("/temperature-history", params={"period": good}).text
        current = re.findall(r'class="tc-segment tc-here"[^>]*>([^<]*)<', page)
        assert current == [label]
        assert page.count(f"period={good}&amp;tile=1") + page.count(f"period={good}&tile=1") == 2
    # Ohne JavaScript bleibt jeder Zeitraum ein gewöhnlicher Verweis.
    assert 'href="/temperature-history?period=7d"' in client.get("/temperature-history").text


def test_a_period_window_changes_the_tile(tenant_client: Client, session: Session) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    client = tenant_client([("zone.read", mine.id)])
    day = client.get(f"/temperature-history/{mine.id}/tile", params={"period": "24h"}).text
    week = client.get(f"/temperature-history/{mine.id}/tile", params={"period": "7d"}).text
    assert _hover(day)["e"] == 24 * 3600
    assert _hover(week)["e"] == 7 * 24 * 3600


# -- Navigation ---------------------------------------------------------------------------


def test_the_navigation_entry_is_bound_to_the_right_the_view_checks() -> None:
    (entry,) = [item for item in NAVIGATION_ITEMS if item.path == "/temperature-history"]
    assert (entry.permission, entry.scope, entry.section) == ("zone.read", "any_zone", "tenant")
    assert entry.profile is WebUiProfile.TENANT
    assert entry.label == "Verlauf"


def test_the_tenant_navigation_offers_the_history_next_to_the_heating_time(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    page = tenant_client([("zone.read", mine.id)]).get("/").text
    top = re.search(r'<nav class="tc-tnav.*?</nav>', page, re.S)
    bottom = re.search(r'<nav class="tc-tbottomnav.*?</nav>', page, re.S)
    assert top is not None and bottom is not None
    for nav in (top.group(0), bottom.group(0)):
        assert 'href="/temperature-history"' in nav
        order = [nav.index(path) for path in ("/heating-time", "/temperature-history", "/account")]
        assert order == sorted(order)
    here = tenant_client([("zone.read", mine.id)]).get("/temperature-history").text
    assert re.search(r'href="/temperature-history"\s+aria-current="page"', here)
    assert not re.search(r'href="/heating-time"\s+aria-current="page"', here)


def test_a_tenant_without_the_right_to_read_a_room_does_not_see_the_entry(
    session: Session,
) -> None:
    from thermoctl.domain.authz import principal_for_user

    zone = create_zone(session, "kein-leserecht")
    reader = user_with_permissions(
        session, "mieter-liest", [("zone.read", zone.id)], ui_profile=WebUiProfile.TENANT
    )
    writer = user_with_permissions(
        session, "mieter-schreibt", [("setpoint.write", zone.id)], ui_profile=WebUiProfile.TENANT
    )
    assert "/temperature-history" in {
        item.path for item in visible_navigation(principal_for_user(session, reader))
    }
    assert "/temperature-history" not in {
        item.path for item in visible_navigation(principal_for_user(session, writer))
    }


def test_the_plant_navigation_does_not_list_the_tenant_history(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "anlagen-navigation")
    page = client_als([("zone.read", zone.id), ("zone.manage", zone.id)]).get("/").text
    nav = re.search(r'<nav class="tc-sidenav.*?</nav>', page, re.S)
    assert nav is not None and "/temperature-history" not in nav.group(0)


def test_the_heating_time_page_points_to_the_history(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    page = tenant_client([("zone.read", mine.id)]).get("/heating-time").text
    assert re.search(
        r'<p class="t-quiet small">\s*Wie die Temperatur[^<]*<a href="/temperature-history">', page
    )


def test_every_address_carries_the_ingress_prefix(
    client_with_prefix: TestClient, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    user_record = user_with_permissions(
        session, "praefix-verlauf", [("zone.read", mine.id)], ui_profile=WebUiProfile.TENANT
    )
    _http, secret = create_session(session, user_record, 3600)
    client_with_prefix.cookies.set(COOKIE_NAME, secret)
    prefix = "/api/hassio_ingress/A1b2C3d4e5"
    page = client_with_prefix.get("/temperature-history").text.replace("&amp;", "&")
    assert f'hx-get="{prefix}/temperature-history/{mine.id}/tile?period=24h&tile=1"' in page
    assert f'href="{prefix}/temperature-history?period=3d"' in page
    assert f'hx-get="{prefix}/temperature-history?period=3d"' in page
    assert f'href="{prefix}/temperature-history"' in page  # Navigation


# -- Texte --------------------------------------------------------------------------------

TECHNICAL_ONLY = (
    "Zeitplan-Soll",
    "Sollwert vor Sonnenabsenkung",
    "Wirksamer Soll",
    "Heizanforderung (untere Spur)",
    "Absenkung unbekannt (vor der Aufzeichnung)",
    "Mittelwert je Abschnitt",
    "Kleinster bis größter",
    "Kurve:",
    "Ist:",
    " K<",
)


def test_the_tile_speaks_the_language_of_the_dwelling(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    tile = tenant_client([("zone.read", mine.id)]).get(f"/temperature-history/{mine.id}/tile").text
    assert "Gemessen: 19,5 °C" in tile
    assert "Zielwert ohne Sonnenabsenkung: 20,5 °C" in tile
    assert "Tatsächlicher Zielwert: 18,5 °C" in tile
    assert "Heizanforderung (Spur unten): Anteil der Zeit" in tile
    assert (
        "Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken; "
        "der Frostschutz soll dabei nicht unterschritten werden"
    ) in tile
    for technical in TECHNICAL_ONLY:
        assert technical not in tile, technical


def test_the_plant_tile_keeps_its_technical_wording(client_als: Client, session: Session) -> None:
    zone = _room(session, "anlage-wortlaut", "Anlage Wortlaut", "19.5")
    tile = client_als([("zone.manage", zone.id)]).get(f"/zones/{zone.id}/history?tile=1").text
    for expected in (
        "Ist: 19,5 °C",
        "Sollwert vor Sonnenabsenkung: 20,5 °C",
        "Wirksamer Soll: 18,5 °C",
        "Heizanforderung (untere Spur)",
        "Kurve: Mittelwert je Abschnitt von bis zu",
    ):
        assert expected in tile, expected
    assert "Tatsächlicher Zielwert" not in tile and "Gemessen:" not in tile
    assert "data-history-data" in tile and '"l":' not in html_module.unescape(tile)


def test_the_page_explains_why_it_can_stay_cooler_than_the_plan(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    page = tenant_client([("zone.read", mine.id)]).get("/temperature-history").text
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    for sentence in (
        "Wenn Sonne erwartet wird, kann die Regelung den Zielwert absenken.",
        "Das kann erklären, warum die Regelung noch keine Heizanforderung hatte, obwohl die "
        "Temperatur unter dem Wert des Zeitplans lag.",
        "Das sagt nichts darüber, wie es im Raum war.",
        "„keine Daten“ heißt: für diese Zeit gibt es keine Aufzeichnung.",
    ):
        assert sentence in text, sentence
    # Fachbegriffe führen ins Glossar (vorhandene Einträge, keine neuen).
    for entry in ("sollwert", "zeitplan", "sonnenabsenkung"):
        assert f'href="/glossar#{entry}"' in page
    for technical in (
        "Zeitplan-Soll",
        "Sollwert vor Sonnenabsenkung",
        "Wirksamer Soll",
        "Trockenlauf",
        "Schattenbetrieb",
        "MQTT",
    ):
        assert technical not in page


def test_the_hover_data_carries_the_tenant_labels_and_the_plant_data_does_not() -> None:
    start = datetime(2026, 10, 1, 8, 0)
    samples = [Sample(start + timedelta(minutes=i), 19.5, 18.5, 20.5, False, 2.0) for i in range(5)]
    end = start + timedelta(hours=1)
    tenant = hover_payload(build_chart(samples, start, end, BERLIN, TENANT), BERLIN)
    plant = hover_payload(build_chart(samples, start, end, BERLIN), BERLIN)
    assert tenant["l"]["effective"] == "Tatsächlicher Zielwert"  # type: ignore[index]
    assert tenant["l"]["scheduled"] == "Zielwert ohne Sonnenabsenkung"  # type: ignore[index]
    assert tenant["l"]["unit"] == "Grad"  # type: ignore[index]
    assert "l" not in plant
    # Die Zahlen sind in beiden Fassungen dieselben.
    assert {k: v for k, v in tenant.items() if k != "l"} == plant


def test_both_wordings_state_the_same_numbers() -> None:
    start = datetime(2026, 10, 1, 8, 0)
    samples = [
        Sample(start + timedelta(minutes=i), 19.0 + i / 10, 18.5, 20.5, i % 2 == 0, 2.0)
        for i in range(20)
    ]
    end = start + timedelta(hours=1)
    plant = build_chart(samples, start, end, BERLIN)
    tenant = build_chart(samples, start, end, BERLIN, TENANT)
    for field in ("points", "heat_lane", "solar_bands", "effective_runs", "scheduled_runs",
                  "minimum", "maximum", "hover", "bucket_text", "actual_text",
                  "scheduled_text", "effective_text"):
        assert getattr(plant, field) == getattr(tenant, field), field
    assert plant.summary != tenant.summary
    assert tenant.summary.startswith("Gemessen 19,0 °C bis 20,9 °C.")
    assert (
        "Zielwert ohne Sonnenabsenkung 20,5 °C, tatsächlicher Zielwert 18,5 °C" in tenant.summary
    )
    assert re.search(r"Heizanforderung \d+ % der erfassten Zeit\.", tenant.summary)
    assert "Sonnenabsenkung" in tenant.summary


def test_missing_data_is_said_plainly() -> None:
    start = datetime(2026, 10, 1, 8, 0)
    end = start + timedelta(hours=1)
    assert build_chart([], start, end, BERLIN, TENANT).summary == (
        "Für diesen Zeitraum liegen keine Daten vor."
    )
    # Zeilen aus der Zeit vor der Aufzeichnung: "keine Angabe" statt "unbekannt".
    old = [Sample(start + timedelta(minutes=i), 19.0, 20.0, None, False, None) for i in range(60)]
    chart = build_chart(old, start, end, BERLIN, TENANT)
    assert "liegt keine Angabe zur Sonnenabsenkung vor." in chart.summary
    assert "(Zielwert ohne Sonnenabsenkung unbekannt)" in chart.summary
    assert [band.label for band in unknown_bands_for(chart, DESKTOP)] == ["keine Angabe"]
    technical = build_chart(old, start, end, BERLIN)
    assert [band.label for band in unknown_bands_for(technical, DESKTOP)] == ["Absenkung unbekannt"]
    assert "Absenkung für" in technical.summary and "(vor der Aufzeichnung)" in technical.summary


def test_a_gap_in_the_measurement_is_not_called_a_sensor_fault() -> None:
    start = datetime(2026, 10, 1, 8, 0)
    samples = [
        Sample(start + timedelta(minutes=i), None, 20.0, 20.0, False, 0.0) for i in range(10)
    ]
    chart = build_chart(samples, start, start + timedelta(hours=1), BERLIN, TENANT)
    assert "gab es keinen Messwert" in chart.summary
    assert "Temperaturmesswert" not in chart.summary and "Sensor" not in chart.summary


def test_the_solar_band_shows_degrees_in_the_tenant_wording() -> None:
    start = datetime(2026, 10, 1, 8, 0)
    samples = [
        Sample(start + timedelta(minutes=i), 19.5, 18.5, 20.5, False, 2.0) for i in range(60)
    ]
    end = start + timedelta(hours=1)
    plant = solar_bands_for(build_chart(samples, start, end, BERLIN), DESKTOP)
    tenant = solar_bands_for(build_chart(samples, start, end, BERLIN, TENANT), DESKTOP)
    assert plant[0].label == "Sonnenabsenkung 2,0 K"
    assert tenant[0].label == "Sonnenabsenkung 2,0 Grad"


def test_no_physical_effect_is_claimed_by_the_tenant_texts() -> None:
    """Die Texte sagen "angefordert", nie dass es warm wurde oder geheizt wurde."""
    forbidden = re.compile(r"geheizt|beheizt|erwärmt|wurde warm|wärmer|schaltet", re.IGNORECASE)
    for wording in (TENANT,):
        for field, value in vars(wording).items():
            if isinstance(value, str):
                assert not forbidden.search(value), (field, value)
    for _key, text in TENANT.hover:
        assert not forbidden.search(text), text
    # Beide Fassungen kennen dieselben Schlüssel, die das Skript ersetzt.
    assert {key for key, _ in TENANT.hover} <= {
        "measured", "scheduled", "effective", "mean", "lowest", "highest", "unit", "unknown",
        "partly", "solar_unknown",
    }
    assert TECHNICAL.hover == ()
