"""The setup token across two startup runs.

The log is the only channel through which an operator gets at this secret.
If every restart produced another one, there would be arbitrarily many valid
tokens at once — each one a key to the still-open setup. Line coverage of the
lifespan hook says nothing about that; only the interaction between two starts
does.
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
def empty_installation(engine: Engine, settings: Settings,
                 monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """The lifespan hook opens its own session and does not see the ``session``
    fixture's rolled-back transaction. It therefore needs a genuinely empty
    database — and must clean up afterward so the remaining tests find it the
    way it was."""
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    with Session(engine) as http_session:
        assert http_session.scalar(select(User.id).limit(1)) is None, (
            "This test assumes an installation with no users."
        )
    yield engine
    with Session(engine) as http_session:
        for marker in http_session.scalars(select(SetupToken)):
            http_session.delete(marker)
        http_session.commit()
    get_settings.cache_clear()


def _start(engine: Engine) -> list[str]:
    """Starts the service once and shuts it down again, returns the token log lines.

    Uses its own handler instead of ``caplog``: `configure_logging()` rebuilds
    the handlers when the application is created, so pytest's log record no
    longer sees the line. The handler is therefore attached only after
    `create_app()`.
    """
    log_lines: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_lines.append(record.getMessage())

    app = create_app()
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = lambda: Session(engine)
    collector = _Collector()
    log = logging.getLogger("thermoctl.app")
    log.addHandler(collector)
    try:
        with TestClient(app):
            pass
    finally:
        log.removeHandler(collector)
    return [line for line in log_lines if "Einmal-Token" in line]


def test_second_start_creates_no_further_token(empty_installation: Engine) -> None:
    first_rows = _start(empty_installation)
    assert len(first_rows) == 1, "The first start must report exactly one token."

    second_rows = _start(empty_installation)
    assert second_rows == [], (
        "The second start must not create a further token — otherwise there "
        "would be arbitrarily many valid keys to the open setup."
    )

    with Session(empty_installation) as http_session:
        open_tokens = list(
            http_session.scalars(select(SetupToken).where(SetupToken.consumed_at.is_(None)))
        )
    assert len(open_tokens) == 1
