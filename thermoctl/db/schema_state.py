"""Prueft beim Start, ob das Datenbankschema zum Code passt.

Ohne diese Pruefung scheitert der Dienst an der ersten Abfrage des Lebenszyklus mit
einem sechzigzeiligen SQLAlchemy-Traceback, dessen aussagekraeftigste Zeile
`no such table: user` lautet -- richtig, aber nutzlos: Sie sagt nicht, dass eine
Migration fehlt, und schon gar nicht, welcher Befehl hilft. Ein veraltetes Schema ist
noch unangenehmer, weil es nicht am Start scheitert, sondern irgendwann spaeter an
einer Spalte, die es noch nicht gibt.

Der Container migriert selbst (`docker/entrypoint.sh`), ein lokaler `uvicorn`-Start
nicht. Genau dort tritt der Fall auf.
"""

import logging
from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)

COMMAND = "alembic upgrade head"


class SchemaPasstNicht(RuntimeError):
    """Der Dienst kann nicht starten, weil das Schema fehlt oder veraltet ist."""


def _migration_head() -> str | None:
    """Die Zielrevision laut Migrationsverzeichnis, oder None, wenn es nicht
    auffindbar ist.

    Das Verzeichnis liegt ausserhalb des Pakets und wird nur mitgeliefert, wo es
    gebraucht wird (Repository, Container-Abbild). Wer thermoctl bloss als Rad
    installiert, hat es nicht -- dann faellt der Vergleich weg, statt einen
    Fehlalarm auszuloesen.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError:  # pragma: no cover - alembic ist eine feste Abhaengigkeit
        return None

    ini = Path("alembic.ini")
    if not ini.is_file():
        return None
    try:
        verzeichnis = ScriptDirectory.from_config(Config(str(ini)))
        koepfe = verzeichnis.get_heads()
    except Exception:  # pragma: no cover - defekte Konfiguration ist kein Startgrund
        return None
    # Mehrere Koepfe waeren ein Fehler in der Historie, keiner in dieser Datenbank.
    return koepfe[0] if len(koepfe) == 1 else None


def database_state(engine: Engine) -> str | None:
    """Die eingetragene Revision, oder None, wenn die Datenbank kein Schema hat."""
    try:
        if not inspect(engine).has_table("alembic_version"):
            return None
        with engine.connect() as verbindung:
            entry = verbindung.scalar(text("SELECT version_num FROM alembic_version"))
            return str(entry) if entry is not None else None
    except SQLAlchemyError:  # pragma: no cover - unerreichbare Datenbank meldet sich selbst
        return None


def check_schema(engine: Engine) -> None:
    """Bricht den Start ab, wenn das Schema fehlt oder hinter dem Code zurueckliegt.

    Ist der Kopf nicht ermittelbar, wird nur das Fehlen des Schemas geprueft -- lieber
    eine Pruefung weniger als eine, die im falschen Moment blockiert.
    """
    state = database_state(engine)
    if state is None:
        if not inspect(engine).has_table("user"):
            raise SchemaPasstNicht(
                f"Die Datenbank hat kein Schema. Vor dem ersten Start einmal '{COMMAND}' "
                "ausfuehren; das Container-Abbild erledigt das selbst."
            )
        # Tabellen ohne Alembic-Stempel: So legt die Testsuite ihr Schema an
        # (`Base.metadata.create_all()`). Das ist kein Startgrund -- nur der
        # Versionsvergleich hat dann keine Grundlage.
        log.warning(
            "Das Schema wurde nicht ueber Alembic angelegt, der Versionsvergleich entfaellt"
        )
        return

    kopf = _migration_head()
    if kopf is None:
        log.debug("Migrationsverzeichnis nicht gefunden, Versionsvergleich entfaellt")
        return
    if state != kopf:
        raise SchemaPasstNicht(
            f"Das Datenbankschema steht auf {state}, der Code erwartet {kopf}. "
            f"'{COMMAND}' ausfuehren."
        )
