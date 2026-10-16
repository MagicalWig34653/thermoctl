"""Alle GET-Seiten der alten Jinja/HTMX-Oberfläche leiten auf die Svelte-Oberfläche um.

Projektinhaber-Entscheidung 2026-10-16: Die Oberfläche unter `/app/` ersetzt die alte
vollständig. Dieses Modul ist der erste Schritt (Umschalten); die alten Vorlagen, Ansichten
und Schreib-POSTs bestehen noch und werden erst in einem späteren Schritt entfernt.

Eigenschaften, auf die es ankommt:

- Der Router wird in `thermoctl.app` **vor** allen alten Routern eingebunden und gewinnt
  damit bei jedem GET. Er hängt an keiner Anmeldung: angemeldete wie abgemeldete Browser
  bekommen dieselbe 303-Antwort, die Oberfläche selbst führt bei Bedarf zur Anmeldung.
  Rechte werden hier nicht entschieden -- die entscheidet jede Anfrage der Oberfläche an
  `/ui-api/` -- und es werden auch keine Daten gelesen.
- Das Ziel ist immer ein fester Pfad aus der Tabelle unten, nie etwas aus der Anfrage.
  Einzige Ausnahme ist `/login`: Dort wird `return_to` übernommen, aber nur, wenn
  `safe_return_to` es bestätigt; sonst entfällt es. Dazu kommen genau drei Direktlink-
  Parameter, die die Oberfläche versteht -- `?zone` (nur eine kurze Ziffernfolge), `?period`
  (nur die bekannten Zeiträume, umgerechnet) und `?ansicht` (nur `auto`/`panel`/`tafel`) --
  und auch nur für die Ziele, die sie lesen (`TARGET_PARAMS`). Jeder Wert wird neu gebildet,
  nie durchgereicht; alles andere wird verworfen. Ob die Raum-Id existiert oder sichtbar ist,
  weiß dieses Modul nicht (es liest keine Daten): Die Oberfläche verwirft eine unbekannte
  oder fremde Id ohne Hinweis. Ein Fragment (`/glossar#sollwert`) behält der Browser über
  die Weiterleitung hinweg von selbst; die Hilfe liest es.
- Der Pfadpräfix (Home-Assistant-Ingress) kommt wie bei jedem handgebauten Redirect aus
  `prefixed()`.
- Ausdrücklich NICHT hier: Schreib-POSTs, die JSON-Routen von Passkey/Kiosk-Cookie,
  `/oauth/consent` (Clickjacking-Schutz, bleibt serverseitig), `/kiosk/{token}`
  (Einlösung setzt das Cookie, siehe `kiosk_views`), `/logout`, `/api`, `/mcp`,
  `/ui-api`, `/kiosk-api`, `/static`, `/healthz`, `/docs`.
- Ein vollständig unbekannter Altpfad (`/gibt-es-nicht`) bleibt 404: Ein Fänger für alles
  würde die Protokollrouten (OAuth, MCP, Wellknown) überdecken. Unbekannte *Unterpfade*
  bekannter Altbereiche (`/zones/abc/foo`) gehen dagegen auf den Bereich.
"""

from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from thermoctl.web.urls import prefixed, safe_return_to

router = APIRouter(include_in_schema=False)

# Alter Pfad (ohne Präfix, ohne Query) -> Pfad unter `/app`. Reihenfolge ist
# bedeutsam, wo Platzhalter gegen feste Pfade stehen: feste zuerst (`/zones/history`
# vor `/zones/{id}`).
EXACT_TARGETS: dict[str, str] = {
    "/": "/",
    "/zones": "/uebersicht/raeume",
    "/zones/new": "/uebersicht/raeume",
    "/zones/history": "/auswertung/verlauf",
    "/zones/data-flow": "/geraete/karte",
    "/statistics": "/auswertung/verlauf",
    "/heating-time": "/auswertung/verlauf",
    "/temperature-history": "/auswertung/verlauf",
    "/relay-wear": "/auswertung/verschleiss",
    "/audit": "/auswertung/ereignisse",
    "/device-commands": "/auswertung/ereignisse",
    "/changes": "/auswertung/ereignisse",
    "/devices": "/geraete/liste",
    "/plant": "/geraete/anlagenbild",
    "/devices/map": "/geraete/karte",
    "/controllers": "/geraete/bediengeraete",
    "/control": "/system/betrieb",
    "/interfaces": "/system/verbindungen",
    "/settings": "/heizung/regelvorgaben",
    "/modes": "/heizung/modi",
    "/modes/new": "/heizung/modi",
    "/vacation": "/zeit/anlagenurlaub",
    "/schedule": "/zeit/wochenplaene",
    "/users": "/personen/rechte",
    "/groups": "/personen/rechte",
    "/tokens": "/personen/tokens",
    "/kiosk-tokens": "/personen/kiosk",
    "/account": "/konto",
    "/account/help": "/hilfe",
    "/passkeys": "/konto",
    "/glossar": "/hilfe",
    "/setup": "/setup",
    "/kiosk": "/kiosk",
}

