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
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, FloatRect, Page, expect
from sqlalchemy import select

from browser_tests import seed
from browser_tests.conftest import LiveServer, _record_console_error
from tests.helpers import sensor_status_of
from thermoctl.auth.tokens import resolve_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.remote_control import set_setpoint
from thermoctl.domain.schedule import create_override

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
        zone = seed.create_constant_schedule_zone(session, zone_name)
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
        # Scoped to the tile, not the whole page: since the panel view, the zone's
        # name also sits in its (invisible, until opened) detail layer further down
        # the same document (kiosk.html) -- `get_by_text` on the whole page would
        # match both and fail on ambiguity.
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


# --- Panel-Ansicht: 480×480 und andere Wandformate ----------------------------------
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
            seed.create_constant_schedule_zone(session, f"{prefix}-{n}").id
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
    *, width: int, height: int, install_clock: bool = False,
) -> tuple[Page, list[str]]:
    """`install_clock=True` installs Playwright's fake clock *before*
    navigation, not after. htmx registers its own `every 20s` poll through the
    real `setInterval` the moment the page first loads; installing the clock
    only after `goto()` leaves that already-running interval real and ticking
    in actual wall-clock time, immune to `page.clock.run_for()` -- a first
    version of the timeout tests below did exactly that and silently verified
    nothing about polling (zero real polls fired during `run_for(46_000)`, and
    the detail still closed on schedule, because only `kiosk_panel.js`'s own
    45s timer, started after `install()`, was fake).
    """
    context = browser.new_context(
        base_url=live_server.base_url, color_scheme="light",
        viewport={"width": width, "height": height},
    )
    errors: list[str] = []
    page = context.new_page()
    page.on("console", lambda message: _record_console_error(errors, message))
    if install_clock:
        page.clock.install()
    page.goto(f"/kiosk/{plaintext}")
    expect(page).to_have_url(re.compile(r"/kiosk$"))
    return page, errors


def _start_override(live_server: LiveServer, zone_id: int, plaintext: str) -> None:
    """Overrides one zone so it actually shows an "Übersteuerung
    aufheben" button to measure -- without one, that button never renders
    (`kiosk_views.py::_dashboard`, `cancel_zone_ids`/`running_overrides`) and its
    tap target size goes untested along with it, same as boost's own button would
    without a schedule. No expiry: a layout test must not lose its cancel button
    because the next schedule point happens to fall during the test.
    """
    with live_server.session() as session:
        token = resolve_token(session, plaintext)
        assert token is not None
        zone = session.get(Zone, zone_id)
        assert zone is not None
        create_override(
            session, zone, Decimal("21.0"), None,
            token_id=token.id, source="kiosk",
        )
        session.commit()


