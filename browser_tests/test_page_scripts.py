"""Lazy enhancements survive boosted navigation, history and server updates."""

import pytest
from playwright.sync_api import Page, Route, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


def _boost(page: Page, path: str) -> None:
    page.evaluate(
        """path => {
            const link = document.createElement('a');
            link.href = document.body.dataset.urlPrefix + path;
            link.textContent = 'Testnavigation';
            document.body.appendChild(link);
            htmx.process(link);
            link.click();
        }""",
        path,
    )
    page.wait_for_url(f"**{path}")


@pytest.mark.parametrize("prefixed", [False, True])
def test_features_load_once_across_boost_and_history(
    request: pytest.FixtureRequest, prefixed: bool
) -> None:
    page: Page = request.getfixturevalue("admin_page_with_prefix" if prefixed else "admin_page")
    server: LiveServer = request.getfixturevalue(
        "live_server_with_prefix" if prefixed else "live_server"
    )
    with server.session() as session:
        zone = seed.create_schedule_zone(session, f"Lazy-{prefixed}")
        # #device-search only renders once at least one device is known
        # (devices.html); seeded explicitly here so the assertion below does
        # not depend on some earlier test having left a device behind in the
        # shared `live_server`/`live_server_with_prefix` database.
        seed.create_temperature_device(session, f"lazy-loader-{prefixed}")
        session.commit()
        schedule_path = f"/zones/{zone.id}/schedule"
    page.wait_for_load_state("networkidle")
    expect(page.locator('script[src*="schedule.js"]')).to_have_count(0)
    page.evaluate("window.lazyDocument = document")
    for _ in range(2):
        _boost(page, "/devices")
        expect(page.locator("#device-search")).to_be_visible()
        page.locator("#device-search").fill("keine passenden Geräte")
        _boost(page, schedule_path)
        expect(page.locator("[data-schedule-hint]")).to_be_visible()
        page.go_back()
        expect(page.locator("#device-search")).to_be_visible()
        page.locator("#device-search").fill("weiter filtern")
        page.go_forward()
        expect(page.locator("[data-schedule-hint]")).to_be_visible()
    assert page.evaluate("window.lazyDocument === document")
    for name in ("page_scripts", "device_filter", "schedule"):
        expect(page.locator(f'head script[src*="/{name}.js"]')).to_have_count(1)
    expect(page.locator('body script[src*="/static/"]')).to_have_count(0)
    expect(page.locator('script[src*="assignment.js"]')).to_have_count(0)


def test_server_update_replaces_the_head_before_boosted_markup_is_used(
    admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    with live_server.session() as session:
        # See the comment in the test above: /devices only shows #device-search
        # once a device is on record, and this test must not depend on one
        # having been left behind by an earlier test.
        seed.create_temperature_device(session, "lazy-loader-server-update")
        session.commit()
    page.evaluate("window.oldAssetDocument = true")

    def updated_response(route: Route) -> None:
        response = route.fetch()
        headers = {**response.headers, "x-thermoctl-assets": "updated-server"}
        route.fulfill(response=response, headers=headers)

    page.route("**/devices", updated_response)
    _boost(page, "/devices")
    expect(page.locator("#device-search")).to_be_visible()
    assert page.evaluate("window.oldAssetDocument === undefined")
