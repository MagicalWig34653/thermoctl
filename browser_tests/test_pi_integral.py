"""I-Anteil im echten Browser: Raumseite, Verlaufsreihe mit Reset-Marker, beide Kacheln.

Der Server läuft ohne Regelschleife (kein MQTT), die gesäten Werte bleiben also stehen.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.live_server import LiveServer
from browser_tests.seed import create_login_tenant_user, create_schedule_zone
from tests.helpers import sensor_status_of
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain.pi_control import (
    INTEGRATOR_CONTINUE,
    INTEGRATOR_RESET,
    RESET_REASON_NO_HEATING,
)

pytestmark = pytest.mark.browser

REASON = "Seit mehr als 24 Stunden gab es keine Wärmeanforderung."
TENANT = ("i-anteil-mieter", "Mieter-Kennwort-7")
#: Zimmer mit PI-Regler: (Name, aktueller I-Anteil)
ROOMS = (("Wohnzimmer", "0.425"), ("Büro", "0.08"), ("Bad", "0.77"))


def _seed_zone(session, name: str, integral: str, now: datetime) -> Zone:  # type: ignore[no-untyped-def]
    zone = create_schedule_zone(session, name)
    zone.pi_enabled = True
    session.add(
        ZoneState(
            zone_id=zone.id,
            temperature_c=Decimal("20.4"),
            measured_at=now,
            sensor_status_id=sensor_status_of(session).id,
            window_open=False,
            updated_at=now,
            pi_integral=Decimal(integral),
        )
    )
    final = float(integral)
    # 24 Stunden im Minutentakt wie die Regelschleife: Anstieg, Reset vor 14 h, 2 h ohne
    # PI-Wert (Lücke), danach Anstieg bis zum aktuellen Wert.
    for step in range(24 * 60, -1, -1):
        at = now - timedelta(minutes=step)
        hours = step / 60
        action = INTEGRATOR_CONTINUE
        reason = None
        value: float | None
        if hours > 14:
            value = 0.30 + (24 - hours) * 0.03
        elif hours == 14:
            value, action, reason = 0.0, INTEGRATOR_RESET, RESET_REASON_NO_HEATING
        elif 6 < hours <= 8:
            value = None
        else:
            value = final * (14 - hours) / 14
        session.add(
            ShadowDecision(
                decided_at=at,
                zone_id=zone.id,
                temperature_c=Decimal("20.0") + Decimal(step // 30 % 12) / Decimal(10),
                setpoint_c=Decimal("21.0"),
                scheduled_setpoint_c=Decimal("21.0"),
                setpoint_reason="Zeitplan",
                would_heat=step // 30 % 6 < 2,
                outcome_code="aus",
                reason="Sollwert ist erreicht.",
                requested_controller="pi",
                effective_controller="pi",
                pi_integral_after=None if value is None else Decimal(f"{value:.6f}"),
                pi_integrator_action=action,
                pi_reset_reason=reason,
            )
        )
    session.flush()
    return zone


@pytest.fixture(scope="module")
def zones(live_server: LiveServer) -> dict[str, int]:
    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    with live_server.session() as session:
        seeded = {name: _seed_zone(session, name, value, now).id for name, value in ROOMS}
        create_login_tenant_user(session, *TENANT, permissions=[("zone.read", None)])
        session.commit()
    return seeded


def sign_in_settled(page: Page, username: str, password: str) -> None:
    """Anmelden, erst wenn die Seite fertig geladen ist (sonst gehen unter Last Eingaben
    verloren). Am Handy steckt „Abmelden“ in der Schublade: angemeldet heißt, die Anmeldung
    ist weg."""
    page.goto("/login")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Benutzername").fill(username)
    page.get_by_label("Passwort").fill(password)
    page.get_by_role("button", name="Anmelden", exact=True).click()
    page.wait_for_url(lambda url: "/login" not in url)


def _history_card(page: Page):  # type: ignore[no-untyped-def]
    section = page.get_by_test_id("room-history")
    section.scroll_into_view_if_needed()
    expect(section.get_by_test_id("chart-surface")).to_be_visible()
    return section


def test_room_page_shows_value_reset_series_and_marker(
    admin_page: Page, zones: dict[str, int]
) -> None:
    admin_page.set_viewport_size({"width": 1280, "height": 900})
    admin_page.goto(f"/app/raeume/{zones['Wohnzimmer']}")
    card = admin_page.get_by_test_id("room-pi")
    expect(card.get_by_test_id("room-pi-value")).to_have_text("42,5 %")
    expect(card.get_by_test_id("room-pi-reset")).to_contain_text(REASON)

    history = _history_card(admin_page)
    # Die Lücke ohne PI-Wert trennt die Linie: mindestens zwei Stücke, nichts überbrückt.
    expect(history.get_by_test_id("pi-line").first).to_be_attached()
    assert history.get_by_test_id("pi-line").count() >= 2
    expect(history.get_by_test_id("pi-reset-mark")).to_have_count(1)
    expect(history.get_by_test_id("pi-axis").last).to_have_text("100 %")

    mark = history.get_by_test_id("pi-reset-mark").bounding_box()
    assert mark is not None
    admin_page.mouse.move(mark["x"] + mark["width"] / 2, mark["y"] - 30)
    tooltip = history.get_by_test_id("tooltip")
    expect(tooltip).to_be_visible()
    expect(history.get_by_test_id("tooltip-reset")).to_contain_text(REASON)

    toggle = history.get_by_role("list", name="Legende").get_by_role("button", name="I-Anteil")
    toggle.click()
    expect(history.get_by_test_id("pi-line")).to_have_count(0)
    expect(history.get_by_test_id("pi-reset-mark")).to_have_count(0)
    toggle.click()
    expect(history.get_by_test_id("pi-line").first).to_be_attached()


def test_dashboard_chart_setting_and_controller_tile(
    admin_page: Page, zones: dict[str, int]
) -> None:
    admin_page.set_viewport_size({"width": 1280, "height": 900})
    admin_page.goto("/app/")
    admin_page.get_by_test_id("edit-layout").click()
    admin_page.get_by_test_id("open-gallery").click()
    gallery = admin_page.get_by_test_id("gallery")
    gallery.get_by_test_id("gallery-zone").select_option(str(zones["Wohnzimmer"]))
    gallery.get_by_role("button", name="Regler hinzufügen").click()
    gallery.get_by_role("button", name="Temperaturverlauf hinzufügen").click()
    gallery.get_by_role("button", name="Fertig").click()
    # Die neue Verlaufskachel: erst ohne I-Anteil benennen, dann die Einstellung setzen.
    admin_page.get_by_role(
        "button", name=re.compile("Kachel „Temperaturverlauf.*Wohnzimmer.*anpassen")
    ).last.click()
    settings = admin_page.get_by_test_id("tile-settings")
    show = settings.get_by_label("I-Anteil zeigen")
    expect(show).not_to_be_checked()
    settings.get_by_label("Titel").fill("Regelblick")
    settings.get_by_role("button", name="Fertig").click()
    admin_page.get_by_test_id("save").click()
    expect(admin_page.get_by_test_id("edit-layout")).to_be_visible()

    tile = admin_page.get_by_test_id("tile-controller").first
    row = tile.get_by_test_id(f"controller-row-{zones['Bad']}")
    expect(row).to_contain_text("77,0 %")
    expect(row).to_contain_text(REASON)

    chart = admin_page.locator("section").filter(
        has=admin_page.get_by_role("heading", name="Regelblick: Wohnzimmer")
    ).last
    chart.scroll_into_view_if_needed()
    expect(chart.get_by_test_id("temp-line").first).to_be_attached()
    expect(chart.get_by_test_id("pi-line")).to_have_count(0)

    admin_page.get_by_test_id("edit-layout").click()
    admin_page.get_by_role(
        "button", name=re.compile("Kachel „Regelblick.*anpassen")
    ).click()
    settings.get_by_label("I-Anteil zeigen").check()
    settings.get_by_role("button", name="Fertig").click()
    admin_page.get_by_test_id("save").click()
    expect(admin_page.get_by_test_id("edit-layout")).to_be_visible()
    chart.scroll_into_view_if_needed()
    expect(chart.get_by_test_id("pi-line").first).to_be_attached()

    # Nach dem Neuladen steht die Einstellung noch.
    admin_page.reload()
    chart.scroll_into_view_if_needed()
    expect(chart.get_by_test_id("pi-line").first).to_be_attached()


def test_controller_row_leads_to_the_room_technik_on_a_phone(
    admin_page: Page, zones: dict[str, int]
) -> None:
    admin_page.set_viewport_size({"width": 390, "height": 844})
    admin_page.goto("/app/")
    row = admin_page.get_by_test_id(f"controller-row-{zones['Büro']}")
    row.click()
    expect(admin_page).to_have_url(
        re.compile(rf"/app/raeume/{zones['Büro']}\?ansicht=technik$")
    )
    card = admin_page.get_by_test_id("room-pi")
    expect(card).to_be_in_viewport()
    expect(card.get_by_test_id("room-pi-value")).to_have_text("8,0 %")


def test_a_tenant_sees_no_trace_of_the_integral(
    page: Page, live_server: LiveServer, zones: dict[str, int]
) -> None:
    sign_in_settled(page, *TENANT)
    with page.expect_response(lambda r: "/ui-api/history?" in r.url) as answer:
        page.goto(f"/app/raeume/{zones['Wohnzimmer']}")
        page.get_by_test_id("room-history").scroll_into_view_if_needed()
    body = answer.value.json()
    assert body["pi_resets"] is None
    assert all(bucket["pi_integral"] is None for bucket in body["buckets"])
    expect(page.get_by_test_id("room-history").get_by_test_id("temp-line").first).to_be_attached()
    expect(page.get_by_test_id("room-pi")).to_have_count(0)
    expect(page.get_by_test_id("pi-line")).to_have_count(0)
    expect(page.get_by_text("I-Anteil")).to_have_count(0)


def test_without_any_temperature_the_integral_and_its_reset_are_still_drawn(
    admin_page: Page, live_server: LiveServer, zones: dict[str, int]
) -> None:
    """Sensorausfall: keine Messung, aber der Reset steht im Protokoll und gehört ins Bild."""
    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Fühlerausfall")
        zone.pi_enabled = True
        for minute in range(180, -1, -1):
            reset = minute == 90
            session.add(
                ShadowDecision(
                    decided_at=now - timedelta(minutes=minute),
                    zone_id=zone.id,
                    setpoint_c=Decimal("21.0"),
                    setpoint_reason="Zeitplan",
                    would_heat=False,
                    outcome_code="aus",
                    reason="Sensorausfall.",
                    pi_integral_after=Decimal("0.5") if minute > 90 else Decimal(0),
                    pi_integrator_action=INTEGRATOR_RESET if reset else INTEGRATOR_CONTINUE,
                    pi_reset_reason=RESET_REASON_NO_HEATING if reset else None,
                )
            )
        session.commit()
        zone_id = zone.id

    admin_page.set_viewport_size({"width": 1280, "height": 900})
    admin_page.goto(f"/app/raeume/{zone_id}")
    history = _history_card(admin_page)

    expect(history.get_by_test_id("pi-line").first).to_be_attached()
    expect(history.get_by_test_id("pi-reset-mark")).to_have_count(1)
    expect(history.get_by_test_id("temp-line")).to_have_count(0)


def test_the_reset_reason_also_shows_when_the_pointer_rests_on_a_gap_beside_it(
    admin_page: Page, live_server: LiveServer, zones: dict[str, int]
) -> None:
    """Reset, danach keine Zeilen mehr: knapp daneben liegt ein leeres Fach."""
    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Reset vor Lücke")
        zone.pi_enabled = True
        for minute in range(600, 299, -1):
            reset = minute == 300
            session.add(
                ShadowDecision(
                    decided_at=now - timedelta(minutes=minute),
                    zone_id=zone.id,
                    temperature_c=Decimal("20.0"),
                    setpoint_c=Decimal("21.0"),
                    setpoint_reason="Zeitplan",
                    would_heat=False,
                    outcome_code="aus",
                    reason="Test.",
                    pi_integral_after=Decimal(0) if reset else Decimal("0.5"),
                    pi_integrator_action=INTEGRATOR_RESET if reset else INTEGRATOR_CONTINUE,
                    pi_reset_reason=RESET_REASON_NO_HEATING if reset else None,
                )
            )
        session.commit()
        zone_id = zone.id

    admin_page.set_viewport_size({"width": 1280, "height": 900})
    admin_page.goto(f"/app/raeume/{zone_id}")
    history = _history_card(admin_page)
    mark = history.get_by_test_id("pi-reset-mark")
    mark.scroll_into_view_if_needed()
    box = mark.bounding_box()
    assert box is not None
    admin_page.mouse.move(box["x"] + box["width"] / 2 + 4, box["y"] - 40)

    expect(history.get_by_test_id("tooltip")).to_contain_text("Messlücke")
    expect(history.get_by_test_id("tooltip-reset")).to_contain_text(REASON)


def test_two_resets_with_the_same_timestamp_do_not_break_the_chart(
    admin_page: Page, live_server: LiveServer, zones: dict[str, int]
) -> None:
    """Reset, Fortsetzen, Reset zur selben Zeit: zwei Ereignisse, das Diagramm steht."""
    now = datetime.now(UTC).replace(tzinfo=None, second=0, microsecond=0)
    errors: list[str] = []
    admin_page.on("pageerror", lambda exc: errors.append(str(exc)))
    with live_server.session() as session:
        zone = create_schedule_zone(session, "Doppelter Reset")
        zone.pi_enabled = True
        at = now - timedelta(hours=2)
        for moment, action in (
            (at - timedelta(minutes=5), INTEGRATOR_CONTINUE),
            (at, INTEGRATOR_RESET),
            (at, INTEGRATOR_CONTINUE),
            (at, INTEGRATOR_RESET),
            (at + timedelta(minutes=5), INTEGRATOR_CONTINUE),
        ):
            session.add(
                ShadowDecision(
                    decided_at=moment,
                    zone_id=zone.id,
                    temperature_c=Decimal("20.0"),
                    setpoint_c=Decimal("21.0"),
                    setpoint_reason="Zeitplan",
                    would_heat=False,
                    outcome_code="aus",
                    reason="Test.",
                    pi_integral_after=Decimal("0.2"),
                    pi_integrator_action=action,
                    pi_reset_reason=RESET_REASON_NO_HEATING
                    if action == INTEGRATOR_RESET
                    else None,
                )
            )
        session.commit()
        zone_id = zone.id

    admin_page.set_viewport_size({"width": 1280, "height": 900})
    admin_page.goto(f"/app/raeume/{zone_id}")
    history = _history_card(admin_page)

    expect(history.get_by_test_id("pi-reset-mark")).to_have_count(2)
    mark = history.get_by_test_id("pi-reset-mark").first
    mark.scroll_into_view_if_needed()
    box = mark.bounding_box()
    assert box is not None
    admin_page.mouse.move(box["x"] + box["width"] / 2, box["y"] - 40)
    expect(history.get_by_test_id("tooltip-reset")).to_have_count(2)
    assert errors == []


def test_tapping_beside_a_reset_mark_on_a_phone_names_the_reason(
    browser: Browser, live_server: LiveServer, zones: dict[str, int]
) -> None:
    """Am Handy ist ein Zeitfach schmaler als ein Pixel: Der Finger trifft die Marke ungenau."""
    context = browser.new_context(
        base_url=live_server.base_url, viewport={"width": 390, "height": 844}, has_touch=True
    )
    try:
        page = context.new_page()
        sign_in_settled(page, live_server.admin_username, live_server.admin_password)
        page.goto(f"/app/raeume/{zones['Wohnzimmer']}")
        history = _history_card(page)
        mark = history.get_by_test_id("pi-reset-mark")
        mark.scroll_into_view_if_needed()
        box = mark.bounding_box()
        assert box is not None
        page.touchscreen.tap(box["x"] + box["width"] / 2 + 5, box["y"] - 40)
        reset = history.get_by_test_id("tooltip-reset")
        expect(reset).to_contain_text(REASON)
        # Der angetippte Punkt kann neben dem Reset liegen: dessen eigene Uhrzeit steht dabei.
        expect(reset).to_contain_text(re.compile(r"zurückgesetzt \(.*\d\d:\d\d Uhr\)"))
    finally:
        context.close()
