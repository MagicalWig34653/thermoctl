"""Vorschau in der Personenverwaltung im echten Browser: Navigation, Rechte und Übersicht
eines anderen Kontos, nur zur Ansicht."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests.conftest import LiveServer
from browser_tests.seed import create_login_tenant_user

pytestmark = pytest.mark.browser


def test_admin_previews_the_interface_of_a_tenant_account(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        create_login_tenant_user(
            session,
            "vorschau-ziel",
            "Ziel-Kennwort-123",
            [("zone.read", None), ("device.read", None)],
        )
        session.commit()
    admin_page.goto("/app/personen/rechte")
    row = admin_page.locator('[data-testid="user-row"][data-username="vorschau-ziel"]')
    row.get_by_test_id("user-preview-open").click()

    body = admin_page.get_by_test_id("user-preview")
    expect(body).to_be_visible()
    expect(admin_page.get_by_test_id("preview-banner")).to_contain_text("Vorschau – nur Ansicht")
    # Navigation als Baum, ohne Links: Das Mieterprofil sperrt die Geräte nicht,
    # das Recht öffnet sie.
    nav = admin_page.get_by_test_id("preview-nav")
    expect(nav).to_contain_text("Geräte")
    expect(nav).not_to_contain_text("Personen & Zugänge")
    assert nav.locator("a").count() == 0
    # Rechte als lesbare Liste.
    expect(admin_page.get_by_test_id("preview-rights")).not_to_be_empty()
    # Übersicht der Person, nicht bedienbar.
    expect(admin_page.get_by_test_id("preview-dashboard")).to_be_visible()
    expect(admin_page.get_by_test_id("edit-layout")).to_have_count(0)

    admin_page.get_by_test_id("preview-close").click()
    expect(body).to_have_count(0)
    # Der Administrator ist weiterhin er selbst.
    expect(admin_page.get_by_test_id("user-list")).to_be_visible()
