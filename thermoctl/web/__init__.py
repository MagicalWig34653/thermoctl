from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Bootstrap und HTMX liegen als Dateien in diesem Verzeichnis (siehe
# static/HERKUNFT.md) und werden lokal ausgeliefert, nicht ueber ein CDN --
# `thermoctl` soll auch ohne Internetzugang im Heimnetz benutzbar bleiben.
STATIC_DIR = Path(__file__).parent / "static"
