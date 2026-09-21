"""Generate the complete UI gallery against an isolated, disposable server."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, sync_playwright

from browser_tests.live_server import LiveServer, _live_server
from tools.screenshot_seed import ZONE_SLUGS, seed_demo


@dataclass(frozen=True)
class View:
    name: str
    path: str
    profile: str = "anlage"
    mobile: bool = False
    open_details: bool = False
    route: str | None = None

    @property
    def stem(self) -> str:
        return f"{self.profile}-{self.name}"


# The single inventory, including parameterized per-zone views and preparations.
VIEWS = (
    View("einrichtung", "/setup", "oeffentlich"),
    View("anmeldung", "/login", "oeffentlich"),
    View("startseite", "/", mobile=True),
    *(
        View(name, path)
        for name, path in (
            ("zonen", "/zones"),
            ("zone-neu", "/zones/new"),
            ("geraete", "/devices"),
            ("anlage", "/plant"),
            ("bediengeraete", "/controllers"),
            ("schaltprotokoll", "/device-commands"),
            ("passkeys", "/passkeys"),
            ("kiosk-token", "/kiosk-tokens"),
            ("urlaub", "/vacation"),
            ("konto", "/account"),
            ("hilfe", "/account/help"),
            ("audit", "/audit"),
            ("benutzer", "/users"),
            ("gruppen", "/groups"),
            ("api-token", "/tokens"),
            ("sollwertmodi", "/modes"),
            ("sollwertmodus-neu", "/modes/new"),
            ("sollwertmodus", "/modes/{mode_id}"),
            ("sollwertmodus-loeschen", "/modes/{mode_id}/delete"),
            ("betrieb", "/control"),
            ("einstellungen", "/settings"),
            ("schnittstellen", "/interfaces"),
            ("statistik", "/statistics"),
            ("relaisverschleiss", "/relay-wear"),
        )
    ),
    *(
        View(
            f"{slug}-{name}",
            path.replace("{zone_id}", "{zone_" + slug + "}").replace(
                "{point_id}", "{point_" + slug + "}"
            ),
            route=path,
        )
        for slug in ZONE_SLUGS
        for name, path in (
            ("details", "/zones/{zone_id}"),
            ("loeschen", "/zones/{zone_id}/delete"),
            ("parameter", "/zones/{zone_id}/parameters"),
            ("geraete", "/zones/{zone_id}/devices"),
            ("wochenplan", "/zones/{zone_id}/schedule"),
            ("schaltpunkt-loeschen", "/zones/{zone_id}/schedule/points/{point_id}/delete"),
            ("wochenplan-uebernehmen", "/zones/{zone_id}/schedule/adopt"),
            ("sollwerte", "/zones/{zone_id}/setpoints"),
        )
    ),
    *(
        View(name, path, "wohnung", mobile=True, open_details=details)
        for name, path, details in (
            ("startseite", "/", False),
            ("abwesenheit", "/", True),
            ("wochenplan", "/schedule", True),
            ("heizzeit", "/heating-time", False),
            ("konto", "/account", False),
            ("hilfe", "/account/help", False),
            ("passkeys", "/passkeys", False),
        )
    ),
    *(
        View(
            f"{slug}-wochenplan",
            "/schedule?zone={zone_" + slug + "}",
            "wohnung",
            mobile=True,
            open_details=True,
            route="/schedule",
        )
        for slug in ZONE_SLUGS[:4]
    ),
    View("dashboard", "/kiosk/{plaintext}", "kiosk", mobile=True),
)
EXCLUDED_ROUTES = {
    "/kiosk": "Ziel der Token-Weiterleitung; wird über /kiosk/{plaintext} aufgenommen.",
}


def selected_views(pattern: str | None) -> list[View]:
    views = [v for v in VIEWS if pattern is None or fnmatchcase(v.stem, pattern)]
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
) -> list[Path]:
    result = []
    for width in (1280, 390) if view.mobile else (1280,):
        name = view.stem + ("-mobil" if width == 390 else "") + ".png"
        errors: list[str] = []
        auth_file = auth_directory / f"{view.profile}.json"
        with browser.new_context(
            storage_state=auth_file if auth_file.exists() else None,
            base_url=server.base_url,
            viewport={"width": width, "height": 900 if width == 1280 else 844},
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


def generate(output: Path, pattern: str | None = None) -> list[Path]:
    views = selected_views(pattern)
    output.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    with (
        TemporaryDirectory(prefix="thermoctl-screenshot-auth-") as auth_temp,
        sync_playwright() as playwright,
    ):
        auth_directory = Path(auth_temp)
        browser = playwright.chromium.launch()
        try:

            def before_setup(server: LiveServer) -> None:
                for view in views:
                    if view.path == "/setup":
                        result.extend(_capture(browser, server, view, {}, output, auth_directory))

            with closing(_live_server("", before_setup=before_setup)) as servers:
                server = next(servers)
                with server.session() as session:
                    paths = seed_demo(session, server.admin_password)
                for view in views:
                    if view.path != "/setup":
                        result.extend(
                            _capture(browser, server, view, paths, output, auth_directory)
                        )
        finally:
            browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ausgabe", type=Path, default=Path("docs/bilder"))
    parser.add_argument("--nur", help="Shell-Muster für <profil>-<ansicht>, z. B. 'wohnung-*'")
    args = parser.parse_args()
    try:
        files = generate(args.ausgabe, args.nur)
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, f"Screenshots fehlgeschlagen: {exc}\n")
    print(f"{len(files)} PNGs, {sum(p.stat().st_size for p in files):,} Bytes")


if __name__ == "__main__":
    main()
