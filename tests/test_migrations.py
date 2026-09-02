import os
import subprocess
import sys

import pytest
from sqlalchemy import Engine, create_engine, text


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
        ("device_capability", "link_quality", "Verbindungsqualitaet", "Verbindungsqualität"),
        ("device_capability", "illuminance", "Beleuchtungsstaerke", "Beleuchtungsstärke"),
        ("device_role", "controller", "Bediengeraet", "Bediengerät"),
        ("actor_source", "web", "Weboberflaeche", "Weboberfläche"),
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
                assert orphans == 0, f"{table} zeigt auf eine geloeschte Faehigkeit"
    finally:
        db_engine.dispose()
