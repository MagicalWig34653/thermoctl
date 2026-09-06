"""Direct tests of `thermoctl.db.migration_lock`.

`tests/test_migrations.py` proves the end-to-end case that actually matters --
two real ``alembic upgrade head`` subprocesses racing against the same
database -- but pytest-cov cannot see into a subprocess, so that test alone
leaves this module at 0% measured coverage. These tests exercise the module
directly, one connection at a time: acquire, release, a contended acquire
that must wait and then time out, and — the part principle 7 makes
non-negotiable — that a crashed holder's lock lets go by itself instead of
silencing the heating forever.

**Both backends, in every run, regardless of which one the rest of the suite
targets.** `migration_lock` branches on the backend, and CI (`.github/workflows/ci.yml`)
enforces 100% coverage separately per matrix leg (sqlite, mariadb) -- the branch the
ambient `THERMOCTL_TEST_DATABASE_URL` is not using would otherwise sit at 0% in
whichever leg runs it. The `mariadb` service in that workflow is declared at the job
level, so it is up in *both* legs regardless of which one `THERMOCTL_TEST_DATABASE_URL`
selects; `backend_engine` below reaches it directly, by the same fixed local/CI
credential the workflow itself already commits in plain text (not a real secret --
a throwaway database that exists only for the duration of one test run), skipping
only if nothing answers there (a contributor's machine without that service running).
"""

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import pytest
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from thermoctl.db.migration_lock import MigrationLockTimeout, migration_lock

# The same throwaway credential `.github/workflows/ci.yml` declares for its `mariadb`
# service (`MARIADB_ROOT_PASSWORD: pruefen`) and CLAUDE.md documents for manual runs --
# not a production secret, a fixed local/CI-only test database that lives for the
# duration of one CI job or one developer's manual check.
_FALLBACK_MARIADB_URL = "mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_sperre_test"


def _candidate_mariadb_url() -> str:
    ambient = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "")
    if ambient and make_url(ambient).get_backend_name() != "sqlite":
        return ambient
    return _FALLBACK_MARIADB_URL


