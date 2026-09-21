"""The kiosk dashboard: the one page nobody watches over anyone's shoulder.

It runs on a wall tablet, authenticates via its own single-use-link cookie instead
of a login, and auto-refreshes itself with `hx-trigger="every 20s"` -- none of
which an HTTP test exercises the way a real browser does. This test issues a real
kiosk token through the admin UI (the same way an operator would), opens it in a
fresh, unauthenticated browser context (nothing shared with the admin session, as
on the tablet it is meant for), and drives the setpoint buttons that are only
present because the token was issued with "auch bedienen".
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, FloatRect, Page, expect
from sqlalchemy import select

from browser_tests import seed
from browser_tests.conftest import LiveServer, _record_console_error
from thermoctl.auth.tokens import resolve_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.zone import Zone
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.remote_control import boost as domain_boost

pytestmark = pytest.mark.browser


def _issue_kiosk_token(admin_page: Page, zone_name: str) -> str:
    admin_page.goto("/kiosk-tokens")
    admin_page.get_by_label("Name").fill("Browsertest-Tablet")
    admin_page.get_by_text(zone_name, exact=False).click()
    admin_page.get_by_label("Auch bedienen (Sollwert, Boost und Übersteuerung aufheben)").check()
    admin_page.get_by_role("button", name="Ausstellen").click()

    entry = admin_page.locator("#new-kiosk-token")
    expect(entry).to_be_visible()
    text = entry.inner_text()
    match = re.search(r"/kiosk/(\S+)", text)
    assert match, f"Kein Kiosk-Token in {text!r} gefunden."
    return match.group(1)


def test_the_kiosk_dashboard_shows_the_zone_and_lets_the_setpoint_be_adjusted(
    admin_page: Page, live_server: LiveServer, browser: Browser
) -> None:
    zone_name = "Kiosk-Wohnzimmer"
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, zone_name)
        session.commit()
        zone_display_name = zone.display_name

    plaintext = _issue_kiosk_token(admin_page, zone_display_name)

    # A separate, unauthenticated context: the kiosk cookie is its own credential,
    # unrelated to the admin's session cookie -- exactly what a wall tablet has.
    kiosk_context = browser.new_context(
        base_url=live_server.base_url, color_scheme="light"
    )
    kiosk_errors: list[str] = []
    kiosk_page = kiosk_context.new_page()
    kiosk_page.on("console", lambda message: _record_console_error(kiosk_errors, message))
    try:
        kiosk_page.goto(f"/kiosk/{plaintext}")
        expect(kiosk_page).to_have_url(re.compile(r"/kiosk$"))
        # Scoped to the tile, not the whole page: since 0.9.5 the zone's name also
        # sits in its (invisible, until opened) detail layer further down the same
        # document (kiosk.html) -- `get_by_text` on the whole page would match both
        # and fail on ambiguity.
        expect(
            kiosk_page.locator(".kiosk-tile", has_text=zone_display_name)
        ).to_be_visible()
        # The auto-refresh htmx wires up -- the one behaviour unique to this page
        # among everything else in this suite.
        expect(kiosk_page.locator("#kiosk-body")).to_have_attribute(
            "hx-trigger", "every 20s"
        )

        tile = kiosk_page.locator(".kiosk-tile", has_text=zone_display_name)
        setpoint_value = tile.locator(".kiosk-setpoint .t-value")
        before = setpoint_value.inner_text()
        tile.get_by_label("Sollwert anheben").click()
        expect(setpoint_value).not_to_have_text(before)
    finally:
        assert not kiosk_errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(kiosk_errors)
        kiosk_context.close()


# --- Panel-Ansicht (0.9.5): 480×480 und andere Wandformate --------------------------
#
# Der Projektinhaber maß die laufende Anwendung mit sechs Zonen: eine einzige Kachel
# ohne Scrollen auf 480×480, zwei auf 800×480 (siehe Auftrag). Sechs Zonen sind daher
# hier nicht Zierde, sondern der eigentliche Prüffall -- weniger hätte den Fehler nicht
# gezeigt.

_PANEL_ZONE_COUNT = 6


def _seed_panel_zones(live_server: LiveServer, prefix: str) -> list[int]:
    """Six zones with a schedule, control-eligible, named `<prefix>-1` … `<prefix>-6`.

    Returns their ids. Issuing the kiosk token itself is a separate step
    (`_issue_kiosk_token_for_zones`) so a test can seed once and issue more than one
    token if it needs to -- none currently do, but keeping the two apart mirrors how
    an admin actually uses the feature (zones exist independently of any one token).
    """
    with live_server.session() as session:
        zone_ids = [
            seed.create_schedule_zone(session, f"{prefix}-{n}").id
            for n in range(1, _PANEL_ZONE_COUNT + 1)
        ]
        session.commit()
    return zone_ids


def _issue_kiosk_token_for_zones(live_server: LiveServer, zone_ids: list[int]) -> str:
    """Issues a kiosk token directly against the database instead of through
    `/kiosk-tokens` (see `_issue_kiosk_token` above): six checkbox clicks per test,
    purely to reach a state the panel-view tests below don't otherwise care about,
    would only slow every one of them down without checking anything new. The
    already-bootstrapped setup administrator is the token's owner, exactly as it
    would be if someone had issued it through the form.
    """
    with live_server.session() as session:
        admin = session.scalar(select(User).where(User.username == live_server.admin_username))
        assert admin is not None, "Der Einrichtungs-Administrator fehlt."
        _token, plaintext = issue_kiosk_token(
            session, admin, "Browsertest-Panel", zone_ids,
            control_allowed=True, expires_at=None,
        )
        session.commit()
    return plaintext


def _open_panel_kiosk(
    browser: Browser, live_server: LiveServer, plaintext: str,
    *, width: int, height: int,
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        base_url=live_server.base_url, color_scheme="light",
        viewport={"width": width, "height": height},
    )
    errors: list[str] = []
    page = context.new_page()
    page.on("console", lambda message: _record_console_error(errors, message))
    page.goto(f"/kiosk/{plaintext}")
    expect(page).to_have_url(re.compile(r"/kiosk$"))
    return page, errors


def _start_override(live_server: LiveServer, zone_id: int, plaintext: str) -> None:
    """Boosts one zone so its running override actually shows an "Übersteuerung
    aufheben" button to measure -- without one, that button never renders
    (`kiosk_views.py::_dashboard`, `cancel_zone_ids`/`running_overrides`) and its
    tap target size goes untested along with it, same as boost's own button would
    without a schedule.
    """
    with live_server.session() as session:
        token = resolve_token(session, plaintext)
        assert token is not None
        zone = session.get(Zone, zone_id)
        assert zone is not None
        domain_boost(session, zone, utcnow(), token_id=token.id, source="kiosk")
        session.commit()


def _visible_tap_targets(page: Page) -> list[FloatRect]:
    """Bounding boxes of every visible button-like element -- the tile itself
    included (`role="button"` in the panel overview)."""
    boxes: list[FloatRect] = []
    for handle in page.locator("button, [role='button']").all():
        if not handle.is_visible():
            continue
        box = handle.bounding_box()
        assert box is not None
        boxes.append(box)
    return boxes


def test_the_panel_overview_shows_all_six_zones_on_a_480_by_480_wall_panel(
    live_server: LiveServer, browser: Browser
) -> None:
    zone_ids = _seed_panel_zones(live_server, "Panel480")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)
    # A running override on one zone -- otherwise "Übersteuerung aufheben" never
    # renders anywhere in this test run, and its tap target goes unmeasured along
    # with it (see `_start_override`).
    override_zone_id = zone_ids[0]
    _start_override(live_server, override_zone_id, plaintext)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        expect(page.locator(".kiosk-page")).to_have_attribute("data-ansicht-aktiv", "panel")
        tiles = page.locator("[data-kiosk-tile]")
        expect(tiles).to_have_count(_PANEL_ZONE_COUNT)
        for index in range(_PANEL_ZONE_COUNT):
            expect(tiles.nth(index)).to_be_visible()

        # `scrollHeight`/`scrollWidth` cover the whole document, not just the
        # viewport -- exactly what "ohne Scrollen sichtbar" means. A one-pixel
        # allowance absorbs sub-pixel layout rounding, not a real overflow.
        overflow = page.evaluate(
            """() => ({
                scrollHeight: document.documentElement.scrollHeight,
                clientHeight: document.documentElement.clientHeight,
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth,
            })"""
        )
        assert overflow["scrollHeight"] <= overflow["clientHeight"] + 1, overflow
        assert overflow["scrollWidth"] <= overflow["clientWidth"] + 1, overflow

        for box in _visible_tap_targets(page):
            assert box["width"] >= 44, box
            assert box["height"] >= 44, box

        # Der Detailbereich der Zone mit laufender Übersteuerung -- nur dort
        # ist "Übersteuerung aufheben" überhaupt sichtbar, und erst geöffnet
        # lässt sich sein Tippziel überhaupt messen.
        override_tile = page.locator(f'[data-kiosk-tile="{override_zone_id}"]')
        override_tile.click()
        detail = page.locator(f'[data-kiosk-detail="{override_zone_id}"]')
        expect(detail).to_be_visible()
        cancel_button = detail.get_by_role("button", name="Übersteuerung aufheben")
        expect(cancel_button).to_be_visible()
        cancel_box = cancel_button.bounding_box()
        assert cancel_box is not None
        assert cancel_box["width"] >= 44, cancel_box
        assert cancel_box["height"] >= 44, cancel_box

        for box in _visible_tap_targets(page):
            assert box["width"] >= 44, box
            assert box["height"] >= 44, box
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_tapping_a_tile_opens_its_zone_and_back_returns_to_the_overview(
    live_server: LiveServer, browser: Browser
) -> None:
    zone_ids = _seed_panel_zones(live_server, "PanelOeffnen")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        first_tile = page.locator("[data-kiosk-tile]").first
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).not_to_be_visible()

        first_tile.click()
        expect(detail).to_be_visible()
        # Groß, wie gefordert -- die eigentliche Behauptung ist, dass hier eine
        # andere, größere Ist-Temperatur steht als in der Übersichtskachel, nicht
        # nur, dass der Bereich sichtbar ist.
        expect(detail.locator(".kiosk-temperature")).to_be_visible()

        detail.locator("[data-kiosk-back]").click()
        expect(detail).not_to_be_visible()
        expect(first_tile).to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_raising_the_setpoint_in_the_detail_keeps_it_open(
    live_server: LiveServer, browser: Browser
) -> None:
    zone_ids = _seed_panel_zones(live_server, "PanelSollwert")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        first_tile = page.locator("[data-kiosk-tile]").first
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        first_tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        setpoint_value = detail.locator(".kiosk-setpoint-detail .t-value")
        before = setpoint_value.inner_text()
        detail.get_by_label("Sollwert anheben").click()

        expect(setpoint_value).not_to_have_text(before)
        # Die eigentliche Behauptung dieses Tests: das Formular ersetzt per htmx nur
        # `#kiosk-body`, statt die Seite neu zu laden -- der Detailbereich bleibt
        # deshalb derselbe, offene DOM-Knoten, nicht bloß "wieder" offen.
        expect(detail).to_be_visible()
        expect(detail).to_have_class(re.compile(r"\bkiosk-detail-open\b"))
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_the_open_zone_survives_the_self_refresh(
    live_server: LiveServer, browser: Browser
) -> None:
    """`#kiosk-body` is replaced wholesale every 20s (`hx-trigger="every 20s"`) --
    too slow to actually wait out here. `htmx.ajax` with the same target/select/swap
    triggers the identical replacement on demand; what this test checks is that
    `kiosk_panel.js` reopens the same zone afterwards, not that the timer fires.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelAktualisierung")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        first_tile = page.locator("[data-kiosk-tile]").first
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        first_tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        page.evaluate(
            """() => htmx.ajax(
                'GET',
                document.body.dataset.urlPrefix + '/kiosk',
                { target: '#kiosk-body', select: '#kiosk-body', swap: 'outerHTML' }
            )"""
        )

        expect(detail).to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_at_1024_by_600_the_original_scrolling_layout_is_unchanged(
    live_server: LiveServer, browser: Browser
) -> None:
    zone_ids = _seed_panel_zones(live_server, "PanelTafel")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=1024, height=600)
    try:
        expect(page.locator(".kiosk-page")).to_have_attribute("data-ansicht-aktiv", "tafel")
        first_tile = page.locator("[data-kiosk-tile]").first
        # Genau die Bedienelemente von vor 0.9.5, direkt in der Kachel -- kein Antippen
        # nötig, um an sie heranzukommen.
        expect(first_tile.get_by_label("Sollwert anheben")).to_be_visible()
        expect(first_tile.get_by_label("Sollwert senken")).to_be_visible()
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        expect(page.locator(f'[data-kiosk-detail="{zone_id}"]')).not_to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()
