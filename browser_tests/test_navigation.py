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
    expect(page.locator(".tc-head")).to_be_visible()

    # The entry must be genuinely absent from the rendered menu -- not merely
    # styled away -- for both the always-visible top level and inside the
    # collapsed "Einstellungen" dropdown, wherever it would otherwise land.
    settings_dropdown = page.get_by_role("button", name="Einstellungen")
    if settings_dropdown.count():
        settings_dropdown.click()
    expect(page.get_by_role("link", name="Benutzer", exact=True)).to_have_count(0)

    # And direct navigation is refused too -- the entry being hidden is a courtesy,
    # not the actual boundary (that is `domain/authz.py`, reviewed separately).
    response = page.goto("/users")
    assert response is not None
    assert response.status == 403


def test_the_administrator_does_see_the_users_menu_entry(admin_page: Page) -> None:
    admin_page.get_by_role("button", name="Einstellungen").click()
    expect(admin_page.get_by_role("link", name="Benutzer", exact=True)).to_be_visible()
