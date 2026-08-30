"""Rauchtest: jede Seite einmal wirklich aufrufen.

Diese Datei entstand, weil ein grundlegender Fehler durch alle Einzeltests und alle
Reviews gerutscht ist: `/` war nicht gebaut, obwohl Anmeldung, Abmeldung und die
Navigationsleiste dorthin fuehrten. Wer sich anmeldete, landete auf einer 404-Seite.

Kein Test hatte das gefunden, weil jeder Test genau einen Endpunkt aufrief und
Weiterleitungen mit `follow_redirects=False` abschnitt: geprueft wurde, DASS
weitergeleitet wird, nie WOHIN und ob dort etwas ist. Die Tests hier schliessen
genau diese Luecke -- sie pruefen das Ganze statt der Teile.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone

TEMPLATE_VERZEICHNIS = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"

# Seiten, die eine angemeldete Sitzung voraussetzen.
PROTECTED_PAGES = [
    "/",
    "/users",
    "/groups",
    "/tokens",
    "/devices",
    "/zones",
    "/modes",
    "/modes/new",
    "/audit",
    "/passkeys",
    "/zones/{zone_id}/setpoints",
    "/zones/{zone_id}/devices",
    "/zones/{zone_id}/schedule",
    "/zones/{zone_id}/schedule/adopt",
    "/zones/{zone_id}/parameters",
    "/control",
    "/settings",
    "/interfaces",
    "/statistics",
    "/plant",
]


def test_jede_seite_antwortet_angemeldet(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Keine Seite darf 404 oder 500 liefern, wenn man angemeldet ist."""
    zone = create_zone(session, "rauchtest")
    create_settings(session)
    errors = []
    for pattern in PROTECTED_PAGES:
        pfad = pattern.format(zone_id=zone.id)
        response = angemeldeter_client.get(pfad)
        if response.status_code != 200:
            errors.append(f"{pfad}: HTTP {response.status_code}")
    assert not errors, "Seiten mit Fehlerstatus: " + ", ".join(errors)


def test_anmeldung_fuehrt_auf_eine_existierende_seite(client: TestClient, user) -> None:
    """Der Weiterleitung folgen, statt nur ihr Vorhandensein zu pruefen.

    Genau diese Luecke hat den fehlenden `/`-Endpunkt verdeckt.
    """
    response = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=True,
    )
    assert response.status_code == 200, (
        f"Nach der Anmeldung landet man auf HTTP {response.status_code} "
        f"({response.url})"
    )


def test_nicht_angemeldet_fuehrt_die_startseite_zur_anmeldung(
    client: TestClient, user
) -> None:
    """Wer die Adresse im Browser eingibt, soll ein Formular sehen, keine Fehlermeldung."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert "/login" in str(response.url)


def test_ohne_benutzer_fuehrt_die_startseite_zur_einrichtung(client: TestClient) -> None:
    """Dieselbe Zusicherung fuer den Zustand davor: Vor der Einrichtung gibt es niemanden,
    der sich anmelden koennte -- ein Anmeldeformular waere hier die Sackgasse, die dieser
    Rauchtest verhindern soll."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert "/setup" in str(response.url)


@pytest.mark.parametrize("vorlage", sorted(TEMPLATE_VERZEICHNIS.glob("*.html")))
def test_verweise_in_vorlagen_zeigen_auf_vorhandene_seiten(
    vorlage: Path, angemeldeter_client: TestClient, session: Session
) -> None:
    """Jeder interne Verweis in den Vorlagen muss irgendwo hinfuehren.

    Die Navigationsleiste verwies auf `/`, das es nicht gab -- ein Klick auf den
    Projektnamen fuehrte auf eine Fehlerseite. Ein solcher Verweis ist im Quelltext
    unauffaellig und faellt nur beim Benutzen auf.
    """
    # Die Einstellungszeile gibt es in jeder eingerichteten Anlage -- die Einrichtung legt
    # sie an. Ohne sie prueft dieser Test einen Zustand, den keine Instanz je hat.
    create_settings(session)
    ziele = {
        z
        for z in re.findall(r'href="(/[^"]*)"', vorlage.read_text(encoding="utf-8"))
        if "{{" not in z and "{%" not in z
    }
    tote = []
    for ziel in sorted(ziele):
        response = angemeldeter_client.get(ziel, follow_redirects=True)
        if response.status_code >= 400:
            tote.append(f"{ziel}: HTTP {response.status_code}")
    assert not tote, f"Tote Verweise in {vorlage.name}: " + ", ".join(tote)


