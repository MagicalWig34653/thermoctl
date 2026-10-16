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
from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.operations import Setting
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


























def test_an_api_token_does_not_open_the_html_history(client: TestClient, session: Session) -> None:
    """`current_principal` kennt für die HTML-Routen nur das Sitzungs-Cookie. Ein Bearer-Token
    mit allen Rechten öffnet Seite und Kachel deshalb nicht -- Tokens gehören zur REST-API."""
    mine, _theirs, _bath = _wohnungen(session)
    owner = user_with_permissions(
        session, "token-mieter", [("zone.read", None)], ui_profile=WebUiProfile.TENANT
    )
    _token, plaintext = issue_token(session, owner, "verlauf", [("zone.read", None)], None)
    headers = {"Authorization": f"Bearer {plaintext}"}
    # Gegenprobe: dasselbe Token ist an der API gültig, sonst bewiese der Test nichts.
    assert client.get("/api/v1/zones", headers=headers).status_code == 200
    for path in ("/temperature-history", f"/temperature-history/{mine.id}/tile"):
        answer = client.get(path, headers=headers, follow_redirects=False)
        assert answer.status_code in (303, 401), path
        assert mine.display_name not in answer.text


# -- Profil und Anmeldung ----------------------------------------------------------------






def test_without_login_neither_page_nor_tile_shows_anything(
    client: TestClient, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    for path in ("/temperature-history", f"/temperature-history/{mine.id}/tile"):
        answer = client.get(path, follow_redirects=False)
        assert answer.status_code in (303, 401), path
        assert mine.display_name not in answer.text




# -- Anlage nicht scharf -------------------------------------------------------------------

DRY_RUN_NOTICE = "Die Anlage steuert derzeit nichts."


def _armed(session: Session, armed: bool) -> None:
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.control_armed = armed
    session.flush()




def test_a_page_of_an_armed_plant_carries_no_such_notice(
    tenant_client: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    _armed(session, True)
    page = tenant_client([("zone.read", mine.id)]).get("/temperature-history")
    assert DRY_RUN_NOTICE not in page.text
    assert "keine Aufzeichnung dessen" not in page.text




def test_without_any_room_there_is_nothing_to_explain(
    tenant_client: Client, session: Session
) -> None:
    _wohnungen(session)
    _armed(session, False)
    page = tenant_client([]).get("/temperature-history")
    assert DRY_RUN_NOTICE not in page.text


def test_the_plant_history_pages_show_no_tenant_notice(
    client_als: Client, session: Session
) -> None:
    mine, _theirs, _bath = _wohnungen(session)
    _armed(session, False)
    admin = client_als([("zone.read", None), ("zone.manage", None)])
    assert DRY_RUN_NOTICE not in admin.get("/zones/history").text
    assert DRY_RUN_NOTICE not in admin.get(f"/zones/{mine.id}/history").text


# -- Zeitraum -----------------------------------------------------------------------------








# -- Navigation ---------------------------------------------------------------------------


def test_the_navigation_entry_is_bound_to_the_right_the_view_checks() -> None:
    (entry,) = [item for item in NAVIGATION_ITEMS if item.path == "/temperature-history"]
    assert (entry.permission, entry.scope, entry.section) == ("zone.read", "any_zone", "tenant")
    assert entry.profile is WebUiProfile.TENANT
    assert entry.label == "Verlauf"




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
