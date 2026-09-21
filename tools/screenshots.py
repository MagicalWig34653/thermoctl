"""Generate the complete UI gallery against an isolated, disposable server."""

from __future__ import annotations

import argparse
from contextlib import closing
from fnmatch import fnmatchcase
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from browser_tests.live_server import LiveServer, _live_server
from tools.screenshot_seed import seed_demo
from tools.screenshot_views import VIEWS, View

# The demo administrator used only for screenshot generation -- deliberately not the
# shared `browser_tests.live_server.ADMIN_USERNAME`, which stays test plumbing and must
# not appear inside a documentation image (see docs/bilder/*.png reviews).
SCREENSHOT_ADMIN_USERNAME = "demo-verwaltung"


def selected_views(pattern: str | None, *, documented_only: bool = False) -> list[View]:
    views = [v for v in VIEWS if pattern is None or fnmatchcase(v.stem, pattern)]
    if documented_only:
        views = [v for v in views if v.documented or (v.mobile and v.documented_mobile)]
    if not views:
        raise ValueError(f"Keine Ansicht passt zu --nur {pattern!r}")
    return views


def _login(page: Page, username: str, password: str) -> None:
    page.goto("/login", wait_until="networkidle")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort", exact=True).fill(password)
    page.get_by_role("button", name="Anmelden").click()
    page.locator(".tc-topbar, .tc-theader").wait_for()


def _listen_for_errors(page: Page, errors: list[str]) -> None:
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "response",
        lambda response: (
            errors.append(f"HTTP {response.status}: {urlsplit(response.url).path}")
            if response.status >= 400
            else None
        ),
    )
    page.on(
        "requestfailed",
        lambda request: errors.append(
            f"Anfrage fehlgeschlagen: {urlsplit(request.url).path}: {request.failure}"
        ),
    )


def _capture(
    browser: Browser,
    server: LiveServer,
    view: View,
    paths: dict[str, str | int],
    output: Path,
    auth_directory: Path,
    *,
    documented_only: bool = False,
) -> list[Path]:
    result = []
    sizes = (view.viewport, (390, 844)) if view.mobile else (view.viewport,)
    for index, (width, height) in enumerate(sizes):
        is_mobile_capture = index == 1
        if documented_only and not (
            view.documented_mobile if is_mobile_capture else view.documented
        ):
            continue
        name = view.stem + ("-mobil" if is_mobile_capture else "") + ".png"
        errors: list[str] = []
        auth_file = auth_directory / f"{view.profile}.json"
        with browser.new_context(
            storage_state=auth_file if auth_file.exists() else None,
            base_url=server.base_url,
            viewport={"width": width, "height": height},
            has_touch=view.kiosk_mode is not None,
            device_scale_factor=1,
            color_scheme="light",
            locale="de-DE",
            timezone_id="Europe/Berlin",
            reduced_motion="reduce",
        ) as context:
            page = context.new_page()
            _listen_for_errors(page, errors)
            try:
                if view.profile in ("anlage", "wohnung") and not auth_file.exists():
                    _login(
                        page,
                        server.admin_username if view.profile == "anlage" else "demo-mieter",
                        server.admin_password,
                    )
                    context.storage_state(path=auth_file)
                response = page.goto(view.path.format_map(paths), wait_until="networkidle")
                if response is None or response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status if response else 'ohne Antwort'}")
                if urlsplit(page.url).path == "/login" and view.path != "/login":
                    raise RuntimeError("Unerwartete Weiterleitung auf /login")
                if view.kiosk_mode:
                    # The token entry redirects to bare /kiosk and drops query parameters.
                    response = page.goto(
                        f"/kiosk?ansicht={view.kiosk_mode}", wait_until="networkidle"
                    )
                    if response is None or response.status >= 400:
                        raise RuntimeError("Kiosk-Ansicht konnte nicht geladen werden")
                    try:
                        page.locator(
                            f'body[data-ansicht-aktiv="{view.kiosk_mode}"]'
                        ).wait_for(timeout=10_000)
                    except PlaywrightTimeoutError as exc:
                        raise RuntimeError(
                            f"Kiosk-{view.kiosk_mode}: data-ansicht-aktiv fehlt oder ist falsch; "
                            "kiosk_panel.js hat die angeforderte Ansicht nicht aktiviert"
                        ) from exc
                if view.open_details:
                    for detail in page.locator("details").all():
                        if detail.get_attribute("open") is None:
                            detail.locator("summary").click()
                # Opening details scrolls the page; sticky headers must start at the top.
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_function("window.scrollY === 0")
                page.evaluate("document.fonts.ready")
                page.wait_for_load_state("networkidle")
                page.locator(".htmx-request").wait_for(state="detached")
                if not page.evaluate(
                    "Array.from(document.styleSheets).some(s => "
                    "s.href && s.href.includes('thermoctl.css'))"
                ):
                    raise RuntimeError("thermoctl.css fehlt")
                if errors:
                    raise RuntimeError("; ".join(errors))
                if view.kiosk_detail_zone:
                    # Real touch interaction immediately before capture: no URL shortcut
                    # and no waits that could run into the 45-second return timer.
                    zone_id = paths[view.kiosk_detail_zone]
                    page.locator(f'[data-kiosk-tile="{zone_id}"]').tap()
                    page.locator(
                        f'[data-kiosk-detail="{zone_id}"].kiosk-detail-open'
                    ).wait_for(state="visible", timeout=5_000)
                target = output / name
                page.screenshot(path=str(target), full_page=True, animations="disabled")
                if errors:
                    target.unlink(missing_ok=True)
                    raise RuntimeError("; ".join(errors))
                result.append(target)
                print(name, flush=True)
            except Exception as exc:
                raise RuntimeError(f"{name} ({view.path}): {exc}; {'; '.join(errors)}") from exc
    return result


