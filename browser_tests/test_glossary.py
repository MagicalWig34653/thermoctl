"""Das Glossar im echten Browser: Suche, Sprungmarken, Hilfe-Symbol.

Was ein HTTP-Test nicht sehen kann: dass das Suchfeld erst durch das Skript sichtbar wird
und wirklich filtert, dass ein Sprung den Eintrag in den Blick holt und hervorhebt
(`:target`), und dass das Hilfe-Symbol in einem neuen Tab auf genau den Eintrag führt.
Die Konsolenfehler-Prüfung der `page`-Fixture (conftest.py) läuft dabei auf jeder Seite mit.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser


def test_the_search_field_appears_and_filters_entries_and_jump_marks(admin_page: Page) -> None:
    admin_page.goto("/glossar")
    search = admin_page.get_by_label("Glossar durchsuchen")
    expect(search).to_be_visible()

    entries = admin_page.locator("[data-glossary-entry]")
    total = entries.count()
    assert total >= 50

    search.fill("hysterese")
    visible = admin_page.locator("[data-glossary-entry]:not([hidden])")
    count = visible.count()
    assert 1 <= count < total
    expect(admin_page.locator("#hysterese")).to_be_visible()
    expect(admin_page.locator("#notbetrieb")).to_be_hidden()
    # Ein Buchstabe ohne sichtbaren Eintrag ist keine Sprungmarke mehr.
    expect(admin_page.locator("[data-glossary-letter='Z']")).to_have_attribute(
        "aria-disabled", "true"
    )

    # Synonyme zählen: "Schalthysterese" steht nicht im Begriff selbst.
    search.fill("Schalthysterese")
    expect(admin_page.locator("#hysterese")).to_be_visible()

    search.fill("gibtesnicht")
    expect(admin_page.locator("#glossary-empty")).to_be_visible()
    expect(admin_page.locator("[data-glossary-entry]:not([hidden])")).to_have_count(0)

    search.fill("")
    expect(admin_page.locator("[data-glossary-entry]:not([hidden])")).to_have_count(total)
    expect(admin_page.locator("#glossary-empty")).to_be_hidden()


def test_a_jump_mark_scrolls_to_its_letter_and_an_anchor_highlights_its_entry(
    admin_page: Page,
) -> None:
    admin_page.goto("/glossar")
    admin_page.locator("[data-glossary-letter='Z']").click()
    expect(admin_page).to_have_url(re.compile(r"/glossar#buchstabe-z$"))
    expect(admin_page.locator("#buchstabe-z")).to_be_in_viewport()

    # Frischer Aufruf statt Hash-Wechsel auf derselben Seite: Bootstrap scrollt weich, und
    # eine zweite Navigation mitten in der laufenden Animation käme sonst nicht an.
    admin_page.goto("/settings")
    admin_page.goto("/glossar#trockenlauf")
    entry = admin_page.locator("#trockenlauf")
    expect(entry).to_be_in_viewport()
    # `:target` hebt den Eintrag hervor: Rand in der Primärfarbe statt der Linienfarbe.
    border = entry.evaluate("el => getComputedStyle(el).borderTopColor")
    other = admin_page.locator("#notbetrieb").evaluate("el => getComputedStyle(el).borderTopColor")
    assert border != other


def test_the_help_icon_opens_the_matching_entry_in_a_new_tab(admin_page: Page) -> None:
    admin_page.goto("/settings")
    icon = admin_page.get_by_role(
        "link", name="Erklärung zu Außenkennlinie im Glossar (öffnet in neuem Tab)"
    )
    expect(icon).to_be_visible()
    box = icon.bounding_box()
    assert box is not None and box["width"] >= 24 and box["height"] >= 24

    with admin_page.expect_popup() as popup_info:
        icon.click()
    popup = popup_info.value
    popup.wait_for_load_state()
    assert popup.url.endswith("/glossar#aussenkennlinie")
    expect(popup.locator("#aussenkennlinie")).to_be_in_viewport()
    popup.close()
    # Die ursprüngliche Seite samt ungespeicherter Eingaben bleibt stehen.
    assert admin_page.url.endswith("/settings")


def test_the_glossary_is_reachable_from_the_navigation_and_the_footer(admin_page: Page) -> None:
    admin_page.goto("/")
    admin_page.get_by_role("navigation", name="Hauptnavigation").get_by_role(
        "link", name="Glossar"
    ).click()
    expect(admin_page.locator("h1")).to_have_text("Glossar")
    admin_page.goto("/zones")
    admin_page.locator("footer").get_by_role("link", name="Glossar").click()
    expect(admin_page.locator("h1")).to_have_text("Glossar")
