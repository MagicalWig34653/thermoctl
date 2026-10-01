import asyncio
import os
import subprocess
import sys
import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

import thermoctl.app as app_module
from thermoctl.config import get_settings
from thermoctl.db.models.lookup import PERMISSIONS
from thermoctl.db.models.operations import ClusterClaim, Setting
from thermoctl.db.models.zone import SetpointMode
from thermoctl.services import cluster
from thermoctl.services.shadow_run import cycle as shadow_cycle


def _alembic(url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Calls Alembic as a subprocess so real migration runs are exercised.

    Invoked via ``sys.executable -m alembic`` rather than the ``alembic``
    script: only that way does the project directory end up on the
    subprocess's module path. With the script invocation, the module path
    starts at ``.venv/bin``, and the ``thermoctl`` package is then only
    discoverable through the editable install's ``.pth`` file -- which on
    macOS can carry the ``hidden`` flag and then gets skipped at startup.
    The detour through ``-m`` makes the test independent of how the venv
    was set up.

    Runs against ``url`` instead of ``TEST_DATABASE_URL``: the migration
    tests need their own database, separate from the ``engine`` fixture --
    otherwise Alembic would create tables that ``Base.metadata.create_all()``
    has already created.
    """
    environment = {
        **os.environ,
        "THERMOCTL_DATABASE_URL": url,
        "THERMOCTL_SECRET_KEY": "t" * 32,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.migration
def test_migration_forward_and_backward(migrations_database_url: str) -> None:
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic(migrations_database_url, "downgrade", "base")
    assert down.returncode == 0, down.stderr
    up_again = _alembic(migrations_database_url, "upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr


@pytest.mark.migration
def test_models_and_migrations_are_in_sync(migrations_database_url: str) -> None:
    """`alembic check` reports when a model was changed without a migration."""
    prep = _alembic(migrations_database_url, "upgrade", "head")
    assert prep.returncode == 0, prep.stderr
    result = _alembic(migrations_database_url, "check")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.anyio
@pytest.mark.migration
async def test_migrated_cluster_claim_does_not_freeze_the_startup_bolt_closed(
    migrations_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises startup and the first shadow pass on a genuinely migrated schema.

    The migration seed is essential: an empty ``cluster_claim`` table deliberately
    fails open, which made every ``Base.metadata.create_all()`` test miss the
    production-only startup deadlock this test guards against.
    """
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    upgrade = _alembic(migrations_database_url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    db_engine = create_engine(migrations_database_url)
    app = None
    try:
        with Session(db_engine) as session:
            frost_mode = SetpointMode(
                code="frostschutz", name="Frostschutz", sort_order=0, is_builtin=True
            )
            session.add(frost_mode)
            session.flush()
            session.add(
                Setting(
                    id=1,
                    control_armed=True,
                    frost_protection_mode_id=frost_mode.id,
                )
            )
            session.commit()

        monkeypatch.setenv("THERMOCTL_DATABASE_URL", migrations_database_url)
        monkeypatch.setenv("THERMOCTL_SECRET_KEY", "t" * 32)
        get_settings.cache_clear()
        monkeypatch.setattr(app_module, "_shadow_loop_needed", lambda settings: True)
        monkeypatch.setattr(app_module, "_start_meross_refresh", lambda app, now: None)

        first_pass_finished = asyncio.Event()
        hold_second_pass = asyncio.Event()
        claim_attempts = 0
        interval_reads = 0
        original_try_become_leader = cluster.try_become_leader

        def observed_try_become_leader(
            session: Session, *, holder: str, timeout_seconds: int
        ) -> bool:
            nonlocal claim_attempts
            result = original_try_become_leader(
                session, holder=holder, timeout_seconds=timeout_seconds
            )
            claim_attempts += 1
            return result

        def observed_cycle(*args: object, **kwargs: object) -> object:
            result = shadow_cycle(*args, **kwargs)  # type: ignore[arg-type]
            first_pass_finished.set()
            return result

        async def immediate_first_interval(session_factory: object) -> int:
            nonlocal interval_reads
            interval_reads += 1
            if interval_reads > 1:
                await hold_second_pass.wait()
            return 0

        monkeypatch.setattr(cluster, "try_become_leader", observed_try_become_leader)
        monkeypatch.setattr(app_module, "cycle", observed_cycle)
        monkeypatch.setattr(app_module, "_shadow_interval_s", immediate_first_interval)

        app = app_module.create_app()
        async with app_module._lifespan(app):
            await asyncio.wait_for(first_pass_finished.wait(), timeout=2)
            assert app.state.sending_allowed is True
            assert claim_attempts == 2
            with Session(app.state.engine) as session:
                claim = session.get(ClusterClaim, 1)
                assert claim is not None
                assert claim.holder_id == cluster.instance_id()
    finally:
        get_settings.cache_clear()
        if app is not None:
            app.state.engine.dispose()
        db_engine.dispose()
        _alembic(migrations_database_url, "downgrade", "base")
        _alembic(migrations_database_url, "upgrade", "head")


@pytest.mark.migration
def test_pi_migration_keeps_existing_zones_off_and_state_neutral(
    migrations_database_url: str,
) -> None:
    before = _alembic(migrations_database_url, "downgrade", "f6a9d4c12b70")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            operating_mode_id = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            sensor_status_id = connection.execute(
                text("SELECT id FROM sensor_status WHERE code = 'ok'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone "
                    "(name, display_name, operating_mode_id, sort_order, created_at, updated_at) "
                    "VALUES ('pi-migration', 'PI-Migration', :mode_id, 0, "
                    "'2026-09-02 10:00:00', '2026-09-02 10:00:00')"
                ),
                {"mode_id": operating_mode_id},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'pi-migration'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone_state "
                    "(zone_id, sensor_status_id, updated_at) "
                    "VALUES (:zone_id, :status_id, '2026-09-02 10:00:00')"
                ),
                {"zone_id": zone_id, "status_id": sensor_status_id},
            )
            connection.execute(
                text(
                    "INSERT INTO shadow_decision "
                    "(decided_at, zone_id, setpoint_reason, would_heat, outcome_code, reason) "
                    "VALUES ('2026-09-02 10:00:00', :zone_id, 'Zeitplan', false, "
                    "'aus', 'Sollwert erreicht')"
                ),
                {"zone_id": zone_id},
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            configuration = connection.execute(
                text(
                    "SELECT pi_enabled, pi_gain_per_k, pi_integral_time_minutes, "
                    "pi_min_on_seconds, pi_min_off_seconds FROM zone "
                    "WHERE name = 'pi-migration'"
                )
            ).one()
            state = connection.execute(
                text(
                    "SELECT pi_integral, pi_last_evaluated_at, pi_setpoint_context_key, "
                    "pi_last_control_armed, pi_window_started_at, pi_window_duty, "
                    "pi_time_balance_seconds, pi_last_switch_at, pi_last_switch_heating, "
                    "pi_awaiting_boundary_until, pi_last_reset_reason FROM zone_state "
                    "WHERE zone_id = :zone_id"
                ),
                {"zone_id": zone_id},
            ).one()
            snapshot = connection.execute(
                text(
                    "SELECT requested_controller, effective_controller, "
                    "controller_fallback_reason, pi_error_k, "
                    "pi_integral_before, pi_integral_after, pi_raw_duty, pi_frozen_duty "
                    "FROM shadow_decision WHERE zone_id = :zone_id"
                ),
                {"zone_id": zone_id},
            ).one()

        assert tuple(configuration) == (False, Decimal("0.25"), 180, 60, 60)
        assert tuple(state) == (
            Decimal("0"),
            None,
            None,
            None,
            None,
            None,
            Decimal("0"),
            None,
            None,
            None,
            None,
        )
        assert tuple(snapshot) == (
            "hysteresis",
            "hysteresis",
            None,
            None,
            None,
            None,
            None,
            None,
        )

        down = _alembic(migrations_database_url, "downgrade", "f6a9d4c12b70")
        assert down.returncode == 0, down.stderr
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_assumed_relay_lifetime_defaults_to_500000_for_existing_installations(
    migrations_database_url: str,
) -> None:
    """The project owner's explicit request -- 500,000, not the 100,000 the retired
    constant used to carry -- reaches an installation that existed before this
    setting did, without anyone touching it."""
    before = _alembic(migrations_database_url, "downgrade", "d2f4a7c91e63")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO setpoint_mode "
                    "(code, name, sort_order, is_builtin) "
                    "VALUES ('relais-migration-frost', 'Migration Frost', 0, false)"
                )
            )
            mode_id = connection.execute(
                text(
                    "SELECT id FROM setpoint_mode WHERE code = 'relais-migration-frost'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO setting "
                    "(id, timezone, polling_interval_seconds, default_hysteresis_k, "
                    "default_min_on_seconds, default_min_off_seconds, "
                    "default_sensor_timeout_seconds, default_window_resume_delay_seconds, "
                    "frost_protection_mode_id, session_lifetime_seconds, updated_at) "
                    "VALUES (1, 'UTC', 30, 0.30, 300, 300, 1800, 120, :mode_id, "
                    "1209600, '2026-09-03 08:00:00')"
                ),
                {"mode_id": mode_id},
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            value = connection.execute(
                text("SELECT assumed_relay_lifetime_operations FROM setting WHERE id = 1")
            ).scalar_one()
        assert value == 500_000

        down = _alembic(migrations_database_url, "downgrade", "d2f4a7c91e63")
        assert down.returncode == 0, down.stderr
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
    finally:
        db_engine.dispose()


def test_foreign_keys_are_enforced_under_sqlite(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("only meaningful for SQLite")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


@pytest.mark.migration
def test_shadow_schema_reference_data_and_settings(
    migrations_database_url: str,
) -> None:
    base = _alembic(migrations_database_url, "downgrade", "base")
    assert base.returncode == 0, base.stderr
    before = _alembic(migrations_database_url, "upgrade", "4d43756aecd3")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO setpoint_mode "
                    "(code, name, sort_order, is_builtin) "
                    "VALUES ('migration-frost', 'Migration Frost', 0, false)"
                )
            )
            mode_id = connection.execute(
                text("SELECT id FROM setpoint_mode WHERE code = 'migration-frost'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO setting "
                    "(id, timezone, polling_interval_seconds, default_hysteresis_k, "
                    "default_min_on_seconds, default_min_off_seconds, "
                    "default_sensor_timeout_seconds, default_window_resume_delay_seconds, "
                    "frost_protection_mode_id, session_lifetime_seconds, updated_at) "
                    "VALUES (1, 'UTC', 30, 0.30, 300, 300, 1800, 120, :mode_id, "
                    "1209600, '2026-08-29 08:00:00')"
                ),
                {"mode_id": mode_id},
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            capabilities = set(
                connection.execute(text("SELECT code FROM device_capability")).scalars()
            )
            status = set(connection.execute(text("SELECT code FROM sensor_status")).scalars())
            setting = connection.execute(
                text(
                    "SELECT timezone, control_armed, measurement_retention_days, "
                    "shadow_decision_retention_days, shadow_interval_seconds "
                    "FROM setting WHERE id = 1"
                )
            ).one()
        assert {
            "humidity", "illuminance", "occupancy", "link_quality", "power", "energy",
            "valve_position", "setpoint", "availability",
        } <= capabilities
        assert status == {"ok", "veraltet", "keine_quelle"}
        assert tuple(setting) == ("UTC", False, 30, 365, 60)

        down = _alembic(migrations_database_url, "downgrade", "4d43756aecd3")
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            remaining = set(
                connection.execute(text("SELECT code FROM device_capability")).scalars()
            )
        assert remaining == {
            "temperature", "switch", "setpoint_display", "contact", "battery",
        }
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_umlauts_are_backfilled_into_existing_labels(
    migrations_database_url: str,
) -> None:
    """Four labels sat transliterated in the database.

    A freshly set-up installation gets the correct spelling already at seed
    time -- the seed revision reads the constants from the code. Existing
    installations, however, carry the old spelling in their rows, and that is
    exactly what this test creates before letting the revision run over it.
    Without this detour, it would only check that the constant is correct,
    never that the revision does anything.
    """
    base = _alembic(migrations_database_url, "downgrade", "base")
    assert base.returncode == 0, base.stderr
    before = _alembic(migrations_database_url, "upgrade", "c8e21a5f4d70")
    assert before.returncode == 0, before.stderr

    old_spelling = [
        ("device_capability", "link_quality", "Verbindungsqualität", "Verbindungsqualität"),
        ("device_capability", "illuminance", "Beleuchtungsstärke", "Beleuchtungsstärke"),
        ("device_role", "controller", "Bediengerät", "Bediengerät"),
        ("actor_source", "web", "Weboberfläche", "Weboberfläche"),
    ]
    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            for table, code, old, _ in old_spelling:
                connection.execute(
                    text(f"UPDATE {table} SET label = :old WHERE code = :code"),  # noqa: S608
                    {"old": old, "code": code},
                )
            # A manually assigned label that the revision must leave alone.
            connection.execute(
                text("UPDATE device_role SET label = 'Mein Aktor' WHERE code = 'actuator'")
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr

        def label(table: str, code: str) -> str | None:
            with db_engine.connect() as connection:
                return connection.execute(
                    text(f"SELECT label FROM {table} WHERE code = :code"),  # noqa: S608
                    {"code": code},
                ).scalar()

        for table, code, _, new in old_spelling:
            assert label(table, code) == new, code
        assert label("device_role", "actuator") == "Mein Aktor"

        down = _alembic(migrations_database_url, "downgrade", "c8e21a5f4d70")
        assert down.returncode == 0, down.stderr
        for table, code, old, _ in old_spelling:
            assert label(table, code) == old, code
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_the_last_german_column_names_are_renamed_with_their_data(
    migrations_database_url: str,
) -> None:
    """A column rename must not lose what is in it.

    `batch_alter_table` rebuilds the table under SQLite. If the copy were to go wrong,
    the schema would still look right afterwards and the rows would be gone -- which is
    exactly the kind of fault that only shows up on someone's real installation.
    """
    base = _alembic(migrations_database_url, "downgrade", "base")
    assert base.returncode == 0, base.stderr
    before = _alembic(migrations_database_url, "upgrade", "e4b8a21c7f10")
    assert before.returncode == 0, before.stderr

    werk = create_engine(migrations_database_url)
    try:
        with werk.begin() as db_connection:
            db_connection.execute(
                text(
                    "INSERT INTO user (username, display_name, password_hash, is_active, "
                    "created_at) VALUES ('umzug', 'Umzug', 'x', true, '2026-08-30 08:00:00')"
                )
            )
            user_id = db_connection.execute(
                text("SELECT id FROM user WHERE username = 'umzug'")
            ).scalar_one()
            db_connection.execute(
                text(
                    "INSERT INTO user_passkey (user_id, credential_id, public_key, "
                    "sign_count, bezeichnung, created_at) VALUES (:u, 'cred-1', 'pub', 0, "
                    "'Mein Telefon', '2026-08-30 08:00:00')"
                ),
                {"u": user_id},
            )
            db_connection.execute(
                text(
                    "INSERT INTO passkey_challenge (challenge, zeremonie, created_at) "
                    "VALUES ('chal-1', 'login', '2026-08-30 08:00:00')"
                )
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with werk.connect() as db_connection:
            assert db_connection.execute(
                text("SELECT label FROM user_passkey WHERE credential_id = 'cred-1'")
            ).scalar_one() == "Mein Telefon"
            assert db_connection.execute(
                text("SELECT ceremony FROM passkey_challenge WHERE challenge = 'chal-1'")
            ).scalar_one() == "login"

        down = _alembic(migrations_database_url, "downgrade", "e4b8a21c7f10")
        assert down.returncode == 0, down.stderr
        with werk.connect() as db_connection:
            # The counter-check to the rename: backwards the old name is there again,
            # and the value with it.
            assert db_connection.execute(
                text("SELECT bezeichnung FROM user_passkey WHERE credential_id = 'cred-1'")
            ).scalar_one() == "Mein Telefon"
        again = _alembic(migrations_database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
    finally:
        werk.dispose()


@pytest.mark.migration
def test_the_thermostat_downgrade_survives_a_device_that_used_the_capability(
    migrations_database_url: str,
) -> None:
    """Downgrading after the feature was actually used, not on an empty schema.

    `test_migration_forward_and_backward` walks the whole history up and down, but
    over a schema in which nobody ever stored anything. That is the one case in which
    deleting a row from `device_capability` is harmless. In every other case
    `device_capability_link.capability_id` and `measurement.capability_id` point at
    it -- neither with `ON DELETE CASCADE` -- and the plain DELETE fails on the
    foreign key. Which means it would fail exactly when someone needs the downgrade:
    after a thermostat was recognised.

    Found by a cross-review, not by the suite; the same thing had already been
    noticed once in `d1a7c3e59b40`, whose downgrade clears its references first.
    """
    base = _alembic(migrations_database_url, "downgrade", "base")
    assert base.returncode == 0, base.stderr
    up = _alembic(migrations_database_url, "upgrade", "b6e9f14d2a83")
    assert up.returncode == 0, up.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            integration_id = connection.execute(
                text("SELECT id FROM integration LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO device (integration_id, external_id, display_name,"
                    " is_enabled, is_group)"
                    " VALUES (:integration_id, 'trv-1', 'Thermostatventil', 1, 0)"
                ),
                {"integration_id": integration_id},
            )
            device_id = connection.execute(
                text("SELECT id FROM device WHERE external_id = 'trv-1'")
            ).scalar_one()
            for code in ("thermostat", "running_state"):
                capability_id = connection.execute(
                    text("SELECT id FROM device_capability WHERE code = :code"),
                    {"code": code},
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO device_capability_link (device_id, capability_id)"
                        " VALUES (:device_id, :capability_id)"
                    ),
                    {"device_id": device_id, "capability_id": capability_id},
                )
            connection.execute(
                text(
                    "INSERT INTO measurement (device_id, capability_id, value_text,"
                    " measured_at, received_at)"
                    " VALUES (:device_id, :capability_id, 'heat',"
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"device_id": device_id, "capability_id": capability_id},
            )

        down = _alembic(migrations_database_url, "downgrade", "-1")
        assert down.returncode == 0, down.stderr

        with db_engine.connect() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM device_capability WHERE code = 'thermostat'")
            ).scalar_one() == 0
            # The device itself stays -- only what pointed at the removed capability goes.
            assert connection.execute(
                text("SELECT count(*) FROM device WHERE id = :id"), {"id": device_id}
            ).scalar_one() == 1
            # Checked as *orphans*, not by expecting the downgrade to blow up. Under
            # SQLite the alembic subprocess does not enforce foreign keys, so a plain
            # DELETE on the lookup table succeeds there and leaves rows pointing at an
            # id that no longer exists -- silently, and only until MariaDB refuses the
            # same downgrade outright. Asking for orphans catches both.
            for table in ("device_capability_link", "measurement"):
                orphans = connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} t WHERE NOT EXISTS "  # noqa: S608
                        "(SELECT 1 FROM device_capability c WHERE c.id = t.capability_id)"
                    )
                ).scalar_one()
                assert orphans == 0, f"{table} zeigt auf eine gelöschte Fähigkeit"
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_two_concurrent_upgrade_head_runs_do_not_collide(
    migrations_database_url: str,
) -> None:
    """Two ``alembic upgrade head`` subprocesses, released at the same instant
    against the same, fresh MariaDB -- not two calls made one after another,
    which would pass even without any lock at all. Two threads with a barrier
    (same technique as ``tests/test_cluster.py``'s racing-claim test), each
    driving its own real ``alembic`` subprocess (same technique as every other
    test in this file).

    Without the lock in `migrations/env.py`, this reliably goes wrong: built
    against a real MariaDB while writing that lock, the second of two
    concurrently started ``upgrade head`` runs failed with
    ``Table 'alembic_version' already exists`` at the very first statement of
    the run -- the *best* case, since MariaDB commits DDL implicitly and
    nothing rules out a worse interleaving further into a run with several DDL
    statements. With the lock, one of the two must simply wait for the other,
    find nothing left to do, and return successfully -- both processes exit
    0, and the schema ends up at head exactly once.
    """
    if make_url(migrations_database_url).get_backend_name() == "sqlite":
        pytest.skip("Der Ernstfall ist MariaDB, siehe Auftrag -- SQLite ist Einzelbetrieb")

    base = _alembic(migrations_database_url, "downgrade", "base")
    assert base.returncode == 0, base.stderr

    barrier = threading.Barrier(2)
    results: dict[int, subprocess.CompletedProcess[str]] = {}
    errors: list[BaseException] = []

    def run(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = _alembic(migrations_database_url, "upgrade", "head")
        except BaseException as exc:  # pragma: no cover - only on an actual failure
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        # Generous: this run can legitimately spend most of the migration
        # lock's own timeout waiting its turn, on top of actually migrating.
        thread.join(timeout=150)

    assert not errors, errors
    assert set(results) == {0, 1}
    for index, result in results.items():
        assert result.returncode == 0, f"Lauf {index}: {result.stderr}"

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.connect() as connection:
            versions = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
    finally:
        db_engine.dispose()
    # Migrated exactly once, by whichever of the two got there -- not twice,
    # and not left half-done by an interleaving that snuck past the lock.
    assert len(versions) == 1

    check = _alembic(migrations_database_url, "check")
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.migration
def test_upgrade_head_against_an_already_current_database_is_a_quick_no_op(
    migrations_database_url: str,
) -> None:
    """The lock must cost nothing in the common case: a single instance
    starting against a database that is already at head takes the lock
    uncontended and finds nothing to migrate -- fast, and without emitting a
    single "Running upgrade" line.
    """
    first = _alembic(migrations_database_url, "upgrade", "head")
    assert first.returncode == 0, first.stderr

    started = time.monotonic()
    second = _alembic(migrations_database_url, "upgrade", "head")
    elapsed = time.monotonic() - started

    assert second.returncode == 0, second.stderr
    assert "Running upgrade" not in second.stdout + second.stderr
    # Nowhere near the migration lock's own default timeout (60s) -- a
    # no-op run against an uncontended lock must not even come close.
    assert elapsed < 20, elapsed


@pytest.mark.migration
def test_existing_groups_keep_the_admin_interface_on_upgrade(
    migrations_database_url: str,
) -> None:
    """Der eine Punkt, an dem diese Migration schiefgehen könnte.

    Eine bestehende Installation hat ihre Gruppen nie als Mietergruppe
    gekennzeichnet -- es gab das Feld nicht. Bekämen sie beim Upgrade `tenant`,
    verlöre die laufende Anlage mit einem Aufruf von `alembic upgrade head` ihre
    gesamte Verwaltung, ohne dass jemand etwas geändert hätte. Deshalb wird hier
    eine Gruppe *vor* der Migration angelegt und danach nachgesehen.

    Und zwar eine, deren Name nach Mieter klingt: die Migration darf ausdrücklich
    nicht anhand des Namens klassifizieren -- ein Name ist kein Modell.
    """
    before = _alembic(migrations_database_url, "downgrade", "43aa18ba1c12")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO access_group (name, description, is_builtin) "
                    "VALUES ('Mieter', 'klingt nach Wohnung, ist aber Verwaltung', false)"
                )
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            profile = connection.execute(
                text("SELECT ui_profile FROM access_group WHERE name = 'Mieter'")
            ).scalar_one()
        assert profile == "admin"

        down = _alembic(migrations_database_url, "downgrade", "43aa18ba1c12")
        assert down.returncode == 0, down.stderr
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
        with db_engine.begin() as connection:
            connection.execute(text("DELETE FROM access_group WHERE name = 'Mieter'"))
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_absence_migration_keeps_existing_override_unassigned(
    migrations_database_url: str,
) -> None:
    """Eine Übersteuerung, die es vor der Abwesenheit schon gab, gehört zu keiner.

    Die Zuordnung `zone_override.absence_id` ist nullbar, damit genau das gilt.
    Bekämen bestehende Zeilen beim Upgrade eine Klammer, ließe sich eine einzelne
    Übersteuerung anschließend über "Abwesenheit beenden" mitbeenden, ohne dass
    jemand sie je zu einer Abwesenheit erklärt hätte.
    """
    before = _alembic(migrations_database_url, "downgrade", "c4d18b7e2a95")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            mode_id = connection.execute(
                text("SELECT id FROM operating_mode ORDER BY id LIMIT 1")
            ).scalar_one()
            source_id = connection.execute(
                text("SELECT id FROM actor_source WHERE code = 'web'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone "
                    "(name, display_name, operating_mode_id, sort_order, created_at, updated_at) "
                    "VALUES ('absence-migration', 'Abwesenheit Migration', :mode_id, 0, "
                    "'2026-09-07 08:00:00', '2026-09-07 08:00:00')"
                ),
                {"mode_id": mode_id},
            )
            zone_id = connection.execute(
                text("SELECT id FROM zone WHERE name = 'absence-migration'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone_override "
                    "(zone_id, temperature_c, starts_at, ends_at, created_at, source_id) "
                    "VALUES (:zone_id, 17.0, '2026-09-07 08:00:00', "
                    "'2026-09-08 08:00:00', '2026-09-07 08:00:00', :source_id)"
                ),
                {"zone_id": zone_id, "source_id": source_id},
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            absence_id = connection.execute(
                text("SELECT absence_id FROM zone_override WHERE zone_id = :zone_id"),
                {"zone_id": zone_id},
            ).scalar_one()
        assert absence_id is None

        down = _alembic(migrations_database_url, "downgrade", "c4d18b7e2a95")
        assert down.returncode == 0, down.stderr
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_the_report_permission_reaches_no_group_by_itself(
    migrations_database_url: str,
) -> None:
    """Ein Recht, das eine Meldung nach außen auslöst, darf niemand geschenkt kriegen.

    `control.arm` ging seinerzeit ausdrücklich an alle Gruppen mit `setting.manage`
    -- dort war es die Fortsetzung eines Rechts, das dieselben Leute schon hatten.
    Hier ist es das Gegenteil: eine bestehende Gruppe mit `zone.read` bekäme über
    Nacht die Möglichkeit, den Webhook des Betreibers zu bedienen. Der Test hält
    fest, dass die Migration genau das nicht tut.
    """
    before = _alembic(migrations_database_url, "downgrade", "c724de89a13f")
    assert before.returncode == 0, before.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO access_group (name, description, is_builtin) "
                    "VALUES ('Bestandsgruppe', 'darf lesen', false)"
                )
            )
            group_id = connection.execute(
                text("SELECT id FROM access_group WHERE name = 'Bestandsgruppe'")
            ).scalar_one()
            read_id = connection.execute(
                text("SELECT id FROM permission WHERE code = 'zone.read'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO group_permission (access_group_id, permission_id, zone_id) "
                    "VALUES (:group_id, :permission_id, NULL)"
                ),
                {"group_id": group_id, "permission_id": read_id},
            )

        up = _alembic(migrations_database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT is_zone_scoped FROM permission WHERE code = 'report.create'")
            ).scalar_one()
            assigned = connection.execute(
                text(
                    "SELECT count(*) FROM group_permission gp "
                    "JOIN permission p ON p.id = gp.permission_id "
                    "WHERE p.code = 'report.create'"
                )
            ).scalar_one()
        assert bool(exists) is True
        assert assigned == 0

        down = _alembic(migrations_database_url, "downgrade", "c724de89a13f")
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            gone = connection.execute(
                text("SELECT count(*) FROM permission WHERE code = 'report.create'")
            ).scalar_one()
        assert gone == 0
        up_again = _alembic(migrations_database_url, "upgrade", "head")
        assert up_again.returncode == 0, up_again.stderr
        with db_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM group_permission WHERE access_group_id = :group_id"),
                {"group_id": group_id},
            )
            connection.execute(
                text("DELETE FROM access_group WHERE name = 'Bestandsgruppe'")
            )
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_every_permission_exists_after_a_full_upgrade(
    migrations_database_url: str,
) -> None:
    """Nach `alembic upgrade head` steht **jedes** Recht aus `PERMISSIONS` in der
    Tabelle -- sonst gibt es Seiten, die niemand öffnen kann.

    Der Test hat einen konkreten Anlass. Die Seed-Revision
    `3685e30419a4_nachschlagetabellen` spielte den Stand von damals über einen
    *positionellen* Schnitt in die lebende Liste ein (`PERMISSIONS[:15]`). Beim
    Einsortieren eines neuen Rechts in die Mitte rutschte `audit.read` aus dem
    Schnitt: eine frisch eingerichtete Anlage hatte danach kein Konto mehr, das
    Protokoll, Schaltprotokoll oder Relaisverschleiß öffnen konnte. Nichts schlug
    dabei fehl -- weder die Migration noch die Einrichtung noch die Testsuite. Nur
    drei Seiten antworteten jedem mit 403.

    Gefunden hat es ein Browsertest, weil dort ein echter Server frisch eingerichtet
    wird. Dieser Test hier findet dasselbe eine Ebene tiefer und ohne Browser.
    """
    up = _alembic(migrations_database_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.connect() as connection:
            vorhanden = {
                row[0]
                for row in connection.execute(text("SELECT code FROM permission"))
            }
    finally:
        db_engine.dispose()

    fehlend = {code for code, _beschreibung, _zonenbezogen in PERMISSIONS} - vorhanden
    assert not fehlend, (
        "Diese Rechte stehen in PERMISSIONS, legt aber keine Migration an: "
        f"{sorted(fehlend)}. Ein neues Recht gehört ans **Ende** von PERMISSIONS "
        "und braucht seine eigene Migration."
    )


@pytest.mark.migration
@pytest.mark.parametrize("with_existing_data", [False, True])
def test_sensor_failure_upgrade_preserves_installation(
    migrations_database_url: str,
    with_existing_data: bool,
) -> None:
    """Fresh install and existing installations receive the same editable curve.

    Compare every old column, not just the zone names, also after downgrade/re-upgrade.
    """
    from datetime import datetime

    import sqlalchemy as sa
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    # Trip-wire: this pins the *current* head so a later migration stacked on
    # top of the sensor-failure schema forces a deliberate look at this test
    # -- in particular at the "downgrade -1 removes the whole schema" check
    # below, which silently narrows to "removes only the newest migration"
    # once another one lands on top. Already happened three times:
    # `8423190df6f9` added `handover_due_signalled`, `05f7842e4d69` (Auftrag 7a
    # follow-up: `simulated_cycle_source`/`simulated_warm_locked` on
    # `actuator_emergency_state` plus `sensor_failure_source_comparison`), then
    # `9d3f1a7c2b84` (Auftrag 7b: `armed_episode_id` on
    # `actuator_emergency_state` plus the `decided_no_command` command outcome)
    # -- every time the downgrade target stayed the absolute pre-sensor-failure
    # revision below (never "-1"), so the check keeps its original meaning
    # regardless of how many migrations now sit on top of it.
    assert scripts.get_heads() == ["9d3f1a7c2b84"]
    for args in (("downgrade", "base"), ("upgrade", "d31f6a04c7e9")):
        result = _alembic(migrations_database_url, *args)
        assert result.returncode == 0, result.stderr
    db_engine = create_engine(migrations_database_url)
    metadata = sa.MetaData()
    metadata.reflect(db_engine)
    old_rows: dict[str, list[dict[str, object]]] = {}
    try:
        with db_engine.begin() as connection:
            if with_existing_data:
                mode_id = connection.execute(
                    sa.select(metadata.tables["operating_mode"].c.id)
                ).first()[0]
                connection.execute(
                    metadata.tables["zone"]
                    .insert()
                    .values(
                        name="nb-bestand",
                        display_name="Bestand",
                        operating_mode_id=mode_id,
                        sort_order=7,
                        created_at=datetime(2026, 9, 28),
                        updated_at=datetime(2026, 9, 28),
                        hysteresis_k=Decimal("0.45"),
                        min_on_seconds=420,
                    )
                )
                zone_id = connection.execute(sa.select(metadata.tables["zone"].c.id)).scalar_one()
                integration_id = connection.execute(
                    sa.select(metadata.tables["integration"].c.id)
                ).first()[0]
                connection.execute(
                    metadata.tables["device"]
                    .insert()
                    .values(
                        integration_id=integration_id,
                        external_id="nb-aktor",
                        display_name="Bestandsaktor",
                        is_enabled=True,
                    )
                )
                device_id = connection.execute(
                    sa.select(metadata.tables["device"].c.id)
                ).scalar_one()
                role_id = connection.execute(
                    sa.select(metadata.tables["device_role"].c.id)
                ).first()[0]
                connection.execute(
                    metadata.tables["zone_device"]
                    .insert()
                    .values(
                        zone_id=zone_id,
                        device_id=device_id,
                        device_role_id=role_id,
                        sort_order=8,
                        self_regulating=True,
                    )
                )
                connection.execute(
                    metadata.tables["setpoint_mode"]
                    .insert()
                    .values(
                        code="nb-frost",
                        name="Frost",
                        sort_order=0,
                        is_builtin=True,
                    )
                )
                frost_id = connection.execute(
                    sa.select(metadata.tables["setpoint_mode"].c.id)
                ).scalar_one()
                source_id = connection.execute(
                    sa.select(metadata.tables["actor_source"].c.id)
                ).first()[0]
                outcome_id = connection.execute(
                    sa.select(metadata.tables["command_outcome"].c.id)
                ).first()[0]
                connection.execute(metadata.tables["device_command"].insert().values(
                    sent_at=datetime(2026, 9, 28), source_id=source_id,
                    zone_id=zone_id, zone_name="Bestand", device_id=device_id,
                    device_name="Bestandsaktor", command="switch", payload="OFF",
                    outcome_id=outcome_id, reason="Bestand",
                ))
                # Existing ORM defaults supply the old required Setting columns.
                values = {}
                for column in Setting.__table__.columns:
                    if column.name in metadata.tables["setting"].c and column.default is not None:
                        if column.default.is_scalar:
                            values[column.name] = column.default.arg
                values.update(
                    id=1, frost_protection_mode_id=frost_id, updated_at=datetime(2026, 9, 28)
                )
                connection.execute(metadata.tables["setting"].insert().values(**values))
            for name in ("zone", "zone_device", "setting", "device_command"):
                old_rows[name] = [
                    dict(row)
                    for row in connection.execute(sa.select(metadata.tables[name])).mappings()
                ]
        for iteration in range(2):
            result = _alembic(migrations_database_url, "upgrade", "head")
            assert result.returncode == 0, result.stderr
            with db_engine.connect() as connection:
                for name, rows in old_rows.items():
                    assert [
                        dict(row)
                        for row in connection.execute(sa.select(metadata.tables[name])).mappings()
                    ] == rows
                profile = (
                    connection.execute(text("SELECT * FROM sensor_failure_profile"))
                    .mappings()
                    .one()
                )
                assert (
                    profile["fixed_on_seconds"],
                    profile["fixed_off_seconds"],
                    profile["recovery_seconds"],
                    profile["recovery_samples"],
                    profile["warm_restart_hysteresis_k"],
                ) == (600, 1200, 60, 2, 1)
                assert connection.execute(
                    text(
                        "SELECT outdoor_c, on_seconds, off_seconds FROM sensor_failure_curve_point "
                        "WHERE profile_id = :id ORDER BY outdoor_c"
                    ),
                    {"id": profile["id"]},
                ).all() == [(-10, 1200, 600), (0, 600, 1200), (15, 0, 1800)]
                assert "emergency_role_override" not in {
                    column["name"] for column in sa.inspect(connection).get_columns("zone_device")
                }
                if with_existing_data:
                    assert connection.execute(text(
                        "SELECT reason_code, episode_id, actuator_decision_id FROM device_command"
                    )).one() == (None, None, None)
                    assert connection.execute(
                        text(
                            "SELECT sensor_failure_enabled, sensor_failure_profile_id, "
                            "sensor_failure_emergency_setpoint_c FROM zone"
                        )
                    ).one() == (False, None, None)
                    assert (
                        connection.execute(
                            text("SELECT temperature_backup_offset_k FROM zone_device")
                        ).scalar_one()
                        == 0
                    )
                    assert connection.execute(
                        text(
                            "SELECT sensor_failure_default_profile_id, "
                            "sensor_failure_default_emergency_setpoint_c FROM setting"
                        )
                    ).one() == (profile["id"], 20)
            if iteration == 0:
                # Absolute target, not "-1": the sensor-failure schema is no
                # longer the head by itself (see the guard assertion above),
                # so a relative single step would only undo the newest
                # migration on top and this check would stop meaning what it
                # says.
                result = _alembic(migrations_database_url, "downgrade", "d31f6a04c7e9")
                assert result.returncode == 0, result.stderr
                assert "sensor_failure_profile" not in sa.inspect(db_engine).get_table_names()
    finally:
        db_engine.dispose()


@pytest.mark.migration
def test_handover_due_signalled_migration_upgrade_and_downgrade(
    migrations_database_url: str,
) -> None:
    """`handover_due_signalled` persists the once-per-episode handover latch.

    Without this column (Kreuzreview finding on `emergency_operation.py`), the
    latch would live only in memory and either re-fire or vanish across a
    process restart mid-episode. Covers: fresh install gets the column with a
    `False` default, an existing row gets `False` too (not NULL), and
    downgrading by exactly one step removes only this column -- the rest of
    `zone_sensor_failure_state` and its sibling tables stay intact.
    """
    from datetime import datetime

    import sqlalchemy as sa

    # `migrations_database_url` is session-scoped and shared with every other
    # migration test -- start from an empty schema, not whatever an earlier
    # test in the session left behind.
    reset = _alembic(migrations_database_url, "downgrade", "base")
    assert reset.returncode == 0, reset.stderr
    up_to_previous = _alembic(migrations_database_url, "upgrade", "a0110b03c001")
    assert up_to_previous.returncode == 0, up_to_previous.stderr

    db_engine = create_engine(migrations_database_url)
    try:
        with db_engine.begin() as connection:
            metadata = sa.MetaData()
            metadata.reflect(db_engine, only=["zone", "operating_mode"])
            mode_id = connection.execute(sa.select(metadata.tables["operating_mode"].c.id)).first()[
                0
            ]
            connection.execute(
                metadata.tables["zone"]
                .insert()
                .values(
                    name="nb-latch",
                    display_name="Übergabesperre",
                    operating_mode_id=mode_id,
                    sort_order=9,
                    created_at=datetime(2026, 9, 29),
                    updated_at=datetime(2026, 9, 29),
                    hysteresis_k=Decimal("0.45"),
                    min_on_seconds=420,
                )
            )
            zone_id = connection.execute(sa.select(metadata.tables["zone"].c.id)).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO zone_sensor_failure_state (zone_id, stage, "
                    "recovery_sample_count) VALUES (:zone_id, 'normal', 0)"
                ),
                {"zone_id": zone_id},
            )

        # Absolute target, not "head": this migration is no longer the head
        # by itself once a later one stacks on top (Auftrag 7a follow-up,
        # `05f7842e4d69`) -- see the identical fix in
        # `test_sensor_failure_upgrade_preserves_installation` above. Without
        # it, "upgrade head" + "downgrade -1" below would only undo that
        # newer migration and this column would still be present, silently
        # passing for the wrong reason.
        up = _alembic(migrations_database_url, "upgrade", "8423190df6f9")
        assert up.returncode == 0, up.stderr
        with db_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT stage, recovery_sample_count, handover_due_signalled "
                    "FROM zone_sensor_failure_state WHERE zone_id = :zone_id"
                ),
                {"zone_id": zone_id},
            ).one()
            assert row == ("normal", 0, False)
            columns_after_upgrade = {
                column["name"]
                for column in sa.inspect(connection).get_columns("zone_sensor_failure_state")
            }
            assert "handover_due_signalled" in columns_after_upgrade

        down = _alembic(migrations_database_url, "downgrade", "-1")
        assert down.returncode == 0, down.stderr
        with db_engine.connect() as connection:
            columns_after_downgrade = {
                column["name"]
                for column in sa.inspect(connection).get_columns("zone_sensor_failure_state")
            }
            assert "handover_due_signalled" not in columns_after_downgrade
            # Only the one column disappeared -- the row and its other
            # columns, and the table itself, are still there.
            row = connection.execute(
                text(
                    "SELECT stage, recovery_sample_count FROM zone_sensor_failure_state "
                    "WHERE zone_id = :zone_id"
                ),
                {"zone_id": zone_id},
            ).one()
            assert row == ("normal", 0)
    finally:
        db_engine.dispose()
