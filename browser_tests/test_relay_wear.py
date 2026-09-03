"""The relay-wear page: does it render, and does its period switch actually work.

Mostly covered by the "no console errors on any page" check every `page` fixture
enforces (see conftest.py) -- this file adds the one thing that check cannot see:
that clicking a period button visibly changes which one is marked current, i.e.
the link actually navigates and the server actually re-renders with the new
`period` query parameter applied.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


def test_the_page_renders_and_the_period_switch_changes_the_active_choice(
    admin_page: Page,
) -> None:
    admin_page.goto("/relay-wear")
    expect(admin_page.locator("h1")).to_contain_text("Relaisverschleiß")

    period_nav = admin_page.get_by_role("navigation", name="Zeitraum")
    buttons = period_nav.get_by_role("link")
    count = buttons.count()
    assert count >= 2, "Erwarte mehrere Zeitraum-Knoepfe, um den Wechsel zu pruefen."

    currently_active = period_nav.locator("[aria-current='page']")
    expect(currently_active).to_have_count(1)
    active_label_before = currently_active.inner_text()

    other = next(
        button for button in buttons.all() if button.inner_text() != active_label_before
    )
    other_label = other.inner_text()
    other.click()

    expect(period_nav.locator("[aria-current='page']")).to_have_text(other_label)
