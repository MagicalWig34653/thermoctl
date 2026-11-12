"""Raumseite: Betreiber-Einstellungen sind auffindbar und lesbar (UX-Prüfung 2026-11-11).

Gemessen im echten Browser, Desktop (1280) und Handy (390), mit Betreiberkonto:
- der Schalter „Ausfallbenachrichtigungen“ ist ohne Aufklappen sichtbar,
- „Erbt“ und „Erben“ werden erklärt (sichtbarer Hilfetext, kein Tooltip allein),
- der Notbetriebs-Hinweis läuft nicht über den Rand, auch bei einem langen Gerätenamen,
- die Raumtechnik beginnt mit sichtbarer Überschrift; ihre Gruppen sind eingeklappt
  (kürzere Seite), der Schalter für Ausfallbenachrichtigungen steht davor.
Mit `THERMOCTL_SHOTS_DIR` werden Bildschirmfotos der ganzen Seite abgelegt.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_emergency_zone

pytestmark = pytest.mark.browser

# Ohne Leerzeichen und Bindestriche: Der Browser findet keine Umbruchstelle.
LONG_NAME = "WohnzimmerThermostat" + "Fensterseite" * 8 + "Sensor0123456789abcdef"


@pytest.fixture(scope="module")
def room_id(live_server: LiveServer) -> int:
    with live_server.session() as session:
        zone = create_emergency_zone(session, "Layoutraum", LONG_NAME)
        session.commit()
        return int(zone.id)


@contextmanager
def _open_room(browser: Browser, live_server: LiveServer, width: int, room: int) -> Iterator[Page]:
    ctx = browser.new_context(
        base_url=live_server.base_url,
        viewport={"width": width, "height": 900 if width > 600 else 844},
        has_touch=width < 600,
        is_mobile=width < 600,
    )
    page = ctx.new_page()
    page.goto("/login")
    page.get_by_label("Benutzername").fill(live_server.admin_username)
    page.get_by_label("Passwort").fill(live_server.admin_password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.goto(f"/app/raeume/{room}")
    page.wait_for_load_state("networkidle")
    page.locator("#rt-title").wait_for(state="attached")
    try:
        yield page
    finally:
        ctx.close()


def _shot(page: Page, name: str) -> None:
    shots = os.environ.get("THERMOCTL_SHOTS_DIR")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.screenshot(path=f"{shots}/{name}.png", full_page=True)


def _to_technik(page: Page, width: int) -> None:
    if width < 600:
        page.get_by_role("tab", name="Technik").click()


@pytest.mark.parametrize("width", [1280, 390])
def test_page_height_report(
    browser: Browser, live_server: LiveServer, room_id: int, width: int
) -> None:
    with _open_room(browser, live_server, width, room_id) as page:
        _to_technik(page, width)
        height = page.evaluate("document.documentElement.scrollHeight")
        print(f"\n[{width}] Seitenhöhe: {height}px")
        _shot(page, f"seite-{width}")


@pytest.mark.parametrize("width", [1280, 390])
def test_fault_notification_switch_visible_without_expanding(
    browser: Browser, live_server: LiveServer, room_id: int, width: int
) -> None:
    with _open_room(browser, live_server, width, room_id) as page:
        _to_technik(page, width)
        switch = page.get_by_role("switch", name="Ausfallbenachrichtigungen")
        expect(switch).to_be_visible()
        switch.scroll_into_view_if_needed()
        _shot(page, f"ausfall-{width}")


@pytest.mark.parametrize("width", [1280, 390])
def test_inherit_is_explained_visibly(
    browser: Browser, live_server: LiveServer, room_id: int, width: int
) -> None:
    with _open_room(browser, live_server, width, room_id) as page:
        _to_technik(page, width)
        technik = page.get_by_test_id("room-technik")
        hint = technik.get_by_test_id("inherit-explanation")
        expect(hint).to_be_visible()
        text = hint.inner_text()
        assert "Erbt" in text and "Anlage" in text and "Erben" in text, text


@pytest.mark.parametrize("width", [1280, 390])
def test_emergency_notice_does_not_overflow(
    browser: Browser, live_server: LiveServer, room_id: int, width: int
) -> None:
    with _open_room(browser, live_server, width, room_id) as page:
        _to_technik(page, width)
        notice = page.get_by_role("status").filter(has_text="Ersatzquelle aktiv")
        expect(notice).to_be_visible()
        notice.scroll_into_view_if_needed()
        _shot(page, f"notbetrieb-{width}")
        box = notice.evaluate(
            "el => ({ sw: el.scrollWidth, cw: el.clientWidth,"
            " right: el.getBoundingClientRect().right,"
            " inner: innerWidth, doc: document.documentElement.scrollWidth })"
        )
        assert box["sw"] <= box["cw"], box
        assert box["right"] <= box["inner"] + 0.5, box
        assert box["doc"] <= box["inner"], box


@pytest.mark.parametrize("width", [1280, 390])
def test_technik_groups_titled(
    browser: Browser, live_server: LiveServer, room_id: int, width: int
) -> None:
    with _open_room(browser, live_server, width, room_id) as page:
        _to_technik(page, width)
        technik = page.get_by_test_id("room-technik")
        expect(technik.get_by_role("heading", name="Raumtechnik")).to_be_visible()
        for title in ("Regelverhalten", "Sonne", "Ventilschutz", "Notbetrieb"):
            summary = technik.locator("summary").filter(has_text=title).first
            expect(summary).to_be_visible()
            assert summary.evaluate("el => el.parentElement.open") is False, title