@pytest.mark.parametrize("vorlage", sorted(TEMPLATE_VERZEICHNIS.glob("*.html")))
def test_kein_skript_im_rumpf_einer_vorlage(vorlage: Path) -> None:
    """Skripte gehoeren in den Kopf, nicht in den Rumpf.

    `hx-boost` tauscht bei jeder Navigation den Inhalt des ``<body>`` aus, und htmx
    fuehrt ``<script>``-Tags im eingetauschten Inhalt erneut aus. Bootstrap registrierte
    seine Menuebehandlung dadurch ein zweites Mal am ``document``: Der Umschalter feuerte
    doppelt, das Menue ging auf und im selben Klick wieder zu -- fuer den Benutzer sah es
    aus, als liesse es sich nicht mehr oeffnen. Im Browser nachgestellt und nach der
    Umstellung ebenso nachgemessen; hier steht nur die Invariante, damit das naechste
    Skript nicht wieder im Rumpf landet.
    """
    text = vorlage.read_text(encoding="utf-8")
    kopfende = text.find("</head>")
    stellen = [match.start() for match in re.finditer(r"<script\b", text)]
    if kopfende == -1:
        # Teilvorlagen ohne eigenen Kopf duerfen ueberhaupt kein Skript mitbringen:
        # Sie werden ausschliesslich in ausgetauschten Inhalt hineingerendert.
        assert not stellen, f"{vorlage.name} bringt ein Skript mit, hat aber keinen Kopf"
        return
    zu_spaet = [stelle for stelle in stellen if stelle > kopfende]
    assert not zu_spaet, (
        f"{vorlage.name}: {len(zu_spaet)} Skript(e) stehen hinter </head>. "
        "hx-boost fuehrt sie bei jeder Navigation erneut aus."
    )


def test_passkey_skript_haengt_nicht_nur_an_domcontentloaded() -> None:
    """`DOMContentLoaded` feuert nach einem hx-boost-Wechsel nie wieder.

    Wer /passkeys ueber das Menue ansteuerte, bekam deshalb einen Abschnitt, den das
    Skript nie eingeblendet hat -- die Passkey-Verwaltung war unsichtbar, solange man
    die Seite nicht direkt neu lud. Aufgefallen ist das erst im Browser, keinem Test.
    """
    source = (
        Path(__file__).resolve().parent.parent / "thermoctl/web/static/passkey.js"
    ).read_text(encoding="utf-8")
    assert 'document.addEventListener("htmx:load"' in source
    assert 'document.addEventListener("DOMContentLoaded"' in source


# --- Waechter fuer die Rahmenseiten ------------------------------------------