def _visible_tap_targets(page: Page) -> list[FloatRect]:
    """Bounding boxes of every visible button-like element -- the tile itself
    included (`role="button"` in the panel overview).

    Deliberately not every clickable element: the AGPL source link in the
    heading is excluded on purpose (see the comment on `.kiosk-source-link` in
    thermoctl.css) -- it is not a control of the plant, and forcing it to 44 px
    would have cost the grid the vertical space six zones need to fit without
    scrolling. The 44 px guarantee below covers this page's actual controls
    (tiles, buttons), not incidental links.
    """
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
    """Checks the exact effect, not just "some text changed": +0.5°C on the one
    zone touched (`THERMOSTAT_STEP`, kiosk_views.py), and the five neighbours
    left exactly as they were -- a review found the previous version of this
    test would have missed a wrong-zone regression as long as the display text
    changed at all, in either direction, by any amount.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelSollwert")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        tiles = page.locator("[data-kiosk-tile]")
        first_tile = tiles.first
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        vorher_je_kachel = {
            tile.get_attribute("data-kiosk-tile"): tile.locator(".kiosk-tile-summary").inner_text()
            for tile in tiles.all()
        }
        assert vorher_je_kachel[zone_id] == "Soll 21,0 °C", vorher_je_kachel

        first_tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        setpoint_value = detail.locator(".kiosk-setpoint-detail .t-value")
        expect(setpoint_value).to_have_text("21,0 °C")
        detail.get_by_label("Sollwert anheben").click()

        # Genau +0,5 °C (THERMOSTAT_STEP) -- nicht nur "irgendein anderer Text".
        expect(setpoint_value).to_have_text("21,5 °C")
        # Die eigentliche Behauptung dieses Tests: das Formular ersetzt per htmx nur
        # `#kiosk-body`, statt die Seite neu zu laden -- der Detailbereich bleibt
        # deshalb derselbe, offene DOM-Knoten, nicht bloß "wieder" offen.
        expect(detail).to_be_visible()
        expect(detail).to_have_class(re.compile(r"\bkiosk-detail-open\b"))

        nachher_je_kachel = {
            tile.get_attribute("data-kiosk-tile"): tile.locator(".kiosk-tile-summary").inner_text()
            for tile in page.locator("[data-kiosk-tile]").all()
        }
        assert nachher_je_kachel[zone_id] == "Soll 21,5 °C", nachher_je_kachel
        for andere_zone_id, text in nachher_je_kachel.items():
            if andere_zone_id == zone_id:
                continue
            assert text == vorher_je_kachel[andere_zone_id], (
                f"Zone {andere_zone_id} hat sich mitverändert: {text!r}"
            )
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
        # Genau die Bedienelemente von vor der Panel-Ansicht, direkt in der Kachel --
        # kein Antippen nötig, um an sie heranzukommen.
        expect(first_tile.get_by_label("Sollwert anheben")).to_be_visible()
        expect(first_tile.get_by_label("Sollwert senken")).to_be_visible()
        zone_id = first_tile.get_attribute("data-kiosk-tile")
        expect(page.locator(f'[data-kiosk-detail="{zone_id}"]')).not_to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


# --- Regressionen aus dem Kreuzreview -----------------------------------------------


def test_the_auto_breakpoint_is_exclusive_at_exactly_600px(
    live_server: LiveServer, browser: Browser
) -> None:
    """Die Vorgabe lautet "automatisch nach Bildschirmbreite ... ab 600 px die
    Tafel-Ansicht" -- ein Review maß, dass `max-width: 600px`
    (`kiosk_panel.js`) 600 px selbst noch ins Panel einschloss. Prüft genau die
    Grenze, nicht nur deutlich kleinere/größere Breiten wie die übrigen Tests.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelGrenze")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=600, height=480)
    try:
        expect(page.locator(".kiosk-page")).to_have_attribute("data-ansicht-aktiv", "tafel")
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_the_inactivity_timeout_fires_even_while_the_page_keeps_polling(
    live_server: LiveServer, browser: Browser
) -> None:
    """Ein Review reproduzierte das in einem echten Browser: Detail öffnen, 67 s
    nichts tun, während die eigene Selbstaktualisierung der Seite alle 20 s
    (`hx-trigger="every 20s"`) weiterläuft -- das Detail schloss sich nie.
    `kiosk_panel.js` stellte den DOM-Zustand nach jedem Tausch bisher über
    dieselbe Funktion wieder her, die auch ein echtes Öffnen benutzt -- die
    startet den 45-s-Timer jedes Mal neu. `page.clock` spult virtuelle Zeit vor
    (und löst dabei die echten 20-s-Abrufe tatsächlich aus), statt 45+ echte
    Sekunden lang stumpf zu warten.

    Zählt die tatsächlichen Abrufe mit (`response`-Ereignis), statt sich auf
    die Behauptung im Kommentar zu verlassen: Eine erste Fassung dieses Tests
    installierte die Uhr erst nach `page.goto()` -- htmx registriert seinen
    Intervall-Timer aber schon beim ersten Laden über die *echten*
    Timer-Funktionen, und eine danach installierte Uhr fälscht einen bereits
    laufenden `setInterval` nicht rückwirkend. Der Test bestand damals trotzdem
    (der eigene, danach gestartete 45-s-Timer von `kiosk_panel.js` war ja
    tatsächlich gefälscht), maß aber am eigentlichen Verhalten -- ob ein Poll
    dazwischenfunkt -- vorbei: null gezählte Abrufe. Ein unabhängiges Review hat
    das nachgewiesen.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelTimeoutPoll")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    polls: list[str] = []

    page, errors = _open_panel_kiosk(
        browser, live_server, plaintext, width=480, height=480, install_clock=True
    )
    page.on(
        "response",
        lambda response: polls.append(response.url)
        if response.request.method == "GET" and response.url.rstrip("/").endswith("/kiosk")
        else None,
    )
    try:
        page.wait_for_load_state("networkidle")
        tile = page.locator("[data-kiosk-tile]").first
        zone_id = tile.get_attribute("data-kiosk-tile")
        tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        polls.clear()
        # Zwei Schritte zu 20 s -- an jedem ist die Selbstaktualisierung fällig;
        # `wait_for_timeout` danach gibt der wirklich losgeschickten, echten
        # Anfrage Zeit für Antwort und Tausch, bevor der nächste Schritt kommt.
        for _ in range(2):
            page.clock.run_for(20_000)
            page.wait_for_timeout(200)
        assert len(polls) >= 2, polls
        # 40 s seit dem Öffnen, unter den 45 s -- trotz zweier Abrufe noch offen.
        expect(detail).to_be_visible()

        page.clock.run_for(10_000)  # 50 s insgesamt
        expect(detail).not_to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_a_real_interaction_shortly_before_the_timeout_extends_it(
    live_server: LiveServer, browser: Browser
) -> None:
    """Die andere Hälfte derselben Behebung: Ein Abruf der Selbstaktualisierung
    darf den Rücksprung nicht verlängern, eine echte Bedienung (hier: den
    Sollwert anheben -- tauscht `#kiosk-body` ebenfalls per htmx) aber schon.
    Übt den `pointerdown`-Zurücksetzer in `kiosk_panel.js` zusammen mit dem
    htmx-getriebenen Wiederherstellen aus, nicht isoliert.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelTimeoutVerlaengern")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(
        browser, live_server, plaintext, width=480, height=480, install_clock=True
    )
    try:
        # htmx verdrahtet die Formulare der eingangs gerenderten Seite synchron
        # beim Laden, unabhängig von der gefälschten Uhr -- unter Last (viele
        # Browsertests hintereinander) reichte das aber nicht immer aus, bevor
        # gleich darauf tatsächlich geklickt wird: eine noch nicht verdrahtete
        # Form fällt auf eine gewöhnliche, volle Navigation zurück, die den
        # gesamten JavaScript-Zustand dieser Seite (samt gefälschter Uhr)
        # verwirft. `networkidle` gibt dem Aufbau Zeit, bevor der Test lostippt.
        page.wait_for_load_state("networkidle")
        tile = page.locator("[data-kiosk-tile]").first
        zone_id = tile.get_attribute("data-kiosk-tile")
        tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        page.clock.run_for(40_000)  # kurz vor dem 45-s-Ablauf
        expect(detail).to_be_visible()
        detail.get_by_label("Sollwert anheben").click()  # echte Bedienung (Maus)
        expect(detail).to_be_visible()

        # 80 s seit dem Öffnen, aber nur 40 s seit der Bedienung -- noch offen.
        page.clock.run_for(40_000)
        expect(detail).to_be_visible()

        # 50 s seit der Bedienung -- jetzt schließt es.
        page.clock.run_for(10_000)
        expect(detail).not_to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_a_real_keyboard_interaction_shortly_before_the_timeout_extends_it(
    live_server: LiveServer, browser: Browser
) -> None:
    """Ein Review fand: Nur `pointerdown` zählte im Detailbereich als Bedienung.
    Eine Tastaturaktivierung (Eingabetaste auf einem fokussierten Knopf) löst
    kein `pointerdown` aus -- seit die Wiederherstellung nach einem Tausch den
    Timer korrekterweise nicht mehr neu startet, schloss das Detail dadurch
    schon rund fünf Sekunden nach einer erfolgreichen Tastaturbedienung
    (unabhängig mit echter Uhr nachgemessen: Enter bei 41 s, zu bei 46 s).
    """
    zone_ids = _seed_panel_zones(live_server, "PanelTimeoutTastatur")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(
        browser, live_server, plaintext, width=480, height=480, install_clock=True
    )
    try:
        page.wait_for_load_state("networkidle")  # siehe Begründung im Test oberhalb
        tile = page.locator("[data-kiosk-tile]").first
        zone_id = tile.get_attribute("data-kiosk-tile")
        tile.click()
        detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
        expect(detail).to_be_visible()

        page.clock.run_for(40_000)  # kurz vor dem 45-s-Ablauf
        expect(detail).to_be_visible()
        plus = detail.get_by_label("Sollwert anheben")
        plus.focus()
        page.keyboard.press("Enter")  # echte Bedienung (Tastatur, kein Klick)
        expect(detail).to_be_visible()

        # 80 s seit dem Öffnen, aber nur 40 s seit der Tastaturbedienung.
        page.clock.run_for(40_000)
        expect(detail).to_be_visible()

        # 50 s seit der Bedienung -- jetzt schließt es.
        page.clock.run_for(10_000)
        expect(detail).not_to_be_visible()
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_every_detail_control_stays_within_480px_with_an_error_and_a_sensor_warning(
    live_server: LiveServer, browser: Browser
) -> None:
    """Ein Review fand: Mit einem serverseitig abgelehnten Sollwert (Fehlertext),
    einem Sensor-Warnhinweis und einer laufenden Übersteuerung zusammen ragte
    "Übersteuerung aufheben" bis zu 20 px unter den sichtbaren Rand, und der
    Statuschip überlagerte Raumname und Zurück-Knopf -- mit Bildschirmfoto
    belegt. Bloße Sichtbarkeits-/Größenprüfungen (wie im Übersichtstest oben)
    hätten das nicht gefangen: Ein abgeschnittenes oder überlagertes Element
    bleibt trotzdem `is_visible()` und behält seine volle Größe. Dieser Test
    prüft stattdessen die Lage im Sichtfeld.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelUeberlauf")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)
    override_zone_id = zone_ids[0]
    _start_override(live_server, override_zone_id, plaintext)
    with live_server.session() as session:
        # `sensor_stuck` wird nur berechnet, während `sensor_status_id` "ok"
        # liest (siehe der Kommentar am Feld, thermoctl/db/models/state.py) --
        # beides zusammen mit einer eigenen, nicht "ok" lautenden Warnung ist ein
        # Zustand, den die Anlage selbst nie erzeugt. Der Sensor-Chip hier ist
        # deshalb genau der eine Warnhinweis, den ein echter Betrieb zeigen
        # könnte, nicht ein zweiter, unerreichbarer obendrauf.
        session.add(
            ZoneState(
                zone_id=override_zone_id, sensor_status_id=sensor_status_of(session, "ok").id,
                updated_at=utcnow(), sensor_stuck=True,
            )
        )
        # Knapp unter der oberen Grenze (-20..35 °C,
        # thermoctl/domain/remote_control.py) vorbelegt, statt sie über
        # Dutzende Klicks in der Oberfläche zu erklimmen: Ein Klick je Sekunde
        # Testlaufzeit wird schnell länger als die 20-s-Selbstaktualisierung der
        # Seite, und deren nächster, fälliger Abruf (ohne die Fehler-Abfrage in
        # seiner URL) überschreibt dann den gerade erst angekommenen Fehlertext
        # wieder -- ein Testartefakt, keine Verhaltensänderung an der Anlage.
        zone = session.get(Zone, override_zone_id)
        assert zone is not None
        token = resolve_token(session, plaintext)
        assert token is not None
        set_setpoint(
            session, zone, Decimal("34.5"), utcnow(), token_id=token.id, source="kiosk"
        )
        session.commit()

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        page.locator(f'[data-kiosk-tile="{override_zone_id}"]').click()
        detail = page.locator(f'[data-kiosk-detail="{override_zone_id}"]')
        expect(detail).to_be_visible()

        # 34,5 → 35,0 (noch erlaubt) → 35,5 (abgelehnt).
        raise_button = detail.get_by_label("Sollwert anheben")
        raise_button.click()
        raise_button.click()
        expect(detail.locator(".kiosk-error")).to_be_visible()
        expect(detail.get_by_text("Messwert unverändert")).to_be_visible()

        for control in (
            detail.locator("[data-kiosk-back]"),
            detail.get_by_label("Sollwert senken"),
            raise_button,
            detail.get_by_role("button", name="Nächste Schaltung vorziehen"),
            detail.get_by_role("button", name="Übersteuerung aufheben"),
        ):
            box = control.bounding_box()
            assert box is not None
            assert box["y"] >= 0, box
            assert box["y"] + box["height"] <= 480, box

        # Der feste Kopf (Name/Zurück) wird nie überlagert: Der Rumpf mit dem
        # übrigen Inhalt beginnt erst, wo der Kopf endet, nicht darüber.
        head_box = detail.locator(".kiosk-detail-head").bounding_box()
        body_box = detail.locator(".kiosk-detail-body").bounding_box()
        assert head_box is not None and body_box is not None
        assert body_box["y"] >= head_box["y"] + head_box["height"] - 1, (head_box, body_box)
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_a_double_tap_on_raise_is_locked_out_instead_of_racing(
    live_server: LiveServer, browser: Browser
) -> None:
    """Kreuzreview-Befund: Die Formulare (Sollwert, Boost, Übersteuerung
    aufheben) tauschten `#kiosk-body` komplett aus (`hx-swap="outerHTML"`), und
    nichts hinderte einen zweiten Tipp auf "Sollwert anheben" daran, den Knopf
    noch vor dem Austausch ein zweites Mal zu treffen. Nachgestellt mit zwei
    `HTMLElement.click()`-Aufrufen auf denselben Knoten ohne jede Verzögerung
    dazwischen (`page.evaluate`, nicht `Locator.click()` -- das wartet vor
    jedem Klick auf Aktivierbarkeit und wäre damit selbst schon eine
    Verzögerung, die den Fehler verdeckt): Zuverlässig in allen 15 Versuchen
    landete der zweite Tipp folgenlos, ohne Fehlermeldung, bei 34,5 → 35,0 --
    exakt der gemeldete Befund ("blieb bei 35,0 statt 35,5-Ablehnung"). Unter
    CPU-Drosselung bleibt das Bild gleich; sie ist hier zusätzlich gesetzt, um
    das schmale Zeitfenster zwischen Klick und einer etwaigen asynchronen
    Deaktivierung real zu dehnen.

    Entscheidung: Am Wandtablett soll kein Tipp *zu einem falschen Endwert*
    führen, aber ein Tipp, der während einer laufenden Anfrage eintrifft,
    darf sichtbar ins Leere greifen -- besser ein bewusst gesperrter, klar
    erkennbarer Knopf als ein leise verschluckter, dabei unbemerkt in der
    Reihenfolge vertauschter Tipp. Die Behebung sperrt deshalb den Knopf für
    die Dauer der eigenen Anfrage (`hx-disabled-elt="find fieldset"`,
    `kiosk.html`, Bootstraps `fieldset:disabled .btn` liefert die sichtbare
    Sperre ohne eigenes CSS); ein `HTMLButtonElement.click()` auf einen
    deaktivierten Knopf ist für den Browser wirkungslos, `hx-sync="this:queue
    first"` sichert zusätzlich ab, falls doch einmal zwei Anfragen von
    demselben Formular entstehen. Ergebnis: derselbe Doppelklick endet
    deterministisch bei genau einem angewandten Schritt (34,5 → 35,0, keine
    Fehlermeldung -- der zweite, gesperrte Tipp fand schlicht nicht statt),
    nie bei einem falschen oder vertauschten Wert. Wichtig ist der zweite
    Teil dieses Tests: Ein *echter*, späterer Tipp (nachdem die Anfrage
    durchgelaufen und der Knopf wieder frei ist) wirkt weiterhin ganz normal
    -- die Sperre ist vorübergehend, kein dauerhaft verlorener Tipp.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelDoppeltipp")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)
    zone_id = zone_ids[0]
    with live_server.session() as session:
        token = resolve_token(session, plaintext)
        assert token is not None
        zone = session.get(Zone, zone_id)
        assert zone is not None
        set_setpoint(session, zone, Decimal("34.5"), utcnow(), token_id=token.id, source="kiosk")
        session.commit()

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=480, height=480)
    try:
        cdp = page.context.new_cdp_session(page)
        # 20-fach: dehnt ein etwaiges schmales Zeitfenster zwischen dem ersten
        # Klick und der (in `htmx` tatsächlich synchronen) Deaktivierung --
        # ändert am deterministischen Ergebnis nichts, ist aber die im Auftrag
        # verlangte Reproduktion über `Emulation.setCPUThrottlingRate`.
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 20})
        try:
            page.locator(f'[data-kiosk-tile="{zone_id}"]').click()
            detail = page.locator(f'[data-kiosk-detail="{zone_id}"]')
            expect(detail).to_be_visible()

            wert = detail.locator(".kiosk-setpoint-detail .t-value")
            fehler = detail.locator(".kiosk-error")

            # Zwei Tipps ohne jede Verzögerung dazwischen -- siehe Docstring,
            # warum das ausdrücklich nicht über `Locator.click()` zweimal
            # geschieht.
            page.evaluate(
                """() => {
                    const knopf = document.querySelector(
                        '[data-kiosk-detail] [aria-label="Sollwert anheben"]');
                    knopf.click();
                    knopf.click();
                }"""
            )
            expect(wert).to_have_text("35,0 °C")
            expect(fehler).not_to_be_visible()

            # Der Knopf ist nur vorübergehend gesperrt: ein echter, späterer
            # Tipp wirkt wieder ganz normal und stößt den zweiten,
            # tatsächlichen Schritt an -- der landet über der Obergrenze
            # (`MAXIMUM_TEMPERATURE_C`, `domain/modes.py`) und wird abgelehnt.
            detail.get_by_label("Sollwert anheben").click()
            expect(fehler).to_be_visible()
            expect(wert).to_have_text("35,0 °C")
        finally:
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


