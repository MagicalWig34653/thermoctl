"""The global loading bar (`#tc-loading-bar`, `loading_indicator.js`).

Anlass: als Home-Assistant-Add-on hinter Ingress reagiert thermoctl spuerbar
traeger (zusaetzlicher Proxy-Sprung). Die Latenz laesst sich nicht wegnehmen, aber
sichtbar machen -- ein Balken, der bei einer langsamen Antwort nach kurzer
Verzoegerung erscheint, bei einer schnellen Antwort **nicht** aufblitzt, und in
beiden Faellen -- Erfolg wie Fehlschlag -- wieder verschwindet.

Kein HTTP-Test kann das pruefen: Timing- und CSS-Zustand existieren nur im
laufenden Browser. Jede Anfrage hier wird ueber `page.route()` kuenstlich verzoegert
oder scheitern gelassen, statt auf eine zufaellig langsame echte Antwort zu hoffen.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.browser

_SICHTBAR = re.compile(r"\btc-loading-bar-sichtbar\b")


def _verzoegern(sekunden: float) -> Callable[[Route], None]:
    def handler(route: Route) -> None:
        time.sleep(sekunden)
        route.continue_()

    return handler


def test_the_loading_bar_appears_after_a_delay_for_a_slow_request(admin_page: Page) -> None:
    """800 ms Antwortzeit liegt deutlich ueber der 400-ms-Verzoegerung in
    `loading_indicator.js` -- der Balken muss sichtbar werden, bevor die Anfrage
    fertig ist, aber nicht sofort.

    Zeitpunkte werden im Browser selbst mitgeschrieben (ein `setInterval`), nicht
    auf der Python-Seite an einem einzelnen festen Moment abgefragt: ein
    `.click()` auf einen boosteten Link wartet in Playwright auf die daraus
    folgende `pushState`-Navigation, blockiert also bis die Anfrage (samt der
    kuenstlichen Verzoegerung) durchgelaufen ist -- Python-seitige Zeitstempel
    rund um den Klick sagen dadurch nichts uber den tatsaechlichen Ablauf im
    Browser. `no_wait_after=True` umgeht die Blockade; die im Browser
    mitgeschriebene Zeitreihe bleibt trotzdem die einzig verlaessliche Quelle.
    """
    admin_page.route("**/control", _verzoegern(0.8))
    admin_page.evaluate(
        """() => {
            window.__start = Date.now();
            window.__zeitreihe = [];
            window.__intervall = window.setInterval(() => {
                const balken = document.getElementById('tc-loading-bar');
                window.__zeitreihe.push([
                    Date.now() - window.__start,
                    balken.classList.contains('tc-loading-bar-sichtbar'),
                ]);
            }, 20);
        }"""
    )

    admin_page.locator("#main-navigation").get_by_role(
        "link", name="Betrieb", exact=True
    ).click(no_wait_after=True)
    # 800 ms Anfrage plus Sicherheitsabstand nach beiden Seiten.
    admin_page.wait_for_timeout(1300)
    admin_page.evaluate("window.clearInterval(window.__intervall)")

    zeitreihe = admin_page.evaluate("window.__zeitreihe")
    assert zeitreihe, "Zeitreihe leer -- der Test misst nichts"

    erster_sichtbare_zeitpunkt = next((t for t, sichtbar in zeitreihe if sichtbar), None)
    assert erster_sichtbare_zeitpunkt is not None, (
        "Balken wurde waehrend der 800-ms-Anfrage nie sichtbar"
    )
    # Grosszuegiger Sicherheitsabstand zur 400-ms-Schwelle nach unten, damit die
    # eigentliche Aussage -- "nicht sofort, sondern erst nach einer Verzoegerung"
    # -- auch unter Last im Testlauf nicht an ein paar Millisekunden Jitter
    # scheitert.
    assert erster_sichtbare_zeitpunkt > 200, (
        f"Balken war schon nach {erster_sichtbare_zeitpunkt} ms sichtbar -- das "
        "ist zu frueh fuer die 400-ms-Verzoegerung und wuerde bei jeder "
        "schnellen Antwort aufblitzen"
    )

    # Und nach dem Eintreffen der Antwort (spaetestens bei 800 ms) wieder weg.
    spaete_zeitpunkte = [sichtbar for t, sichtbar in zeitreihe if t > 1000]
    assert spaete_zeitpunkte and not any(spaete_zeitpunkte), (
        "Balken ist auch lange nach dem Eintreffen der Antwort noch sichtbar"
    )


def test_the_loading_bar_does_not_flash_for_a_fast_request(admin_page: Page) -> None:
    """50 ms liegt deutlich unter der 400-ms-Verzoegerung -- der Balken darf zu
    keinem Zeitpunkt sichtbar werden, nicht einmal kurz."""
    admin_page.route("**/devices", _verzoegern(0.05))

    admin_page.evaluate(
        """() => {
            window.__ladebeobachtung = [];
            window.__ladeIntervall = window.setInterval(() => {
                const balken = document.getElementById('tc-loading-bar');
                window.__ladebeobachtung.push(
                    balken.classList.contains('tc-loading-bar-sichtbar')
                );
            }, 15);
        }"""
    )

    admin_page.locator("#main-navigation").get_by_role(
        "link", name="Geräte", exact=True
    ).click(no_wait_after=True)
    admin_page.get_by_role("heading", name="Geräte", exact=True).wait_for()
    # Weit ueber die 400-ms-Schwelle hinaus beobachten, falls der Balken doch
    # (fehlerhaft) verzoegert erschiene.
    admin_page.wait_for_timeout(500)
    admin_page.evaluate("window.clearInterval(window.__ladeIntervall)")

    beobachtungen = admin_page.evaluate("window.__ladebeobachtung")
    assert beobachtungen, "Beobachtungsintervall lief nie -- Test misst nichts"
    assert not any(beobachtungen), (
        "Balken war waehrend einer 50-ms-Anfrage sichtbar -- er blitzt bei "
        "schnellen Antworten auf"
    )


def test_the_loading_bar_disappears_again_after_a_failed_request(admin_page: Page) -> None:
    """Auch wenn die Anfrage scheitert, darf der Balken nicht ewig weiterlaufen
    und einen Ladezustand vortaeuschen, der nicht mehr stimmt."""

    def scheitern(route: Route) -> None:
        time.sleep(0.6)
        route.fulfill(status=500, body="kuenstlicher Fehler")

    admin_page.route("**/zones", scheitern)
    balken = admin_page.locator("#tc-loading-bar")

    admin_page.locator("#main-navigation").get_by_role(
        "link", name="Zonen", exact=True
    ).click(no_wait_after=True)

    expect(balken).to_have_class(_SICHTBAR, timeout=1000)
    expect(balken).not_to_have_class(_SICHTBAR, timeout=2000)
