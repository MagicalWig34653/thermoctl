"""Kiosk-Einlösung und -Ansicht: das Wandtablett öffnet die Adresse mit dem Token."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, expect
from sqlalchemy import select

from browser_tests import seed
from browser_tests.conftest import LiveServer, _record_console_error
from thermoctl.db.models.identity import User
from thermoctl.domain.kiosk import issue_kiosk_token

pytestmark = pytest.mark.browser


def _issue(live_server: LiveServer, zone_name: str) -> str:
    with live_server.session() as session:
        zone = seed.create_constant_schedule_zone(session, zone_name)
        admin = session.scalar(select(User).where(User.username == live_server.admin_username))
        assert admin is not None
        _token, plaintext = issue_kiosk_token(
            session, admin, "Browsertest-Kiosk", [zone.id], control_allowed=True, expires_at=None
        )
        session.commit()
    return plaintext


def test_the_kiosk_link_sets_the_cookie_and_shows_the_zone(
    browser: Browser, live_server: LiveServer
) -> None:
    plaintext = _issue(live_server, "Kiosk-Raum")
    context = browser.new_context(base_url=live_server.base_url, color_scheme="light")
    errors: list[str] = []
    page = context.new_page()
    page.on("console", lambda message: _record_console_error(errors, message))
    page.goto(f"/kiosk/{plaintext}")
    expect(page).to_have_url(re.compile(r"/app/kiosk"))
    expect(page.get_by_text("Kiosk-Raum").first).to_be_visible()
    # Das Token steht nicht mehr in der Adresse.
    assert plaintext not in page.url
    context.close()
    assert not errors, errors


def test_an_invalid_kiosk_link_shows_the_plain_notice(
    browser: Browser, live_server: LiveServer
) -> None:
    context = browser.new_context(base_url=live_server.base_url, color_scheme="light")
    page = context.new_page()
    response = page.goto("/kiosk/gibt-es-nicht")
    assert response is not None and response.status == 401
    expect(page.get_by_text("Dieser Zugang ist nicht mehr gültig")).to_be_visible()
    context.close()
