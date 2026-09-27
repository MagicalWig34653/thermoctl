"""Die Fläche eines Icon-Glyphen (die "−"/"+"-Sollwert-Stepper) muss in der
Mitte ihres Knopfes sitzen -- unabhängig von der Schrift, in der der Browser
rendert.

Meldung des Projektinhabers: "Auf der Übersichtsseite sind manche Icons in den
Buttons nicht zentriert." Nur ein echter Browser kann das zeigen -- Ort und
Größe einer Textzeile hängen von Schriftmetriken ab, die eine HTTP-Antwort
nicht kennt.

**Erste Fassung (Commit 029de74) war fehlerhaft, per Kreuzreview gefunden.**
Sie beließ es bei den Textzeichen "−"/"+" und glich die Font-Metrik-Asymmetrie
über ein an der macOS-Systemschrift (`-apple-system`, da `--font-ui`s erster
Eintrag "Inter" keine eingebundene Webschrift ist) kalibriertes, asymmetrisches
`padding-block` aus. Auf jeder anderen Schrift überkorrigierte das in die
Gegenrichtung -- am deutlichsten gemessen mit `Arial` (dy ≈ -2,25 px statt der
vorher positiven +1,3 px). Am Kiosk-Wandtablett mit unbekanntem Betriebssystem
wäre das potenziell schlimmer als der ursprüngliche Fund. `_prove_the_old_
padding_hack_was_font_specific` unten belegt das anhand der historischen
CSS-Regel, ohne die echte Anwendung dafür zu verändern.

**Jetzige Fassung:** `stepper_icon()` (`icons.html`) liefert ein Inline-SVG mit
fester, zu seiner eigenen Bounding-Box symmetrischer Pfadgeometrie statt eines
Zeichens. `display: flex`/`align-items: center` zentriert dessen *tatsächliche*
Fläche, nicht nur eine schriftabhängige Zeilenbox -- das Ergebnis hängt nicht
mehr von der Schrift ab, mit der der Browser rendert. Genau das prüfen die vier
Tests unten, indem sie `--font-ui` (die Variable, die `body`s `font-family`
setzt, `thermoctl.css`) der Reihe nach auf vier verbreitete, garantiert nicht
zueinander metrisch identische Schriften umstellen (Arial, Times New Roman,
Courier New, Georgia) und danach dieselbe Zentrierung verlangen wie ohne
Umstellung.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from playwright.sync_api import Page
from sqlalchemy import select

from browser_tests import seed
from browser_tests.conftest import LiveServer
from browser_tests.test_tenant_ui import _login_as_tenant
from tests.helpers import source
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.override import ZoneOverride
from thermoctl.domain.kiosk import issue_kiosk_token

pytestmark = pytest.mark.browser

# Ab hier gilt ein Fund: bei 1 px optischer Abweichung sieht ein Auge noch
# nichts, aber die Messung selbst ist bei Sub-Pixel-Rundung der Layout-Engine
# nicht beliebig genau -- 1 px ist der in der Aufgabenstellung vorgegebene
# Schwellenwert.
_TOLERANCE_PX = 1.0

# Vier Schriften mit deutlich unterschiedlicher Ascent-/Descent-Metrik,
# allesamt auf macOS **und** in der CI (Linux, über Fontconfig-Ersatz) verfügbar
# -- keine davon ist `-apple-system`, an dem die erste Fassung kalibriert war.
_TEST_FONTS = ("Arial", "'Times New Roman'", "'Courier New'", "Georgia")


def _set_ui_font(page: Page, font_family: str) -> None:
    """Überschreibt `--font-ui` (bestimmt `body`s `font-family`,
    `thermoctl.css`) nach dem Laden der Seite. Wirkt auf die ganze Seite,
    nicht nur auf die Icon-Knöpfe -- das ist gewollt: `--font-ui` ist genau die
    eine Stelle, über die eine reale Anlage (oder ein Kiosk-Wandtablett mit
    einem anderen Betriebssystem) tatsächlich eine andere Schrift bekäme."""
    page.add_style_tag(content=f":root {{ --font-ui: {font_family}; }}")


# Misst je Element, das `selector` trifft, die Abweichung seiner Icon-Fläche
# von der Mitte der Knopf-Innenfläche (ohne Rahmen). Ein SVG-Icon (heutiger
# Stand) hat eine zu seiner eigenen Bounding-Box symmetrische Pfadgeometrie
# (siehe `icons.html`) -- seine `getBoundingClientRect()` ist deshalb bereits
# die Tintenfläche, ohne Schriftmetrik-Umweg. Ein Textzeichen (historischer
# Stand, siehe `_prove_the_old_padding_hack_was_font_specific` unten) braucht
# stattdessen `CanvasRenderingContext2D.measureText()`s
# `actualBoundingBox*`-Metriken (echte Tintenausdehnung, MDN), weil eine reine
# `Range.getBoundingClientRect()`-Prüfung nur die *Zeilenbox* der Schrift
# misst, nicht die tatsächlich gezeichnete Tinte -- deren Lage relativ zur
# Zeilenbox hängt von Ascent/Descent der jeweiligen Schrift ab, nicht vom
# Zeichen selbst.
_MEASURE_JS = """
(selector) => {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const out = [];
  for (const btn of document.querySelectorAll(selector)) {
    const rect = btn.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const cs = getComputedStyle(btn);
    const bl = parseFloat(cs.borderLeftWidth) || 0;
    const br = parseFloat(cs.borderRightWidth) || 0;
    const bt = parseFloat(cs.borderTopWidth) || 0;
    const bb = parseFloat(cs.borderBottomWidth) || 0;
    const btnCenterX = (rect.left + bl + rect.right - br) / 2;
    const btnCenterY = (rect.top + bt + rect.bottom - bb) / 2;

    const svg = btn.querySelector('svg');
    let inkCenterX, inkCenterY;
    if (svg) {
      const svgRect = svg.getBoundingClientRect();
      if (svgRect.width === 0 || svgRect.height === 0) continue;
      inkCenterX = (svgRect.left + svgRect.right) / 2;
      inkCenterY = (svgRect.top + svgRect.bottom) / 2;
    } else {
      const txt = btn.textContent.trim();
      if (txt.length === 0 || txt.length > 2) continue;
      const walker = document.createTreeWalker(btn, NodeFilter.SHOW_TEXT, null);
      let node, lastNode = null;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      let found = false;
      while ((node = walker.nextNode())) {
        if (!node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const r of range.getClientRects()) {
          if (r.width === 0 || r.height === 0) continue;
          found = true;
          minX = Math.min(minX, r.left); minY = Math.min(minY, r.top);
          maxX = Math.max(maxX, r.right); maxY = Math.max(maxY, r.bottom);
          lastNode = node;
        }
      }
      if (!found) continue;
      const lineBox = { left: minX, top: minY, width: maxX - minX, height: maxY - minY };

      const parentStyle = getComputedStyle(lastNode.parentElement);
      ctx.font = `${parentStyle.fontStyle} ${parentStyle.fontVariant} ` +
                 `${parentStyle.fontWeight} ${parentStyle.fontSize} ${parentStyle.fontFamily}`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      const metrics = ctx.measureText(lastNode.textContent.trim());
      const fontBoxHeight = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
      const halfLeading = (lineBox.height - fontBoxHeight) / 2;
      const baselineY = lineBox.top + halfLeading + metrics.fontBoundingBoxAscent;
      const inkTop = baselineY - metrics.actualBoundingBoxAscent;
      const inkBottom = baselineY + metrics.actualBoundingBoxDescent;
      const inkLeft = lineBox.left - metrics.actualBoundingBoxLeft;
      const inkRight = lineBox.left + metrics.actualBoundingBoxRight;
      inkCenterX = (inkLeft + inkRight) / 2;
      inkCenterY = (inkTop + inkBottom) / 2;
    }

    out.push({
      cls: btn.className,
      kind: svg ? 'svg' : 'text',
      dx: inkCenterX - btnCenterX,
      dy: inkCenterY - btnCenterY,
    });
  }
  return out;
}
"""


def _glyph_ink_offsets(page: Page, selector: str) -> list[dict]:
    return page.evaluate(_MEASURE_JS, selector)


def _assert_centered(offsets: list[dict], *, expected_min_count: int) -> None:
    assert len(offsets) >= expected_min_count, (
        f"Erwartete mindestens {expected_min_count} Icon-Knöpfe, gefunden: {offsets}"
    )
    failures = [
        o for o in offsets if abs(o["dx"]) > _TOLERANCE_PX or abs(o["dy"]) > _TOLERANCE_PX
    ]
    assert not failures, (
        f"Icon-Fläche nicht zentriert (> {_TOLERANCE_PX} px Abweichung): {failures}\n"
        f"Alle Messungen: {offsets}"
    )


@pytest.mark.parametrize("font_family", _TEST_FONTS)
def test_the_thermostat_stepper_icons_are_ink_centered_on_the_start_page(
    admin_page: Page, live_server: LiveServer, font_family: str
) -> None:
    """`.tc-stage` auf `/` (Anlagensicht) -- der ursprünglich gemeldete Knopf,
    jetzt gegen vier unterschiedliche Schriften geprüft (siehe Modul-Docstring:
    genau das hätte die erste, textbasierte Fassung nicht überstanden)."""
    with live_server.session() as session:
        seed.create_schedule_zone(session, f"icon-zentrierung-start-{font_family}")
        session.commit()

    admin_page.goto("/")
    _set_ui_font(admin_page, font_family)
    offsets = _glyph_ink_offsets(admin_page, "button.tc-stage")
    _assert_centered(offsets, expected_min_count=2)


@pytest.mark.parametrize("font_family", _TEST_FONTS)
def test_the_thermostat_stepper_icons_are_ink_centered_on_the_tenant_start_page(
    page: Page, live_server: LiveServer, font_family: str
) -> None:
    """`.tc-stepbtn` auf der Wohnungssicht-Startseite -- dieselbe Ursache, eine
    eigene CSS-Klasse (siehe `thermoctl.css`)."""
    password = "Icon-Zentrierung-Mieter-7"  # noqa: S105 -- ephemeral local test account
    username = f"icon-zentrierung-mieter-{abs(hash(font_family))}"[:32]
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, f"icon-zentrierung-wohnung-{font_family}")
        seed.create_login_tenant_user(
            session, username, password,
            [("zone.read", None), ("setpoint.write", zone.id)],
        )
        session.commit()

    _login_as_tenant(page, username, password)
    _set_ui_font(page, font_family)
    offsets = _glyph_ink_offsets(page, "button.tc-stepbtn")
    _assert_centered(offsets, expected_min_count=2)


@pytest.mark.parametrize("font_family", _TEST_FONTS)
def test_the_setpoint_stepper_icons_are_ink_centered_on_the_kiosk(
    page: Page, live_server: LiveServer, font_family: str
) -> None:
    """`.kiosk-stage` auf `/kiosk/{token}` -- die größte der drei Tasten (48 px),
    an der die frühere, textbasierte Fassung auch die größte Abweichung hatte
    (rund 2 px auf der macOS-Systemschrift, rund 2,25 px in die Gegenrichtung
    auf Arial)."""
    with live_server.session() as session:
        zone = seed.create_constant_schedule_zone(
            session, f"icon-zentrierung-kiosk-{font_family}"
        )
        session.flush()
        admin = session.scalar(select(User).where(User.username == live_server.admin_username))
        assert admin is not None
        _token, plaintext = issue_kiosk_token(
            session, admin, f"Icon-Zentrierung-Panel-{font_family}", [zone.id],
            control_allowed=True, expires_at=None,
        )
        session.commit()

    page.goto(f"/kiosk/{plaintext}")
    _set_ui_font(page, font_family)
    offsets = _glyph_ink_offsets(page, "button.kiosk-stage")
    _assert_centered(offsets, expected_min_count=2)


def test_the_thermostat_stepper_icons_stay_centered_with_an_active_override(
    admin_page: Page, live_server: LiveServer
) -> None:
    """Regressionsanlass: der Screenshot beim Aufsetzen dieses Tests zeigte den
    Knopf zunächst nur ohne eine laufende Übersteuerung -- mit einer verschwindet
    das Thermostat (siehe `start.html`s Begründung dort), aber ein zweiter Knopf
    ("Übersteuerung aufheben") bleibt stehen. Kein Icon-Glyph dort, aber diese
    Zone stellt sicher, dass die Diagnose auch mit einer laufenden Übersteuerung
    im DOM anderer Karten keine falschen Treffer liefert."""
    now = utcnow()
    with live_server.session() as session:
        zone_plain = seed.create_schedule_zone(session, "icon-zentrierung-mit-override-plain")
        zone_override = seed.create_schedule_zone(session, "icon-zentrierung-mit-override")
        session.add(ZoneOverride(
            zone_id=zone_override.id, temperature_c=Decimal("22.0"),
            starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=6),
            source_id=source(session).id,
        ))
        session.commit()
        assert zone_plain.id  # nur zur Klarheit referenziert, kein separater Check

    admin_page.goto("/")
    offsets = _glyph_ink_offsets(admin_page, "button.tc-stage")
    _assert_centered(offsets, expected_min_count=2)


def test_the_old_font_specific_padding_hack_fails_on_a_different_font(page: Page) -> None:
    """Belegt den Kreuzreview-Fund, ohne die echte Anwendung dafür zurückzudrehen:
    baut den historischen Stand aus Commit 029de74 (Textzeichen "−"/"+" plus ein
    an `-apple-system` bemessenes `padding-block: 0 0.27rem` -- der `.kiosk-stage`-
    Wert, dort mit der größten Abweichung) auf einer eigenständigen Seite nach und
    misst mit genau demselben `_MEASURE_JS` wie oben. Unter Arial (nicht die
    Schrift, an der der Wert kalibriert wurde) muss die Abweichung die 1-px-Grenze
    reißen -- sonst wäre der gemeldete Kreuzreview-Fund nicht reproduzierbar."""
    page.set_content(
        """
        <!doctype html>
        <html><head><style>
            body { margin: 0; }
            .kiosk-stage {
                font-size: 1.5rem;
                width: 3rem;
                height: 3rem;
                line-height: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                padding-block: 0 0.27rem;
                font-family: Arial, sans-serif;
            }
        </style></head>
        <body>
            <button class="kiosk-stage" type="button">−</button>
            <button class="kiosk-stage" type="button">+</button>
        </body>
        </html>
        """
    )
    offsets = _glyph_ink_offsets(page, "button.kiosk-stage")
    assert len(offsets) == 2, offsets
    worst = max(abs(o["dy"]) for o in offsets)
    assert worst > _TOLERANCE_PX, (
        f"Erwartete eine Abweichung > {_TOLERANCE_PX} px mit dem alten, "
        f"font-spezifischen `padding-block`-Ausgleich unter Arial, gemessen "
        f"aber nur {worst:.2f} px -- der Kreuzreview-Fund wäre damit nicht "
        f"reproduzierbar: {offsets}"
    )
