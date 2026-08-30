"""Tests fuer die Bausteine, die zwischen Anfrage und Datenbank sitzen.

Sie waren lange ungetestet, weil die Testfixture sie umgeht: Sie reicht ihre eigene
Sitzung herein, statt `get_session` laufen zu lassen. Damit blieb ausgerechnet der
Pfad ungeprueft, den jede echte Anfrage nimmt.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.db.engine import session_factory, session_scope


class _FakeApp:
    def __init__(self, factory: Any) -> None:
        self.state = type("Zustand", (), {"session_factory": factory})()


class _FakeRequest:
    def __init__(self, factory: Any) -> None:
        self.app = _FakeApp(factory)


def test_get_session_committet_bei_erfolg(engine: Engine) -> None:
    """Der Pfad jeder echten Anfrage — von der Testfixture sonst umgangen."""
    from thermoctl.auth.dependencies import get_session

    erzeuger: Iterator[Session] = get_session(_FakeRequest(session_factory(engine)))  # type: ignore[arg-type]
    http_session = next(erzeuger)
    http_session.execute(text("SELECT 1"))
    with pytest.raises(StopIteration):
        next(erzeuger)


def test_get_session_rollt_bei_fehler_zurueck(engine: Engine) -> None:
    from thermoctl.auth.dependencies import get_session

    erzeuger: Iterator[Session] = get_session(_FakeRequest(session_factory(engine)))  # type: ignore[arg-type]
    next(erzeuger)
    with pytest.raises(RuntimeError):
        erzeuger.throw(RuntimeError("abbruch"))


def test_session_scope_rollt_bei_fehler_zurueck(engine: Engine) -> None:
    with pytest.raises(RuntimeError):
        with session_scope(session_factory(engine)) as http_session:
            http_session.execute(text("SELECT 1"))
            raise RuntimeError("abbruch")


def test_geschuetzte_seite_ohne_cookie_ist_401(client: TestClient) -> None:
    assert client.get("/users").status_code == 401


def test_geschuetzte_seite_mit_unbekanntem_cookie_ist_401(client: TestClient) -> None:
    """Dieselbe Antwort wie ohne Cookie — ein anderer Status wuerde verraten,
    dass die Sitzung einmal existiert hat."""
    client.cookies.set(COOKIE_NAME, "ein-geheimnis-das-es-nie-gab")
    assert client.get("/users").status_code == 401


def test_geschuetzte_seite_bei_inaktivem_benutzer_ist_401(
    client: TestClient, user, session: Session
) -> None:
    """Ein deaktiviertes Konto verliert seine laufende Sitzung sofort, nicht erst
    beim naechsten Anmelden."""
    _http_session, geheimnis = create_session(session, user, 3600)
    session.flush()
    client.cookies.set(COOKIE_NAME, geheimnis)
    assert client.get("/users").status_code != 401, "Vorbedingung: angemeldet"

    user.is_active = False
    session.flush()
    assert client.get("/users").status_code == 401


def test_cli_reicht_die_einstellungen_an_uvicorn_durch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Test bliebe der Startbefehl ungeprueft — und ein vertippter
    Parametername faellt dann erst im Betrieb auf."""
    from thermoctl.cli import main
    from thermoctl.config import get_settings

    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "t" * 32)
    monkeypatch.setenv("THERMOCTL_BIND_PORT", "8123")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings, "cache_clear", get_settings.cache_clear)

    with patch("uvicorn.run") as started:
        main()
    get_settings.cache_clear()

    started.assert_called_once()
    _args, kwargs = started.call_args
    assert kwargs["factory"] is True
    assert kwargs["log_config"] is None, "sonst ueberschreibt uvicorn das JSON-Logging"
    assert isinstance(kwargs["port"], int)
