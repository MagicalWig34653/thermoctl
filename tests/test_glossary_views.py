"""Die Seite `/glossar` und die Hilfe-Symbole, die dorthin verweisen.

Zugriff: wie die anderen reinen Leseseiten -- anmelden muss sich jeder (Grundsatz 4),
ein Recht braucht es nicht, weil die Seite nur Erklärtext zeigt. Beides wird hier
festgehalten, nicht nur das Erfreuliche.
"""

import re
from collections.abc import Callable

from fastapi.testclient import TestClient
from markupsafe import escape
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, user_with_permissions
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.domain.glossary import default_glossary

Client = Callable[[list[tuple[str, int | None]]], TestClient]

PREFIX = "/api/hassio_ingress/A1b2C3d4e5"


def test_a_signed_in_user_without_any_permission_can_read_the_glossary(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    response = client_als([]).get("/glossar")
    assert response.status_code == 200
    assert "Glossar" in response.text


def test_without_a_session_the_glossary_is_not_served_but_leads_to_the_login(
    client: TestClient, user: object
) -> None:
    """Auth ist Pflicht: Der Erklärtext ist kein Geheimnis, die Seite aber ein Teil der
    Anwendung und gibt ohne Anmeldung keinen Hinweis, dass hier eine Heizung läuft."""
    plain = client.get("/glossar", follow_redirects=False)
    # Dasselbe Verhalten wie jede andere geschützte Seite (`current_principal`).
    assert plain.status_code == client.get("/users", follow_redirects=False).status_code
    assert plain.status_code in {303, 401}
    assert "Hysterese" not in plain.text


def test_the_glossary_lists_every_entry_with_its_texts(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    for entry in default_glossary().entries:
        assert f'id="{entry.id}"' in page, entry.id
        assert f'<h3 class="h5 mb-1">{escape(entry.term)}</h3>' in page, entry.id
        assert f"<strong>{escape(entry.short)}</strong>" in page, entry.id
        assert escape(entry.explanation) in page, entry.id
    # Text mit Umlauten und Anführungszeichen kommt unverfälscht an.
    assert "Spielraum um den Sollwert" in page


def test_the_glossary_is_alphabetical_in_the_page(client_als: Client, session: Session) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    positions = [page.index(f'id="{entry.id}"') for entry in default_glossary().entries]
    assert positions == sorted(positions)


def test_the_jump_marks_cover_a_to_z_and_link_only_to_letters_that_exist(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    nav = re.search(r'<nav class="tc-glossary-letters.*?</nav>', page, re.DOTALL)
    assert nav is not None
    available = set(default_glossary().letters())
    linked = set(re.findall(r'data-glossary-letter="([A-Z])"', nav.group(0)))
    assert linked == available
    # Das Alphabet ist vollständig: 26 Zeichen, die einen Buchstaben tragen.
    shown = re.findall(r">\s*([A-Z])\s*<", nav.group(0))
    assert "".join(shown) == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for letter in available:
        assert f'id="buchstabe-{letter.lower()}"' in page, f"Kein Ziel für Sprungmarke {letter}"
    for target in re.findall(r'href="#(buchstabe-[a-z])"', nav.group(0)):
        assert f'id="{target}"' in page


def test_every_see_also_link_on_the_page_hits_an_entry_on_the_page(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    ids = set(re.findall(r'<article[^>]* id="([^"]+)"', page))
    # Nur die Verweise innerhalb der Einträge ("Siehe auch"), nicht Sprungmarken oder
    # der Sprung an den Inhalt ("#tc-main") der Hülle.
    blocks = "".join(re.findall(r"Siehe auch:.*?</p>", page, re.DOTALL))
    targets = set(re.findall(r'href="#([a-z0-9-]+)"', blocks))
    assert targets, "Es gibt keine 'Siehe auch'-Verweise?"
    assert targets <= ids


def test_the_search_field_exists_but_stays_hidden_until_the_script_runs(
    client_als: Client, session: Session
) -> None:
    """Ohne JavaScript würde das Feld nichts filtern -- also darf es nicht sichtbar sein."""
    create_settings(session)
    page = client_als([]).get("/glossar").text
    field = re.search(r'<input[^>]*id="glossary-search"[^>]*>', page)
    assert field is not None
    assert " hidden" in field.group(0)
    assert 'aria-label="Glossar durchsuchen"' in field.group(0)
    assert 'id="glossary-empty"' in page


def test_every_entry_carries_the_text_the_search_looks_in(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    for entry in default_glossary().entries:
        for synonym in entry.synonyms:
            assert synonym.casefold() in entry.search_text
    assert page.count("data-search-text=") == len(default_glossary().entries)


def test_the_search_script_is_registered_for_lazy_loading() -> None:
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "thermoctl" / "web" / "static"
    loader = (static / "page_scripts.js").read_text(encoding="utf-8")
    assert '["glossary_filter.js", "#glossary-search"]' in loader
    assert (static / "glossary_filter.js").is_file()


# --- Hülle und Navigation --------------------------------------------------------------


def test_the_plant_view_links_to_the_glossary_in_navigation_and_footer(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([("zone.read", None)]).get("/").text
    nav = re.search(r'<nav class="tc-sidenav.*?</nav>', page, re.DOTALL)
    assert nav is not None
    assert 'href="/glossar"' in nav.group(0)
    footer = re.search(r'<footer class="tc-footer">.*?</footer>', page, re.DOTALL)
    assert footer is not None
    assert 'href="/glossar"' in footer.group(0)


def test_the_glossary_link_in_the_navigation_is_there_even_without_any_permission(
    client_als: Client, session: Session
) -> None:
    """Die Hilfe braucht kein Recht -- sie darf also auch nicht an einem hängen."""
    create_settings(session)
    page = client_als([]).get("/").text
    nav = re.search(r'<nav class="tc-sidenav.*?</nav>', page, re.DOTALL)
    assert nav is not None
    assert 'href="/glossar"' in nav.group(0)


def test_the_current_page_is_marked_in_the_navigation(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    page = client_als([]).get("/glossar").text
    assert re.search(r'href="/glossar"\s+aria-current="page"', page)


def test_the_tenant_view_gets_the_glossary_in_its_own_shell(
    tenant_client: Client, session: Session
) -> None:
    create_settings(session)
    client = tenant_client([])
    response = client.get("/glossar")
    assert response.status_code == 200
    assert "tc-tbottomnav" in response.text
    assert "tc-sidenav" not in response.text
    assert 'href="/glossar"' in client.get("/").text, "Die Wohnungssicht verlinkt es im Fuß"


def test_the_tenant_help_page_points_to_the_glossary(
    tenant_client: Client, session: Session
) -> None:
    create_settings(session)
    page = tenant_client([]).get("/account/help").text
    assert 'href="/glossar"' in page


def test_the_glossary_page_carries_no_plant_data(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "fremdes-bad")
    zone.display_name = "Fremdes Bad"
    session.flush()
    assert "Fremdes Bad" not in client_als([]).get("/glossar").text


def test_a_boosted_navigation_returns_the_whole_page(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    response = client_als([]).get(
        "/glossar", headers={"HX-Request": "true", "HX-Boosted": "true"}
    )
    assert response.status_code == 200
    assert "tc-topbar" in response.text


# --- Die Hilfe-Symbole in den Seiten ---------------------------------------------------


def _help_links(page: str) -> list[str]:
    return re.findall(r'<a class="tc-help" href="([^"]+)"', page)


def test_the_zone_parameter_page_offers_help_for_its_hard_terms(
    angemeldeter_client: TestClient, session: Session
) -> None:
    zone = create_zone(session, "hilfe-parameter")
    create_settings(session)
    page = angemeldeter_client.get(f"/zones/{zone.id}/parameters").text
    links = _help_links(page)
    for entry_id in (
        "hysterese",
        "mindestschaltdauer",
        "sonnenabsenkung",
        "pi-regelung",
        "notbetrieb",
    ):
        assert f"/glossar#{entry_id}" in links, entry_id
    known = default_glossary().ids()
    assert all(link.split("#", 1)[1] in known for link in links)


def test_the_settings_page_offers_help_for_the_emergency_profile_and_the_curve(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    links = _help_links(angemeldeter_client.get("/settings").text)
    assert "/glossar#notbetrieb" in links
    assert "/glossar#aussenkennlinie" in links


def test_the_operation_page_offers_help_for_the_dry_run(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    assert "/glossar#trockenlauf" in _help_links(angemeldeter_client.get("/control").text)


def test_a_help_icon_is_an_accessible_link_that_keeps_unsaved_form_input(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Neuer Tab, damit niemand mitten im Formular seine Eingaben verliert; `aria-label`,
    weil ein Fragezeichen allein einem Vorleseprogramm nichts sagt."""
    zone = create_zone(session, "hilfe-a11y")
    create_settings(session)
    page = angemeldeter_client.get(f"/zones/{zone.id}/parameters").text
    icon = re.search(r'<a class="tc-help"[^>]*glossar#hysterese[^>]*>', page)
    assert icon is not None
    assert 'target="_blank"' in icon.group(0)
    assert 'rel="noopener"' in icon.group(0)
    assert 'aria-label="Erklärung zu Hysterese im Glossar (öffnet in neuem Tab)"' in icon.group(0)


def test_help_icons_carry_the_ingress_prefix(
    client_with_prefix: TestClient, session: Session
) -> None:
    zone = create_zone(session, "hilfe-ingress")
    create_settings(session)
    user_record = user_with_permissions(
        session, "ingress-hilfe", [("zone.read", None), ("zone.manage", None)]
    )
    _entry, secret = create_session(session, user_record, 3600)
    client_with_prefix.cookies.set(COOKIE_NAME, secret)
    page = client_with_prefix.get(f"/zones/{zone.id}/parameters").text
    assert f'href="{PREFIX}/glossar#hysterese"' in page
    assert 'href="/glossar#' not in page
    assert f'href="{PREFIX}/glossar"' in client_with_prefix.get("/glossar").text
