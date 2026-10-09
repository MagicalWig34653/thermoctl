"""Route inventory and actual CLI output, deliberately in the local browser suite."""

import os
import struct
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from browser_tests.live_server import LiveServer
from tests.helpers import alle_api_routen
from thermoctl.app import create_app
from thermoctl.config import get_settings
from tools.screenshot_views import EXCLUDED_ROUTES, VIEWS

pytestmark = pytest.mark.browser


def test_every_web_get_route_has_a_capture_or_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in os.environ:
        if key.startswith("THERMOCTL_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("THERMOCTL_ENV_FILE", "")
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "s" * 32)
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()
    routes = {
        route.path
        for route in alle_api_routen(app)
        if "GET" in (route.methods or set())
        and route.endpoint.__module__.startswith("thermoctl.web.")
    }
    covered = {view.route or view.path for view in VIEWS}
    assert not routes - covered - EXCLUDED_ROUTES.keys(), "Neue Ansicht ohne Screenshot"
    assert not covered - routes, "Ansichtenliste enthält nicht mehr vorhandene Routen"
    assert not EXCLUDED_ROUTES.keys() - routes, "Veraltete Ausschlüsse"
    assert not covered & EXCLUDED_ROUTES.keys(), "Aufgenommene Route gleichzeitig ausgeschlossen"
    assert all(reason.strip() for reason in EXCLUDED_ROUTES.values())
    assert len({view.stem for view in VIEWS}) == len(VIEWS)


