"""The startup check of the database schema.

Reason: a real failed start. After the database file was moved, the service
started against an empty file and failed with a sixty-line traceback whose
core was `no such table: user` -- a message that names neither the missing
migration run nor the command that would fix it.
"""

import logging

import pytest
from sqlalchemy import Engine, create_engine, text

from thermoctl.db.base import Base
from thermoctl.db.schema_state import (
    COMMAND,
    SchemaMismatch,
    check_schema,
    database_state,
)


def _empty_database(tmp_path, name: str = "leer.db") -> Engine:
    return create_engine(f"sqlite:///{tmp_path / name}")


def _stamped_database(tmp_path, revision: str, name: str = "gestempelt.db") -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision}
        )
    return engine


def test_an_empty_database_names_the_command(tmp_path) -> None:
    """The message must be actionable: what is missing, and what to do about it."""
    with pytest.raises(SchemaMismatch) as errors:
        check_schema(_empty_database(tmp_path))
    assert COMMAND in str(errors.value)
    assert "kein Schema" in str(errors.value)


def test_an_outdated_state_names_both_revisions(tmp_path, monkeypatch) -> None:
    """The less pleasant case: the schema exists, but is old. Without this check
    that only surfaces later, at some arbitrary column that does not exist yet."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: "neue_revision")
    with pytest.raises(SchemaMismatch) as errors:
        check_schema(_stamped_database(tmp_path, "alte_revision"))
    notice = str(errors.value)
    assert "alte_revision" in notice
    assert "neue_revision" in notice
    assert COMMAND in notice


def test_a_current_state_lets_the_start_through(tmp_path, monkeypatch) -> None:
    """Counter-check to the two cases above. Without it, they would also be
    satisfied by a function that always aborts."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: "kopf")
    check_schema(_stamped_database(tmp_path, "kopf"))


def test_without_a_determinable_head_there_is_no_false_alarm(tmp_path, monkeypatch) -> None:
    """Anyone running thermoctl without the migrations directory should still be
    able to start -- better one check fewer than one that blocks at the wrong moment."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: None)
    check_schema(_stamped_database(tmp_path, "irgendeine"))


def test_schema_without_a_stamp_is_not_a_reason_to_refuse_starting(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """`Base.metadata.create_all()` leaves behind no Alembic stamp. That is exactly
    how the test suite builds its schema; aborting on that would cripple half the run."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ohne_stempel.db'}")
    Base.metadata.create_all(engine)
    with caplog.at_level(logging.WARNING):
        check_schema(engine)
    assert "nicht ueber Alembic" in caplog.text


def test_database_state_reports_an_empty_file_as_unknown(tmp_path) -> None:
    assert database_state(_empty_database(tmp_path, "blank.db")) is None


def test_the_migration_head_finds_the_real_revision() -> None:
    """Not a stub test: it reads the actual migrations directory, proving the
    comparison has a real basis in operation."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from thermoctl.db.schema_state import _migration_head

    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert _migration_head() == expected[0]
