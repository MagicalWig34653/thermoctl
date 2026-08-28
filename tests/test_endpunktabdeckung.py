"""Jeder Endpunkt muss in mindestens einem Test wirklich aufgerufen werden.

Zeilenabdeckung allein genuegt dafuer nicht: Eine Route kann durch einen Test
mitlaufen, der etwas ganz anderes prueft. Dieser Test zaehlt echte Aufrufe --
er wird rot, sobald jemand eine Route ergaenzt, ohne sie zu pruefen.

Der Anlass: Die Startseite fehlte vollstaendig, obwohl Anmeldung, Abmeldung und
Navigation dorthin fuehrten. Kein Test hatte sie je aufgerufen.
"""

import re

import pytest
from fastapi.routing import APIRoute

from thermoctl.app import create_app

# Von FastAPI selbst erzeugt, nicht von uns geschrieben.
NICHT_UNSER = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _unsere_routen() -> set[tuple[str, str]]:
    paare: set[tuple[str, str]] = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or route.path in NICHT_UNSER:
            continue
        for methode in route.methods - {"HEAD", "OPTIONS"}:
            paare.add((methode, route.path))
    return paare


def _als_muster(pfad: str) -> re.Pattern[str]:
    """Aus `/api/v1/zones/{zone_id}` wird ein Muster, das `/api/v1/zones/7` trifft."""
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", pfad) + "$")


@pytest.fixture(scope="session")
def aufgerufene_endpunkte(request: pytest.FixtureRequest) -> set[tuple[str, str]]:
    return getattr(request.config, "_aufgerufene_endpunkte", set())


def test_jeder_endpunkt_wird_in_einem_test_aufgerufen(
    aufgerufene_endpunkte: set[tuple[str, str]],
    request: pytest.FixtureRequest,
) -> None:
    # Die Mitschrift entsteht waehrend des Laufs. Wer nur diese Datei ausfuehrt, sieht
    # eine fast leere Mitschrift und bekaeme sonst einen Fehlalarm ueber jeden
    # Endpunkt des Projekts.
    if len(request.session.items) < 50:
        pytest.skip("nur im vollstaendigen Testlauf aussagekraeftig")

    ungeprueft = []
    for methode, pfad in sorted(_unsere_routen()):
        muster = _als_muster(pfad)
        if not any(m == methode and muster.match(p) for m, p in aufgerufene_endpunkte):
            ungeprueft.append(f"{methode} {pfad}")
    assert not ungeprueft, (
        "Diese Endpunkte ruft kein Test auf: " + ", ".join(ungeprueft)
    )
