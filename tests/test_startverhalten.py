"""Das Einrichtungs-Token ueber zwei Startvorgaenge hinweg.

Das Log ist der einzige Kanal, ueber den ein Betreiber an dieses Geheimnis kommt.
Entstuende bei jedem Neustart ein weiteres, gaebe es beliebig viele gueltige Tokens
gleichzeitig — jedes davon ein Schluessel zur noch offenen Einrichtung. Die
Zeilenabdeckung des Lifespan-Hooks sagt darueber nichts; nur das Zusammenspiel
zweier Starts tut es.
"""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from thermoctl.app import create_app
from thermoctl.config import Settings, get_settings
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.identity import User


@pytest.fixture
def leere_anlage(engine: Engine, settings: Settings,
                 monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Der Lifespan-Hook oeffnet eine eigene Sitzung und sieht die zurueckgerollte
    Transaktion der Fixture ``session`` nicht. Er braucht deshalb eine wirklich leere
    Datenbank — und muss hinterher aufraeumen, damit die uebrigen Tests sie so
    vorfinden, wie sie war."""
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    with Session(engine) as sitzung:
        assert sitzung.scalar(select(User.id).limit(1)) is None, (
            "Dieser Test setzt eine Anlage ohne Benutzer voraus."
        )
    yield engine
    with Session(engine) as sitzung:
        for marke in sitzung.scalars(select(SetupToken)):
            sitzung.delete(marke)
        sitzung.commit()
    get_settings.cache_clear()


def _start(engine: Engine) -> list[str]:
    """Faehrt den Dienst einmal hoch und wieder herunter, liefert die Tokenzeilen.

    Mit eigenem Handler statt ``caplog``: `configure_logging()` baut die Handler beim
    Erzeugen der Anwendung neu auf, wodurch die Mitschrift von pytest die Zeile nicht
    mehr sieht. Der Handler wird deshalb erst nach `create_app()` angehaengt.
    """
    mitschrift: list[str] = []

    class _Sammler(logging.Handler):
        def emit(self, satz: logging.LogRecord) -> None:
            mitschrift.append(satz.getMessage())

    app = create_app()
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = lambda: Session(engine)
    sammler = _Sammler()
    log = logging.getLogger("thermoctl.app")
    log.addHandler(sammler)
    try:
        with TestClient(app):
            pass
    finally:
        log.removeHandler(sammler)
    return [zeile for zeile in mitschrift if "Einmal-Token" in zeile]


def test_zweiter_start_erzeugt_kein_weiteres_token(leere_anlage: Engine) -> None:
    erste_zeilen = _start(leere_anlage)
    assert len(erste_zeilen) == 1, "Der erste Start muss genau ein Token melden."

    zweite_zeilen = _start(leere_anlage)
    assert zweite_zeilen == [], (
        "Der zweite Start darf kein weiteres Token erzeugen — sonst gibt es "
        "beliebig viele gueltige Schluessel zur offenen Einrichtung."
    )

    with Session(leere_anlage) as sitzung:
        offene = list(sitzung.scalars(select(SetupToken).where(SetupToken.consumed_at.is_(None))))
    assert len(offene) == 1
