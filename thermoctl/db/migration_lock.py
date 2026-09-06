"""One migration run at a time, against the same database.

`docker/entrypoint.sh` runs ``alembic upgrade head`` unconditionally on every
container start. Under an orchestrator, two replicas easily run it at the same
moment: Swarm's `docker stack deploy` ignores `depends_on`, and a Kubernetes
rolling update briefly runs the old and the new replica side by side. MariaDB
commits DDL implicitly, so two concurrent ``upgrade head`` runs are not saved
by wrapping either one in a transaction -- confirmed by actually racing two
``alembic upgrade head`` subprocesses against the same fresh MariaDB database
while building this module: the second reliably fails with
``Table 'alembic_version' already exists`` at the very first statement in the
best case, and nothing rules out a worse interleaving further into a run with
multiple DDL statements (a `batch_alter_table` rebuild, for instance).

**This lives in `migrations/env.py`, not in the entrypoint.** The entrypoint
only covers a container start; `env.py` fires for every Alembic invocation,
including one run by hand from a shell inside the container or during
development -- exactly the case the orchestration guides' pre-flight job
(`docker/swarm.migrate.compose.beispiel.yml`, `k8s/thermoctl-migrate-job.beispiel.yaml`)
cannot help with, since nothing forces an operator to use it.

**MariaDB and SQLite get genuinely different mechanisms, not a shared one
forced onto both.** This is not the domain schema principle 3 guards against
(no vendor-specific columns or functions in what is persisted) -- this lock
holds no data and outlives no process. `services/cluster.py` uses a portable
row-locking `UPDATE` for the active/standby claim because that claim **is**
domain state, read and written throughout a running process's whole
lifetime, and both backends must show identical behaviour to application
code that queries it. A migration lock only needs to be held by the one-shot
`alembic` invocation itself, and each backend already has a purpose-built
primitive for exactly that:

- **MariaDB**: `GET_LOCK()`, a named lock scoped to the connection that took
  it. It takes its own timeout in seconds -- no polling loop needed -- and,
  crucially, releases itself the moment that connection goes away, whether
  the process exited cleanly or was killed mid-migration. A row in a table
  cannot offer that: a crashed holder would leave it locked, and the whole
  point of this module is to keep a stuck lock from silencing the heating
  forever (principle 7).
- **SQLite**: no server process to ask, so an OS file lock (`flock`) on a
  small sentinel file next to the database file plays the same role -- held
  by one open file descriptor, released by the kernel itself when that
  descriptor closes, including on a crash. An in-memory database belongs to
  exactly one process by construction and needs no lock at all.

Both give the same two properties this module actually needs: a bounded wait,
and automatic release when the holder dies without a chance to clean up.
Neither leaks into anything this project persists.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import make_url as _make_url

log = logging.getLogger(__name__)

# MariaDB's GET_LOCK() name -- arbitrary, but must be the same for every process
# that should serialise against each other. Well under MariaDB's 64-character
# limit for lock names.
_LOCK_NAME = "thermoctl_migrations"

_DEFAULT_TIMEOUT_S = 60


class MigrationLockTimeout(RuntimeError):
    """The lock was not acquired within the configured deadline.

    Raised instead of waiting forever: a container that aborts gets restarted
    by the orchestrator and tries again; one that hangs looks indistinguishable
    from a stuck migration and nobody is told why.
    """


def _timeout_seconds() -> int:
    """`THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS`, falling back to 60.

    Deliberately read straight from the environment rather than through
    `thermoctl.config.Settings`: this only matters to Alembic invocations
    (`migrations/env.py`), never to the running application, and adding it to
    `Settings` would suggest otherwise.
    """
    raw = os.environ.get("THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS=%r ist keine Zahl, "
            "verwende die Vorgabe von %ss",
            raw,
            _DEFAULT_TIMEOUT_S,
        )
        return _DEFAULT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_TIMEOUT_S


@contextlib.contextmanager
def migration_lock(connection: Connection, url: str) -> Iterator[None]:
    """Holds the one migration lock appropriate for ``url``'s backend.

    ``connection`` is the same connection Alembic goes on to run the
    migrations over -- required for the MariaDB branch, where the lock is
    scoped to the connection that took it. Raises `MigrationLockTimeout` if
    the lock is held elsewhere and does not free up within the configured
    timeout (default 60s).
    """
    backend = _make_url(url).get_backend_name()
    if backend == "sqlite":
        with _sqlite_file_lock(url, _timeout_seconds()):
            yield
    else:
        with _mariadb_named_lock(connection, _timeout_seconds()):
            yield


@contextlib.contextmanager
def _mariadb_named_lock(connection: Connection, timeout: int) -> Iterator[None]:
    result = connection.execute(
        text("SELECT GET_LOCK(:name, :timeout)"), {"name": _LOCK_NAME, "timeout": timeout}
    ).scalar()
    # Closes out the transaction GET_LOCK's own SELECT auto-began on this
    # connection -- Alembic starts its own right after, via
    # `context.begin_transaction()`. GET_LOCK itself is session-scoped, not
    # transaction-scoped, so committing here does not release it.
    connection.commit()
    if result != 1:
        raise MigrationLockTimeout(
            f"Konnte die Migrationssperre '{_LOCK_NAME}' nicht innerhalb von "
            f"{timeout}s erhalten (GET_LOCK lieferte {result!r}) -- vermutlich "
            "migriert eine andere Instanz gerade dieselbe Datenbank. Abbruch, "
            "damit ein Orchestrierer neu startet, statt endlos zu warten."
        )
    log.info("Migrationssperre erhalten (MariaDB GET_LOCK, Frist %ss)", timeout)
    try:
        yield
    finally:
        connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK_NAME})
        connection.commit()


@contextlib.contextmanager
def _sqlite_file_lock(url: str, timeout: int) -> Iterator[None]:
    import fcntl

    database = _make_url(url).database
    if not database or database == ":memory:":
        # Belongs to exactly one process by construction -- nothing to
        # serialise against, and no file to lock either.
        yield
        return

    lock_path = Path(f"{database}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MigrationLockTimeout(
                        f"Konnte die Migrationssperre fuer {database} nicht "
                        f"innerhalb von {timeout}s erhalten -- vermutlich migriert "
                        "ein anderer Prozess gerade dieselbe Datenbank. Abbruch, "
                        "damit ein Orchestrierer neu startet, statt endlos zu "
                        "warten."
                    ) from None
                time.sleep(0.1)
        log.info(
            "Migrationssperre erhalten (SQLite-Dateisperre %s, Frist %ss)",
            lock_path,
            timeout,
        )
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
