"""Rendered geometry of the documentation's real demo data, not CSS declarations."""

from collections.abc import Iterator
from contextlib import closing

import pytest
from playwright.sync_api import Browser, Page

from browser_tests.live_server import LiveServer, _live_server
from tools.screenshot_seed import seed_demo
from tools.screenshots import _login

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def demo_server() -> Iterator[LiveServer]:
    # Isolated from the shared suite: other tests must not change these examples.
    with closing(_live_server("", admin_username="demo-verwaltung")) as servers:
        server = next(servers)
        with server.session() as session:
            seed_demo(session, server.admin_password)
        yield server


@pytest.fixture
def demo_page(browser: Browser, demo_server: LiveServer) -> Iterator[Page]:
    with browser.new_context(
        base_url=demo_server.base_url, locale="de-DE", timezone_id="Europe/Berlin"
    ) as context:
        page = context.new_page()
        _login(page, demo_server.admin_username, demo_server.admin_password)
        yield page


# Range rectangles expose painted text, including overflow from a perfectly
# non-overlapping pair of table-cell boxes. Check words separately so wrapping
# at spaces remains allowed, while "Wohnzimm / er" fails.
_GEOMETRY = r"""root => {
    const failures = [];
    const visible = el => el.getClientRects().length &&
        getComputedStyle(el).visibility !== 'hidden';
    const contains = (outer, inner) => inner.left >= outer.left - 1 &&
        inner.right <= outer.right + 1 && inner.top >= outer.top - 1 &&
        inner.bottom <= outer.bottom + 1;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        const node = walker.currentNode, parent = node.parentElement;
        if (!visible(parent) || parent.closest('option, select')) continue;
        if (parent.closest('details:not([open])') && !parent.closest('summary')) continue;
        const head = parent.closest('thead');
        if (head && head.getBoundingClientRect().height <= 1) continue;
        const cell = parent.closest('td, th');
        for (const match of node.textContent.matchAll(/[\p{L}\p{N}]+/gu)) {
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + match[0].length);
            const rects = [...range.getClientRects()].filter(r => r.width && r.height);
            if (!rects.length) continue; // e.g. closed details
            if (new Set(rects.map(r => Math.round(r.top))).size > 1)
                failures.push(`Wortumbruch: ${match[0]}`);
            if (cell && rects.some(r => !contains(cell.getBoundingClientRect(), r)))
                failures.push(`Text außerhalb der Zelle: ${match[0]}`);
        }
    }
    for (const row of root.querySelectorAll('tbody tr')) {
        const cells = [...row.cells].filter(visible);
        for (let i = 1; i < cells.length; i++) {
            const a = cells[i-1].getBoundingClientRect(), b = cells[i].getBoundingClientRect();
            if (Math.min(a.right,b.right)-Math.max(a.left,b.left) > 1 &&
                Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top) > 1)
                failures.push('Nachbarzellen schneiden sich');
        }
    }
    for (const el of root.querySelectorAll('.tc-chip, button, select')) {
        if (!visible(el)) continue;
        const box = el.getBoundingClientRect();
        const cell = el.closest('td') || el.parentElement;
        if (!contains(cell.getBoundingClientRect(), box))
            failures.push(`Element außerhalb: ${el.textContent.trim().slice(0,80)}`);
    }
    if (document.documentElement.scrollWidth > innerWidth)
        failures.push('Seite breiter als Bildschirm');
    return [...new Set(failures)];
}"""


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("route, selector", [
    ("/device-commands", ".tc-command-table"),
    ("/users", "#user-list table"),
    ("/devices", "#device-list"),
])
def test_demo_table_content_stays_inside_cells_without_split_words(
    demo_page: Page, width: int, route: str, selector: str
) -> None:
    demo_page.set_viewport_size({"width": width, "height": 900})
    demo_page.goto(route)
    demo_page.evaluate("document.fonts.ready")
    root = demo_page.locator(selector)
    assert root.locator("tbody tr").count() >= 3
    failures = root.evaluate(_GEOMETRY)
    assert not failures, "\n".join(failures)


def test_group_choices_fit_at_documentation_width(demo_page: Page) -> None:
    # The documented image has CLOSED editors. Check the actual opened controls
    # as well, without manufacturing a failure by changing demo data or CSS.
    demo_page.set_viewport_size({"width": 1280, "height": 900})
    demo_page.goto("/groups")
    editors = demo_page.locator(".tc-permission-editor")
    assert editors.count() >= 3
    for editor in editors.all():
        editor.locator("summary").click()
        assert editor.locator(".tc-permission-zones input").count() > 0
        assert editor.evaluate(_GEOMETRY) == []
