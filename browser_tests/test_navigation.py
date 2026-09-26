"""Menu entries actually disappear for a user without the matching permission.

`tests/test_navigation.py` (the HTTP suite) already checks the data behind this --
`visible_navigation()` filters `NAVIGATION_ITEMS` by permission. What it cannot see
is the rendered menu a real person actually looks at: this test signs in as an
account that plainly lacks `user.manage` and checks the "Benutzer" entry is not on
the page at all, not merely unreachable if guessed.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser

_PASSWORD = "Kein-Verwaltungsrecht-3"  # noqa: S105 -- local, ephemeral, throwaway DB


def test_a_user_without_user_manage_does_not_see_the_users_menu_entry(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_user(
            session, "browsertest-eingeschraenkt", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    page.goto("/login")
    page.get_by_label("Benutzername").fill("browsertest-eingeschraenkt")
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    # `.tc-topbar` statt der seit v0.9.0 entfallenen `.tc-head` -- dieselbe Aussage
    # ("angemeldet, Anlagenhuelle sichtbar"), an einem Element, das auf jeder
    # Bildschirmbreite sichtbar bleibt.
    expect(page.locator(".tc-topbar")).to_be_visible()

    # Seit v0.9.0 gibt es kein Sammelmenü "Einstellungen" mehr, das erst
    # aufgeklappt werden müsste -- die Seitenleiste zeigt jeden Eintrag
    # unmittelbar als Verweis. Der Test prüft deshalb nur noch direkt, dass der
    # Eintrag genuin fehlt -- nicht bloß gestylt weg -- in der ganzen Seitenleiste.
    expect(page.get_by_role("link", name="Benutzer", exact=True)).to_have_count(0)

    # And direct navigation is refused too -- the entry being hidden is a courtesy,
    # not the actual boundary (that is `domain/authz.py`, reviewed separately).
    response = page.goto("/users")
    assert response is not None
    assert response.status == 403


def test_the_administrator_does_see_the_users_menu_entry(admin_page: Page) -> None:
    # Kein Aufklappen mehr nötig (siehe Kommentar oben) -- der Eintrag steht
    # unmittelbar sichtbar in der Seitenleiste, Abschnitt "Zugänge".
    expect(admin_page.get_by_role("link", name="Benutzer", exact=True)).to_be_visible()


def test_there_is_no_navigation_button_in_the_header_on_a_narrow_screen(
    admin_page: Page,
) -> None:
    """Zwei Zugänge zur selben Seitenleiste liefen früher auseinander: der
    Kopfzeilen-Knopf "Navigation" klappte dieselbe `#tc-sidebar` per Bootstrap-
    `collapse` im Seitenfluss auf, "Mehr" in der unteren Leiste ebenfalls -- beide
    landeten dabei aus gescrollter Position außerhalb des Sichtbereichs. Jetzt gibt
    es nur noch "Mehr"; der Knopf "Navigation" ist ganz entfernt.
    """
    admin_page.set_viewport_size({"width": 390, "height": 844})
    expect(admin_page.get_by_role("button", name="Navigation")).to_have_count(0)


def test_the_sidebar_opens_as_a_drawer_over_the_content_regardless_of_scroll_position(
    admin_page: Page,
) -> None:
    """Die Seitenleiste stand im DOM vor dem Inhalt: Wer weit auf `/devices`
    heruntergescrollt hatte und dann aufklappte, sah sie nicht -- gemessen
    `sidebar.y = -2788` bei 390x844. Jetzt ist `#tc-sidebar` ein Bootstrap-
    Offcanvas (`offcanvas-lg`), das als fixe Schublade über dem Inhalt öffnet,
    unabhängig von der Scrollposition.
    """
    admin_page.set_viewport_size({"width": 390, "height": 844})
    admin_page.goto("/devices")
    admin_page.locator(".tc-footer").scroll_into_view_if_needed()

    sidebar = admin_page.locator("#tc-sidebar")
    expect(sidebar).not_to_be_visible()

    admin_page.get_by_role("button", name="Mehr").click()
    expect(sidebar).to_be_visible()

    box = sidebar.bounding_box()
    assert box is not None
    viewport = admin_page.viewport_size
    assert viewport is not None
    assert box["y"] >= 0
    assert box["y"] < viewport["height"]

    # Und der einzige mobile Zugang: keine zweite, separate Navigation im Kopf.
    expect(admin_page.get_by_role("button", name="Navigation")).to_have_count(0)


def test_the_drawer_closes_on_escape(admin_page: Page) -> None:
    admin_page.set_viewport_size({"width": 390, "height": 844})
    sidebar = admin_page.locator("#tc-sidebar")
    admin_page.get_by_role("button", name="Mehr").click()
    expect(sidebar).to_be_visible()
    # Bootstraps Escape-Behandlung hängt am Element selbst (nicht an `document`)
    # und feuert nur, wenn der Tastaturfokus wirklich darin liegt -- den setzt der
    # eigene Fokus-Trap erst, sobald die Öffnen-Animation fertig ist. `to_be_visible`
    # allein greift schon während des Übergangs.
    expect(sidebar).to_be_focused()

    admin_page.keyboard.press("Escape")
    expect(sidebar).not_to_be_visible()


def test_the_drawer_closes_on_a_tap_outside(admin_page: Page) -> None:
    admin_page.set_viewport_size({"width": 390, "height": 844})
    sidebar = admin_page.locator("#tc-sidebar")
    admin_page.get_by_role("button", name="Mehr").click()
    expect(sidebar).to_be_visible()
    expect(sidebar).to_be_focused()

    # Die Schublade ist bewusst schmaler als Bootstraps 400px-Vorgabe
    # (`--bs-offcanvas-width` in thermoctl.css) -- randlos bliebe auf einem
    # 390px breiten Telefon kein Hintergrund übrig, auf den "Tippen daneben"
    # überhaupt träfe. Ein Klick weit rechts trifft ihn zuverlässig.
    admin_page.mouse.click(380, 400)
    expect(sidebar).not_to_be_visible()


def test_the_drawer_closes_when_a_navigation_link_is_chosen(admin_page: Page) -> None:
    admin_page.set_viewport_size({"width": 390, "height": 844})
    sidebar = admin_page.locator("#tc-sidebar")
    admin_page.get_by_role("button", name="Mehr").click()
    expect(sidebar).to_be_visible()

    sidebar.get_by_role("link", name="Benutzer", exact=True).click()
    expect(sidebar).not_to_be_visible()

    # Waehrend die Schublade offen steht, sperrt Bootstrap den Hintergrund
    # (`document.body.style.overflow`) und legt einen `.offcanvas-backdrop`
    # an. `hx-boost` tauscht nur den Inhalt des <body> aus, nicht den Knoten
    # selbst -- beide Spuren muessen nach der Navigation wirklich weg sein,
    # sonst bliebe die Seite (und jede folgende Seite) dauerhaft gegen
    # Scrollen gesperrt, ohne dass eine sichtbare Schublade das erklaeren
    # wuerde.
    expect(admin_page.locator(".offcanvas-backdrop")).to_have_count(0)
    admin_page.wait_for_function("document.body.style.overflow === ''")
    assert admin_page.evaluate("document.body.style.overflow") == ""


def test_the_desktop_sidebar_stands_fixed_without_a_navigation_button(
    admin_page: Page,
) -> None:
    """Am Desktop bleibt die Seitenleiste exakt wie vor der Umstellung: fest
    sichtbar, kein Offcanvas-Verhalten, kein Knopf "Navigation" im Kopf."""
    admin_page.set_viewport_size({"width": 1280, "height": 800})
    sidebar = admin_page.locator("#tc-sidebar")
    expect(sidebar).to_be_visible()
    expect(admin_page.get_by_role("button", name="Navigation")).to_have_count(0)
    expect(admin_page.get_by_role("button", name="Mehr")).to_have_count(0)
