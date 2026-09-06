"""Außentemperatur: Quellenauswahl unter `/settings` und die drei Zustände auf der
Startseite -- Wert, „Wert veraltet" und „keine Quelle gewählt".

Diese drei Zustände unterscheidet der Code nach `domain.outdoor.OutdoorReading.status`
über einen von drei verschiedenen Texten (`domain.fault.OK`/`VERALTET`/`NO_SOURCE`,
siehe `settings.html` und `start.html`) -- ob sie auf der gerenderten Seite tatsächlich
auseinanderzuhalten sind, statt sich zu überlappen oder ununterscheidbar auszusehen,
ist genau die Frage, die nur ein Browser beantworten kann.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer
from thermoctl.db.base import utcnow
from thermoctl.db.models.operations import Setting

pytestmark = pytest.mark.browser

_PASSWORD = "Kein-Einstellungsrecht-4"  # noqa: S105 -- local, ephemeral, throwaway DB


def _reset_outdoor_source(live_server: LiveServer) -> None:
    with live_server.session() as session:
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.outdoor_temperature_source_device_id = None
        settings.default_sensor_timeout_seconds = 1800
        session.commit()


@pytest.fixture(autouse=True)
def _clean_outdoor_source(live_server: LiveServer) -> None:
    """Sitzungsweite Datenbank, wie bei den anderen Browsertests -- jeder Test
    beginnt ohne gewählte Außentemperaturquelle, statt vom Zufall abzuhängen, was
    ein vorheriger Test dort stehen gelassen hat."""
    _reset_outdoor_source(live_server)


def test_no_source_chosen_shows_its_own_text_not_a_number(admin_page: Page) -> None:
    admin_page.goto("/")
    chip = admin_page.locator(".tc-chip", has_text="Außentemperatur")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text("keine Quelle gewählt")


def test_a_fresh_reading_shows_the_value_and_a_stale_one_shows_veraltet(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        device = seed.create_temperature_device(session, "aussen-sensor-1")
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.outdoor_temperature_source_device_id = device.id
        settings.default_sensor_timeout_seconds = 1800
        seed.seed_outdoor_measurement(
            session, device, value_c=Decimal("3.5"), measured_at=utcnow()
        )
        session.commit()

    admin_page.goto("/")
    chip = admin_page.locator(".tc-chip", has_text="Außentemperatur")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text("3,5")
    expect(chip).not_to_contain_text("veraltet")
    expect(chip).not_to_contain_text("keine Quelle")
    fresh_class = chip.get_attribute("class")

    # Derselbe Sensor, dieselbe Messung -- aber die erlaubte Zeitüberschreitung ist
    # jetzt auf null gesetzt, sodass genau dieselbe Messung ab sofort als veraltet
    # gilt. So bleibt eindeutig, welche Messung `sensor_state()` sieht: nicht eine
    # jüngere, die nach `measured_at` zufällig doch als aktueller gewertet würde.
    with live_server.session() as session:
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.default_sensor_timeout_seconds = 0
        session.commit()

    admin_page.goto("/")
    stale_chip = admin_page.locator(".tc-chip", has_text="Außentemperatur")
    expect(stale_chip).to_have_count(1)
    expect(stale_chip).to_contain_text("Wert veraltet")
    expect(stale_chip).not_to_contain_text("3,5")
    # Die veraltete Meldung ist als Warnung ausgezeichnet -- eine andere Klasse
    # als der unauffällige, ruhige Chip mit einem tatsächlichen Messwert.
    expect(stale_chip).to_have_class("tc-chip tc-chip-warning")
    assert fresh_class != stale_chip.get_attribute("class"), (
        "Frischer Messwert und 'Wert veraltet' tragen dieselbe Chip-Klasse -- "
        "optisch wären beide Zustände nicht zu unterscheiden."
    )


def test_the_no_source_and_the_fresh_value_chip_share_a_style_but_differ_in_text(
    admin_page: Page, live_server: LiveServer
) -> None:
    """Nicht unbedingt ein Fehler, aber eine bewusste Randnotiz: "keine Quelle
    gewählt" und ein tatsächlicher Messwert benutzen beide `tc-chip-quiet` --
    ausschließlich der Text unterscheidet sie, keine Farbe. Die Projektbegründung
    (STATUS.md) wählt eigene Texte statt einer verwechselbaren Zahl bewusst so;
    dieser Test hält lediglich fest, dass der Text dafür wirklich eindeutig ist."""
    admin_page.goto("/")
    no_source_chip = admin_page.locator(".tc-chip", has_text="Außentemperatur")
    expect(no_source_chip).to_contain_text("keine Quelle gewählt")
    no_source_class = no_source_chip.get_attribute("class")

    with live_server.session() as session:
        device = seed.create_temperature_device(session, "aussen-sensor-2")
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.outdoor_temperature_source_device_id = device.id
        seed.seed_outdoor_measurement(
            session, device, value_c=Decimal("7.0"), measured_at=utcnow()
        )
        session.commit()

    admin_page.goto("/")
    value_chip = admin_page.locator(".tc-chip", has_text="Außentemperatur")
    expect(value_chip).to_contain_text("7,0")
    assert no_source_class == value_chip.get_attribute("class")


def test_selecting_a_source_in_settings_updates_the_current_reading_shown_there(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        device = seed.create_temperature_device(session, "aussen-sensor-3")
        seed.seed_outdoor_measurement(
            session, device, value_c=Decimal("-2.0"), measured_at=utcnow()
        )
        session.commit()
        device_id = device.id

    admin_page.goto("/settings")
    expect(admin_page.get_by_text("keine Quelle gewählt")).to_be_visible()

    admin_page.locator("#outdoor-source-device").select_option(str(device_id))
    admin_page.get_by_role("button", name="Quelle speichern").click()

    expect(admin_page.get_by_text("-2,0 °C", exact=False)).to_be_visible()
    # Die Auswahl selbst bleibt nach dem Neuladen sichtbar ausgewählt.
    expect(admin_page.locator("#outdoor-source-device")).to_have_value(str(device_id))

    # Und auf der Startseite kommt derselbe Wert an.
    admin_page.goto("/")
    expect(admin_page.locator(".tc-chip", has_text="Außentemperatur")).to_contain_text("-2,0")


def test_a_device_without_temperature_capability_is_rejected_with_a_visible_error(
    admin_page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        device = seed.create_switch_only_device(session, "nur-schalter-1")
        session.commit()
        device_id = device.id

    admin_page.goto("/settings")
    admin_page.locator("#outdoor-source-device").select_option(str(device_id))
    admin_page.get_by_role("button", name="Quelle speichern").click()

    # Die Fehlermeldung erscheint als eigener Alarmblock über dem Formular, nicht
    # als roher Text irgendwo auf der Seite.
    error = admin_page.locator(".alert-danger")
    expect(error).to_be_visible()
    expect(error).to_contain_text("misst keine Temperatur")
    # Und die (falsche) Auswahl wurde nicht gespeichert.
    expect(admin_page.get_by_text("keine Quelle gewählt")).to_be_visible()


def test_a_user_without_setting_manage_does_not_see_the_source_picker(
    page: Page, live_server: LiveServer
) -> None:
    with live_server.session() as session:
        device = seed.create_temperature_device(session, "aussen-sensor-4")
        settings = session.get(Setting, 1)
        assert settings is not None
        settings.outdoor_temperature_source_device_id = device.id
        seed.seed_outdoor_measurement(
            session, device, value_c=Decimal("10.0"), measured_at=utcnow()
        )
        seed.create_login_user(
            session, "browsertest-ohne-einstellungsrecht", _PASSWORD, [("zone.read", None)]
        )
        session.commit()

    page.goto("/login")
    page.get_by_label("Benutzername").fill("browsertest-ohne-einstellungsrecht")
    page.get_by_label("Passwort").fill(_PASSWORD)
    page.get_by_role("button", name="Anmelden").click()
    expect(page.locator(".tc-head")).to_be_visible()

    page.goto("/settings")
    # Ohne `setting.manage` steht nur noch, welches Gerät die Quelle ist -- kein
    # Formular, das sie ändern könnte. Der aktuelle Wert selbst steht ohnehin
    # bereits sichtbar auf der Startseite, für jeden mit `zone.read`.
    expect(page.get_by_text("aussen-sensor-4")).to_be_visible()
    expect(page.locator("#outdoor-source-device")).to_have_count(0)
    expect(page.get_by_text("setting.manage")).to_be_visible()
