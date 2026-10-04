"""No visible word breaks mid-letter, and no horizontal page overflow, at 390x844.

`test_admin_table_layout.py` checks this for three specific tables that were
reported broken. This test sweeps every Anlagensicht view in
`tools/screenshot_views.py` -- the same inventory and the same demo data
`tools/screenshots.py` uses for the documentation gallery -- so a layout that
breaks on a page nobody thought to name explicitly still fails here instead of
surfacing only when someone happens to open that page on a phone.

The global `overflow-wrap: anywhere` (`thermoctl.css`) is the deliberate last
resort against a genuinely unbreakable, freely chosen name overrunning its box.
It is not a substitute for giving a layout enough room; when it fires on an
ordinary label or heading, that is the bug this test catches.
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

# Views whose path is only meaningful with the seeded demo's real IDs
# (`{zone_id}`, `{mode_id}`, `{point_id}`, ...) get them from `seed_demo`'s
# return value via `str.format_map`, exactly like `tools/screenshots.py` does.
_ANLAGE_VIEWS = [view for view in VIEWS if view.profile == "anlage" and view.kiosk_mode is None]
assert len(_ANLAGE_VIEWS) >= 20, "Ansichtenliste unerwartet kurz -- Filter geprüft?"

# `.tc-zone-track` auf der Startseite ist eine absichtlich horizontal
# angelegte Tagesspur (siehe `start.html`); anders als die scrollenden
# Wrapper anderswo (`.table-responsive`, `.tc-schedule-scroll`,
# `.tc-code-block`) ist sie nicht selbst der Scrollcontainer, sondern der
# Grund, warum diese eine Seite von der Dokument-Überlaufprüfung ausgenommen
# bleibt -- wie in der Aufgabenstellung benannt.
_OVERFLOW_EXCEPTIONS = {"/"}

# Wörtlich zu kopierende Blöcke (Homebridge-Konfiguration, Audit-Detail-JSON):
# lange, unzerteilte Kennungen und Zeitstempel sind dort Inhalt, keine
# Beschriftung, und liegen ohnehin in einem eigenen scrollenden Rahmen.
_WORD_WRAP_EXEMPT_SELECTOR = "pre, code"

_GEOMETRY = r"""(exemptSelector) => {
    const failures = [];
    const visible = el => el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        const node = walker.currentNode, parent = node.parentElement;
        if (!parent || !visible(parent)) continue;
        if (parent.closest('option, select')) continue;
        if (parent.closest('details:not([open])') && !parent.closest('summary')) continue;
        if (parent.closest(exemptSelector)) continue;
        const head = parent.closest('thead');
        if (head && head.getBoundingClientRect().height <= 1) continue;
        for (const match of node.textContent.matchAll(/[\p{L}\p{N}]+/gu)) {
            // A single character can never itself be split across lines.
            if (match[0].length < 2) continue;
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + match[0].length);
            const rects = [...range.getClientRects()].filter(r => r.width && r.height);
            if (!rects.length) continue;
            if (new Set(rects.map(r => Math.round(r.top))).size > 1) {
                const tag = parent.tagName.toLowerCase();
                const cls = parent.className ? '.' + parent.className.toString().split(' ')[0] : '';
                failures.push(`Wortumbruch: "${match[0]}" in <${tag}${cls}>`);
            }
        }
    }
    return [...new Set(failures)];
}"""


@pytest.fixture(scope="module")
def demo_server() -> Iterator[tuple[LiveServer, dict[str, str | int]]]:
    # Isolated from the shared suite: other tests must not change this data
    # while a whole-module sweep is under way. `LiveServer` is a frozen
    # dataclass -- the seeded paths travel alongside it in a tuple instead of
    # a bolted-on attribute.
    with closing(_live_server("", admin_username="demo-verwaltung")) as servers:
        server = next(servers)
        with server.session() as session:
            paths = seed_demo(session, server.admin_password)
        yield server, paths


@pytest.fixture
def demo_page(
    browser: Browser, demo_server: tuple[LiveServer, dict[str, str | int]]
) -> Iterator[Page]:
    server, _ = demo_server
    with browser.new_context(
        base_url=server.base_url,
        viewport=_VIEWPORT,
        locale="de-DE",
        timezone_id="Europe/Berlin",
    ) as context:
        page = context.new_page()
        _login(page, server.admin_username, server.admin_password)
        yield page


@pytest.mark.parametrize("view", _ANLAGE_VIEWS, ids=[v.stem for v in _ANLAGE_VIEWS])
def test_no_word_splits_or_horizontal_overflow_at_phone_width(
    demo_page: Page,
    demo_server: tuple[LiveServer, dict[str, str | int]],
    view: View,
) -> None:
    _, paths = demo_server
    target = view.path.format_map(paths)
    response = demo_page.goto(target, wait_until="networkidle")
    assert response is not None and response.status < 400, (
        f"{target}: HTTP {response.status if response else 'ohne Antwort'}"
    )
    demo_page.evaluate("document.fonts.ready")
    demo_page.locator(".htmx-request").wait_for(state="detached")

    failures = demo_page.evaluate(_GEOMETRY, _WORD_WRAP_EXEMPT_SELECTOR)
    assert not failures, f"{target}:\n" + "\n".join(failures)

    if view.path not in _OVERFLOW_EXCEPTIONS:
        overflow = demo_page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 1, f"{target}: Seite {overflow}px breiter als der Bildschirm"


_DESKTOP_VIEWPORT = {"width": 1280, "height": 900}

# Bewusst lange Protokollinhalte: ein Rohcode als Ergebnis einer Notbetriebs-
# entscheidung (ohne Anzeigebezeichnung, nur mit Unterstrichen), ein langer
# Satz als Begründung, ein langer Fehlertext mit Pfad und ein Fehlercode ohne
# jede Trennstelle als letzte Rückfalloption.
_LONG_CODE = "sensorausfall_rueckkehr_quellenwechsel"
_LONG_REASON = (
    "Notbetriebstakt läuft weiter, aktuelle Ein-Phase für 600 s, Taktquelle "
    "Festtakt, keine Außentemperaturmessung, Übergabe an den Thermostat "
    "wurde bewusst nicht gesendet, weil der Sollwert bereits erreicht ist."
)
_LONG_ERROR = (
    "Zeitüberschreitung beim Zugriff auf "
    "http://geraet.example.invalid/api/v1/geraete/wohnzimmer/heizkoerper/sollwert"
    "?versuch=3&modus=komfort nach 30 Sekunden"
)
_UNBROKEN_ERROR = "ERR_" + "X" * 60


def _seed_long_log_rows(server: LiveServer) -> None:
    """Fügt Protokollzeilen mit überlangem Ergebnis, Fehlertext und Begründung ein.

    Eigene Zeilen statt Demo-Daten: Die Doku-Bilder bleiben inhaltlich unberührt.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from tests.helpers import create_device_command
    from thermoctl.db.base import utcnow
    from thermoctl.db.models.device import Device
    from thermoctl.db.models.sensor_failure import ActuatorDecision
    from thermoctl.db.models.zone import Zone

    with server.session() as session:
        existing = select(ActuatorDecision).where(ActuatorDecision.reason_code == _LONG_CODE)
        if session.scalar(existing):
            return
        zone = session.scalars(select(Zone).order_by(Zone.id)).first()
        device = session.scalars(select(Device).order_by(Device.id)).first()
        assert zone is not None and device is not None
        now: datetime = utcnow()
        for index, error in enumerate((_LONG_ERROR, _UNBROKEN_ERROR)):
            entry = create_device_command(
                session, zone, device, at=now - timedelta(minutes=index),
                outcome_code="failed",
            )
            entry.reason = _LONG_REASON
            entry.error = error
        session.add(
            ActuatorDecision(
                episode_id=None, zone_device_id=None, zone_name=zone.display_name,
                device_name=device.display_name, decided_at=now - timedelta(minutes=2),
                action="no_write", reason_code=_LONG_CODE, reason=_LONG_REASON,
                phase=None, phase_deadline_at=None, simulated=False, cycle_source=None,
                on_seconds=None, off_seconds=None, outdoor_c=None, profile_version=1,
            )
        )
        session.commit()


