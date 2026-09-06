"""The start page's self-refresh (`#tc-live` in start.html).

Nothing here (auto-update of a changed value, survival of an open override area and
a started input across an unattended refresh, a loading bar that stays quiet for the
self-triggered poll) is observable through an HTTP test -- all three depend on real
timers, real DOM diffing (htmx's `hx-preserve`) and real CSS class state in a running
browser. `shadow_interval_seconds` is turned down to 1 second for every test here so
the suite does not have to wait out the production default of 60.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from decimal import Decimal

import pytest
from playwright.sync_api import Page, Route, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import sensor_status_of
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState

pytestmark = pytest.mark.browser


def _seed_zone_with_state(
    live_server: LiveServer, name: str, temperature_c: Decimal
) -> int:
    """A schedule zone with a known reading, and the shadow cycle turned down to
    1 second so the page's self-refresh (derived from that same setting, see
    `start_views.py`) fires quickly enough for a test to wait on it."""
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        status = sensor_status_of(session)
        session.add(
            ZoneState(
                zone_id=zone.id,
                sensor_status_id=status.id,
                temperature_c=temperature_c,
                updated_at=datetime(2026, 9, 4, 8, 0),
            )
        )
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 1
        session.commit()
        return zone.id


def test_the_poll_interval_is_derived_from_the_shadow_cycle_setting(
    admin_page: Page, live_server: LiveServer
) -> None:
    """Not a hard-coded number (unlike the kiosk's fixed `every 20s`): the interval
    on the page must be exactly the configured `shadow_interval_seconds`, because a
    faster poll would be wasted load for a value that provably cannot have changed
    yet, and a slower one would show a stale reading longer than necessary."""
    with live_server.session() as session:
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 37
        session.commit()

    admin_page.goto("/")
    expect(admin_page.locator("#tc-live")).to_have_attribute(
        "hx-trigger", "every 37s [!document.hidden]"
    )


def test_a_changed_reading_refreshes_without_any_user_action(
    admin_page: Page, live_server: LiveServer
) -> None:
    zone_id = _seed_zone_with_state(live_server, "wohnzimmer-live", Decimal("19.5"))

    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="wohnzimmer-live")
    reading = article.locator(".t-value.t-large")
    expect(reading).to_contain_text("19,5")

    # Changed from the outside -- nothing on the page does this itself -- while the
    # tab just sits there. Only the page's own poll can pick this up.
    with live_server.session() as session:
        state = session.get(ZoneState, zone_id)
        assert state is not None
        state.temperature_c = Decimal("22.0")
        session.commit()

    expect(reading).to_contain_text("22,0", timeout=4000)


def test_an_open_override_area_and_a_started_input_survive_a_refresh(
    admin_page: Page, live_server: LiveServer
) -> None:
    """The actual point of this task: an outerHTML swap of the whole live region
    must not throw away a form someone is in the middle of filling in, nor collapse
    a section they just opened -- both `hx-preserve`d in start.html."""
    zone_id = _seed_zone_with_state(live_server, "büro-live", Decimal("20.0"))

    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="büro-live")
    reading = article.locator(".t-value.t-large")
    expect(reading).to_contain_text("20,0")

    # `get_by_role("button", name="Übersteuern")` would be ambiguous -- the
    # (currently collapsed) form below has its own submit button with the same
    # text. The toggle is the only one with `data-bs-toggle`.
    toggle = article.locator('button[data-bs-toggle="collapse"]')
    toggle.click()
    temperature_input = article.locator('input[name="temperature_c"]')
    expect(temperature_input).to_be_visible()
    temperature_input.fill("23.5")

    # A real refresh, not just a wait: change something the page displays, then
    # prove it actually came through before asserting anything survived it -- an
    # untouched page would pass a "still open, still filled in" check trivially,
    # for the wrong reason.
    with live_server.session() as session:
        state = session.get(ZoneState, zone_id)
        assert state is not None
        state.temperature_c = Decimal("21.0")
        session.commit()
    expect(reading).to_contain_text("21,0", timeout=4000)

    expect(temperature_input).to_be_visible()
    expect(temperature_input).to_have_value("23.5")


def test_the_loading_bar_stays_quiet_for_the_self_refresh_even_when_it_is_slow(
    admin_page: Page, live_server: LiveServer
) -> None:
    """`test_loading_indicator.py` already proves the bar *does* appear for an
    ordinary slow request. This is the deliberate exception: a poll nobody is
    waiting on must stay quiet even under the exact same slowness."""
    with live_server.session() as session:
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.shadow_interval_seconds = 1
        session.commit()

    admin_page.goto("/")

    angefragt: list[str] = []

    def verzoegern(route: Route) -> None:
        angefragt.append(route.request.url)
        time.sleep(0.8)
        route.continue_()

    # Only the bare start-page poll, not every request the page could ever make.
    admin_page.route(re.compile(r"^https?://[^/]+/$"), verzoegern)

    admin_page.evaluate(
        """() => {
            window.__ladebeobachtung = [];
            window.__ladeIntervall = window.setInterval(() => {
                const balken = document.getElementById('tc-loading-bar');
                window.__ladebeobachtung.push(
                    balken.classList.contains('tc-loading-bar-sichtbar')
                );
            }, 30);
        }"""
    )
    # Comfortably past two 1-second poll cycles plus their artificial 800 ms delay.
    admin_page.wait_for_timeout(2500)
    admin_page.evaluate("window.clearInterval(window.__ladeIntervall)")

    assert angefragt, "Der Poll ist nie gefeuert -- der Test prüft nichts"
    beobachtungen = admin_page.evaluate("window.__ladebeobachtung")
    assert beobachtungen, "Beobachtungsintervall lief nie -- Test misst nichts"
    assert not any(beobachtungen), (
        "Ladebalken war während des selbstauslösenden Abrufs sichtbar -- er sollte "
        "dafür stumm bleiben (data-tc-quiet-poll)"
    )
