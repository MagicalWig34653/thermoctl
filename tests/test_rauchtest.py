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

from tests.hilfen import zone_anlegen

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
    "/zonen/{zone_id}/sollwerte",
    "/zonen/{zone_id}/geraete",
    "/zonen/{zone_id}/zeitplan",
    "/zonen/{zone_id}/zeitplan/uebernehmen",
]


def test_jede_seite_antwortet_angemeldet(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Keine Seite darf 404 oder 500 liefern, wenn man angemeldet ist."""
    zone = zone_anlegen(session, "rauchtest")
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


def test_nicht_angemeldet_fuehrt_die_startseite_zur_anmeldung(client: TestClient) -> None:
    """Wer die Adresse im Browser eingibt, soll ein Formular sehen, keine Fehlermeldung."""
    antwort = client.get("/", follow_redirects=True)
    assert antwort.status_code == 200
    assert "/login" in str(antwort.url)


@pytest.mark.parametrize("vorlage", sorted(TEMPLATE_VERZEICHNIS.glob("*.html")))
def test_verweise_in_vorlagen_zeigen_auf_vorhandene_seiten(
    vorlage: Path, angemeldeter_client: TestClient
) -> None:
    """Jeder interne Verweis in den Vorlagen muss irgendwo hinfuehren.

    Die Navigationsleiste verwies auf `/`, das es nicht gab -- ein Klick auf den
    Projektnamen fuehrte auf eine Fehlerseite. Ein solcher Verweis ist im Quelltext
    unauffaellig und faellt nur beim Benutzen auf.
    """
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
