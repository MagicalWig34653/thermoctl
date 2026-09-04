"""The whole interface, run through `INGRESS_PREFIX` -- the Home Assistant Ingress case.

Every fixture here (`live_server_with_prefix`, `page_with_prefix`, `admin_page_with_prefix`
in `conftest.py`) is the same machinery the rest of this suite uses, started with
`THERMOCTL_ROOT_PATH` set and fronted by `browser_tests/_ingress_proxy.py` -- a small
stand-in for Home Assistant's own Ingress proxy that strips the prefix before the
request ever reaches thermoctl, exactly like the real thing. A bare `page.goto("/login")`
against `page_with_prefix` therefore actually crosses the proxy at
`.../api/hassio_ingress/A1b2C3d4e5/login`.

This file does not re-derive what already has full HTTP-level coverage in
`tests/test_ingress_prefix.py` (redirect targets, cookie `Path=`, rendered `href`
values as text). What only a real browser can show, and what this file checks
instead: that navigating the *rendered* prefixed links actually works, that htmx's
boosted forms and redirects land where the address bar shows, that the stylesheet
still applies, and that the schedule editor's pointer-driven drag -- the most
JavaScript-heavy surface in the project -- still reaches the server correctly
when every URL it builds at runtime carries a prefix neither `schedule.js` nor
`thermoctl.css` know about by name.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import INGRESS_PREFIX, LiveServer

pytestmark = pytest.mark.browser

_PREFIXED_URL = re.compile(re.escape(INGRESS_PREFIX) + r"(/.*)?$")


def test_login_reaches_the_dashboard_under_the_prefix(
    page_with_prefix: Page, live_server_with_prefix: LiveServer
) -> None:
    page_with_prefix.goto("login")
    assert page_with_prefix.url == f"{live_server_with_prefix.base_url}login"

    page_with_prefix.get_by_label("Benutzername").fill(live_server_with_prefix.admin_username)
    page_with_prefix.get_by_label("Passwort").fill(live_server_with_prefix.admin_password)
    page_with_prefix.get_by_role("button", name="Anmelden").click()

    expect(page_with_prefix.locator(".tc-head")).to_be_visible()
    expect(page_with_prefix).to_have_url(live_server_with_prefix.base_url)


def test_logout_leads_back_to_a_working_login_page_under_the_prefix(
    admin_page_with_prefix: Page,
) -> None:
    admin_page_with_prefix.get_by_role("button", name="Browsertest-Verwaltung").click()
    admin_page_with_prefix.get_by_role("button", name="Abmelden").click()

    expect(admin_page_with_prefix.get_by_label("Benutzername")).to_be_visible()
    expect(admin_page_with_prefix.get_by_label("Passwort")).to_be_visible()
    assert _PREFIXED_URL.search(admin_page_with_prefix.url)
    assert admin_page_with_prefix.url.endswith("/login")


def test_a_boosted_navigation_link_stays_under_the_prefix(admin_page_with_prefix: Page) -> None:
    # `hx-boost` on <body> intercepts this click and swaps the page in via
    # `pushState` -- an HTTP test never runs that path at all; it can only check
    # that the rendered `href` carries the prefix (`tests/test_ingress_prefix.py`),
    # not that the browser actually ends up there after clicking.
    admin_page_with_prefix.get_by_role("link", name="Zonen", exact=True).click()
    expect(admin_page_with_prefix.get_by_role("heading", name="Zonen")).to_be_visible()
    assert admin_page_with_prefix.url.endswith(f"{INGRESS_PREFIX}/zones")


def test_the_stylesheet_still_applies_under_the_prefix(page_with_prefix: Page) -> None:
    """The second historical incident (missing stylesheet, see `CLAUDE.md`) --
    checked again here because `href="{{ url_prefix }}/static/thermoctl.css"` is a
    different code path from the unprefixed one `test_stylesheet.py` already covers,
    and a wrong prefix computation would 404 exactly this link and silently fall
    back to Bootstrap's own colours.
    """
    page_with_prefix.goto("login")
    button = page_with_prefix.get_by_role("button", name="Anmelden")
    expect(button).to_be_visible()
    background = button.evaluate("el => getComputedStyle(el).backgroundColor")
    assert background == "rgb(47, 57, 65)", (
        f"Errechnete Hintergrundfarbe war {background!r} -- thermoctl.css scheint "
        "unter dem Präfix nicht geladen zu sein (Bootstraps Standardblau wäre "
        "rgb(13, 110, 253))."
    )


def test_kiosk_dashboard_works_end_to_end_under_the_prefix(
    admin_page_with_prefix: Page, live_server_with_prefix: LiveServer, browser
) -> None:
    with live_server_with_prefix.session() as session:
        zone = seed.create_schedule_zone(session, "Kiosk-unter-Präfix")
        session.commit()
        zone_display_name = zone.display_name

    admin_page_with_prefix.goto("kiosk-tokens")
    admin_page_with_prefix.get_by_label("Name").fill("Präfix-Tablet")
    admin_page_with_prefix.get_by_text(zone_display_name, exact=False).click()
    admin_page_with_prefix.get_by_role("button", name="Ausstellen").click()
    entry = admin_page_with_prefix.locator("#new-kiosk-token")
    expect(entry).to_be_visible()
    match = re.search(r"/kiosk/(\S+)", entry.inner_text())
    assert match, f"Kein Kiosk-Token in {entry.inner_text()!r} gefunden."
    plaintext = match.group(1)

    kiosk_context = browser.new_context(
        base_url=live_server_with_prefix.base_url, color_scheme="light"
    )
    kiosk_errors: list[str] = []
    from browser_tests.conftest import _record_console_error

    kiosk_page = kiosk_context.new_page()
    kiosk_page.on("console", lambda message: _record_console_error(kiosk_errors, message))
    try:
        kiosk_page.goto(f"kiosk/{plaintext}")
        expect(kiosk_page.get_by_text(zone_display_name)).to_be_visible()
        assert kiosk_page.url == f"{live_server_with_prefix.base_url}kiosk"
        assert not kiosk_errors, "Browserkonsole meldete Fehler:\n" + "\n".join(kiosk_errors)
    finally:
        kiosk_context.close()


@pytest.fixture
def schedule_zone_id_under_prefix(live_server_with_prefix: LiveServer) -> int:
    with live_server_with_prefix.session() as session:
        zone = seed.create_schedule_zone(session, "Zeitplan-unter-Präfix")
        session.commit()
        return zone.id


def _drag(page: Page, start: tuple[float, float], end: tuple[float, float]) -> None:
    # Identical to `test_schedule_editor.py`'s own `_drag` -- see there for why each
    # of these steps (the round trip after `down()`, several intermediate `steps`)
    # is needed for schedule.js to recognise this as a drag rather than a click.
    page.mouse.move(*start)
    page.mouse.down()
    page.evaluate("() => document.body.offsetHeight")
    page.mouse.move(*end, steps=8)
    page.evaluate("() => document.body.offsetHeight")
    page.mouse.up()


def test_a_pointer_drag_in_the_schedule_editor_reaches_the_server_under_the_prefix(
    admin_page_with_prefix: Page, schedule_zone_id_under_prefix: int
) -> None:
    """The most JavaScript-heavy surface in the project (see `test_schedule_editor.py`),
    run once under a prefix: `schedule.js` never mentions a path itself, but every
    `hx-post`/`hx-get` it drives is rendered by the server with `{{ url_prefix }}`
    already baked in (`thermoctl/web/templates/schedule.html`) -- a pointer gesture
    that fires against the wrong (un-prefixed) target would look identical to a
    passing test right up until htmx's request 404s, which only a real round trip
    through the DOM and the network can catch. Same gesture and the same assertions
    as `test_schedule_editor.py::test_painting_a_new_block_creates_it_...`, just
    against `admin_page_with_prefix` instead of `admin_page`.
    """
    admin_page_with_prefix.goto(f"zones/{schedule_zone_id_under_prefix}/schedule")

    mode_radios = admin_page_with_prefix.locator('input[name="paint_tool"][type="radio"]')
    chosen_radio = mode_radios.nth(3)
    chosen_id = chosen_radio.get_attribute("id")
    assert chosen_id
    admin_page_with_prefix.locator(f'label[for="{chosen_id}"]').click()
    expect(chosen_radio).to_be_checked()

    tuesday = admin_page_with_prefix.locator('.schedule-day[data-weekday="2"]')
    expect(tuesday.locator(".schedule-draggable")).to_have_count(0)

    box = tuesday.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    _drag(
        admin_page_with_prefix,
        (x, box["y"] + box["height"] * 0.2),
        (x, box["y"] + box["height"] * 0.3),
    )

    # The gesture ends in an htmx request; if it had gone to the wrong (un-prefixed)
    # URL it would 404 and the page would simply stop responding -- these two blocks
    # appearing is the actual proof the request landed on the real server, not just
    # that the click handler fired.
    expect(admin_page_with_prefix.locator("[data-gesture-error]")).to_have_count(0)
    tuesday = admin_page_with_prefix.locator('.schedule-day[data-weekday="2"]')
    expect(tuesday.locator(".schedule-draggable")).to_have_count(2)
    expect(admin_page_with_prefix.locator(f"#{chosen_id}")).to_be_checked()
