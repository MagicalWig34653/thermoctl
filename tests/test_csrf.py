"""Der CSRF-Schutz muss fuer jede kuenftige Route von selbst gelten.

Der Anlass steht im Abschlussreview von Teilprojekt 1: Die Pruefung stand von Hand in
der einen zustandsaendernden Route. Mit jeder weiteren muesste sie wiederholt werden,
und irgendwann vergisst man sie. Seither haengt `csrf_schutz` am Router — und dieser
Test wird rot, sobald eine ungeschuetzte, zustandsaendernde Route dazukommt.
"""

import pytest
from fastapi import status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.hilfen import alle_api_routen
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_COOKIE_NAME, csrf_token
from thermoctl.auth.dependencies import csrf_schutz
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_anlegen

AENDERNDE_METHODEN = {"POST", "PUT", "PATCH", "DELETE"}

# Begruendete Ausnahmen. Die REST-API wertet ausschliesslich den
# `Authorization`-Header aus und niemals ein Cookie — ohne Cookie-Authentifizierung
# gibt es keinen CSRF-Weg. `test_api_nimmt_kein_sitzungscookie_an` haelt genau das
# nach, damit die Ausnahme nicht still ungueltig wird.
AUSGENOMMENE_PRAEFIXE = ("/api/",)


def _aendernde_routen() -> list[APIRoute]:
    return [
        route
        for route in alle_api_routen(create_app())
        if route.methods & AENDERNDE_METHODEN
    ]


def test_jede_aendernde_route_haengt_am_csrf_schutz() -> None:
    ungeschuetzt = [
        f"{sorted(route.methods & AENDERNDE_METHODEN)} {route.path}"
        for route in _aendernde_routen()
        if not route.path.startswith(AUSGENOMMENE_PRAEFIXE)
        and not any(d.dependency is csrf_schutz for d in route.dependencies)
    ]
    assert not ungeschuetzt, (
        "Diese zustandsaendernden Routen haben keinen CSRF-Schutz: " + ", ".join(ungeschuetzt)
    )


def test_api_nimmt_kein_sitzungscookie_an(client: TestClient, session, benutzer) -> None:
    """Traegt die Ausnahme oben: Die API laesst sich nicht per Cookie authentifizieren."""
    _eintrag, geheimnis = sitzung_anlegen(session, benutzer, 3600)
    client.cookies.set(COOKIE_NAME, geheimnis)
    antwort = client.get("/api/v1/me")
    assert antwort.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("pfad", ["/logout"])
def test_aendernde_route_ohne_token_wird_abgewiesen(
    client: TestClient, angemeldeter_client: TestClient, pfad: str
) -> None:
    antwort = angemeldeter_client.post(pfad)
    assert antwort.status_code == status.HTTP_403_FORBIDDEN


def test_aendernde_route_mit_gueltigem_token_laeuft_durch(
    angemeldeter_client: TestClient,
) -> None:
    geheimnis = angemeldeter_client.cookies[COOKIE_NAME]
    from thermoctl.config import get_settings

    kopf = {"X-CSRF-Token": csrf_token(geheimnis, get_settings().secret_key.get_secret_value())}
    antwort = angemeldeter_client.post("/logout", headers=kopf, follow_redirects=False)
    assert antwort.status_code == status.HTTP_303_SEE_OTHER


def test_ohne_sitzungscookie_greift_der_schutz_nicht(client: TestClient) -> None:
    """Die Anmeldung selbst traegt kein Cookie und darf nicht am CSRF-Schutz scheitern."""
    antwort = client.post("/login", data={"username": "gibtesnicht", "password": "x"})
    assert antwort.status_code == status.HTTP_401_UNAUTHORIZED


def test_csrf_cookie_wird_bei_der_anmeldung_gesetzt(client: TestClient, benutzer) -> None:
    antwort = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert antwort.status_code == status.HTTP_303_SEE_OTHER
    assert CSRF_COOKIE_NAME in client.cookies


def test_basisvorlage_uebertraegt_csrf_cookie_mit_htmx() -> None:
    """Ohne die Browserbruecke enden echte Formularaufrufe trotz Cookie mit 403."""
    from pathlib import Path

    basis = (
        Path(__file__).parent.parent / "thermoctl" / "web" / "templates" / "basis.html"
    ).read_text(encoding="utf-8")
    assert 'hx-boost="true"' in basis
    assert 'headers["X-CSRF-Token"]' in basis
    assert "thermoctl_csrf=" in basis