def generate(
    output: Path, pattern: str | None = None, *, documented_only: bool = False
) -> list[Path]:
    views = selected_views(pattern, documented_only=documented_only)
    output.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    with (
        TemporaryDirectory(prefix="thermoctl-screenshot-auth-") as auth_temp,
        sync_playwright() as playwright,
    ):
        auth_directory = Path(auth_temp)
        # Chromium's own UI language, not `locale`, decides whether a bare
        # `<input type="time">` renders as a 24-hour or an AM/PM control -- without
        # `--lang` it defaults to the host's UI language regardless of `locale`.
        browser = playwright.chromium.launch(args=["--lang=de-DE"])
        try:

            def before_setup(server: LiveServer) -> None:
                for view in views:
                    if view.path == "/setup":
                        result.extend(
                            _capture(
                                browser,
                                server,
                                view,
                                {},
                                output,
                                auth_directory,
                                documented_only=documented_only,
                            )
                        )

            with closing(
                _live_server(
                    "",
                    before_setup=before_setup,
                    admin_username=SCREENSHOT_ADMIN_USERNAME,
                )
            ) as servers:
                server = next(servers)
                with server.session() as session:
                    paths = seed_demo(session, server.admin_password)
                for view in views:
                    if view.path != "/setup":
                        result.extend(
                            _capture(
                                browser,
                                server,
                                view,
                                paths,
                                output,
                                auth_directory,
                                documented_only=documented_only,
                            )
                        )
        finally:
            browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ausgabe",
        type=Path,
        default=Path("var/bilder"),
        help="Zielverzeichnis; ignoriert, wenn --doku gesetzt ist (siehe dort)",
    )
    parser.add_argument("--nur", help="Shell-Muster für <profil>-<ansicht>, z. B. 'wohnung-*'")
    parser.add_argument(
        "--doku",
        action="store_true",
        help=(
            "Nur die als 'documented' gekennzeichneten Ansichten aufnehmen und nach "
            "docs/bilder schreiben (ignoriert --ausgabe)"
        ),
    )
    args = parser.parse_args()
    output = Path("docs/bilder") if args.doku else args.ausgabe
    try:
        files = generate(output, args.nur, documented_only=args.doku)
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, f"Screenshots fehlgeschlagen: {exc}\n")
    print(f"{len(files)} PNGs, {sum(p.stat().st_size for p in files):,} Bytes")


if __name__ == "__main__":
    main()
