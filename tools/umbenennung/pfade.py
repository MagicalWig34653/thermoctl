"""Die Web-Pfade auf Englisch -- Segment fuer Segment.

Segmentweise und nicht als Textersetzung: `/zonen/{zone_id}/geraete/messquelle` besteht
aus Teilen, die einzeln uebersetzt werden. Eine Ersetzung ueber die ganze Zeichenkette
wuerde `/api/v1/zones/...` mit anfassen -- und das ist ein Vertrag, der bleibt.
"""

import re
from pathlib import Path

SEGMENT = {
    "zonen": "zones", "geraete": "devices", "benutzer": "users", "gruppen": "groups",
    "modi": "modes", "zeitplan": "schedule", "sollwerte": "setpoints",
    "uebersteuerung": "override", "einstellungen": "settings", "steuerung": "control",
    "schnittstellen": "interfaces", "statistik": "statistics", "anlage": "plant",
    "neu": "new", "loeschen": "delete", "aufheben": "cancel", "zuordnen": "assign",
    "loesen": "detach", "messquelle": "source", "tauschen": "swap", "punkte": "points",
    "verschieben": "move", "uebernehmen": "adopt", "aktiv": "active",
    "passwort": "password", "rechte": "permissions", "widerrufen": "revoke",
    "entfernen": "remove", "scharf": "arm", "taste": "button",
    "anmeldung": "authentication", "registrierung": "registration",
    "argumente": "options", "pruefen": "verify", "parameter": "parameters",
    "zonendaten": "zone-data",
    # Platzhalter im Pfad muessen zum Funktionsparameter passen, sonst antwortet
    # FastAPI mit 422 -- genau daran sind sechzehn Tests haengengeblieben.
    "{benutzer_id}": "{user_id}", "{gruppen_id}": "{group_id}",
    "{modus_id}": "{mode_id}", "{punkt_id}": "{point_id}",
}

UNBERUEHRT = ("/api/", "/static/", "/healthz", "/docs", "/openapi", "/redoc",
              "/homeassistant/", "/favicon")


def pfad_uebersetzen(pfad: str) -> str:
    if any(pfad.startswith(p) for p in UNBERUEHRT):
        return pfad
    teile = pfad.split("/")
    return "/".join(SEGMENT.get(t, t) for t in teile)


def in_text(text: str) -> tuple[str, int]:
    """Uebersetzt nur, was **als Ganzes** ein Web-Pfad ist.

    Der erste Anlauf suchte mit einem Muster nach `/…` irgendwo in der Zeichenkette und
    griff damit mitten in die MQTT-Topics: Aus `thermoctl/zonen/1/befehl/parameter/x`
    wurde `.../parameters/x`, und der Befehlsempfaenger kannte die Art nicht mehr. Ein
    Web-Pfad faengt mit einem Schraegstrich an; alles andere geht diesen Schritt nichts an.
    """
    if not text.startswith("/"):
        return text, 0
    neu = pfad_uebersetzen(text)
    return neu, int(neu != text)
