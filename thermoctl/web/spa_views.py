"""Auslieferung der Svelte-Oberfläche (`frontend/`) unter `/app/`.

Die Oberfläche ist eine einzige, gebaute HTML-Hülle (`static/app/index.html`, Ausgabe
von `npm run build`) plus gehashte Assets unter `/static/app/assets/`. Zur Build-Zeit
ist der Pfadpräfix unbekannt -- hinter Home-Assistant-Ingress steht er erst pro Anfrage
fest (`thermoctl.web.urls`). Deshalb schreibt dieser Router ihn zur Laufzeit in die
Hülle: als `<meta name="thermoctl-prefix">` für das Skript und in die relativen
Asset-Pfade (`./assets/...` wird zu `<prefix>/static/app/assets/...`, sonst würde der
Browser sie unter `/app/assets/` suchen).

Die Hülle selbst enthält keine Anlagendaten und braucht keine Anmeldung; jede Anfrage
der Oberfläche geht an die geschützte Schnittstelle (`/ui-api/`), die Sitzung und
Rechte prüft. Ist die Oberfläche nicht gebaut (Entwicklungsstand ohne Node), antwortet
der Router mit 503 und einem Hinweis statt mit einer leeren Seite.
"""

from html import escape
from pathlib import Path

from fastapi import APIRouter, Request, Response

from thermoctl.web.assets import STATIC_DIR

router = APIRouter(include_in_schema=False)

# Modulvariable statt Konstante im Funktionskörper, damit Tests auf ein Verzeichnis
# ohne Build zeigen können.
INDEX_FILE: Path = STATIC_DIR / "app" / "index.html"

NOT_BUILT = (
    "Die Oberfläche ist nicht gebaut. Im Verzeichnis frontend/ `npm ci && npm run build` "
    "ausführen (das Container-Abbild tut das selbst)."
)


def render_shell(html: str, prefix: str) -> str:
    """Setzt Präfix-Meta und absolute Asset-Pfade in die gebaute Hülle ein."""
    safe = escape(prefix, quote=True)
    html = html.replace('"./assets/', f'"{safe}/static/app/assets/')
    meta = f'<meta name="thermoctl-prefix" content="{safe}">'
    return html.replace("</head>", f"    {meta}\n  </head>", 1)


@router.get("/app")
@router.get("/app/{path:path}")
async def spa_shell(request: Request, path: str = "") -> Response:
    """Dieselbe Hülle für `/app` und jeden Direktlink darunter; der Router im Browser
    entscheidet anhand des Pfads, was angezeigt wird."""
    if not INDEX_FILE.is_file():
        return Response(NOT_BUILT, status_code=503, media_type="text/plain; charset=utf-8")
    prefix = str(request.scope.get("root_path", ""))
    body = render_shell(INDEX_FILE.read_text(encoding="utf-8"), prefix)
    # Die Hülle verweist auf gehashte Assets: Sie muss bei jedem Aufruf neu geprüft
    # werden, sonst zeigt ein Browser nach einem Update auf Dateien, die es nicht mehr gibt.
    return Response(
        body, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"}
    )