ROOM_PATHS: tuple[str, ...] = (
    "/zones/{zone_id}",
    "/zones/{zone_id}/parameters",
    "/zones/{zone_id}/setpoints",
    "/zones/{zone_id}/delete",
)
PARAM_TARGETS: dict[str, str] = {
    "/zones/{zone_id}/history": "/auswertung/verlauf",
    "/zones/{zone_id}/data-flow": "/geraete/karte",
    "/zones/{zone_id}/devices": "/geraete/liste",
    "/zones/{zone_id}/schedule": "/zeit/wochenplaene",
    "/zones/{zone_id}/schedule/{rest:path}": "/zeit/wochenplaene",
    "/temperature-history/{zone_id}/tile": "/auswertung/verlauf",
    "/modes/{mode_id}/delete": "/heizung/modi",
    "/modes/{mode_id}": "/heizung/modi",
    "/changes/{change_id}/revert": "/auswertung/ereignisse",
}

# Unterpfade eines bekannten Altbereichs, die keine eigene Seite hatten, gehen auf den
# Bereich statt auf eine 404/422 der Altseite. Zuletzt registriert, damit jede konkrete
# Seite vorher greift.
SECTION_FALLBACKS: dict[str, str] = {
    "/zones": "/uebersicht/raeume",
    "/modes": "/heizung/modi",
    "/changes": "/auswertung/ereignisse",
    "/temperature-history": "/auswertung/verlauf",
}

APP_ROOT = "/app"

# Direktlink-Parameter je Ziel (Pfad unter `/app`), die die Oberfläche versteht. Was hier
# nicht steht, wird nicht übernommen.
TARGET_PARAMS: dict[str, tuple[str, ...]] = {
    "/zeit/wochenplaene": ("zone",),
    "/auswertung/verlauf": ("zone", "period"),
    "/geraete/liste": ("zone",),
    "/geraete/karte": ("zone", "tab"),
    "/kiosk": ("ansicht",),
}

# Alte Zeiträume (Tage) auf die Kurzbezeichnungen der Oberfläche; 90 Tage gibt es dort nicht.
_PERIODS: dict[str, str] = {"7": "7d", "30": "30d", "24h": "24h", "7d": "7d", "30d": "30d"}
_ANSICHTEN = frozenset({"auto", "panel", "tafel"})
_KARTEN_TABS = frozenset({"karte", "liste", "datenfluss"})


def clean_zone_id(value: object) -> str | None:
    """Eine Raum-Id nur als kurze, positive Ziffernfolge (ohne führende Nullen)."""
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()) or len(value) > 9:
        return None
    number = int(value)
    return str(number) if number > 0 else None


def clean_param(name: str, value: object) -> str | None:
    """Der geprüfte, neu gebildete Wert eines Direktlink-Parameters oder `None`."""
    if name == "zone":
        return clean_zone_id(value)
    if name == "period":
        return _PERIODS.get(value) if isinstance(value, str) else None
    if name == "ansicht":
        return value if isinstance(value, str) and value in _ANSICHTEN else None
    if name == "tab":
        return value if isinstance(value, str) and value in _KARTEN_TABS else None
    return None  # pragma: no cover - TARGET_PARAMS nennt nur die bekannten Namen