@pytest.fixture(params=["sqlite", "mariadb"])
def backend_engine(request: pytest.FixtureRequest, tmp_path: object) -> Iterator[Engine]:
    """One engine per backend, independent of the ambient test database.

    See the module docstring for why this does not simply reuse the shared
    ``engine`` fixture from ``conftest.py``.
    """
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path}/sperre.db", future=True)
        try:
            yield engine
        finally:
            engine.dispose()
        return

    url = _candidate_mariadb_url()
    target = make_url(url)
    server_url = URL.create(
        target.drivername,
        username=target.username,
        password=target.password,
        host=target.host,
        port=target.port,
    )
    server_engine = create_engine(server_url, future=True)
    try:
        with server_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{target.database}`"))
            connection.commit()
    except OperationalError:
        server_engine.dispose()
        pytest.skip(f"Keine erreichbare MariaDB unter {server_url} fuer den Sperren-Test")
    server_engine.dispose()

    engine = create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _url_of(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def test_uncontended_lock_is_acquired_and_released_quickly(backend_engine: Engine) -> None:
    """The normal case must not cost anything measurable (see the task's own
    requirement) -- a single instance starting against an unheld lock."""
    url = _url_of(backend_engine)
    with backend_engine.connect() as connection:
        started = time.monotonic()
        with migration_lock(connection, url):
            pass
        assert time.monotonic() - started < 2


def test_release_actually_frees_the_lock_for_a_later_acquire(backend_engine: Engine) -> None:
    """Sequential, not concurrent, by construction -- proves `release` (the
    `finally` in each backend branch) really lets go, not just that a fresh
    lock is acquirable."""
    url = _url_of(backend_engine)
    with backend_engine.connect() as first_connection:
        with migration_lock(first_connection, url):
            pass
    with backend_engine.connect() as second_connection:
        started = time.monotonic()
        with migration_lock(second_connection, url):
            pass
        assert time.monotonic() - started < 2


def test_a_lock_held_elsewhere_blocks_and_then_times_out(
    backend_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second instance's actual behaviour: wait, don't get in, and say so
    within a bounded time rather than hanging -- indistinguishable from a
    stuck migration otherwise."""
    monkeypatch.setenv("THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS", "1")
    url = _url_of(backend_engine)
    holder_ready = threading.Event()
    release_holder = threading.Event()
    holder_errors: list[BaseException] = []

    def hold() -> None:
        try:
            with backend_engine.connect() as connection, migration_lock(connection, url):
                holder_ready.set()
                release_holder.wait(timeout=15)
        except BaseException as exc:  # pragma: no cover - only on an actual failure
            holder_errors.append(exc)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holder_ready.wait(timeout=5), "Halter hat die Sperre nicht rechtzeitig erhalten"

        with backend_engine.connect() as connection:
            started = time.monotonic()
            with pytest.raises(MigrationLockTimeout):
                with migration_lock(connection, url):
                    pass  # pragma: no cover - must never be reached
            elapsed = time.monotonic() - started
        # Bounded by the configured timeout (1s), not instant and not hung.
        # MariaDB's own GET_LOCK(name, 1) can return fractions of a second early
        # (observed: ~0.996s) -- a small tolerance below 1s absorbs that without
        # weakening what the assertion actually guards against (an immediate,
        # un-waited return, or an unbounded hang).
        assert 0.9 <= elapsed < 10, elapsed
    finally:
        release_holder.set()
        holder.join(timeout=15)
    assert not holder_errors, holder_errors


def test_in_memory_sqlite_needs_no_lock_at_all() -> None:
    """An in-memory database belongs to exactly one process by construction --
    the module must recognise that and skip locking, regardless of which
    backend the rest of the suite runs against."""
    engine = create_engine("sqlite:///:memory:", future=True)
    try:
        with engine.connect() as connection:
            with migration_lock(connection, "sqlite:///:memory:"):
                pass
    finally:
        engine.dispose()


def test_invalid_timeout_env_var_falls_back_to_the_default(
    backend_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed override must not crash the migration -- it falls back
    to the built-in default instead."""
    monkeypatch.setenv("THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS", "nicht-eine-zahl")
    url = _url_of(backend_engine)
    with backend_engine.connect() as connection, migration_lock(connection, url):
        pass


def test_non_positive_timeout_env_var_falls_back_to_the_default(
    backend_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS", "0")
    url = _url_of(backend_engine)
    with backend_engine.connect() as connection, migration_lock(connection, url):
        pass


def _spawn_lock_holder(url: str) -> subprocess.Popen[str]:
    """A subprocess that takes the lock, announces it, and then sleeps --
    long enough for the test to kill it with no chance to run any cleanup.
    """
    script = (
        "import sys, time\n"
        "from sqlalchemy import create_engine\n"
        "from thermoctl.db.migration_lock import migration_lock\n"
        f"url = {url!r}\n"
        "engine = create_engine(url, future=True)\n"
        "with engine.connect() as connection:\n"
        "    with migration_lock(connection, url):\n"
        "        print('locked', flush=True)\n"
        "        time.sleep(60)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_a_killed_holder_releases_the_lock_by_itself(backend_engine: Engine) -> None:
    """Principle 7: a lock that survives its holder's death would silence the
    heating indefinitely. A real subprocess takes the lock and is then
    SIGKILLed -- no `finally`, no signal handler, no chance to clean up -- and
    a fresh acquire right afterwards must still succeed well inside the
    timeout, because it is the operating system that tears down the held
    resource (the closed TCP connection behind MariaDB's `GET_LOCK`, the
    closed file descriptor behind SQLite's `flock`), not this module's own
    cleanup code.
    """
    url = _url_of(backend_engine)
    process = _spawn_lock_holder(url)
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        assert line.strip() == "locked", (line, process.stderr)

        process.kill()
        process.wait(timeout=10)

        with backend_engine.connect() as connection:
            started = time.monotonic()
            with migration_lock(connection, url):
                pass
            elapsed = time.monotonic() - started
        # Nowhere near the default 60s timeout -- the crash frees it almost
        # immediately, it is not merely eventually reclaimed at the deadline.
        assert elapsed < 15, elapsed
    finally:
        if process.poll() is None:  # pragma: no cover - cleanup only if the above failed
            process.kill()
            process.wait(timeout=10)
