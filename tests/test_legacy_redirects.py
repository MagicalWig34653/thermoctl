"""Die GET-Seiten der alten Oberfläche leiten auf die Svelte-Oberfläche (`/app/`) um.

Gilt für angemeldete (Admin, Mieter) und abgemeldete Browser gleichermaßen; die Rechte
entscheidet die Oberfläche selbst über `/ui-api`.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.routing import Match

from tests.helpers import alle_api_routen, create_zone
from thermoctl.auth.dependencies import current_principal
from thermoctl.auth.kiosk import KIOSK_COOKIE_NAME
from thermoctl.domain.kiosk import issue_kiosk_token
from thermoctl.domain.principal import Principal
from thermoctl.web import legacy_redirects
from thermoctl.web.urls import safe_return_to

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
PREFIX = "/api/hassio_ingress/A1b2C3d4e5"

# Alter Pfad -> erwartetes Ziel unter /app. Absichtlich ausgeschrieben statt aus der
# Tabelle des Moduls abgeleitet: Ein Test, der die Tabelle liest, bestätigt nur sie selbst.
CASES: list[tuple[str, str]] = [
    ("/", "/app/"),
    ("/zones", "/app/uebersicht/raeume"),
    ("/zones/new", "/app/uebersicht/raeume"),
    ("/zones/7", "/app/raeume/7"),
    ("/zones/7/delete", "/app/raeume/7"),
    ("/zones/7/parameters", "/app/raeume/7"),
    ("/zones/7/setpoints", "/app/raeume/7"),
    ("/zones/7/schedule", "/app/zeit/wochenplaene"),
    ("/zones/7/schedule/adopt", "/app/zeit/wochenplaene"),
    ("/zones/7/schedule/points/3/delete", "/app/zeit/wochenplaene"),
    ("/schedule", "/app/zeit/wochenplaene"),
    ("/zones/7/devices", "/app/geraete/liste"),
    ("/zones/history", "/app/auswertung/verlauf"),
    ("/zones/7/history", "/app/auswertung/verlauf"),
    ("/temperature-history", "/app/auswertung/verlauf"),
    ("/temperature-history/7/tile", "/app/auswertung/verlauf"),
    ("/statistics", "/app/auswertung/verlauf"),
    ("/heating-time", "/app/auswertung/verlauf"),
    ("/relay-wear", "/app/auswertung/verschleiss"),
    ("/audit", "/app/auswertung/ereignisse"),
    ("/device-commands", "/app/auswertung/ereignisse"),
    ("/changes", "/app/auswertung/ereignisse"),
    ("/changes/5/revert", "/app/auswertung/ereignisse"),
    ("/devices", "/app/geraete/liste"),
    ("/plant", "/app/geraete/anlagenbild"),
    ("/devices/map", "/app/geraete/karte"),
    ("/zones/data-flow", "/app/geraete/karte"),
    ("/zones/7/data-flow", "/app/geraete/karte"),
    ("/controllers", "/app/geraete/bediengeraete"),
    ("/control", "/app/system/betrieb"),
    ("/settings", "/app/heizung/regelvorgaben"),
    ("/vacation", "/app/zeit/anlagenurlaub"),
    ("/modes", "/app/heizung/modi"),
    ("/modes/new", "/app/heizung/modi"),
    ("/modes/4", "/app/heizung/modi"),
    ("/modes/4/delete", "/app/heizung/modi"),
    ("/interfaces", "/app/system/verbindungen"),
    ("/users", "/app/personen/rechte"),
    ("/groups", "/app/personen/rechte"),
    ("/tokens", "/app/personen/tokens"),
    ("/kiosk-tokens", "/app/personen/kiosk"),
    ("/account", "/app/konto"),
    ("/account/help", "/app/hilfe"),
    ("/passkeys", "/app/konto"),
    ("/glossar", "/app/hilfe"),
    ("/setup", "/app/setup"),
    ("/kiosk", "/app/kiosk"),
    ("/login", "/app/login"),
    # Unbekannte Unterpfade bekannter Altbereiche gehen auf den Bereich.
    ("/zones/abc", "/app/uebersicht/raeume"),
    ("/zones/7/unbekannt/tief", "/app/uebersicht/raeume"),
    ("/modes/4/unbekannt/tief", "/app/heizung/modi"),
    ("/changes/unbekannt", "/app/auswertung/ereignisse"),
    ("/changes/5/unbekannt", "/app/auswertung/ereignisse"),
    ("/temperature-history/unbekannt", "/app/auswertung/verlauf"),
    ("/temperature-history/7/unbekannt", "/app/auswertung/verlauf"),
    # Eine Id, die keine kurze ASCII-Ziffernfolge ist, kommt nie in die Zieladresse.
    ("/zones/%C2%B2", "/app/uebersicht/raeume"),
    ("/zones/1234567890", "/app/uebersicht/raeume"),
]


def _as(kind: str, client: TestClient, as_client: Callable[..., TestClient]) -> TestClient:
    client.cookies.clear()
    if kind == "admin":
        return as_client([("zone.read", None), ("zone.manage", None)])
    if kind == "tenant":
        return as_client([("zone.read", 1)])
    return client


@pytest.mark.parametrize("kind", ["anonym", "admin", "mieter"])
@pytest.mark.parametrize(("old", "new"), CASES)
def test_every_legacy_page_redirects_to_the_spa(
    client: TestClient,
    client_als: Callable[..., TestClient],
    session: Session,
    kind: str,
    old: str,
    new: str,
) -> None:
    create_zone(session, "wohnen")
    browser = _as(kind, client, client_als)
    for headers in (HTML, {}):
        response = browser.get(old, headers=headers, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == new


@pytest.mark.parametrize(("old", "new"), CASES)
def test_the_ingress_prefix_is_applied_once(
    client_with_prefix: TestClient, old: str, new: str
) -> None:
    response = client_with_prefix.get(old, headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}{new}"


@pytest.mark.parametrize(
    "old",
    [
        "/zones?zone=3&period=7d",
        "/zones/7/history?period=7d",
        "/schedule?zone=3",
        "/kiosk?ansicht=tafel&zone_id=3",
        "/audit?kind=x&return_to=https://evil.example",
        "/setup?return_to=//evil.example",
    ],
)
def test_query_parameters_are_dropped(client: TestClient, old: str) -> None:
    response = client.get(old, follow_redirects=False)
    assert response.status_code == 303
    assert "?" not in response.headers["location"]
    assert "evil" not in response.headers["location"]


def test_unknown_paths_are_not_swallowed(client: TestClient) -> None:
    # Ein Fänger für alles würde OAuth-/MCP-Protokollrouten überdecken.
    assert client.get("/gibt-es-nicht", headers=HTML, follow_redirects=False).status_code == 404
    assert client.get("/authorize", headers=HTML, follow_redirects=False).status_code == 404


def test_post_routes_of_legacy_pages_are_not_touched(client: TestClient) -> None:
    # Schreib-POSTs bleiben bis zum Löschschritt: ohne Sitzung wie bisher "Nicht angemeldet".
    response = client.post("/zones/1/devices/detach", headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/login"


def test_logout_stays_a_post_and_leaves_through_the_login(client: TestClient) -> None:
    assert client.get("/logout", follow_redirects=False).status_code == 405


# --- /login: return_to nur über safe_return_to ----------------------------------------


def test_login_forwards_a_confirmed_return_to(client: TestClient) -> None:
    wanted = "/oauth/consent?request=abc"
    response = client.get(f"/login?return_to={quote(wanted, safe='')}", follow_redirects=False)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/app/login"
    assert parse_qs(location.query) == {"return_to": [wanted]}


def test_login_forwards_the_return_to_under_the_prefix(client_with_prefix: TestClient) -> None:
    response = client_with_prefix.get(
        f"/login?return_to={quote('/zones/1', safe='')}", follow_redirects=False
    )
    assert response.headers["location"] == (
        f"{PREFIX}/app/login?return_to={quote('/zones/1', safe='')}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "//evil.example",
        "//evil.example/x",
        "https://evil.example",
        "https://evil.example/x",
        "/%2e%2e",
        "/%2e%2e/x",
        "/%2E%2E/%2e%2e/x",
        "/\\evil.example",
        "/%5cevil.example",
        "/%2fevil.example",
        "/zones\r\nSet-Cookie: a=b",
        "/login",
        "/setup",
        "/api/v1/zones",
        "evil",
        "",
    ],
)
def test_login_drops_an_unsafe_return_to(
    client: TestClient, client_with_prefix: TestClient, value: str
) -> None:
    assert safe_return_to(value) is None
    for browser, prefix in ((client, ""), (client_with_prefix, PREFIX)):
        response = browser.get(f"/login?return_to={quote(value, safe='')}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"{prefix}/app/login"


def test_login_without_return_to(client: TestClient) -> None:
    response = client.get("/login", follow_redirects=False)
    assert response.headers["location"] == "/app/login"


# --- Kiosk: Einlösung bleibt serverseitig ----------------------------------------------


def _kiosk_token(session: Session) -> str:
    from tests.test_kiosk import _admin  # dieselbe Hilfe wie die Kiosk-Tests

    zone = create_zone(session, "flur")
    _token, plaintext = issue_kiosk_token(
        session, _admin(session), "Flur", [zone.id], control_allowed=False, expires_at=None
    )
    return plaintext


def test_kiosk_redemption_sets_the_cookie_and_goes_to_the_spa_kiosk(
    client: TestClient, session: Session
) -> None:
    plaintext = _kiosk_token(session)
    response = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/kiosk"
    assert response.cookies[KIOSK_COOKIE_NAME] == plaintext
    assert plaintext not in response.headers["location"]
    # Der Browser folgt: Das Cookie ist da, `/kiosk` führt auf dieselbe Seite wie zuvor.
    assert client.get("/kiosk", follow_redirects=False).headers["location"] == "/app/kiosk"


def test_kiosk_redemption_under_the_prefix(
    client_with_prefix: TestClient, session: Session
) -> None:
    plaintext = _kiosk_token(session)
    response = client_with_prefix.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert response.headers["location"] == f"{PREFIX}/app/kiosk"
    cookie = response.headers["set-cookie"]
    assert f"Path={PREFIX}" in cookie


def test_kiosk_redemption_with_an_invalid_token_stays_refused(client: TestClient) -> None:
    response = client.get("/kiosk/tctl_ungueltig", follow_redirects=False)
    assert response.status_code == 401
    assert KIOSK_COOKIE_NAME not in response.cookies


# --- OAuth: Rücksprung nach der Anmeldung ---------------------------------------------


def test_oauth_consent_without_session_goes_through_the_spa_login(client: TestClient) -> None:
    first = client.get("/oauth/consent?request=abc", follow_redirects=False)
    assert first.status_code == 303
    second = client.get(first.headers["location"], follow_redirects=False)
    assert second.status_code == 303
    location = urlsplit(second.headers["location"])
    assert location.path == "/app/login"
    assert parse_qs(location.query) == {"return_to": ["/oauth/consent?request=abc"]}


def test_oauth_consent_is_not_redirected_when_signed_in(
    client: TestClient, client_als: Callable[..., TestClient]
) -> None:
    browser = client_als([("token.self", None)])
    response = browser.get("/oauth/consent?request=unbekannt", follow_redirects=False)
    assert response.status_code != 303  # bleibt die serverseitige Seite (Clickjacking-Schutz)
    assert response.headers["x-frame-options"] == "DENY"


def _guarded_page(client: TestClient) -> None:
    async def page(_: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
        return {}  # pragma: no cover - ohne Sitzung nie erreicht

    client.app.add_api_route("/__geschuetzt", page, methods=["GET"])  # type: ignore[attr-defined]


def test_not_logged_in_handler_points_at_the_spa_login(client: TestClient) -> None:
    _guarded_page(client)
    response = client.get("/__geschuetzt?a=b", headers=HTML, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/app/login?return_to={quote('/__geschuetzt?a=b', safe='')}"
    )


def test_not_logged_in_handler_under_the_prefix(client_with_prefix: TestClient) -> None:
    _guarded_page(client_with_prefix)
    response = client_with_prefix.get("/__geschuetzt", headers=HTML, follow_redirects=False)
    assert response.headers["location"] == (
        f"{PREFIX}/app/login?return_to={quote('/__geschuetzt', safe='')}"
    )
    htmx = client_with_prefix.get(
        "/__geschuetzt",
        headers={"HX-Request": "true", "HX-Current-URL": f"http://h{PREFIX}/oauth/consent?r=1"},
        follow_redirects=False,
    )
    assert htmx.headers["HX-Redirect"] == (
        f"{PREFIX}/app/login?return_to={quote('/oauth/consent?r=1', safe='')}"
    )


# --- Vollständigkeit -----------------------------------------------------------------

# GET-Routen, die bewusst NICHT umgeleitet werden: Oberfläche selbst, Schnittstellen,
# Protokolle, Hülle und Dateien sowie die zwei serverseitigen Seiten mit Begründung.
_KEPT = (
    "/ui-api/",
    "/kiosk-api/",
    "/api/",
    "/static",
    "/app",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/healthz",
    "/oauth/consent",
    "/kiosk/{plaintext}",
    "/mcp",
    "/.well-known/",
    "/passkey/",
)


def test_every_remaining_legacy_get_page_is_shadowed_by_a_redirect(client: TestClient) -> None:
    """Die alten GET-Routen bestehen bis zum Löschschritt, werden aber nie erreicht: Für
    jede steht eine Umleitung davor. Eine neue Altseite ohne Umleitung fällt hier auf."""
    routes = alle_api_routen(client.app)  # type: ignore[arg-type]
    exposed = []
    for route in routes:
        if "GET" not in route.methods or route.path.startswith(_KEPT):
            continue
        concrete = re.sub(r"\{[^}]*\}", "1", route.path)
        scope = {"type": "http", "method": "GET", "path": concrete, "headers": []}
        first = next(r for r in routes if r.matches(scope)[0] == Match.FULL)
        if first.endpoint.__module__ != legacy_redirects.__name__:
            exposed.append(route.path)
    assert exposed == [], f"GET-Seiten ohne Umleitung davor: {exposed}"


def test_the_first_matching_route_for_every_redirect_is_ours(client: TestClient) -> None:
    routes = alle_api_routen(client.app)  # type: ignore[arg-type]
    for old, _new in CASES:
        concrete = old.replace("%C2%B2", "x").replace("1234567890", "1")
        scope = {"type": "http", "method": "GET", "path": concrete, "headers": []}
        first = next(r for r in routes if r.matches(scope)[0] == Match.FULL)
        assert first.endpoint.__module__ == legacy_redirects.__name__, old


def test_every_target_exists_in_the_spa_navigation() -> None:
    navigation = Path(__file__).parent.parent / "frontend" / "src" / "lib" / "navigation.ts"
    source = navigation.read_text(encoding="utf-8")
    ids = set(re.findall(r"id: '([a-z]+)'", source))
    targets = (
        {new for _old, new in CASES}
        | set(legacy_redirects.EXACT_TARGETS.values())
        | {f"/app{t}" for t in legacy_redirects.PARAM_TARGETS.values()}
    )
    for target in targets:
        parts = [p for p in target.removeprefix("/app").split("/") if p]
        if not parts or parts[0] in {"raeume", "konto", "hilfe", "login", "setup", "kiosk"}:
            continue
        assert set(parts) <= ids, target
