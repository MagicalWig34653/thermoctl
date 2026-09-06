"""Urlaubsbetrieb: die eigene Seite `/vacation` und ihr Chip auf der Startseite.

Nichts hier ist über einen HTTP-Test zu sehen -- ob das Formular für eine
Berechtigung ohne `vacation.manage` überhaupt erscheint, ob der Chip auf der
Startseite nach dem Ansetzen wirklich auftaucht und wieder verschwindet, und ob
eine Fehlermeldung tatsächlich am Feld hängt statt irgendwo auf der Seite zu
stehen, sind alles Fragen an das gerenderte Ergebnis, nicht an den Statuscode.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from thermoctl.db.models.vacation import Vacation

pytestmark = pytest.mark.browser

_PASSWORD = "Kein-Urlaubsrecht-7"  # noqa: S105 -- local, ephemeral, throwaway DB


@pytest.fixture(autouse=True)
def _no_leftover_vacation(live_server: LiveServer) -> None:
    """Jeder Test in dieser Datei arbeitet gegen dieselbe, sitzungsweite
    Datenbank -- ein Urlaub, den ein Test angesetzt und nicht beendet hat, würde
    `create_vacation()`s Einmaligkeits-Prüfung im nächsten Test auslösen und den
    dortigen Fehlerfall vortäuschen. Vor jedem Test wird deshalb ein noch
    laufender oder geplanter Urlaub direkt in der Datenbank beendet -- nicht über
    die Oberfläche, um keinen der Tests selbst von einem funktionierenden
    "Vorzeitig beenden" abhängig zu machen.
    """
    with live_server.session() as session:
        for entry in session.query(Vacation).filter(Vacation.cancelled_at.is_(None)):
            entry.cancelled_at = datetime(2000, 1, 1)
        session.commit()


def test_setting_a_vacation_shows_it_on_its_own_page_and_as_a_chip_on_the_start_page(
    admin_page: Page,
) -> None:
    admin_page.goto("/vacation")
    expect(admin_page.get_by_role("heading", name="Urlaubsbetrieb")).to_be_visible()
    expect(admin_page.get_by_text("Kein Urlaub angesetzt")).to_be_visible()

    # Ein Zeitraum, der den heutigen Tag sicher einschließt: weit genug gefasst,
    # unabhängig davon, an welchem Tag die Suite gerade läuft.
    admin_page.locator("#start_date").fill("2020-01-01")
    admin_page.locator("#end_date").fill("2099-12-31")
    admin_page.locator("#setback_temperature_c").fill("15")
    admin_page.get_by_role("button", name="Urlaub ansetzen").click()

    expect(admin_page.get_by_text("Kein Urlaub angesetzt")).to_have_count(0)
    expect(admin_page.locator(".tc-chip", has_text="Läuft")).to_be_visible()
    expect(admin_page.get_by_text("15,0 °C")).to_be_visible()

    # Der Chip auf der Startseite, mit Verweis zurück auf die Urlaubsseite.
    admin_page.goto("/")
    start_chip = admin_page.locator("a.tc-chip", has_text="Urlaub läuft")
    expect(start_chip).to_be_visible()
    start_chip.click()
    expect(admin_page.get_by_role("heading", name="Urlaubsbetrieb")).to_be_visible()

    # Vorzeitig beenden -- danach ist der Urlaub sowohl auf seiner eigenen Seite
    # als auch auf der Startseite wieder verschwunden.
    admin_page.get_by_role("button", name="Vorzeitig beenden").click()
    expect(admin_page.get_by_text("Kein Urlaub angesetzt")).to_be_visible()

    admin_page.goto("/")
    expect(admin_page.locator(".tc-chip", has_text="Urlaub")).to_have_count(0)


def test_a_planned_future_vacation_shows_geplant_not_laeuft(admin_page: Page) -> None:
    admin_page.goto("/vacation")
    admin_page.locator("#start_date").fill("2999-01-01")
    admin_page.locator("#end_date").fill("2999-01-10")
    admin_page.locator("#setback_temperature_c").fill("14")
    admin_page.get_by_role("button", name="Urlaub ansetzen").click()

    expect(admin_page.locator(".tc-chip", has_text="Geplant")).to_be_visible()
    expect(admin_page.locator(".tc-chip", has_text="Läuft")).to_have_count(0)

    admin_page.goto("/")
    expect(admin_page.locator(".tc-chip", has_text="Urlaub geplant")).to_be_visible()


def test_an_end_date_before_the_start_date_shows_the_error_at_the_field(
    admin_page: Page,
) -> None:
    admin_page.goto("/vacation")
    admin_page.locator("#start_date").fill("2026-09-20")
    admin_page.locator("#end_date").fill("2026-09-10")
    admin_page.locator("#setback_temperature_c").fill("15")
    admin_page.get_by_role("button", name="Urlaub ansetzen").click()

    # Die Fehlermeldung sitzt in `invalid-feedback` unter dem Enddatum-Feld, nicht
    # als roher Text irgendwo auf der Seite.
    expect(admin_page.locator("#end_date")).to_have_class(re.compile(r"is-invalid"))
    error = admin_page.locator("#end_date-fehler")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Ende darf nicht vor dem Beginn liegen")
    expect(admin_page.get_by_text("Kein Urlaub angesetzt")).to_be_visible()


def test_a_user_without_vacation_manage_sees_the_state_but_not_the_form(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_user(
            session, "browsertest-ohne-urlaubsrecht", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    page.goto("/login")
    page.get_by_label("Benutzername").fill("browsertest-ohne-urlaubsrecht")
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    expect(page.locator(".tc-head")).to_be_visible()

    page.goto("/vacation")
    expect(page.get_by_role("heading", name="Urlaubsbetrieb")).to_be_visible()
    # Der lesbare Zustand ("kein Urlaub angesetzt") bleibt sichtbar --
    # `zone.read` reicht dafür aus -- aber das Formular zum Ansetzen eines neuen
    # Urlaubs erscheint für diese Berechtigung gar nicht erst.
    expect(page.get_by_text("Kein Urlaub angesetzt")).to_be_visible()
    expect(page.locator("#start_date")).to_have_count(0)
    expect(page.get_by_text("vacation.manage")).to_be_visible()
