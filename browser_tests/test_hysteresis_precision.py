"""Hysterese 0,0001 K: im echten Browser eingeben, speichern, neu laden, wiederfinden.

Echter Server, echte Datenbank, kein Mock. Geprüft werden Raumtechnik (Raumwert) und
Regelvorgaben (Anlagenwert); dazu die Ablehnung von 0,00009 und das, was nach dem
Neuladen im Feld steht (nicht auf 0,1 oder 0,00 gerundet).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_schedule_zone

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def room_id(live_server: LiveServer) -> int:
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Hystereseraum")
        session.commit()
        return int(zone.id)


@pytest.fixture()
def page(browser: Browser, live_server: LiveServer) -> Page:
    ctx = browser.new_context(
        base_url=live_server.base_url, viewport={"width": 1280, "height": 900}
    )
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    yield page
    ctx.close()


def _open_room(page: Page, room: int) -> None:
    page.goto(f"/app/raeume/{room}")
    page.wait_for_load_state("networkidle")
    page.locator("#rt-title").wait_for(state="attached")
    page.locator("summary", has_text="Regelverhalten").click()


def test_room_hysteresis_one_ten_thousandth_survives_save_and_reload(
    page: Page, room_id: int
) -> None:
    _open_room(page, room_id)
    field = page.locator("#rt-hysteresis_k")
    field.fill("0,00009")
    page.get_by_role("button", name="Raumtechnik speichern").click()
    expect(page.get_by_text("mindestens 0.0001").first).to_be_visible()

    field.fill("0,0001")
    page.get_by_role("button", name="Raumtechnik speichern").click()
    expect(page.get_by_text("mindestens 0.0001")).to_have_count(0)
    page.wait_for_load_state("networkidle")

    _open_room(page, room_id)
    expect(page.locator("#rt-hysteresis_k")).to_have_value("0,0001")
    expect(page.get_by_text("Wirksam: 0,0001 K")).to_be_visible()


def test_plant_default_hysteresis_one_ten_thousandth_survives_save_and_reload(
    page: Page,
) -> None:
    page.goto("/app/heizung/regelvorgaben")
    page.wait_for_load_state("networkidle")
    field = page.locator("#rv-default_hysteresis_k")
    field.fill("0,00009")
    page.get_by_role("button", name="Regelvorgaben speichern").click()
    expect(page.locator("#rv-default_hysteresis_k-error, [role=alert]").first).to_be_visible()

    field.fill("0,0001")
    page.get_by_role("button", name="Regelvorgaben speichern").click()
    page.wait_for_load_state("networkidle")

    page.goto("/app/heizung/regelvorgaben")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#rv-default_hysteresis_k")).to_have_value("0,0001")