def test_command_log_has_no_word_splits_or_overlapping_cells_at_desktop_width(
    browser: Browser, demo_server: tuple[LiveServer, dict[str, str | int]]
) -> None:
    """Schaltprotokoll bei 1280px: die Spalte "Art" darf Zeitpunkt, Quelle, Ergebnis
    und Begründung weder überlagern noch zu Umbrüchen mitten im Wort zwingen."""
    server, _ = demo_server
    _seed_long_log_rows(server)
    with browser.new_context(
        base_url=server.base_url,
        viewport=_DESKTOP_VIEWPORT,
        locale="de-DE",
        timezone_id="Europe/Berlin",
    ) as context:
        page = context.new_page()
        _login(page, server.admin_username, server.admin_password)
        page.goto("/device-commands", wait_until="networkidle")
        page.evaluate("document.fonts.ready")

        failures = page.evaluate(_GEOMETRY, _WORD_WRAP_EXEMPT_SELECTOR)
        # Der trennstellenlose Fehlercode ist der dokumentierte Notfall: kein
        # Leerzeichen, keine Trennmarke -- dort darf `break-word` mitten im
        # "Wort" umbrechen, solange nichts überläuft (unten geprüft).
        failures = [line for line in failures if "XXXX" not in line]
        assert not failures, "\n".join(failures)

        cut_off = page.evaluate(
            """() => [...document.querySelectorAll('.tc-command-table tbody td')]
                .filter(td => td.scrollWidth > td.clientWidth + 1)
                .map(td => td.dataset.label || td.textContent.trim().slice(0, 20))"""
        )
        assert not cut_off, f"Zellen laufen über ihre Spalte hinaus: {cut_off}"
        scroll = page.evaluate(
            "() => { const w = document.querySelector('.table-responsive');"
            " return w.scrollWidth - w.clientWidth; }"
        )
        assert scroll <= 1, f"Tabelle scrollt bei 1280px um {scroll}px"
