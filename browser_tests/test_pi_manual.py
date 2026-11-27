"""I-Anteil zurücksetzen und setzen im echten Browser (Karte A391AABC): Bestätigung, Eingabeprüfung,
vorgemerkter Eingriff, kein Schalter ohne Recht -- hell und dunkel.

Der Server läuft ohne Regelschleife: Der Eingriff bleibt hier vorgemerkt, und der angezeigte Wert
ändert sich nicht. Dass der nächste Zyklus ihn übernimmt, belegt
`tests/test_pi_manual_intervention.py`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import delete, select

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_login_user, create_schedule_zone
from tests.helpers import sensor_status_of
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import PiManualRequest, ZoneState

pytestmark = pytest.mark.browser

READER = ("i-leser", "Leser-Kennwort-7")
SHOTS = os.environ.get("THERMOCTL_SHOT_DIR")


@pytest.fixture(scope="module")
def room(live_server: LiveServer) -> int:
    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Arbeitszimmer")
        zone.pi_enabled = True
        session.add(
            ZoneState(
                zone_id=zone.id,
                temperature_c=Decimal("20.4"),
                measured_at=now,
                sensor_status_id=sensor_status_of(session).id,
                window_open=False,
                updated_at=now,
                pi_integral=Decimal("0.425"),
            )
        )
        create_login_user(session, *READER, permissions=[("zone.read", None)])
        session.commit()
        return zone.id


def _sign_in(page: Page, username: str, password: str) -> None:
    page.goto("/login")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort").fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.get_by_test_id("room-pi").screenshot(path=str(Path(SHOTS) / f"{name}.png"))


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_reset_and_set_need_a_confirmation_and_stay_pending(
    browser: Browser, live_server: LiveServer, room: int, scheme: str
) -> None:
    with live_server.session() as session:  # der Server und sein Raum gelten für beide Läufe
        session.execute(delete(PiManualRequest).where(PiManualRequest.zone_id == room))
        session.commit()
    context = browser.new_context(
        base_url=live_server.base_url, color_scheme=scheme, viewport={"width": 1100, "height": 1000}
    )
    errors: list[str] = []
    page = context.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        _sign_in(page, live_server.admin_username, live_server.admin_password)
        page.goto(f"/app/raeume/{room}")
        card = page.get_by_test_id("room-pi")
        value = card.get_by_test_id("room-pi-value")
        expect(value).to_have_text("42,5 %")
        expect(card.get_by_test_id("room-pi-pending")).to_have_count(0)

        # Zurücksetzen: erst die Bestätigung, Abbrechen ändert nichts.
        card.get_by_test_id("room-pi-reset-open").click()
        confirm = card.get_by_test_id("room-pi-confirm")
        expect(confirm).to_contain_text("auf 0 % zurücksetzen?")
        _shot(page, f"{scheme}-1-bestaetigung-reset")
        confirm.get_by_role("button", name="Abbrechen").click()
        expect(confirm).to_have_count(0)
        expect(card.get_by_test_id("room-pi-pending")).to_have_count(0)

        card.get_by_test_id("room-pi-reset-open").click()
        card.get_by_test_id("room-pi-confirm-go").click()
        pending = card.get_by_test_id("room-pi-pending")
        expect(pending).to_contain_text("I-Anteil zurücksetzen")
        expect(pending).to_contain_text("nächsten Regelzyklus")
        expect(value).to_have_text("42,5 %")  # Wirkung erst im nächsten Zyklus
        expect(card.get_by_test_id("room-pi-confirm")).to_have_count(0)

        # Setzen: ungültige Eingaben lassen den Knopf gesperrt, gültige gehen durch die Bestätigung.
        card.get_by_test_id("room-pi-set-open").click()
        go = card.get_by_test_id("room-pi-confirm-go")
        field = card.get_by_label("Neuer I-Anteil in Prozent")
        expect(go).to_be_disabled()
        field.fill("150")
        expect(card).to_contain_text("Bitte einen Wert von 0 bis 100 Prozent angeben.")
        expect(go).to_be_disabled()
        _shot(page, f"{scheme}-2-eingabe-ungueltig")
        field.fill("30,5")
        expect(go).to_be_enabled()
        _shot(page, f"{scheme}-3-bestaetigung-setzen")
        go.click()
        expect(pending).to_contain_text("auf 30,5 % setzen")
        expect(value).to_have_text("42,5 %")
        _shot(page, f"{scheme}-4-vorgemerkt")

        # Beide Eingriffe stehen im Audit-Protokoll, mit dem Konto des Verwalters.
        with live_server.session() as session:
            actions = [
                event.action
                for event in session.scalars(
                    select(AuditEvent).where(AuditEvent.object_id == str(room))
                    .order_by(AuditEvent.id)
                )
                if event.action.startswith("pi.")
            ]
        assert actions[-2:] == ["pi.integral_reset", "pi.integral_set"]

        # Das Neuladen zeigt den vorgemerkten Eingriff weiter (er steht auf dem Server).
        page.reload()
        expect(page.get_by_test_id("room-pi").get_by_test_id("room-pi-pending")).to_contain_text(
            "auf 30,5 % setzen"
        )
    finally:
        context.close()
    assert not errors, errors


def test_without_zone_manage_there_is_no_button(
    browser: Browser, live_server: LiveServer, room: int
) -> None:
    context = browser.new_context(base_url=live_server.base_url, color_scheme="light")
    page = context.new_page()
    try:
        _sign_in(page, *READER)
        page.goto(f"/app/raeume/{room}")
        card = page.get_by_test_id("room-pi")
        expect(card.get_by_test_id("room-pi-value")).to_be_visible()
        expect(card.get_by_test_id("room-pi-reset-open")).to_have_count(0)
        expect(card.get_by_test_id("room-pi-set-open")).to_have_count(0)
    finally:
        context.close()
