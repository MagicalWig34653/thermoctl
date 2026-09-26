"""Visible form fields must have room for their content at phone width.

Anlass: `/controllers` und `/zones/{id}/devices` legten mehrere Auswahlfelder in
einer Bootstrap-`.row` mit bloßen `.col`/`.col-auto`-Kindern nebeneinander --
ohne eigene Breitenklasse hebt `.col` Bootstraps Vorgabe ("jedes Zeilenkind ohne
Breitenklasse bekommt volle Breite") bei *jeder* Bildschirmbreite auf, nicht nur
ab einem Umbruchpunkt. Bei 390px blieb den Auswahlfeldern dadurch nur ein
Bruchteil ihrer nötigen Breite, und ihr ausgewählter Text zeigte sich
abgeschnitten ("Sollwe", "Tei", "Qu", "Zo", "Näcr" -- je nach Feld). `select`s
Text lässt sich nicht zuverlässig über `scrollWidth`/`clientWidth` prüfen (ein
`<select>` clippt seinen Inhalt intern, ohne dass der äußere `scrollWidth`
mitwächst); die Breite selbst ist das verlässliche Kriterium.

Wie `test_form_hygiene.py`: eine isolierte Instanz mit den Doku-Demodaten, ein
Durchlauf über jede eindeutige Anlagensicht-Route aus `tools/screenshot_views.py`.
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

_VIEWPORT = {"width": 390, "height": 844}

# 8rem ist großzügig unter jedem echten Feldinhalt in diesem Programm
# (Zonennamen, Gerätenamen, "Sollwert der Zone" & Co.) und weit über dem, was
# eine `.col`/`.col-auto`-Kollision übrig lässt (dort blieben 50-100px) --
# schlägt also bei einer echten Kollision zuverlässig an, ohne ein absichtlich
# kompaktes Feld zu treffen.
_MINDESTBREITE_REM = 8.0

# Native Datums-/Uhrzeit-Steuerelemente zeigen ein festes, nie abschneidendes
# Format (z. B. "12.03.2026" oder "06:30") -- ihr Inhalt kann bei keiner Breite
# fragmentieren, anders als der frei lange Text eines `<select>` oder
# `<input type="text">`. Farbwähler und Bereichsregler haben aus demselben
# Grund keinen Text, der abschneiden könnte. Deshalb bewusst nicht Teil dieser
# Prüfung -- eine Mindestbreite dort wäre eine Regel ohne zugehöriges Risiko.
_TYP_AUSNAHME = {"date", "time", "datetime-local", "month", "week", "color", "range"}

_GEOMETRY = r"""(mindestbreitePx) => {
    const failures = [];
    const visible = el => el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden' && el.offsetParent !== null;
    const felder = [...document.querySelectorAll('select, input')].filter(el => {
        if (!visible(el)) return false;
        if (el.tagName === 'INPUT') {
            const typ = (el.getAttribute('type') || 'text').toLowerCase();
            const ausgenommeneTypen = new Set([
                'hidden', 'checkbox', 'radio', 'submit', 'button', 'reset', 'file',
                'date', 'time', 'datetime-local', 'month', 'week', 'color', 'range',
            ]);
            if (ausgenommeneTypen.has(typ)) return false;
        }
        if (el.closest('details:not([open])')) return false;
        return true;
    });
    for (const feld of felder) {
        const box = feld.getBoundingClientRect();
        if (box.width < mindestbreitePx) {
            const label = feld.name || feld.id || feld.tagName.toLowerCase();
            failures.push(`${label}: ${Math.round(box.width)}px`);
        }
    }
    return [...new Set(failures)];
}"""


def _anlage_views() -> list[View]:
    """Eine Route je eindeutigem Pfad -- sechs Zonen, die sich `/zones/{zone_id}`
    teilen, sollen nicht sechsmal dieselbe Vorlage besuchen."""
    seen: set[str] = set()
    result = []
    for view in VIEWS:
        if view.profile != "anlage" or view.kiosk_mode is not None:
            continue
        key = view.route or view.path
        if key in seen:
            continue
        seen.add(key)
        result.append(view)
    return result


_ANLAGE_VIEWS = _anlage_views()
assert len(_ANLAGE_VIEWS) >= 20, "Ansichtenliste unerwartet kurz -- Filter geprüft?"


@pytest.fixture(scope="module")
def breite_server() -> Iterator[tuple[LiveServer, dict[str, str | int]]]:
    with closing(_live_server("", admin_username="demo-verwaltung")) as servers:
        server = next(servers)
        with server.session() as session:
            paths = seed_demo(session, server.admin_password)
        yield server, paths


@pytest.fixture
def breite_page(
    browser: Browser, breite_server: tuple[LiveServer, dict[str, str | int]]
) -> Iterator[Page]:
    server, _ = breite_server
    with browser.new_context(
        base_url=server.base_url,
        viewport=_VIEWPORT,
        locale="de-DE",
        timezone_id="Europe/Berlin",
    ) as context:
        page = context.new_page()
        _login(page, server.admin_username, server.admin_password)
        yield page


@pytest.mark.parametrize("view", _ANLAGE_VIEWS, ids=[v.route or v.path for v in _ANLAGE_VIEWS])
def test_visible_form_fields_have_room_for_their_content_at_phone_width(
    breite_page: Page,
    breite_server: tuple[LiveServer, dict[str, str | int]],
    view: View,
) -> None:
    _, paths = breite_server
    target = view.path.format_map(paths)
    response = breite_page.goto(target, wait_until="networkidle")
    assert response is not None and response.status < 400, (
        f"{target}: HTTP {response.status if response else 'ohne Antwort'}"
    )
    for detail in breite_page.locator("details").all():
        if detail.get_attribute("open") is None:
            detail.locator("summary").click()
    breite_page.locator(".htmx-request").wait_for(state="detached")

    root_px = breite_page.evaluate(
        "parseFloat(getComputedStyle(document.documentElement).fontSize)"
    )
    failures = breite_page.evaluate(_GEOMETRY, _MINDESTBREITE_REM * root_px)
    assert not failures, f"{target}: Feld(er) zu schmal bei 390px:\n" + "\n".join(failures)
