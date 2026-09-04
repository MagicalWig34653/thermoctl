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

# thermoctl/web/static/thermoctl.css overrides Bootstrap's default primary button
# colour (`#0d6efd`, rgb(13, 110, 253)) with a deliberately un-blue slate
# (`--bs-btn-bg: #2f3941`). If thermoctl.css failed to load, the button would keep
# Bootstrap's own blue -- a difference visible in the computed style, not the markup.
_THERMOCTL_PRIMARY = "rgb(47, 57, 65)"
_BOOTSTRAP_DEFAULT_PRIMARY = "rgb(13, 110, 253)"


def test_the_primary_button_carries_thermoctls_own_colour_not_bootstraps_default(
    page: Page,
) -> None:
    page.goto("/login")
    button = page.get_by_role("button", name="Anmelden")
    expect(button).to_be_visible()

    background = button.evaluate("el => getComputedStyle(el).backgroundColor")
    assert background == _THERMOCTL_PRIMARY, (
        f"Errechnete Hintergrundfarbe war {background!r}, erwartet {_THERMOCTL_PRIMARY!r} "
        f"(thermoctl.css). Bootstraps unverändertes Standardblau wäre "
        f"{_BOOTSTRAP_DEFAULT_PRIMARY!r} -- das hätte eine Seite, auf der "
        "thermoctl.css nicht geladen ist."
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
