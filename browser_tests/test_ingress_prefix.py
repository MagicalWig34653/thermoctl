"""Die Oberfläche hinter `INGRESS_PREFIX` (Home-Assistant-Ingress) und direkt.

Die Fixtures (`conftest.py`) starten dieselbe Anwendung mit `THERMOCTL_ROOT_PATH` und
setzen `_ingress_proxy.py` davor, der den Präfix wie Home Assistant abschneidet. Geprüft
wird, was nur ein Browser zeigt: Alle Assets der Hülle laden unter dem Präfix (keine
404), Anmeldung, Abmeldung und Navigation bleiben unter dem Präfix, und derselbe laufende
Prozess antwortet gleichzeitig ohne Präfix.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, Response, expect

from browser_tests.conftest import INGRESS_PREFIX, LiveServer

pytestmark = pytest.mark.browser


def _collect_failed(page: Page) -> list[str]:
    failed: list[str] = []

    def on_response(response: Response) -> None:
        if response.status >= 400 and "/ui-api/" not in response.url:
            failed.append(f"{response.status} {response.url}")

    page.on("response", on_response)
    return failed


def test_legacy_path_and_assets_work_under_the_prefix(
    page_with_prefix: Page, live_server_with_prefix: LiveServer
) -> None:
    failed = _collect_failed(page_with_prefix)
    page_with_prefix.goto("zones")  # Altpfad: Umleitung unter den Präfix
    expect(page_with_prefix.get_by_label("Benutzername")).to_be_visible()
    expect(page_with_prefix).to_have_url(
        re.compile(re.escape(INGRESS_PREFIX) + r"/app/uebersicht/raeume")
    )
    assert failed == [], failed


def test_login_navigation_and_logout_stay_under_the_prefix(
    admin_page_with_prefix: Page,
) -> None:
    page = admin_page_with_prefix
    failed = _collect_failed(page)
    assert re.search(re.escape(INGRESS_PREFIX) + r"/app", page.url), page.url
    page.get_by_role("link", name="Geräte").first.click()
    expect(page).to_have_url(re.compile(re.escape(INGRESS_PREFIX) + r"/app/geraete"))
    page.get_by_text("Abmelden").first.click()
    expect(page.get_by_label("Benutzername")).to_be_visible()
    assert failed == [], failed


def test_direct_access_works_alongside_ingress_on_the_same_instance(
    admin_page_with_prefix: Page,
    admin_page_direct_via_prefixed_instance: Page,
) -> None:
    direct = admin_page_direct_via_prefixed_instance
    assert INGRESS_PREFIX not in direct.url
    direct.get_by_role("link", name="Geräte").first.click()
    expect(direct).to_have_url(re.compile(r"/app/geraete"))
    assert INGRESS_PREFIX not in direct.url
    assert INGRESS_PREFIX in admin_page_with_prefix.url
