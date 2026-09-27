"""Die Tintenfläche eines Icon-Glyphen ("−"/"+" in den Sollwert-Steppern) muss
in der Mitte ihres Knopfes sitzen, nicht nur seine Zeilenbox.

Meldung des Projektinhabers: "Auf der Übersichtsseite sind manche Icons in den
Buttons nicht zentriert." Nur ein echter Browser kann das zeigen -- Ort und Größe
einer Textzeile hängen von Schriftmetriken ab, die eine HTTP-Antwort nicht kennt.

Eine reine `Range.getBoundingClientRect()`-Prüfung genügt dafür nicht: sie misst
die *Zeilenbox* der Schrift, nicht die tatsächlich gezeichnete Tinte. Die
Halb-Vorlauf-Aufteilung ("half-leading"), mit der eine Schrift ihre Zeilenbox
aus Ascent/Descent bildet, ist nicht zwingend symmetrisch um das sichtbare
Zeichen -- ein Zeichen kann exakt in der Mitte seiner Zeilenbox sitzen und
trotzdem sichtbar zu tief oder zu hoch wirken. Deshalb misst
`_glyph_ink_offsets()` unten stattdessen über
`CanvasRenderingContext2D.measureText()`s `actualBoundingBox*`-Metriken (siehe
MDN) die echte Tintenausdehnung des gerenderten Zeichens, an derselben Stelle
positioniert wie die Zeilenbox (Basislinie über die Halb-Vorlauf-Regel plus
Schriftmetrik hergeleitet). Vor der Behebung (siehe `thermoctl.css`, `.tc-stage`,
`.kiosk-stage`, `.tc-stepbtn`) lag die Tinte an allen drei Stellen 1--2 px
unterhalb der Knopfmitte; eine reine Zeilenbox-Prüfung hätte das nicht gezeigt,
weil `display: flex` mit `align-items: center` die Zeilenbox selbst bereits
zentriert hatte, ohne die Tinte darin zu verschieben.
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

# Misst je Element, das `selector` trifft, ob dessen Textinhalt aus höchstens
# zwei Zeichen besteht (ein Icon-Glyph wie "−"/"+", keine Beschriftung wie
# "Übersteuern") die Abweichung der *Tintenmitte* von der Mitte der
# Knopf-Innenfläche (ohne Rahmen). Siehe Modul-Docstring für die Herleitung.
_MEASURE_JS = """
(selector) => {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const out = [];
  for (const btn of document.querySelectorAll(selector)) {
    const rect = btn.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const txt = btn.textContent.trim();
    if (txt.length === 0 || txt.length > 2) continue;
    const cs = getComputedStyle(btn);
    const bl = parseFloat(cs.borderLeftWidth) || 0;
    const br = parseFloat(cs.borderRightWidth) || 0;
    const bt = parseFloat(cs.borderTopWidth) || 0;
    const bb = parseFloat(cs.borderBottomWidth) || 0;
    const btnCenterX = (rect.left + bl + rect.right - br) / 2;
    const btnCenterY = (rect.top + bt + rect.bottom - bb) / 2;

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

    out.push({
      cls: btn.className,
      text: txt,
      dx: (inkLeft + inkRight) / 2 - btnCenterX,
      dy: (inkTop + inkBottom) / 2 - btnCenterY,
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
        f"Icon-Tinte nicht zentriert (> {_TOLERANCE_PX} px Abweichung): {failures}\n"
        f"Alle Messungen: {offsets}"
    )


def test_the_thermostat_stepper_icons_are_ink_centered_on_the_start_page(
    admin_page: Page, live_server: LiveServer
) -> None:
    """`.tc-stage` auf `/` (Anlagensicht) -- der ursprünglich gemeldete Knopf."""
    with live_server.session() as session:
        seed.create_schedule_zone(session, "icon-zentrierung-start")
        session.commit()

    admin_page.goto("/")
    offsets = _glyph_ink_offsets(admin_page, "button.tc-stage")
    _assert_centered(offsets, expected_min_count=2)


def test_the_thermostat_stepper_icons_are_ink_centered_on_the_tenant_start_page(
    page: Page, live_server: LiveServer
) -> None:
    """`.tc-stepbtn` auf der Wohnungssicht-Startseite -- dieselbe Ursache, eine
    eigene CSS-Klasse (siehe `thermoctl.css`)."""
    password = "Icon-Zentrierung-Mieter-7"  # noqa: S105 -- ephemeral local test account
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "icon-zentrierung-wohnung")
        seed.create_login_tenant_user(
            session, "icon-zentrierung-mieter", password,
            [("zone.read", None), ("setpoint.write", zone.id)],
        )
        session.commit()

    _login_as_tenant(page, "icon-zentrierung-mieter", password)
    offsets = _glyph_ink_offsets(page, "button.tc-stepbtn")
    _assert_centered(offsets, expected_min_count=2)


def test_the_setpoint_stepper_icons_are_ink_centered_on_the_kiosk(
    page: Page, live_server: LiveServer
) -> None:
    """`.kiosk-stage` auf `/kiosk/{token}` -- die größte der drei Tasten (48 px),
    dort war die Abweichung vor der Behebung auch am größten (rund 2 px)."""
    with live_server.session() as session:
        zone = seed.create_constant_schedule_zone(session, "icon-zentrierung-kiosk")
        session.flush()
        admin = session.scalar(select(User).where(User.username == live_server.admin_username))
        assert admin is not None
        _token, plaintext = issue_kiosk_token(
            session, admin, "Icon-Zentrierung-Panel", [zone.id],
            control_allowed=True, expires_at=None,
        )
        session.commit()

    page.goto(f"/kiosk/{plaintext}")
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