@pytest.mark.parametrize("pfad", PROTECTED_PAGES)
def test_geboostete_navigation_liefert_die_ganze_seite(
    pfad: str, angemeldeter_client: TestClient, session: Session
) -> None:
    """Eine geboostete Navigation ist ein Seitenwechsel, kein Teilaustausch.

    `hx-boost="true"` am <body> laesst jede Navigation einen `HX-Request`-Kopf tragen.
    Sechs Ansichten hielten das fuer einen Teilaustausch und lieferten nur ihren Inhalt
    ohne Rahmen: Wer ueber das Menue auf /geraete oder /audit ging, verlor die
    Kopfleiste und kam nur durch Neuladen zurueck. Beim Direktaufruf war alles in
    Ordnung -- deshalb fiel es niemandem auf, der die Seite zum Pruefen einfach aufrief.
    """
    zone = create_zone(session, "boostzone")
    create_settings(session)
    response = angemeldeter_client.get(
        pfad.format(zone_id=zone.id),
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "tc-kopf" in response.text, f"{pfad} liefert geboostet keine Kopfleiste"


@pytest.mark.parametrize("pfad", ["/devices", "/audit", "/users", "/groups", "/tokens"])
def test_echter_teilaustausch_liefert_weiter_nur_den_inhalt(
    pfad: str, angemeldeter_client: TestClient, session: Session
) -> None:
    """Gegenprobe: Ohne sie waere der Test oben auch von einer Fassung erfuellt, die den
    Teilaustausch ganz abgeschafft hat -- und jede Aktualisierung einer Tabelle wuerde
    die halbe Seite mitschicken."""
    create_settings(session)
    response = angemeldeter_client.get(pfad, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "tc-kopf" not in response.text


@pytest.mark.parametrize("vorlage", sorted(TEMPLATE_VERZEICHNIS.glob("*.html")))
def test_jede_tabelle_scrollt_in_sich_selbst(vorlage: Path) -> None:
    """Eine breite Tabelle darf die Seite nicht aufspannen.

    Auf einem Telefon lief /benutzer 190 Pixel ueber den Rand, und die ganze Seite liess
    sich seitwaerts schieben -- auch die Kopfleiste und jeder Text. Eine Tabelle in
    `.table-responsive` scrollt stattdessen in ihrem eigenen Rahmen.
    """
    text = vorlage.read_text(encoding="utf-8")
    if "<table" not in text:
        return
    for stelle in [t.start() for t in re.finditer(r"<table\b", text)]:
        davor = text[:stelle]
        assert "table-responsive" in davor[-400:], (
            f"{vorlage.name}: Tabelle ohne .table-responsive-Rahmen"
        )


def test_kein_eigener_umschalter_fuer_das_farbschema() -> None:
    """Das Farbschema folgt dem Betriebssystem, und zwar allein.

    Ein eigener Umschalter war eine dritte Einstellung fuer etwas, das jedes Geraet schon
    kennt, und ging beim naechsten Browser wieder verloren.
    """
    basis = (TEMPLATE_VERZEICHNIS / "basis.html").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in basis
    assert "localStorage" not in basis
    assert "schema-umschalten" not in basis


def test_zeitplan_reisst_das_gitter_nicht_unter_der_maus_weg() -> None:
    """Ein Klick ins Zeitplangitter darf die Seite nicht scrollen.

    Vorher holte `vorbelegen` das Anlege-Formular mit `scrollIntoView` heran. Wer zwei
    Punkte nacheinander setzen wollte, klickte beim zweiten Mal auf dieselbe
    Bildschirmstelle und traf eine voellig andere Uhrzeit: Im Browser gemessen verschob
    ein einziger Klick das Gitter um 377 px, bei rund 415 px fuer den ganzen Tag also um
    mehr als zwoelf Stunden. `focus({preventScroll: true})` genuegte dabei nicht -- der
    Aufruf scrollte in Chromium trotzdem, gemessen 377 gegen 0 ohne ihn.

    Der Test liest die Quelle, weil die Suite keinen Browser hat. Er haelt damit nur die
    Invariante fest; nachgewiesen wurde sie mit Playwright.
    """
    source = (
        Path(__file__).resolve().parent.parent / "thermoctl/web/static/zeitplan.js"
    ).read_text(encoding="utf-8")
    assert "scrollIntoView" not in source.replace("`scrollIntoView`", ""), (
        "zeitplan.js scrollt wieder von sich aus"
    )
    # Fokus nur hinter der Sichtbarkeitspruefung -- sonst scrollt der Browser selbst.
    assert "if (sichtbar) {" in source
    before_focus = source[: source.index(".focus(")]
    assert "getBoundingClientRect" in before_focus and "innerHeight" in before_focus, (
        "focus() steht nicht mehr hinter der Sichtbarkeitspruefung"
    )
