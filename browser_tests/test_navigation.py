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


def test_the_sidebar_opens_only_after_clicking_the_navigation_button_on_a_narrow_screen(
    admin_page: Page,
) -> None:
    """Ein Bootstrap-`collapse`, das mit der neuen Hülle nicht mehr auf- oder
    zugeht, sieht im HTML völlig richtig aus -- nur ein echter Browser zeigt, ob
    `.tc-sidebar` auf einem schmalen Bildschirm tatsächlich verborgen bleibt, bis
    der Knopf "Navigation" sie aufklappt.
    """
    admin_page.set_viewport_size({"width": 390, "height": 844})
    sidebar = admin_page.locator("#tc-sidebar")
    expect(sidebar).not_to_be_visible()

    admin_page.get_by_role("button", name="Navigation").click()
    expect(sidebar).to_be_visible()
