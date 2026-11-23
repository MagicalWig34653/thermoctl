"""Eine Anmeldung gilt nur für den Passwortstand, gegen den sie geprüft wurde.

Die Passwortprüfung (Argon2id) dauert; währenddessen kann das Passwort gesetzt oder das
Konto deaktiviert werden. Setzt jemand das Passwort, *endet* jede bestehende Sitzung -- eine
Sitzung, die erst danach entsteht, überlebt das und gehört einem Anmelder, der nur das alte
Passwort kannte. Gemessen über echte Anfragen: 6 von 6 Läufen bei 0 bis 400 ms Versatz.
Deshalb wird beim Anlegen der Sitzung die Zeile des Kontos gesperrt gelesen und der Hash
verglichen (`tests/test_administration_race_http.py` prüft dasselbe gegen echte
Gleichzeitigkeit auf MariaDB).
"""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import thermoctl.web.shared.login as login
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User
from thermoctl.domain.administration import set_password
from thermoctl.web.shared.login import attempt_login

RICHTIG = "passwort-lang-genug"
NEU = "ein-ganz-neues-passwort"


def _request() -> Any:
    return SimpleNamespace(headers={}, client=None)


def _live_sessions(session: Session, user: User) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Session_).where(
                Session_.user_id == user.id,
                Session_.revoked_at.is_(None),
                Session_.expires_at > utcnow(),
            )
        )
        or 0
    )


def _change_during_the_password_check(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]
) -> None:
    """Runs `change` right after the password has been checked (and found right).

    The check normally runs in a worker thread; here it runs inline, so the change goes
    through the very same (test) connection at exactly the moment the race needs.
    """
    real = login.verify_password  # type: ignore[attr-defined]

    async def run_inline(function: Callable[..., Any], *args: Any) -> Any:
        result = function(*args)
        if function is real:
            change()
        return result

    monkeypatch.setattr(login.to_thread, "run_sync", run_inline)  # type: ignore[attr-defined]


def test_without_a_change_the_login_opens_a_session(user: User, session: Session) -> None:
    outcome = asyncio.run(attempt_login(_request(), session, "lino", RICHTIG))

    assert outcome is not None
    assert _live_sessions(session, user) == 1


def test_a_password_set_during_the_check_leaves_no_session_behind(
    user: User, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _change_during_the_password_check(
        monkeypatch, lambda: set_password(session, user, NEU, actor_id=None)
    )

    outcome = asyncio.run(attempt_login(_request(), session, "lino", RICHTIG))

    assert outcome is None  # the old password no longer opens anything
    assert _live_sessions(session, user) == 0


def test_a_deactivation_right_after_the_check_leaves_no_session_behind(
    user: User, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_reset = login.reset_failures

    def deactivate_then_reset(key: str) -> None:
        # After the verdict "right password, active account", before the session exists;
        # past the identity map, as another transaction would write it.
        session.execute(
            update(User).where(User.id == user.id).values(is_active=False),
            execution_options={"synchronize_session": False},
        )
        real_reset(key)

    monkeypatch.setattr(login, "reset_failures", deactivate_then_reset)

    assert asyncio.run(attempt_login(_request(), session, "lino", RICHTIG)) is None
    assert _live_sessions(session, user) == 0


def test_the_new_password_still_logs_in_after_the_change(
    user: User, session: Session
) -> None:
    set_password(session, user, NEU, actor_id=None)

    assert asyncio.run(attempt_login(_request(), session, "lino", RICHTIG)) is None
    assert asyncio.run(attempt_login(_request(), session, "lino", NEU)) is not None
    assert _live_sessions(session, user) == 1