def test_enter_and_space_on_the_plus_button_still_work_in_the_tafel_view(
    live_server: LiveServer, browser: Browser
) -> None:
    """Ein Review fand: Der `keydown`-Behandler an der Kachel (`kiosk_panel.js`)
    fing auch Ereignisse ab, die von den Formularknöpfen in der Kachel
    hochstiegen, und rief bei Eingabetaste/Leertaste immer `preventDefault()`
    auf -- die Prüfung auf Panel-Modus kam erst danach. Folge: In der
    Tafel-Ansicht (1024×600 und breiter, unverändert seit vor der
    Panel-Ansicht) lösten Eingabetaste und Leertaste auf dem fokussierten
    "+"-Knopf nichts mehr aus, per Maus schon.
    """
    zone_ids = _seed_panel_zones(live_server, "PanelTastatur")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)

    page, errors = _open_panel_kiosk(browser, live_server, plaintext, width=1024, height=600)
    try:
        expect(page.locator(".kiosk-page")).to_have_attribute("data-ansicht-aktiv", "tafel")
        first_tile = page.locator("[data-kiosk-tile]").first
        plus = first_tile.get_by_label("Sollwert anheben")
        setpoint_value = first_tile.locator(".kiosk-setpoint .t-value")
        expect(setpoint_value).to_have_text("21,0 °C")

        plus.focus()
        page.keyboard.press("Enter")
        expect(setpoint_value).to_have_text("21,5 °C")

        plus.focus()
        page.keyboard.press(" ")
        expect(setpoint_value).to_have_text("22,0 °C")
    finally:
        assert not errors, "Kiosk-Konsole meldete Fehler:\n" + "\n".join(errors)
        page.context.close()


