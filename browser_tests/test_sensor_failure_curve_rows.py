"""Auftrag 8a: Hinzufügen/Entfernen von Kennlinienzeilen auf `/settings`.

`sensor_failure_curve.js` is pure client-side bookkeeping (clone a `<template>`
row, remove a row on click) -- the actual save/validate/reload round trip for the
resulting fields is already covered at the HTTP level
(`tests/test_control.py::test_saving_the_sensor_failure_defaults_round_trips` and
`::test_emptying_the_curve_rows_falls_back_to_fixed_cycling`). What only a real
browser can show is that the add/remove buttons actually produce the right
`<input>`s for the *next* save -- an HTTP test can only submit a payload by hand
and never exercises the JavaScript at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing

import pytest
from playwright.sync_api import Browser, Page, expect

from browser_tests.live_server import LiveServer, _live_server

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def curve_server() -> Iterator[LiveServer]:
    with closing(_live_server("")) as servers:
        yield next(servers)


@pytest.fixture
def settings_page(browser: Browser, curve_server: LiveServer) -> Iterator[Page]:
    with browser.new_context(base_url=curve_server.base_url, locale="de-DE") as context:
        page = context.new_page()
        page.goto("/login", wait_until="networkidle")
        page.get_by_label("Benutzername").fill(curve_server.admin_username)
        page.get_by_label("Passwort", exact=True).fill(curve_server.admin_password)
        page.get_by_role("button", name="Anmelden").click()
        page.locator(".tc-topbar, .tc-theader").wait_for()
        page.goto("/settings", wait_until="networkidle")
        yield page


def test_adding_a_curve_row_adds_three_new_inputs(settings_page: Page) -> None:
    page = settings_page
    container = page.locator("[data-sensor-failure-curve]")
    before = container.locator("[data-sfc-row]").count()

    container.get_by_role("button", name="Kennlinienzeile hinzufügen").click()

    expect(container.locator("[data-sfc-row]")).to_have_count(before + 1)
    new_row = container.locator("[data-sfc-row]").last
    expect(new_row.locator('input[name="curve_outdoor_c"]')).to_be_visible()
    expect(new_row.locator('input[name="curve_on_seconds"]')).to_be_visible()
    expect(new_row.locator('input[name="curve_off_seconds"]')).to_be_visible()


def test_removing_a_row_and_saving_persists_the_remaining_ones(
    settings_page: Page, curve_server: LiveServer
) -> None:
    page = settings_page
    container = page.locator("[data-sensor-failure-curve]")

    # Die Migration liefert bereits drei Vorgabezeilen (-10/0/15 °C) -- erst
    # alle vorhandenen Zeilen über denselben Knopf entfernen, damit dieser Test
    # unabhängig vom migrationsseitig vorbelegten Kurvenstand bleibt. Dann drei
    # eindeutig befüllte, für sich genommen gültige Zeilen anlegen und die
    # mittlere wieder entfernen -- die verbleibenden zwei (Kennlinie verlangt
    # mindestens zwei, davon genau eine mit 0 Sekunden Ein) müssen nach dem
    # Speichern noch da sein.
    while container.locator("[data-sfc-row]").count() > 0:
        container.locator("[data-sfc-row]").first.get_by_role(
            "button", name="Zeile entfernen"
        ).click()
    expect(container.locator("[data-sfc-row]")).to_have_count(0)

    for _ in range(3):
        container.get_by_role("button", name="Kennlinienzeile hinzufügen").click()
    rows = container.locator("[data-sfc-row]")
    values = (("-10", "1200", "600"), ("-5", "900", "900"), ("15", "0", "1800"))
    for index, (outdoor_c, on_seconds, off_seconds) in enumerate(values):
        row = rows.nth(index)
        row.locator('input[name="curve_outdoor_c"]').fill(outdoor_c)
        row.locator('input[name="curve_on_seconds"]').fill(on_seconds)
        row.locator('input[name="curve_off_seconds"]').fill(off_seconds)

    rows.nth(1).get_by_role("button", name="Zeile entfernen").click()
    expect(container.locator("[data-sfc-row]")).to_have_count(2)

    page.get_by_role("button", name="Notbetrieb speichern").click()
    page.wait_for_load_state("networkidle")

    with page.context.browser.new_context(
        base_url=curve_server.base_url, locale="de-DE"
    ) as fresh_context:
        fresh_page = fresh_context.new_page()
        fresh_page.goto("/login", wait_until="networkidle")
        fresh_page.get_by_label("Benutzername").fill(curve_server.admin_username)
        fresh_page.get_by_label("Passwort", exact=True).fill(curve_server.admin_password)
        fresh_page.get_by_role("button", name="Anmelden").click()
        fresh_page.locator(".tc-topbar, .tc-theader").wait_for()
        fresh_page.goto("/settings", wait_until="networkidle")
        reloaded_rows = fresh_page.locator("[data-sensor-failure-curve] [data-sfc-row]")
        expect(reloaded_rows).to_have_count(2)
        reloaded_values = {
            reloaded_rows.nth(i).locator('input[name="curve_outdoor_c"]').input_value()
            for i in range(2)
        }
        assert reloaded_values == {"-10.00", "15.00"}
