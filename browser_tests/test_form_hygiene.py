"""Generic sweep for two recurring complaints: duplicated 'Speichern' buttons or
checkboxes, and settings that vanish after a reload.

The project owner reported both "immer mal wieder doppelte Speichern-Knöpfe und
Checkboxen" and "Einstellungen, die nach Neuladen weg sind" without being able to
say which page. `tools/screenshot_views.py` already lists every GET view in both
the Anlage and the Wohnung profile (the demo data `tools/screenshot_seed.py`
creates covers both) -- this test walks that same list once and checks a property
every one of those pages should have, rather than any one page's specific content.

Isolated from the shared `live_server` fixture on purpose (see
`test_admin_table_layout.py` for the same reasoning): the demo data this test
seeds must not leak into, or be disturbed by, the rest of the suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing

import pytest
from playwright.sync_api import Browser, Page

from browser_tests.live_server import LiveServer, _live_server
from tools.screenshot_seed import seed_demo
from tools.screenshot_views import VIEWS, View
from tools.screenshots import _login

pytestmark = pytest.mark.browser

_PASSWORD = "Formularhygiene-9"  # noqa: S105 -- ephemeral local database only

# Checkbox/radio groups where the same `name` legitimately appears more than once
# -- a group of related switches, not a setting that was accidentally duplicated.
# Keyed by (profile, path) exactly as `View.route or View.path` identifies it. Empty
# for now: the JavaScript below already excludes every checkbox and radio before
# counting names (see `test_no_visible_field_with_the_same_name_appears_twice_in_one_form`),
# so `/kiosk-tokens`' repeated checkbox `zone_id` never reaches this list in the
# first place -- an entry for it here would be dead weight. Kept as the place to
# list a *non*-checkbox exception, should one turn up.
# `/settings`' Notbetriebs-Kennlinie (Auftrag 8a) is a real array of rows -- each
# `curve_outdoor_c`/`curve_on_seconds`/`curve_off_seconds` triple repeats once per
# row by design (`thermoctl/web/control_views.py::_parse_curve_points` reads them
# back with `form.getlist`), not the accidentally-duplicated single field the
# project owner reported. The module docstring's own closing sentence names this
# dict as the place for exactly this kind of non-checkbox exception.
_ALLOWED_NAME_DUPLICATES: dict[str, set[str]] = {
    "/settings": {"curve_outdoor_c", "curve_on_seconds", "curve_off_seconds"},
}

# `/controllers` deliberately puts one small "Speichern" form per device property
# in the same `.tc-panel` card -- as many as the device has readable/writable
# properties, each configuring a different channel. That is the page's actual
# design (see `thermoctl/web/templates/controllers.html`), not the duplicated
# control the project owner reported; a card here legitimately repeats the label.
# The tenant's `/schedule` is the same shape one level down: one card holds one
# independent per-weekday form, each with its own "Speichern"
# (`thermoctl/web/templates/tenant_schedule.html`).
_ALLOWED_REPEATED_SPEICHERN = {"/controllers", "/schedule"}

# Same exception, but for the *page-wide* sweep below (`test_at_most_one_visible_
# speichern_button_per_page`): this one does not stop at the innermost `.tc-panel`/
# `.card`/`section` the way `test_the_label_speichern_never_appears_twice_in_the_
# same_card` does, because the reported bug (v0.10.1) was exactly a case that test
# missed -- `parameter.html` had two separate, unheaded `<form>`s one after another
# with no shared `.tc-panel`/`.card`/`section` wrapper, so the per-card test saw two
# containers with one "Speichern" each and called it fine. The reasons for
# `/controllers` and the tenant `/schedule` above hold here too, unchanged.


def _unique_views() -> list[View]:
    """One representative per route: six zones sharing `/zones/{zone_id}` would
    otherwise mean visiting the identical template six times over."""
    seen: set[tuple[str, str]] = set()
    result = []
    for view in VIEWS:
        if view.profile not in ("anlage", "wohnung"):
            continue
        key = (view.profile, view.route or view.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(view)
    return result


@pytest.fixture(scope="module")
def hygiene_server() -> Iterator[tuple[LiveServer, dict[str, str | int]]]:
    with closing(_live_server("", admin_username="demo-verwaltung")) as servers:
        server = next(servers)
        with server.session() as session:
            paths = seed_demo(session, _PASSWORD)
        yield server, paths


@pytest.fixture
def hygiene_page(browser: Browser, hygiene_server: tuple[LiveServer, dict]) -> Iterator[Page]:
    server, _ = hygiene_server
    with browser.new_context(
        base_url=server.base_url, locale="de-DE", timezone_id="Europe/Berlin"
    ) as context:
        yield context.new_page()


@pytest.mark.parametrize("view", _unique_views(), ids=lambda v: f"{v.profile}-{v.route or v.path}")
def test_at_most_one_visible_submit_button_per_form_with_the_same_label(
    hygiene_page: Page, hygiene_server: tuple[LiveServer, dict], view: View
) -> None:
    page = hygiene_page
    server, paths = hygiene_server
    username = server.admin_username if view.profile == "anlage" else "demo-mieter"
    password = server.admin_password if view.profile == "anlage" else _PASSWORD
    _login(page, username, password)
    response = page.goto(view.path.format_map(paths), wait_until="networkidle")
    assert response is not None and response.status < 400
    for detail in page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    page.locator(".htmx-request").wait_for(state="detached")

    duplicate_buttons = page.evaluate(
        """() => {
        const problems = [];
        for (const form of document.querySelectorAll('form')) {
            const buttons = [...form.querySelectorAll(
                'button[type=submit], button:not([type]), input[type=submit]'
            )].filter(el => el.offsetParent !== null || el.getClientRects().length);
            const counts = new Map();
            for (const b of buttons) {
                const label = (b.value || b.textContent || '').trim();
                if (!label) continue;
                counts.set(label, (counts.get(label) || 0) + 1);
            }
            for (const [label, count] of counts) {
                if (count > 1) problems.push(`${label} x${count}`);
            }
        }
        return problems;
    }"""
    )
    assert not duplicate_buttons, (
        f"{view.profile}-{view.path}: mehrfacher gleichlautender Speichern-Knopf "
        f"im selben Formular: {duplicate_buttons}"
    )


@pytest.mark.parametrize("view", _unique_views(), ids=lambda v: f"{v.profile}-{v.route or v.path}")
def test_no_visible_field_with_the_same_name_appears_twice_in_one_form(
    hygiene_page: Page, hygiene_server: tuple[LiveServer, dict], view: View
) -> None:
    page = hygiene_page
    server, paths = hygiene_server
    username = server.admin_username if view.profile == "anlage" else "demo-mieter"
    password = server.admin_password if view.profile == "anlage" else _PASSWORD
    _login(page, username, password)
    response = page.goto(view.path.format_map(paths), wait_until="networkidle")
    assert response is not None and response.status < 400
    for detail in page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    page.locator(".htmx-request").wait_for(state="detached")

    allowed = _ALLOWED_NAME_DUPLICATES.get(view.route or view.path, set())
    duplicates = page.evaluate(
        """(allowed) => {
        const problems = [];
        for (const form of document.querySelectorAll('form')) {
            const counts = new Map();
            for (const field of form.querySelectorAll('input, select, textarea')) {
                if (!(field.offsetParent !== null || field.getClientRects().length)) continue;
                const skip = field.type === 'checkbox' || field.type === 'radio'
                    || field.type === 'hidden';
                if (skip) {
                    continue;
                }
                const name = field.name;
                if (!name) continue;
                counts.set(name, (counts.get(name) || 0) + 1);
            }
            for (const [name, count] of counts) {
                if (count > 1 && !allowed.includes(name)) problems.push(`${name} x${count}`);
            }
        }
        return problems;
    }""",
        list(allowed),
    )
    assert not duplicates, (
        f"{view.profile}-{view.path}: Eingabefeld mehrfach im selben Formular: {duplicates}"
    )


@pytest.mark.parametrize("view", _unique_views(), ids=lambda v: f"{v.profile}-{v.route or v.path}")
def test_the_label_speichern_never_appears_twice_in_the_same_card(
    hygiene_page: Page, hygiene_server: tuple[LiveServer, dict], view: View
) -> None:
    if (view.route or view.path) in _ALLOWED_REPEATED_SPEICHERN:
        pytest.skip("Mehrere unabhängige Speichern-Knöpfe je Karte sind hier das Layout")
    page = hygiene_page
    server, paths = hygiene_server
    username = server.admin_username if view.profile == "anlage" else "demo-mieter"
    password = server.admin_password if view.profile == "anlage" else _PASSWORD
    _login(page, username, password)
    response = page.goto(view.path.format_map(paths), wait_until="networkidle")
    assert response is not None and response.status < 400
    for detail in page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    page.locator(".htmx-request").wait_for(state="detached")

    duplicate_labels = page.evaluate(
        """() => {
        const containerSelector = '.tc-panel, .card, section';
        const problems = [];
        for (const container of document.querySelectorAll(containerSelector)) {
            // Only the innermost matching container "owns" a label -- otherwise
            // every ancestor container would double-count the same button.
            const nested = container.querySelector(containerSelector);
            if (nested) continue;
            let count = 0;
            for (const el of container.querySelectorAll('button, label, span, strong')) {
                if (!(el.offsetParent !== null || el.getClientRects().length)) continue;
                if (el.textContent.trim() === 'Speichern') count++;
            }
            if (count > 1) problems.push(container.className || container.tagName);
        }
        return problems;
    }"""
    )
    assert not duplicate_labels, (
        f"{view.profile}-{view.path}: Beschriftung 'Speichern' mehrfach im selben "
        f"Abschnitt: {duplicate_labels}"
    )


@pytest.mark.parametrize("view", _unique_views(), ids=lambda v: f"{v.profile}-{v.route or v.path}")
def test_at_most_one_visible_speichern_button_per_page(
    hygiene_page: Page, hygiene_server: tuple[LiveServer, dict], view: View
) -> None:
    """Whole-page version of the check above (v0.10.1 regression). The per-card
    check only ever compares elements inside the same innermost `.tc-panel`/`.card`/
    `section` -- `parameter.html` had exactly the pattern that slips past it: its
    old, separate window-detection `<form>` sat directly below the main one with no
    shared card wrapper, so each container had its own single "Speichern" and the
    per-card check saw nothing wrong. A user looking at the rendered page sees one
    settings area with two identical "Speichern" buttons regardless of the markup
    structure underneath -- this counts every visible one across the whole page.
    """
    if (view.route or view.path) in _ALLOWED_REPEATED_SPEICHERN:
        pytest.skip(
            "Mehrere unabhängige, klar überschriebene Speichern-Knöpfe sind hier "
            "das Layout (s. Begründung bei _ALLOWED_REPEATED_SPEICHERN)"
        )
    page = hygiene_page
    server, paths = hygiene_server
    username = server.admin_username if view.profile == "anlage" else "demo-mieter"
    password = server.admin_password if view.profile == "anlage" else _PASSWORD
    _login(page, username, password)
    response = page.goto(view.path.format_map(paths), wait_until="networkidle")
    assert response is not None and response.status < 400
    for detail in page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    page.locator(".htmx-request").wait_for(state="detached")

    count = page.evaluate(
        """() => {
        let count = 0;
        for (const el of document.querySelectorAll('button, input[type=submit]')) {
            if (!(el.offsetParent !== null || el.getClientRects().length)) continue;
            const label = (el.value || el.textContent || '').trim();
            if (label === 'Speichern') count++;
        }
        return count;
    }"""
    )
    assert count <= 1, (
        f"{view.profile}-{view.path}: {count} sichtbare 'Speichern'-Knöpfe auf einer "
        "Seite, die für den Nutzer nach einem zusammenhängenden Einstellungsbereich "
        "aussieht"
    )
