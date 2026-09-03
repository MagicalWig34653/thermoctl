"""The PI (Beta) switch and its warning disclosure on the zone parameter page.

An HTTP test can already check that the response contains the word "disabled" and
the numbers in the calculation table. What it cannot check is whether a person can
actually open the `<details>` element to read that table, and whether the browser
genuinely refuses to toggle a `disabled` checkbox on click -- both are native
browser behaviour that only exists once real DOM/CSS/event handling runs.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def test_the_switch_is_locked_for_a_zone_without_a_suitable_actuator(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        zone = seed.create_bare_zone(session, "pi-ungeeignet")
        session.commit()
        zone_id = zone.id

    admin_page.goto(f"/zones/{zone_id}/parameters")
    expect(admin_page.get_by_text("eignet sich derzeit nicht für PI")).to_be_visible()

    switch = admin_page.locator("#pi_enabled")
    expect(switch).to_be_disabled()
    # The strongest form of "locked": clicking a genuinely disabled control does
    # nothing in a real browser (a stub that merely styled it grey would not stop
    # a synthetic click reaching the DOM checkbox state).
    switch.click(force=True)
    expect(switch).not_to_be_checked()


def test_the_pi_calculation_can_be_opened_and_names_the_relevant_figures(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        zone = seed.create_bare_zone(session, "pi-rechnung")
        session.commit()
        zone_id = zone.id

    admin_page.goto(f"/zones/{zone_id}/parameters")
    disclosure = admin_page.locator("details.tc-info-disclosure")
    table = disclosure.locator("table")

    # `<details>` without `open` hides its content via the browser's own default
    # stylesheet -- nothing thermoctl renders itself, and nothing an HTTP test
    # could ever observe.
    expect(table).to_be_hidden()
    disclosure.locator("summary").click()
    expect(table).to_be_visible()
    # These are the two headline numbers from the STATUS.md entry for this switch,
    # not asserted against the intermediate 30%-duty-cycle row -- an adjacent task
    # is free to change the simulated assumption without breaking this test.
    expect(table).to_contain_text("262.800")
    expect(table).to_contain_text("52.560")
