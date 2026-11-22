"""Verlaufsseite („Temperatur & Heizzeit“): Standard ist EIN Raum, „Alle Räume“ ist wählbar.

Vorher zeigte die Seite am Handy standardmäßig jeden Raum als eigenes Diagramm (rund 4500 px
bei drei Räumen, bei sechs über 5000 px). Jetzt gilt: ohne Angabe der erste sichtbare Raum in
der Reihenfolge der Seite; „Alle Räume“ und jeder Einzelraum stehen im Auswahlfeld und in der
Adresse (`?zone=alle` bzw. `?zone=<id>`) und überleben so Neuladen und Verlinken. Der neue
Standard gilt auch am Desktop (keine Unterscheidung nach Bildschirmbreite).

Ein eigener Server mit genau drei Räumen, damit die Zahlen nicht von den Räumen anderer Tests
abhängen. Die Verlaufszeilen sind echte `ShadowDecision`-Zeilen (`seed_history`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, ViewportSize, expect

from browser_tests.conftest import _record_console_error
from browser_tests.live_server import LiveServer, _live_server
from browser_tests.seed import create_login_user, create_schedule_zone, seed_history

pytestmark = pytest.mark.browser

PHONE: ViewportSize = {"width": 390, "height": 844}
DESKTOP: ViewportSize = {"width": 1280, "height": 900}
PATH = "/app/auswertung/verlauf"
# Eine Karte je Raum. Nicht `chart-surface` zählen: Karten außerhalb des Bildschirms laden erst beim
# Scrollen (`lazy`) und haben bis dahin keine Zeichenfläche.
CARDS = "main ul > li > section"
SURFACE = "[data-testid=chart-surface]"
# Reihenfolge der Seite = sort_order, dann Name (nicht alphabetisch: „Wohnzimmer“ steht vorn).
ROOMS = [("Wohnzimmer", "20.5"), ("Schlafzimmer", "18.5"), ("Kinderzimmer", "19.5")]
PASSWORD = "verlauf-pruefen-1"  # noqa: S105 -- ephemeral local database only


@dataclass
class Setup:
    server: LiveServer
    ids: dict[str, int] = field(default_factory=dict)


@pytest.fixture(scope="module")
def setup() -> Iterator[Setup]:
    generator = _live_server("")
    server = next(generator)
    try:
        result = Setup(server)
        with server.session() as session:
            for index, (name, base) in enumerate(ROOMS):
                zone = create_schedule_zone(session, name)
                zone.sort_order = index
                seed_history(session, zone, base_c=Decimal(base))
                result.ids[name] = int(zone.id)
            session.flush()
            # Wer das erste Zimmer nicht sehen darf, bekommt als Standard das erste SICHTBARE.
            schlaf, kinder = result.ids["Schlafzimmer"], result.ids["Kinderzimmer"]
            create_login_user(
                session, "zwei_raeume", PASSWORD, [("zone.read", schlaf), ("zone.read", kinder)]
            )
            create_login_user(session, "ein_raum", PASSWORD, [("zone.read", kinder)])
            session.commit()
        yield result
    finally:
        next(generator, None)


@pytest.fixture
def new_page(browser: Browser, setup: Setup) -> Iterator[Callable[..., Page]]:
    """Fabrik für angemeldete Seiten (Handy mit Touch oder Desktop, hell/dunkel)."""
    contexts: list[BrowserContext] = []
    errors: list[str] = []

    def make(
        *, phone: bool = True, user: str | None = None, scheme: str = "light"
    ) -> Page:
        context = browser.new_context(
            base_url=setup.server.base_url,
            color_scheme="dark" if scheme == "dark" else "light",
            viewport=PHONE if phone else DESKTOP,
            has_touch=phone,
            is_mobile=phone,
        )
        contexts.append(context)
        page = context.new_page()
        page.on("console", lambda m: _record_console_error(errors, m))
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))
        page.goto("/login")
        page.get_by_label("Benutzername").fill(user or setup.server.admin_username)
        page.get_by_label("Passwort").fill(PASSWORD if user else setup.server.admin_password)
        page.get_by_role("button", name="Anmelden", exact=True).click()
        page.wait_for_url(lambda url: "/login" not in url)
        return page

    yield make
    for context in contexts:
        context.close()
    assert not errors, "Browserkonsole meldete Fehler:\n" + "\n".join(errors)


def _open(page: Page, query: str = "") -> None:
    page.goto(f"{PATH}{query}")
    expect(page.locator(SURFACE).first).to_be_visible()
    # Die ersten beiden Karten laden sofort: die erste ist fertig, wenn der letzte Wert steht.
    expect(page.get_by_test_id("big-value").first).to_be_visible()


def _titles(page: Page) -> list[str]:
    """Raumnamen der Diagramme in Seitenreihenfolge."""
    return [t.strip() for t in page.locator(CARDS).locator("h2").all_inner_texts()]


def _room_select(page: Page):  # type: ignore[no-untyped-def]
    return page.get_by_test_id("history-room")


def _height(page: Page) -> int:
    """Seitenhöhe, nachdem alle Karten geladen sind: Karten außerhalb des Bildschirms laden
    erst beim Scrollen und sind bis dahin niedriger. Deshalb einmal bis unten durchscrollen."""
    page.wait_for_load_state("networkidle")
    for y in range(0, int(page.evaluate("document.documentElement.scrollHeight")), 400):
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(100)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_load_state("networkidle")
    return int(page.evaluate("document.documentElement.scrollHeight"))


def _no_sideways_scroll(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    ), "Die Seite scrollt seitwärts."


def test_default_is_exactly_one_room_on_the_phone(new_page: Callable[..., Page]) -> None:
    page = new_page()
    _open(page)
    assert _titles(page) == ["Wohnzimmer"]
    expect(page.locator(CARDS)).to_have_count(1)
    expect(_room_select(page)).to_have_value(str(_first_id(page)))
    _no_sideways_scroll(page)


def _first_id(page: Page) -> int:
    # Der Wert der ersten Raum-Option (nach „Alle Räume“): bestimmt unabhängig vom Auswahlstand.
    return int(page.get_by_test_id("history-room").locator("option").nth(1).get_attribute("value"))


def test_default_room_is_first_in_page_order_not_alphabetical(
    new_page: Callable[..., Page], setup: Setup
) -> None:
    page = new_page()
    _open(page)
    # „Kinderzimmer“ wäre alphabetisch zuerst; maßgeblich ist die Reihenfolge der Seite.
    assert _first_id(page) == setup.ids["Wohnzimmer"]
    assert _titles(page) == ["Wohnzimmer"]
    options = _room_select(page).locator("option").all_inner_texts()
    assert options == ["Alle Räume", "Wohnzimmer", "Schlafzimmer", "Kinderzimmer"]


def test_switching_room_shows_the_other_room(new_page: Callable[..., Page], setup: Setup) -> None:
    page = new_page()
    _open(page)
    _room_select(page).select_option(label="Schlafzimmer")
    expect(page.locator(CARDS)).to_have_count(1)
    assert _titles(page) == ["Schlafzimmer"]
    assert f"zone={setup.ids['Schlafzimmer']}" in page.url
    # Die Heizzeit darunter folgt der Auswahl: nur der gewählte Raum.
    heating = page.locator("section[aria-labelledby=heating-title]")
    expect(heating).to_contain_text("Schlafzimmer")
    expect(heating).not_to_contain_text("Wohnzimmer")


def test_all_rooms_shows_every_room(new_page: Callable[..., Page]) -> None:
    page = new_page()
    _open(page)
    _room_select(page).select_option(label="Alle Räume")
    expect(page.locator(CARDS)).to_have_count(3)
    assert _titles(page) == ["Wohnzimmer", "Schlafzimmer", "Kinderzimmer"]
    assert "zone=alle" in page.url
    _no_sideways_scroll(page)


def test_choice_survives_reload(new_page: Callable[..., Page]) -> None:
    page = new_page()
    _open(page)
    _room_select(page).select_option(label="Alle Räume")
    expect(page.locator(CARDS)).to_have_count(3)
    page.reload()
    expect(page.locator(CARDS)).to_have_count(3)
    expect(_room_select(page)).to_have_value("alle")

    _room_select(page).select_option(label="Kinderzimmer")
    expect(page.locator(CARDS)).to_have_count(1)
    page.reload()
    expect(page.locator(CARDS)).to_have_count(1)
    assert _titles(page) == ["Kinderzimmer"]


def test_choice_keeps_the_period(
    new_page: Callable[..., Page], setup: Setup
) -> None:
    page = new_page()
    _open(page, "?period=30d")
    page.get_by_role("group", name="Zeitraum").first.get_by_role("button", name="24 Std.").click()
    _room_select(page).select_option(label="Schlafzimmer")
    expect(page.locator(CARDS)).to_have_count(1)
    # Raumwahl ändert den Zeitraum nicht zurück auf den der Adresse.
    expect(
        page.get_by_role("group", name="Zeitraum").first.get_by_role("button", name="24 Std.")
    ).to_have_attribute("aria-pressed", "true")
    assert "period=30d" in page.url and f"zone={setup.ids['Schlafzimmer']}" in page.url


def test_link_with_room_and_unknown_room(new_page: Callable[..., Page], setup: Setup) -> None:
    page = new_page()
    _open(page, f"?zone={setup.ids['Kinderzimmer']}")
    assert _titles(page) == ["Kinderzimmer"]
    _open(page, "?zone=99999")
    assert _titles(page) == ["Wohnzimmer"]


def test_page_is_much_shorter_with_one_room_than_with_all(new_page: Callable[..., Page]) -> None:
    page = new_page()
    _open(page)
    one = _height(page)
    _room_select(page).select_option(label="Alle Räume")
    expect(page.locator(CARDS)).to_have_count(3)
    everything = _height(page)
    assert one < everything * 0.6, f"ein Raum {one} px, alle Räume {everything} px"
    # Gemessen, nicht geschätzt: die lange Seite war der Befund.
    assert everything > 3000, everything


def test_desktop_default_is_one_room_and_all_is_selectable(
    new_page: Callable[..., Page],
) -> None:
    page = new_page(phone=False)
    _open(page)
    assert _titles(page) == ["Wohnzimmer"]
    _room_select(page).select_option(label="Alle Räume")
    expect(page.locator(CARDS)).to_have_count(3)
    page.reload()
    expect(page.locator(CARDS)).to_have_count(3)
    _no_sideways_scroll(page)


def test_dark_phone_page_has_one_room_and_the_selector(new_page: Callable[..., Page]) -> None:
    page = new_page(scheme="dark")
    _open(page)
    assert _titles(page) == ["Wohnzimmer"]
    expect(_room_select(page)).to_be_visible()


def test_default_is_the_first_visible_room_for_a_restricted_user(
    new_page: Callable[..., Page], setup: Setup
) -> None:
    page = new_page(user="zwei_raeume")
    _open(page)
    # „Wohnzimmer“ ist nicht sichtbar: Standard ist das erste Zimmer, das der Nutzer sehen darf.
    assert _titles(page) == ["Schlafzimmer"]
    assert _room_select(page).locator("option").all_inner_texts() == [
        "Alle Räume", "Schlafzimmer", "Kinderzimmer",
    ]
    # Ein nicht sichtbarer Raum in der Adresse fällt auf den Standard zurück, er wird nicht gezeigt.
    _open(page, f"?zone={setup.ids['Wohnzimmer']}")
    assert _titles(page) == ["Schlafzimmer"]
    _open(page, "?zone=alle")
    assert _titles(page) == ["Schlafzimmer", "Kinderzimmer"]


def test_single_visible_room_needs_no_selector(new_page: Callable[..., Page]) -> None:
    page = new_page(user="ein_raum")
    _open(page)
    assert _titles(page) == ["Kinderzimmer"]
    expect(page.get_by_test_id("history-room")).to_have_count(0)
    expect(page.locator(CARDS)).to_have_count(1)


def test_cycling_through_all_choices_logs_no_console_errors(new_page: Callable[..., Page]) -> None:
    """Wechsel zwischen allen Auswahlen ohne Konsolenfehler (die Fabrik prüft beim Abbau)."""
    page = new_page()
    _open(page)
    for label in ("Alle Räume", "Kinderzimmer", "Wohnzimmer", "Alle Räume"):
        _room_select(page).select_option(label=label)
        expect(_room_select(page).locator("option:checked")).to_have_text(label)
