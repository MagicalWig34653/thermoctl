"""Die Dokumentation muss beschreiben, was es wirklich gibt.

Anlass: Beim Nachfragen fiel auf, dass `docs/mcp.md` zwei von neun Werkzeugen benannte,
`.env.example` eine Einstellung nicht kannte und `docs/api.md` noch behauptete, Tokens
liessen sich nur ueber eine Python-Funktion ausstellen — was seit Teilprojekt 3 falsch war.

Eine Beschreibung, die hinterherhinkt, ist schlimmer als keine: Sie wird geglaubt. Diese
Tests halten die drei Stellen nach, an denen Doku und Code am schnellsten auseinanderlaufen.
Sie pruefen ausdruecklich nur die *Vollstaendigkeit* — ob der Text daneben stimmt, muss
weiterhin jemand lesen.
"""

import re
from pathlib import Path

from tests.hilfen import alle_api_routen
from thermoctl.app import create_app
from thermoctl.config import Settings

WURZEL = Path(__file__).resolve().parent.parent
AENDERNDE_UND_LESENDE = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def test_jeder_rest_endpunkt_steht_in_der_beschreibung() -> None:
    text = (WURZEL / "docs" / "api.md").read_text(encoding="utf-8")
    fehlend = [
        f"{methode} {route.path}"
        for route in alle_api_routen(create_app())
        if route.path.startswith("/api/")
        for methode in sorted(route.methods & AENDERNDE_UND_LESENDE)
        if f"{methode} {route.path}" not in text
    ]
    assert not fehlend, "Diese Endpunkte beschreibt docs/api.md nicht: " + ", ".join(fehlend)


def test_jedes_mcp_werkzeug_steht_in_der_beschreibung() -> None:
    from thermoctl.mcp import server

    text = (WURZEL / "docs" / "mcp.md").read_text(encoding="utf-8")
    # Die oeffentlichen Werkzeugfunktionen des Moduls, ohne den Starter `main`.
    werkzeuge = [
        name
        for name in dir(server)
        if not name.startswith("_")
        and name != "main"
        and callable(getattr(server, name))
        and getattr(getattr(server, name), "__module__", "") == server.__name__
    ]
    assert werkzeuge, "Es wurden gar keine Werkzeuge gefunden — der Test prüft dann nichts."
    fehlend = [name for name in werkzeuge if f"`{name}(" not in text]
    assert not fehlend, "Diese MCP-Werkzeuge beschreibt docs/mcp.md nicht: " + ", ".join(fehlend)


def test_jede_einstellung_steht_in_der_beispieldatei() -> None:
    """`.env.example` ist die einzige Liste, die ein Betreiber zu sehen bekommt.

    Eine Einstellung, die dort fehlt, existiert für ihn nicht — er findet sie höchstens,
    wenn er den Quelltext liest.
    """
    text = (WURZEL / ".env.example").read_text(encoding="utf-8")
    vorhanden = set(re.findall(r"^(THERMOCTL_[A-Z_]+)=", text, re.MULTILINE))
    erwartet = {f"THERMOCTL_{name.upper()}" for name in Settings.model_fields}
    fehlend = sorted(erwartet - vorhanden)
    assert not fehlend, ".env.example kennt diese Einstellungen nicht: " + ", ".join(fehlend)
