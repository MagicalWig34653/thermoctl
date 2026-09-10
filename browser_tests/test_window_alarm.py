"""Kälte-Alarm (im Code und in der Oberfläche „Fenster-Alarm"): Schalter und
Schwellen unter `/settings`, Anzeige an der betroffenen Zone auf der Startseite.

Was hier nicht über einen HTTP-Test zu sehen ist: ob die Schwellen wirklich
ausfüllbar sind und danach sichtbar gespeichert stehen, ob der Chip an der Zone
tatsächlich neben den übrigen Chips (Übersteuerung, Betriebsart, Messwert
unverändert) Platz findet statt das Layout zu sprengen, und ob eine Berechtigung
ohne `setting.manage` die Schwellen nur liest, statt sie ändern zu können.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from tests.helpers import sensor_status_of
from thermoctl.db.models.state import ZoneState

pytestmark = pytest.mark.browser

_PASSWORD = "Kein-Einstellungsrecht-5"  # noqa: S105 -- local, ephemeral, throwaway DB


def _seed_zone_with_window_alarm(
    live_server: LiveServer, name: str, *, window_alarm: bool | None
) -> int:
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        status = sensor_status_of(session)
        session.add(
            ZoneState(
                zone_id=zone.id,
                sensor_status_id=status.id,
                temperature_c=Decimal("18.0"),
                updated_at=datetime(2026, 9, 4, 8, 0),
                window_alarm=window_alarm,
            )
        )
        session.commit()
        return zone.id


def test_the_zone_shows_a_window_forgotten_chip_when_the_alarm_is_active(
    admin_page: Page, live_server: LiveServer
) -> None:
    zone_id = _seed_zone_with_window_alarm(
        live_server, "kaelte-alarm-aktiv", window_alarm=True
    )
    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="kaelte-alarm-aktiv")
    chip = article.locator(".tc-chip", has_text="Fenster vergessen")
    expect(chip).to_be_visible()

    # Und derselbe Zustand wirkt auch anlagenweit oben in der Kopfzeile, mit dem
    # Zonennamen benannt -- nicht nur an der Zone selbst.
    header_chip = admin_page.locator("section.tc-system-row .tc-chip", has_text="Fenster vergessen")
    expect(header_chip).to_be_visible()
    expect(header_chip).to_contain_text("Kaelte-alarm-aktiv")
    assert zone_id  # die Zone wurde tatsächlich angelegt, nicht nur benannt


def test_a_zone_without_an_active_alarm_shows_no_such_chip(
    admin_page: Page, live_server: LiveServer
) -> None:
    _seed_zone_with_window_alarm(live_server, "kaelte-alarm-ruhig", window_alarm=False)
    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="kaelte-alarm-ruhig")
    expect(article.locator(".tc-chip", has_text="Fenster vergessen")).to_have_count(0)


def test_a_zone_with_unknown_alarm_state_shows_no_chip_either(
    admin_page: Page, live_server: LiveServer
) -> None:
    """`window_alarm` ist tri-state: `None` heißt "kein bekannter Zustand", nicht
    "kein Alarm" -- ein Chip hier wäre eine Behauptung, die der Code selbst nicht
    aufstellt (siehe `domain.window_alarm.window_alarm_state`)."""
    _seed_zone_with_window_alarm(live_server, "kaelte-alarm-unbekannt", window_alarm=None)
    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="kaelte-alarm-unbekannt")
    expect(article.locator(".tc-chip", has_text="Fenster vergessen")).to_have_count(0)


def test_several_chips_on_one_zone_wrap_instead_of_overflowing(
    admin_page: Page, live_server: LiveServer
) -> None:
    """Eine Zone mit Kälte-Alarm, gleichzeitig festhängendem Messwert *und* einer
    eigenen Sensorstörung -- drei Chips an derselben Zone auf einmal. Sie dürfen
    sich dabei nicht überlappen, und die Seite darf dadurch keinen horizontalen
    Scrollbalken bekommen (`tc-system-row` / die Chip-Zeile einer Zone sind mit
    `flex-wrap` gebaut, siehe thermoctl.css)."""
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, "kaelte-alarm-viele-chips")
        status = sensor_status_of(session, "veraltet")
        session.add(
            ZoneState(
                zone_id=zone.id,
                sensor_status_id=status.id,
                temperature_c=Decimal("16.0"),
                updated_at=datetime(2026, 9, 4, 8, 0),
                window_alarm=True,
                sensor_stuck=True,
            )
        )
        session.commit()

    admin_page.goto("/")
    article = admin_page.locator("article.tc-zone", has_text="kaelte-alarm-viele-chips")
    expect(article.locator(".tc-chip", has_text="Fenster vergessen")).to_be_visible()
    expect(article.locator(".tc-chip", has_text="Messwert veraltet")).to_be_visible()

    # Diese Zone allein darf ihren eigenen Chip-Bereich nicht sprengen -- absichtlich
    # geprüft an ihrem eigenen Container statt am ganzen Dokument: die
    # sitzungsweite Datenbank sammelt über die volle Suite hinweg weitere Zonen
    # (siehe die Randnotiz unten), deren *eigene* Chips ein dokumentweiter Test
    # fälschlich dieser Zone anlasten würde.
    chip_row = article.locator("div.d-flex.flex-wrap.gap-1")
    row_overflow = chip_row.evaluate(
        "el => el.scrollWidth > el.clientWidth + 1"
    )
    assert not row_overflow, (
        "Die Chip-Zeile dieser Zone ist breiter als ihr eigener Rahmen -- "
        "einzelne Chips überlaufen, statt umzubrechen."
    )

    # Keine zwei Chips derselben Zone überlappen sich sichtbar.
    boxes = article.locator(".tc-chip").evaluate_all(
        "els => els.map(e => e.getBoundingClientRect())"
        ".map(r => ({x: r.x, y: r.y, w: r.width, h: r.height}))"
    )
    assert len(boxes) >= 3, "Weniger Chips als erwartet -- der Test prüft dann nichts Sinnvolles"
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            horizontally_apart = a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
            vertically_apart = a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]
            assert horizontally_apart or vertically_apart, (
                f"Zwei Chips überlappen sich: {a} / {b}"
            )


def test_the_thresholds_can_be_filled_in_and_the_saved_values_show_afterwards(
    admin_page: Page,
) -> None:
    admin_page.goto("/settings")
    admin_page.locator("#window_alarm_open_minutes").fill("45")
    admin_page.locator("#window_alarm_outdoor_threshold_c").fill("2.5")
    admin_page.get_by_role("button", name="Fenster-Alarm speichern").click()

    expect(admin_page.locator("#window_alarm_open_minutes")).to_have_value("45")
    expect(admin_page.locator("#window_alarm_outdoor_threshold_c")).to_have_value("2.5")


def test_the_notify_toggle_actually_switches_and_survives_a_reload(
    admin_page: Page,
) -> None:
    admin_page.goto("/settings")
    toggle = admin_page.get_by_label("Fenster vergessen offen")
    expect(toggle).to_be_visible()
    was_checked = toggle.is_checked()
    if was_checked:
        toggle.uncheck()
    else:
        toggle.check()
    admin_page.get_by_role("button", name="Meldungen speichern").click()

    toggle_after = admin_page.get_by_label("Fenster vergessen offen")
    expect(toggle_after).to_be_checked(checked=not was_checked)


def test_an_out_of_range_threshold_shows_the_error_at_its_own_field(
    admin_page: Page,
) -> None:
    admin_page.goto("/settings")
    # 999 liegt weit über der oberen Grenze (240 Minuten, `domain/control.py`).
    admin_page.locator("#window_alarm_open_minutes").fill("999")
    admin_page.get_by_role("button", name="Fenster-Alarm speichern").click()

    error = admin_page.locator("#window_alarm_open_minutes-fehler")
    expect(error).to_be_visible()


def test_a_user_without_setting_manage_only_sees_the_current_thresholds(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        seed.create_login_user(
            session, "browsertest-ohne-fensteralarmrecht", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    page.goto("/login")
    page.get_by_label("Benutzername").fill("browsertest-ohne-fensteralarmrecht")
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    # `.tc-topbar` statt der seit v0.9.0 entfallenen `.tc-head` -- dieselbe
    # Aussage ("angemeldet, Anlagenhuelle sichtbar"), an einem Element, das
    # auf jeder Bildschirmbreite sichtbar bleibt.
    expect(page.locator(".tc-topbar")).to_be_visible()

    page.goto("/settings")
    expect(page.get_by_text("Fenster-Alarm", exact=True)).to_be_visible()
    expect(page.locator("#window_alarm_open_minutes")).to_have_count(0)
    expect(page.locator("#notify_window_alarm")).to_have_count(0)
    expect(page.get_by_text("setting.manage")).to_be_visible()
