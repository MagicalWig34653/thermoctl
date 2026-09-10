"""Die Wohnungssicht (v0.9.0) -- was nur ein echter Browser sehen kann.

`tests/test_navigation.py` (die HTTP-Suite) prueft bereits, dass `visible_navigation()`
und die Wächter (`web/guards.py`) fuer ein Mieterprofil die richtigen Daten liefern.
Was dort nicht sichtbar ist: ob die Wohnungshuelle tatsaechlich gerendert wird (und
nicht die Anlagenhuelle), ob die vier Bereiche im Browser wirklich anklickbar sind,
ob der Sollwert-Stepper den angezeigten Wert nach einem Klick tatsaechlich aendert
-- der Server rechnet, nicht JavaScript --, und ob CSS-Medienabfragen auf einem
schmalen Bildschirm die richtige Navigation ein- und ausblenden.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser

_PASSWORD = "Mieterprofil-Kennwort-7"  # noqa: S105 -- local, ephemeral, throwaway DB


def _login_as_tenant(page: Page, username: str, password: str) -> None:
    page.goto("/login")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort").fill(password)
    page.get_by_role("button", name="Anmelden").click()
    # Die Wohnungshuelle traegt `.tc-theader`, nicht `.tc-topbar` -- das ist die
    # Anlagenhuelle (`base_admin.html`). Beide erben `base_core.html`, aber nur
    # `base_admin.html` bringt `.tc-topbar` mit.
    expect(page.locator(".tc-theader")).to_be_visible()


def test_a_tenant_lands_on_the_tenant_shell_not_the_admin_shell(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, "browsertest-mieter-huelle", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    _login_as_tenant(page, "browsertest-mieter-huelle", _PASSWORD)

    expect(page.locator(".tc-tnav")).to_be_visible()
    expect(page.locator(".tc-sidenav")).to_have_count(0)


def test_the_four_tenant_sections_are_visible_and_each_leads_to_a_working_page(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, "browsertest-mieter-bereiche", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    _login_as_tenant(page, "browsertest-mieter-bereiche", _PASSWORD)

    # "Zuhause" ist der einzige Eintrag, der nicht aus `NAVIGATION_ITEMS` kommt
    # (`base_tenant.html` verdrahtet ihn fest auf "/") -- die anderen drei stehen
    # dort mit dem Profil `tenant`.
    for label, path in (
        ("Zuhause", "/"),
        ("Zeitplan", "/schedule"),
        ("Heizzeit", "/heating-time"),
        ("Mehr", "/account"),
    ):
        link = page.locator(".tc-tnav").get_by_role("link", name=label, exact=True)
        expect(link).to_be_visible()
        response = page.goto(path)
        assert response is not None
        assert response.status == 200, f"{path} ({label}) antwortete nicht mit 200"


def test_the_setpoint_stepper_actually_changes_the_shown_value_after_the_server_replies(
    page: Page, live_server: LiveServer
) -> None:
    """Ein Klick auf "+" laedt die Seite neu (der Server rechnet den neuen Wert),
    nicht bloss eine clientseitige Zusicherung -- geprueft wird der Wert **nach**
    dem Klick, nicht ein `hx-*`-Attribut im Markup.
    """
    with live_server.session() as session:
        zone = seed.create_schedule_zone(
            session, "Wohnzimmer", day_temperature=Decimal("21.0")
        )
        seed.create_login_tenant_user(
            session,
            "browsertest-mieter-stepper",
            _PASSWORD,
            [("zone.read", None), ("setpoint.write", zone.id)],
        )
        session.commit()

    _login_as_tenant(page, "browsertest-mieter-stepper", _PASSWORD)

    value = page.locator(".tc-stepvalue")
    expect(value).to_be_visible()
    before = value.inner_text()

    page.get_by_role(
        "button", name="Temperatur um ein halbes Grad anheben"
    ).click()

    # `hx-boost` tauscht den Rumpf aus statt einer klassischen Navigation --
    # `to_have_text` mit seiner eingebauten Wiederholung wartet das ab, statt an
    # einem einzelnen, moeglicherweise zu fruehen Lesevorgang zu scheitern.
    expect(value).not_to_have_text(before)


def test_settings_is_forbidden_for_a_tenant_account(
    page: Page, live_server: LiveServer
) -> None:
    """Die ausgeblendete Verknuepfung ist eine Hoeflichkeit -- der eigentliche
    Riegel ist der Profil-Waechter (`web/guards.py::admin_ui_only`), unabhaengig
    davon, ob jemand die Adresse von Hand eintippt.
    """
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, "browsertest-mieter-riegel", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    _login_as_tenant(page, "browsertest-mieter-riegel", _PASSWORD)

    response = page.goto("/settings")
    assert response is not None
    assert response.status == 403


def test_the_bottom_navigation_shows_on_a_narrow_viewport_not_the_top_text_navigation(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, "browsertest-mieter-schmal", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    page.set_viewport_size({"width": 390, "height": 844})
    _login_as_tenant(page, "browsertest-mieter-schmal", _PASSWORD)

    expect(page.locator(".tc-tbottomnav")).to_be_visible()
    expect(page.locator(".tc-tnav")).not_to_be_visible()


def test_the_loading_bar_exists_in_the_tenant_shell_too(
    page: Page, live_server: LiveServer
) -> None:
    """Dasselbe Element wie in der Anlagenhuelle
    (`browser_tests/test_loading_indicator.py`), hier nur auf seine blosse
    Existenz geprueft -- die Zeit- und Verzoegerungslogik selbst ist bereits dort
    fuer die Anlagenhuelle abgedeckt und unterscheidet sich nicht nach Oberflaeche
    (beide erben `base_core.html`).
    """
    with live_server.session() as session:
        seed.create_login_tenant_user(
            session, "browsertest-mieter-ladebalken", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    _login_as_tenant(page, "browsertest-mieter-ladebalken", _PASSWORD)

    expect(page.locator("#tc-loading-bar")).to_have_count(1)
