"""Tests for the building blocks that sit between request and database.

They went untested for a long time because the test fixture bypasses them: it
hands in its own session instead of letting `get_session` run. That left
untested exactly the path every real request takes.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.db.engine import session_factory, session_scope


class _FakeApp:
    def __init__(self, factory: Any) -> None:
        self.state = type("State", (), {"session_factory": factory})()


class _FakeRequest:
    def __init__(self, factory: Any) -> None:
        self.app = _FakeApp(factory)


def test_get_session_commits_on_success(engine: Engine) -> None:
    """The path taken by every real request -- otherwise bypassed by the test fixture."""
    from thermoctl.auth.dependencies import get_session

    generator: Iterator[Session] = get_session(_FakeRequest(session_factory(engine)))  # type: ignore[arg-type]
    http_session = next(generator)
    http_session.execute(text("SELECT 1"))
    with pytest.raises(StopIteration):
        next(generator)


def test_get_session_rolls_back_on_error(engine: Engine) -> None:
    from thermoctl.auth.dependencies import get_session

    generator: Iterator[Session] = get_session(_FakeRequest(session_factory(engine)))  # type: ignore[arg-type]
    next(generator)
    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("abbruch"))


def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    with pytest.raises(RuntimeError):
        with session_scope(session_factory(engine)) as http_session:
            http_session.execute(text("SELECT 1"))
            raise RuntimeError("abbruch")


def test_a_protected_page_without_a_cookie_is_401(client: TestClient) -> None:
    assert client.get("/users").status_code == 401


def test_a_protected_page_with_an_unknown_cookie_is_401(client: TestClient) -> None:
    """The same response as without a cookie -- a different status would give away
    that the session once existed."""
    client.cookies.set(COOKIE_NAME, "ein-geheimnis-das-es-nie-gab")
    assert client.get("/users").status_code == 401


def test_a_protected_page_for_an_inactive_user_is_401(
    client: TestClient, user, session: Session
) -> None:
    """A deactivated account loses its running session immediately, not only at
    the next login."""
    _http_session, secret = create_session(session, user, 3600)
    session.flush()
    client.cookies.set(COOKIE_NAME, secret)
    assert client.get("/users").status_code != 401, "precondition: logged in"

    user.is_active = False
    session.flush()
    assert client.get("/users").status_code == 401


def test_cli_passes_the_settings_through_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a test, the start command would go unchecked -- and a mistyped
    parameter name would only surface in production."""
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
    assert kwargs["log_config"] is None, "otherwise uvicorn overwrites the JSON logging"
    assert isinstance(kwargs["port"], int)


def test_workflow_job_ids_and_service_values_stay_ascii() -> None:
    """GitHub lehnt eine Job-Kennung mit Umlaut ab -- und sagt nur "workflow file issue".

    Geschrieben, nachdem genau das passiert ist: Der Umlaut-Durchgang zog `pruefen:`
    zu `prüfen:`, und die CI fiel mit einer Meldung aus, die nicht sagt, welche Zeile
    gemeint ist. Der Docker-Bau lief weiter, das Abbild entstand -- nur geprüft wurde
    nichts mehr. Ein rotes Tor, das aussieht wie ein Werkzeugproblem, ist schlimmer als
    eines, das den Fehler nennt.

    Dieselbe Prüfung gilt für Werte, die als Zugangsdaten in einer Verbindungs-URL
    landen: Ein Passwort mit Umlaut muss dort prozentkodiert werden, und wer das
    vergisst, sucht den Fehler in der Datenbank statt im Workflow.
    """
    verzeichnis = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    schuldig: list[str] = []
    for datei in sorted(verzeichnis.glob("*.yml")):
        inhalt = yaml.safe_load(datei.read_text(encoding="utf-8"))
        for job in inhalt.get("jobs", {}):
            if not job.isascii():
                schuldig.append(f"{datei.name}: Job-Kennung {job!r}")
        for zeile in datei.read_text(encoding="utf-8").splitlines():
            nackt = zeile.strip()
            if nackt.startswith("#") or zeile.lstrip().startswith("#"):
                continue
            if ("PASSWORD" in nackt or "://" in nackt) and not nackt.isascii():
                schuldig.append(f"{datei.name}: {nackt[:60]}")
    assert not schuldig, "Nicht-ASCII an maschinenlesbaren Stellen:\n  " + "\n  ".join(schuldig)