def target_with_query(
    request: Request, target: str, extra: dict[str, str] | None = None
) -> str:
    """`target` mit den erlaubten, geprüften Parametern der Anfrage; `extra` sind feste Werte
    (zum Beispiel die Raum-Id aus dem Altpfad), sie gelten vor der Anfrage."""
    values: dict[str, str] = dict(extra or {})
    for name in TARGET_PARAMS.get(target, ()):
        cleaned = clean_param(name, request.query_params.get(name))
        if cleaned is not None and name not in values:
            values[name] = cleaned
    ordered = {name: values[name] for name in TARGET_PARAMS.get(target, ()) if name in values}
    return f"{target}?{urlencode(ordered)}" if ordered else target


def app_location(request: Request, target: str) -> str:
    """Vollständige Adresse (mit Präfix) eines Pfads unter `/app`."""
    return prefixed(request, f"{APP_ROOT}{target}")


def _handler(
    target: str, *, zone_from_path: bool = False, fixed: dict[str, str] | None = None
) -> Callable[[Request], Awaitable[Response]]:
    async def redirect(request: Request) -> Response:
        extra: dict[str, str] = {}
        if zone_from_path:
            zone_id = clean_zone_id(request.path_params.get("zone_id"))
            if zone_id is not None:
                extra["zone"] = zone_id
        extra.update(fixed or {})
        return RedirectResponse(
            app_location(request, target_with_query(request, target, extra)),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return redirect


async def _room(request: Request) -> Response:
    """`/zones/{id}...` -> `/app/raeume/{id}`. Die Id kommt aus der Anfrage und landet in
    der Adresse, also nur als kurze Ziffernfolge; alles andere geht auf die Raumliste."""
    zone_id = str(request.path_params.get("zone_id", ""))
    valid = zone_id.isascii() and zone_id.isdigit() and len(zone_id) <= 9
    target = f"/raeume/{zone_id}" if valid else "/uebersicht/raeume"
    return RedirectResponse(app_location(request, target), status_code=status.HTTP_303_SEE_OTHER)


# Der Datenfluss ist ein Reiter der Gerätekarte.
FIXED_PARAMS: dict[str, dict[str, str]] = {
    "/zones/data-flow": {"tab": "datenfluss"},
    "/zones/{zone_id}/data-flow": {"tab": "datenfluss"},
}

for _path, _target in EXACT_TARGETS.items():
    if _path in ("/setup", "/kiosk"):
        continue  # eigene Handler weiter unten
    router.add_api_route(_path, _handler(_target, fixed=FIXED_PARAMS.get(_path)), methods=["GET"])
for _path in ROOM_PATHS:
    router.add_api_route(_path, _room, methods=["GET"])
for _path, _target in PARAM_TARGETS.items():
    router.add_api_route(
        _path,
        _handler(_target, zone_from_path="{zone_id}" in _path, fixed=FIXED_PARAMS.get(_path)),
        methods=["GET"],
    )
for _path, _target in SECTION_FALLBACKS.items():
    router.add_api_route(f"{_path}/{{rest:path}}", _handler(_target), methods=["GET"])


@router.get("/setup")
async def setup(request: Request) -> Response:
    """Die Ersteinrichtung der Oberfläche. Ob sie offen ist, sagt `/ui-api/setup`."""
    return RedirectResponse(app_location(request, "/setup"), status_code=status.HTTP_303_SEE_OTHER)


@router.get("/kiosk")
async def kiosk(request: Request) -> Response:
    """Die Kioskansicht der Oberfläche; das Cookie liest `/kiosk-api`."""
    return RedirectResponse(
        app_location(request, target_with_query(request, "/kiosk")),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/login")
async def login(request: Request) -> Response:
    """Die Anmeldeseite der Oberfläche.

    `return_to` wird nur übernommen, wenn `safe_return_to` es bestätigt -- dieselbe
    Prüfung, die der Server beim Anmelden noch einmal anwendet, bevor er das Ziel an die
    Oberfläche zurückgibt. Es wird als einzelner, kodierter Parameter angehängt, nie als
    Teil des Pfads.
    """
    location = app_location(request, "/login")
    return_to = safe_return_to(request.query_params.get("return_to"))
    if return_to is not None:
        location = f"{location}?{urlencode({'return_to': return_to})}"
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)