@pytest.mark.parametrize("mode,width,height,detail", [
    ("tafel", 1440, 900, False), ("tafel", 390, 844, False),
    ("panel", 480, 480, False), ("panel", 390, 844, False),
    ("panel", 480, 480, True), ("panel", 390, 844, True),
])
def test_kiosk_has_no_wordmark_and_keeps_source_link_visible(
    page: Page, live_server: LiveServer, mode: str, width: int, height: int, detail: bool,
) -> None:
    zone_ids = _seed_panel_zones(live_server, prefix=f"Logo-{mode}-{width}-{detail}")
    plaintext = _issue_kiosk_token_for_zones(live_server, zone_ids)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"/kiosk/{plaintext}")
    page.goto(f"/kiosk?ansicht={mode}")
    expect(page.locator("body")).to_have_attribute("data-ansicht-aktiv", mode)
    expect(page.locator(".kiosk-clock")).to_be_visible()
    scope = page.locator(".kiosk-heading")
    if detail:
        page.locator("[data-kiosk-tile]").first.click()
        scope = page.get_by_role("dialog")
        expect(scope).to_be_visible()
    source = scope.get_by_role("link", name="Quelltext (AGPL-3.0)", exact=True)
    expect(source).to_be_visible()
    expect(source).to_be_in_viewport()
    expect(source).to_have_attribute("href", "https://github.com/MagicalWig34653/thermoctl")
    source.click(trial=True)  # Also detects an overlay intercepting the link.
    expect(page.get_by_text("thermoctl", exact=True)).to_have_count(0)
    expect(page.locator(".tc-marker, .kiosk-marker")).to_have_count(0)
