"""Der Raumreiter im Mieter-Zeitplan (`.tc-roomtab`, `tenant_schedule.html`).

Bei mehreren Räumen entsteht eine Reiterleiste (`.tc-roomtabs`). Auf einem
schmalen Bildschirm (390 px) brach ein langer, zusammengesetzter Raumname
(ein einzelnes "Wort" ohne Leerzeichen oder Bindestrich -- an denen ist ganz
gewöhnlicher Zeilenumbruch ohnehin erlaubt) bisher mitten im Wort um: das
globale `overflow-wrap: anywhere` (`thermoctl/web/static/thermoctl.css`)
erlaubt einem ungebremst schrumpfenden Flex-Kind, seinen Text an beliebiger
Stelle zu trennen, statt ihn unverändert stehen zu lassen oder die Zeile
scrollen zu lassen. Reine HTTP-Tests sehen das nicht -- Zeilenumbruch ist ein
reines Renderingergebnis.

Ein kurzes Wort wie "Schlafzimmer" allein bricht dabei nicht -- es passt auf
eine eigene Zeile, ohne dass irgendetwas geschrumpft werden müsste. Der Fehler
zeigt sich erst, wenn ein einzelnes Wort für sich genommen breiter ist als die
verfügbare Breite; deshalb der lange zusammengesetzte Name unten.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser

_PASSWORD = "Mieterprofil-Kennwort-7"  # noqa: S105 -- local, ephemeral, throwaway DB


def test_a_long_room_name_does_not_break_mid_word_in_the_room_tab_on_a_narrow_viewport(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        # Ein Bindestrich wäre selbst ein normaler Umbruchpunkt (ganz ohne
        # `overflow-wrap: anywhere`) und würde den Fehler verdecken -- daher ein
        # einzelnes, langes zusammengesetztes Wort ohne Trennzeichen, wie es im
        # Deutschen für einen Raumnamen plausibel ist. `create_zone` setzt
        # `display_name = name.capitalize()`, das Ergebnis bleibt also ein Wort.
        zone_a = seed.create_schedule_zone(session, "schlafzimmermitbalkonundgartenblickreiter")
        zone_b = seed.create_schedule_zone(session, "wohnzimmerreiter")
        # `zone.read` je Zone statt global (`None`) -- die Browsersuite teilt sich
        # eine einzige Datenbank über die ganze Sitzung hinweg (siehe
        # `browser_tests/conftest.py`), ein globales Recht würde also auch die
        # Zonen jedes anderen bereits gelaufenen Tests auf diese Reiterleiste
        # bringen und die Zählung unten unvorhersagbar machen.
        seed.create_login_tenant_user(
            session,
            "browsertest-mieter-reiter",
            _PASSWORD,
            [
                ("zone.read", zone_a.id),
                ("zone.read", zone_b.id),
                ("setpoint.write", zone_a.id),
                ("setpoint.write", zone_b.id),
            ],
        )
        session.commit()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/login")
    page.get_by_label("Benutzername").fill("browsertest-mieter-reiter")
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    page.goto("/schedule")

    tabs = page.locator(".tc-roomtab")
    expect(tabs).to_have_count(2)

    for tab in tabs.all():
        # Ein einzeiliger Reiter ist nie höher als breit knapp bemessen -- er
        # bleibt nahe seiner `min-height` (2.75rem = 44px). Bricht das Wort
        # (oder der ganze Text) auf eine zweite Zeile um, wächst die Höhe
        # sichtbar darüber hinaus. Das prüft das tatsächliche Rendering, nicht
        # nur eine CSS-Eigenschaft, die selbst nichts über das Ergebnis sagt.
        box = tab.bounding_box()
        assert box is not None
        assert box["height"] < 50, (
            f"Reiter '{tab.inner_text()}' ist {box['height']}px hoch -- "
            "der Text bricht auf eine zweite Zeile um."
        )

    # Die Leiste selbst darf bei vielen/langen Räumen nicht über den Bildschirm
    # hinausschießen -- sie muss stattdessen in sich selbst scrollbar sein.
    tabs_bar = page.locator(".tc-roomtabs")
    bar_box = tabs_bar.bounding_box()
    assert bar_box is not None
    viewport_overflow = bar_box["x"] + bar_box["width"]
    assert viewport_overflow <= 390 + 1, (
        "Die Reiterleiste ragt über den Bildschirm hinaus, statt zu scrollen."
    )