def test_cli_filter_produces_real_pngs(tmp_path: Path) -> None:
    # Both profiles and setup use separate runs: catches ignored filters, setup
    # captured after bootstrap, missing tenant permissions and mobile sizing.
    for pattern, stems in (
        ("oeffentlich-*", {"oeffentlich-einrichtung", "oeffentlich-anmeldung"}),
        ("wohnung-wochenplan", {"wohnung-wochenplan", "wohnung-wochenplan-mobil"}),
        (
            "anlage-startseite",
            {"anlage-startseite", "anlage-startseite-2", "anlage-startseite-mobil"},
        ),
        (
            "wohnung-konto",
            {"wohnung-konto", "wohnung-konto-mobil", "wohnung-konto-mobil-2"},
        ),
        (
            "anlage-*-notbetrieb",
            {"anlage-einstellungen-notbetrieb", "anlage-bad-parameter-notbetrieb"},
        ),
        (
            "kiosk-*",
            {"kiosk-dashboard", "kiosk-dashboard-mobil", "kiosk-panel-uebersicht",
             "kiosk-panel-detail", "kiosk-tafel"},
        ),
    ):
        directory = tmp_path / pattern.replace("*", "alle")
        result = subprocess.run(  # noqa: S603 -- fixed local Python module
            [
                sys.executable,
                "-m",
                "tools.screenshots",
                "--ausgabe",
                str(directory),
                "--nur",
                pattern,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env={
                key: value for key, value in os.environ.items() if not key.startswith("THERMOCTL_")
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert {p.stem for p in directory.glob("*.png")} == stems
        for path in directory.glob("*.png"):
            _assert_capture_dimensions(path)


# Stems of the views captured as an excerpt of one section instead of the viewport.
SECTION_CAPTURE_STEMS = {view.stem for view in VIEWS if view.section}


def test_documented_png_dimensions() -> None:
    for path in (Path(__file__).resolve().parents[1] / "docs" / "bilder").glob("*.png"):
        _assert_capture_dimensions(path)


def _assert_capture_dimensions(path: Path) -> None:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    if path.stem in {"kiosk-panel-uebersicht", "kiosk-panel-detail", "kiosk-tafel"}:
        assert width == 480
        if path.stem == "kiosk-tafel":
            assert height > 480  # Full-page capture of the scrolling board.
        else:
            assert height == 480
    elif path.stem in SECTION_CAPTURE_STEMS:
        # Deliberate excerpt of one form section (`View.section`, cropped to the element,
        # full page so nothing is cut off): narrower than the viewport and as tall as the
        # section needs. Only views that declare a section get this exemption; it is
        # derived from the inventory, so a full-page capture can never slip through here.
        assert 0 < width <= 1280
        assert height > 0
    elif "-mobil" in path.stem:
        # Viewport capture, so the fixed bottom navigation stays at the bottom.
        assert width == 390
        assert height == 844
    else:
        assert width == 1280
        # One 1280 × 900 desktop viewport stays legible in the docs.
        # Longer pages need a second capture, never a taller PNG.
        assert height == 900
    assert len(data) > 10_000

def test_phone_captures_start_at_top_and_keep_navigation_at_bottom(
    browser: Browser, live_server: LiveServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_tests.seed import create_login_tenant_user
    from tools.screenshots import _capture

    with live_server.session() as session:
        create_login_tenant_user(
            session, "demo-mieter", live_server.admin_password, [("zone.read", None)]
        )
        session.commit()

    original = Page.screenshot
    positions = []

    def checked_screenshot(page: Page, **kwargs: Any) -> bytes:
        if page.viewport_size == {"width": 390, "height": 844}:
            assert kwargs["full_page"] is False
            nav = page.locator(".tc-tbottomnav")
            box = nav.bounding_box()
            assert box is not None
            assert box["y"] + box["height"] == pytest.approx(844)
            links = nav.get_by_role("link")
            assert links.count() == 4
            for link in links.all():
                bounds = link.bounding_box()
                assert bounds is not None
                assert 0 <= bounds["x"] < bounds["x"] + bounds["width"] <= 390
                assert box["y"] <= bounds["y"] < bounds["y"] + bounds["height"] <= 844
            positions.append(page.evaluate("window.scrollY"))
        return original(page, **kwargs)

    monkeypatch.setattr(Page, "screenshot", checked_screenshot)
    view = next(view for view in VIEWS if view.stem == "wohnung-konto")
    _capture(browser, live_server, view, {}, tmp_path, tmp_path, documented_only=True)
    assert len(positions) == 2
    assert positions[0] == 0
    assert positions[1] > 0


@pytest.mark.parametrize("path, message", [("/not-a-demo-page", "404"), ("/", "/login")])
def test_capture_rejects_failed_pages(
    browser: Browser, live_server: LiveServer, tmp_path: Path, path: str, message: str
) -> None:
    from tools.screenshots import View, _capture

    with pytest.raises(RuntimeError, match=message):
        _capture(browser, live_server, View("fehler", path, "oeffentlich"), {}, tmp_path, tmp_path)
    assert not list(tmp_path.glob("*.png"))


def test_capture_rejects_browser_console_errors(
    browser: Browser,
    live_server: LiveServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.screenshots import View, _capture

    original = browser.new_context

    def with_console_error(**kwargs: Any) -> BrowserContext:
        context = original(**kwargs)
        context.add_init_script("console.error('Screenshot-Konsole-Testfehler')")
        return context

    monkeypatch.setattr(browser, "new_context", with_console_error)
    with pytest.raises(RuntimeError, match="Screenshot-Konsole-Testfehler"):
        _capture(
            browser, live_server, View("fehler", "/login", "oeffentlich"), {}, tmp_path, tmp_path
        )
    assert not list(tmp_path.glob("*.png"))


def test_panel_capture_rejects_missing_panel_javascript(
    browser: Browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_tests.live_server import _live_server
    from tools.screenshot_seed import seed_demo
    from tools.screenshots import _capture

    original = browser.new_context

    def without_panel_script(**kwargs: Any) -> BrowserContext:
        context = original(**kwargs)
        # A successful but empty script response isolates the readiness guard:
        # no HTTP/console error may substitute for checking the active view.
        context.route(
            "**/kiosk_panel.js*",
            lambda route: route.fulfill(status=200, content_type="text/javascript", body=""),
        )
        return context

    monkeypatch.setattr(browser, "new_context", without_panel_script)
    view = next(view for view in VIEWS if view.stem == "kiosk-panel-uebersicht")
    with closing(_live_server("")) as servers:
        server = next(servers)
        with server.session() as session:
            paths = seed_demo(session, server.admin_password)
        with pytest.raises(RuntimeError, match="data-ansicht-aktiv fehlt oder ist falsch"):
            _capture(browser, server, view, paths, tmp_path, tmp_path)
    assert not list(tmp_path.glob("*.png"))
