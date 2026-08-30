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

from tests.hilfen import einstellungen_anlegen, zone_anlegen

TEMPLATE_VERZEICHNIS = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"

# Seiten, die eine angemeldete Sitzung voraussetzen.
GESCHUETZTE_SEITEN = [
    "/",
    "/benutzer",
    "/gruppen",
    "/tokens",
    "/geraete",
    "/zonen",
    "/modi",
    "/modi/neu",
    "/audit",
    "/passkeys",
    "/zonen/{zone_id}/sollwerte",
    "/zonen/{zone_id}/geraete",
    "/zonen/{zone_id}/zeitplan",
    "/zonen/{zone_id}/zeitplan/uebernehmen",
    "/zonen/{zone_id}/parameter",
    "/steuerung",
    "/einstellungen",
]


def test_jede_seite_antwortet_angemeldet(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Keine Seite darf 404 oder 500 liefern, wenn man angemeldet ist."""
    zone = zone_anlegen(session, "rauchtest")
    einstellungen_anlegen(session)
    fehler = []
    for muster in GESCHUETZTE_SEITEN:
        pfad = muster.format(zone_id=zone.id)
        antwort = angemeldeter_client.get(pfad)
        if antwort.status_code != 200:
            fehler.append(f"{pfad}: HTTP {antwort.status_code}")
    assert not fehler, "Seiten mit Fehlerstatus: " + ", ".join(fehler)


def test_anmeldung_fuehrt_auf_eine_existierende_seite(client: TestClient, benutzer) -> None:
    """Der Weiterleitung folgen, statt nur ihr Vorhandensein zu pruefen.

    Genau diese Luecke hat den fehlenden `/`-Endpunkt verdeckt.
    """
    antwort = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=True,
    )
    assert antwort.status_code == 200, (
        f"Nach der Anmeldung landet man auf HTTP {antwort.status_code} "
        f"({antwort.url})"
    )


def test_nicht_angemeldet_fuehrt_die_startseite_zur_anmeldung(
    client: TestClient, benutzer
) -> None:
    """Wer die Adresse im Browser eingibt, soll ein Formular sehen, keine Fehlermeldung."""
    antwort = client.get("/", follow_redirects=True)
    assert antwort.status_code == 200
    assert "/login" in str(antwort.url)


def test_ohne_benutzer_fuehrt_die_startseite_zur_einrichtung(client: TestClient) -> None:
    """Dieselbe Zusicherung fuer den Zustand davor: Vor der Einrichtung gibt es niemanden,
    der sich anmelden koennte -- ein Anmeldeformular waere hier die Sackgasse, die dieser
    Rauchtest verhindern soll."""
    antwort = client.get("/", follow_redirects=True)
    assert antwort.status_code == 200
    assert "/setup" in str(antwort.url)


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
    einstellungen_anlegen(session)
    ziele = {
        z
        for z in re.findall(r'href="(/[^"]*)"', vorlage.read_text(encoding="utf-8"))
        if "{{" not in z and "{%" not in z
    }
    tote = []
    for ziel in sorted(ziele):
        antwort = angemeldeter_client.get(ziel, follow_redirects=True)
        if antwort.status_code >= 400:
            tote.append(f"{ziel}: HTTP {antwort.status_code}")
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
    stellen = [treffer.start() for treffer in re.finditer(r"<script\b", text)]
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
    quelle = (
        Path(__file__).resolve().parent.parent / "thermoctl/web/static/passkey.js"
    ).read_text(encoding="utf-8")
    assert 'document.addEventListener("htmx:load"' in quelle
    assert 'document.addEventListener("DOMContentLoaded"' in quelle
