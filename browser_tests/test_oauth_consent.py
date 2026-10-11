"""Zustimmungsseite im Browser mit echter Anmeldung und Preset-Wechsel."""

import pytest
from playwright.sync_api import Page, expect

from browser_tests.live_server import LiveServer
from thermoctl.domain.oauth import register_client, start_request

pytestmark = pytest.mark.browser


def test_consent_shows_client_and_changes_preset(admin_page: Page, live_server: LiveServer) -> None:
    with live_server.session() as session:
        client = register_client(session, "Browsertest-Client", ["https://client.example/callback"])
        request_id = start_request(
            session,
            client,
            "https://client.example/callback",
            "x" * 43,
            "https://mcp.example",
            None,
            "https://mcp.example",
        )
        session.commit()

    admin_page.goto(f"/oauth/consent?request={request_id}")
    expect(admin_page.get_by_text("Browsertest-Client")).to_be_visible()
    expect(admin_page.get_by_text("Selbst angemeldeter Client, nicht geprüft")).to_be_visible()
    expect(admin_page.locator('input[name="permission"][value="zone.read"]')).to_be_checked()
    admin_page.locator("#preset").select_option("read_write")
    admin_page.get_by_role("button", name="Vorauswahl übernehmen").click()
    expect(admin_page.locator('input[name="permission"][value="override.create"]')).to_be_checked()
