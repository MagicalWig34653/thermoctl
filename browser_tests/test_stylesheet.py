"""Is the stylesheet actually applied -- not just present as a `<link>` in the HTML.

This is the check that would have caught the second historical incident named in
the task ("eine Oberfläche ohne eingebundenes Stylesheet"): a smoke test that
merely greps the HTML for `<link rel="stylesheet">` still passes when the file
404s, is empty, or never loaded because of a path typo. Only a real browser knows
whether a rule actually took effect on a rendered element.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

# Woran man erkennt, dass `thermoctl.css` wirklich gewirkt hat.
#
# Ein fester Farbwert taugt dafür nicht: er scheitert bei jeder gewollten
# Farbanpassung, obwohl das Stylesheet einwandfrei geladen ist -- und ein Test,
# der aus dem falschen Grund rot wird, wird irgendwann angepasst statt gelesen.
# Seit dem Redesign v0.9.0 wäre er zusätzlich stumpf: die Primärfarbe ist jetzt
# selbst ein Blau (#2463eb) und liegt damit nah an Bootstraps eigenem (#0d6efd).
#
# Stattdessen eine Eigenschaft, die **nur** dieses Stylesheet überhaupt setzt: die
# Gestaltungsvariablen auf `:root`. Bootstrap kennt sie nicht, der Browser bringt
# sie nicht mit -- fehlt `thermoctl.css`, sind sie schlicht leer. Das ist genau die
# Aussage, für die dieser Test da ist, und sie überlebt jede Farbänderung.
_THERMOCTL_TOKENS = ("--warmth", "--cool", "--ink", "--surface", "--radius")


def _tokens(page: Page) -> dict[str, str]:
    return page.evaluate(
        """(names) => {
            const wurzel = getComputedStyle(document.documentElement);
            return Object.fromEntries(
                names.map((name) => [name, wurzel.getPropertyValue(name).trim()])
            );
        }""",
        list(_THERMOCTL_TOKENS),
    )


def test_the_stylesheets_own_design_tokens_actually_reach_the_page(page: Page) -> None:
    page.goto("/login")
    expect(page.get_by_role("button", name="Anmelden")).to_be_visible()

    werte = _tokens(page)
    leer = [name for name, wert in werte.items() if not wert]
    assert not leer, (
        f"Diese Gestaltungsvariablen sind leer: {leer}. Sie stehen ausschließlich in "
        "thermoctl.css -- fehlen sie, ist das Stylesheet nicht geladen (404, "
        "Pfadfehler, leere Datei), und die Seite trägt nur noch Bootstraps eigene "
        "Gestaltung."
    )


def test_the_primary_button_is_not_bootstraps_untouched_default(page: Page) -> None:
    """Zweiter, unabhängiger Nachweis: die Variablen oben zeigen, dass die Datei
    geladen ist -- dieser hier, dass ihre Regeln auch tatsächlich greifen.

    Ein völlig ungestylter Knopf ist in Chromium durchsichtig; Bootstraps eigener
    ist `#0d6efd`. Beides hätte eine Seite ohne wirksames thermoctl.css.
    """
    page.goto("/login")
    button = page.get_by_role("button", name="Anmelden")
    expect(button).to_be_visible()

    background = button.evaluate("el => getComputedStyle(el).backgroundColor")
    radius = button.evaluate("el => getComputedStyle(el).borderRadius")
    assert background not in ("rgb(13, 110, 253)", "rgba(0, 0, 0, 0)"), background
    # `--radius-small` (10 px) statt Bootstraps 0.375 rem (6 px): eine Regel dieses
    # Stylesheets, die keine Farbe ist und deshalb von einer Palettenänderung
    # unberührt bleibt.
    assert radius.startswith("10px"), (
        f"Eckenradius war {radius!r}, erwartet 10px aus `--radius-small`. "
        "Bootstraps unveränderter Wert wäre 6px."
    )


def test_the_instrument_font_differs_from_the_interface_font(page: Page) -> None:
    """`.t-value` (temperatures, durations, clock digits) uses a monospace
    "instrument" font on purpose, so numbers stacked above each other actually
    line up (thermoctl.css, "Schrift" section) -- a second, independent rule this
    stylesheet is responsible for, distinct from the button colour above.
    """
    page.goto("/login")
    body_font, value_font = page.evaluate(
        """() => {
            const probe = document.createElement('span');
            probe.className = 't-value';
            probe.textContent = '00:00';
            document.body.appendChild(probe);
            const result = [
                getComputedStyle(document.body).fontFamily,
                getComputedStyle(probe).fontFamily,
            ];
            probe.remove();
            return result;
        }"""
    )
    assert value_font != body_font, (
        f"Instrumentschrift ({value_font!r}) und Fliesstext ({body_font!r}) sind "
        "gleich -- thermoctl.css scheint für .t-value keine eigene Schrift zu setzen."
    )
