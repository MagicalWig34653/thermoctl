"""Empirical proof for the v0.10.1 report: "doppelte Speichern-Buttons und z. B.
eine Checkbox, die nicht gespeichert wird" on `/zones/{id}/parameters`.

`parameter.html` used to render two `<form>`s, each with its own "Speichern": the
main one (parameters, valve protection, PI) and a second, separate one just below
it for "Fenster aus Temperatursturz erkennen". A person looking at the rendered
page sees one settings area with two identical-looking save buttons; flipping that
switch and pressing the *other* one silently lost the change, because it belonged
to a request the main button never sent. An HTTP test that counts `<form>` tags
cannot see this -- it would happily report "one button per form, both fine". Only
a real browser, a real click on the button an actual user would press, and a fresh
context to rule out any client-side illusion of persistence, can show it.

This suite exercises every checkbox on the page through the one remaining button.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy.orm import Session

from browser_tests.live_server import LiveServer, _live_server
from tests.helpers import capability, create_device, create_zone, role
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.zone import Zone

pytestmark = pytest.mark.browser


def _assign_eligible_actuator(session: Session, zone: Zone) -> None:
    """The minimal device assignment `pi_eligible()` accepts, mirroring
    `tests/test_daily_views.py::_assign_switch_actuator` -- needed here so the PI
    (Beta) switch actually renders enabled instead of permanently disabled."""
    device = create_device(session, f"{zone.name}-relais")
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()


@pytest.fixture(scope="module")
def parameter_server() -> Iterator[tuple[LiveServer, int]]:
    with closing(_live_server("")) as servers:
        server = next(servers)
        with server.session() as session:
            zone = create_zone(session, "einspeichern-parameter")
            _assign_eligible_actuator(session, zone)
            session.commit()
            zone_id = zone.id
        yield server, zone_id


@pytest.fixture
def page_at_parameters(
    browser: Browser, parameter_server: tuple[LiveServer, int]
) -> Iterator[Page]:
    server, zone_id = parameter_server
    with browser.new_context(base_url=server.base_url, locale="de-DE") as context:
        page = context.new_page()
        page.goto("/login", wait_until="networkidle")
        page.get_by_label("Benutzername").fill(server.admin_username)
        page.get_by_label("Passwort", exact=True).fill(server.admin_password)
        page.get_by_role("button", name="Anmelden").click()
        page.locator(".tc-topbar, .tc-theader").wait_for()
        page.goto(f"/zones/{zone_id}/parameters", wait_until="networkidle")
        yield page


def test_the_page_has_exactly_one_visible_speichern_button(page_at_parameters: Page) -> None:
    buttons = page_at_parameters.locator("button:visible", has_text="Speichern")
    expect(buttons).to_have_count(1)


@pytest.mark.parametrize(
    "checkbox_id",
    ["valve_protection_enabled", "window_temp_drop_detection_enabled", "sensor_failure_enabled"],
)
def test_a_checkbox_survives_the_single_save_button_and_a_fresh_context(
    page_at_parameters: Page,
    parameter_server: tuple[LiveServer, int],
    browser: Browser,
    checkbox_id: str,
) -> None:
    """Checks the box, presses the *one* button a user would press for the whole
    page, then reloads in a brand-new browser context (no client-side cache, no
    shared cookies jar reused by accident) to rule out any illusion that the change
    only looked saved."""
    server, zone_id = parameter_server
    page = page_at_parameters
    box = page.locator(f"#{checkbox_id}")
    expect(box).not_to_be_disabled()
    if not box.is_checked():
        box.check()
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_load_state("networkidle")

    with browser.new_context(base_url=server.base_url, locale="de-DE") as fresh_context:
        fresh_page = fresh_context.new_page()
        fresh_page.goto("/login", wait_until="networkidle")
        fresh_page.get_by_label("Benutzername").fill(server.admin_username)
        fresh_page.get_by_label("Passwort", exact=True).fill(server.admin_password)
        fresh_page.get_by_role("button", name="Anmelden").click()
        fresh_page.locator(".tc-topbar, .tc-theader").wait_for()
        fresh_page.goto(f"/zones/{zone_id}/parameters", wait_until="networkidle")
        expect(fresh_page.locator(f"#{checkbox_id}")).to_be_checked()

    # Uncheck again through the same single button, for the next parametrized run
    # and to prove the "off" direction just as much as "on".
    box = page.locator(f"#{checkbox_id}")
    box.uncheck()
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_load_state("networkidle")
    with browser.new_context(base_url=server.base_url, locale="de-DE") as fresh_context:
        fresh_page = fresh_context.new_page()
        fresh_page.goto("/login", wait_until="networkidle")
        fresh_page.get_by_label("Benutzername").fill(server.admin_username)
        fresh_page.get_by_label("Passwort", exact=True).fill(server.admin_password)
        fresh_page.get_by_role("button", name="Anmelden").click()
        fresh_page.locator(".tc-topbar, .tc-theader").wait_for()
        fresh_page.goto(f"/zones/{zone_id}/parameters", wait_until="networkidle")
        expect(fresh_page.locator(f"#{checkbox_id}")).not_to_be_checked()


def test_pi_enabled_and_pi_confirm_survive_the_single_save_button(
    page_at_parameters: Page,
    parameter_server: tuple[LiveServer, int],
    browser: Browser,
) -> None:
    """`pi_confirm` is deliberately excluded from the generic round trip above: it
    is a one-time confirmation for the moment PI is switched on, not a persisted
    preference (see `save_parameter`'s own comment) -- checking it and reloading is
    expected to show it *unchecked* again, and that is correct, not a bug. What
    must survive is `pi_enabled` itself."""
    server, zone_id = parameter_server
    page = page_at_parameters
    page.locator("#pi_confirm").check()
    page.locator("#pi_enabled").check()
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_load_state("networkidle")

    with browser.new_context(base_url=server.base_url, locale="de-DE") as fresh_context:
        fresh_page = fresh_context.new_page()
        fresh_page.goto("/login", wait_until="networkidle")
        fresh_page.get_by_label("Benutzername").fill(server.admin_username)
        fresh_page.get_by_label("Passwort", exact=True).fill(server.admin_password)
        fresh_page.get_by_role("button", name="Anmelden").click()
        fresh_page.locator(".tc-topbar, .tc-theader").wait_for()
        fresh_page.goto(f"/zones/{zone_id}/parameters", wait_until="networkidle")
        expect(fresh_page.locator("#pi_enabled")).to_be_checked()
        expect(fresh_page.locator("#pi_enabled")).not_to_be_disabled()
        # The confirmation is not re-demanded on every later save of an
        # already-enabled zone -- unchecked here is correct, not lost data.
        expect(fresh_page.locator("#pi_confirm")).not_to_be_checked()

    # Turn PI back off through the same one button (reusing `page`, not a further
    # fresh context -- the "off" direction is already exercised by the generic
    # checkbox round trip above for the other two switches, and by the HTTP-level
    # `test_switching_pi_on_and_off_through_the_interface_with_audit_entry`; this
    # only has to leave the zone as it was found).
    page.locator("#pi_enabled").uncheck()
    page.get_by_role("button", name="Speichern").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#pi_enabled")).not_to_be_checked()
