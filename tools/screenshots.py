"""Generate the UI gallery of the Svelte app against an isolated, disposable server."""

from __future__ import annotations

import argparse
from contextlib import closing
from fnmatch import fnmatchcase
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Browser, ConsoleMessage, Page, Response, sync_playwright

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
    page.goto("/app/login", wait_until="networkidle")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort", exact=True).fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: not urlsplit(url).path.endswith("/login"))
    page.wait_for_load_state("networkidle")


def _settle(page: Page) -> None:
    """Warten, bis die Oberfläche fertig geladen und gezeichnet ist."""
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    # Ladeanzeigen (Platzhalter mit `animate-pulse`) und Meldungen "wird geladen" sind weg.
    page.wait_for_function(
        "document.querySelectorAll('.animate-pulse, [aria-busy=\"true\"]').length === 0",
        timeout=15_000,
    )
    page.wait_for_timeout(400)


# Erwartete Antworten der Oberfläche beim Anmelden: Ohne Sitzung fragt sie den Bootstrap an
# (401) und, ob eine Einrichtung offen ist (404, sobald es einen Benutzer gibt).
_EXPECTED_ANONYMOUS = {(401, "/ui-api/bootstrap"), (404, "/ui-api/setup")}


def _listen_for_errors(page: Page, errors: list[str]) -> None:
    def on_response(response: Response) -> None:
        path = urlsplit(response.url).path
        if response.status >= 400 and (response.status, path) not in _EXPECTED_ANONYMOUS:
            errors.append(f"HTTP {response.status}: {path}")

    def on_console(message: ConsoleMessage) -> None:
        # Fehlgeschlagene Ladevorgänge meldet `on_response` mit Status und Pfad.
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            errors.append(message.text)

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("response", on_response)
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
            has_touch=view.profile == "kiosk" or is_mobile_capture,
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
                if urlsplit(page.url).path.endswith("/login") and not view.path.endswith("/login"):
                    raise RuntimeError("Unerwartete Weiterleitung auf die Anmeldung")
                if view.kiosk_mode:
                    # Die Einlösung des Tokens leitet auf /app/kiosk um und verwirft die Abfrage.
                    response = page.goto(
                        f"/app/kiosk?ansicht={view.kiosk_mode}", wait_until="networkidle"
                    )
                    if response is None or response.status >= 400:
                        raise RuntimeError("Kiosk-Ansicht konnte nicht geladen werden")
                    page.get_by_role("button", name="Panel", exact=True).wait_for(timeout=10_000)
                _settle(page)
                if view.click:
                    page.get_by_role("button", name=view.click, exact=True).click()
                    _settle(page)
                page.evaluate("window.scrollTo(0, 0)")
                if errors:
                    raise RuntimeError("; ".join(errors))
                if view.kiosk_detail_zone:
                    # Echte Antippen-Interaktion unmittelbar vor der Aufnahme.
                    page.locator('[data-testid="kiosk-tiles"] button').first.tap()
                    page.get_by_role("dialog").wait_for(state="visible", timeout=5_000)
                    page.wait_for_timeout(300)
                target = output / name
                page.screenshot(path=str(target), full_page=False, animations="disabled")
                if errors:
                    target.unlink(missing_ok=True)
                    raise RuntimeError("; ".join(errors))
                result.append(target)
                print(name, flush=True)
                followup = view.mobile_followup if is_mobile_capture else view.desktop_followup
                if followup:
                    page.evaluate(f"window.scrollBy(0, {height - 120})")
                    page.wait_for_timeout(300)
                    target = output / f"{Path(name).stem}-2.png"
                    page.screenshot(path=str(target), full_page=False, animations="disabled")
                    if errors:
                        target.unlink(missing_ok=True)
                        raise RuntimeError("; ".join(errors))
                    result.append(target)
                    print(target.name, flush=True)
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
                    if view.path == "/app/setup":
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
                    if view.path != "/app/setup":
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
