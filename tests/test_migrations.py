import os
import subprocess
import sys

import pytest
from sqlalchemy import Engine, create_engine, text


def _alembic(url: str, *argumente: str) -> subprocess.CompletedProcess[str]:
    """Ruft Alembic als Unterprozess, damit echte Migrationslaeufe geprueft werden.

    Aufruf ueber ``sys.executable -m alembic`` und nicht ueber das Skript ``alembic``:
    Nur so liegt das Projektverzeichnis im Modulpfad des Unterprozesses. Beim
    Skriptaufruf beginnt der Modulpfad in ``.venv/bin``, und das Paket ``thermoctl``
    ist dann nur ueber die ``.pth`` des editierbaren Installs auffindbar — die
    unter macOS das Flag ``hidden`` tragen kann und dann beim Start uebersprungen
    wird. Der Umweg ueber ``-m`` macht den Test unabhaengig davon, wie das venv
    eingerichtet wurde.

    Laeuft gegen ``url`` statt gegen ``TEST_DATABASE_URL``: Die Migrationstests brauchen
    eine eigene Datenbank, getrennt von der Fixture ``engine`` — sonst legt Alembic
    Tabellen an, die ``Base.metadata.create_all()`` schon erzeugt hat.
    """
    umgebung = {
        **os.environ,
        "THERMOCTL_DATABASE_URL": url,
        "THERMOCTL_SECRET_KEY": "t" * 32,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *argumente],
        env=umgebung,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.migration
def test_migration_vorwaerts_und_rueckwaerts(migrations_database_url: str) -> None:
    hoch = _alembic(migrations_database_url, "upgrade", "head")
    assert hoch.returncode == 0, hoch.stderr
    runter = _alembic(migrations_database_url, "downgrade", "base")
    assert runter.returncode == 0, runter.stderr
    wieder_hoch = _alembic(migrations_database_url, "upgrade", "head")
    assert wieder_hoch.returncode == 0, wieder_hoch.stderr


@pytest.mark.migration
def test_modelle_und_migrationen_stimmen_ueberein(migrations_database_url: str) -> None:
    """`alembic check` meldet, wenn ein Modell ohne Migration geaendert wurde."""
    vorbereitung = _alembic(migrations_database_url, "upgrade", "head")
    assert vorbereitung.returncode == 0, vorbereitung.stderr
    ergebnis = _alembic(migrations_database_url, "check")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_fremdschluessel_werden_unter_sqlite_geprueft(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("nur fuer SQLite sinnvoll")
    with engine.connect() as verbindung:
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


@pytest.mark.migration
def test_schatten_schema_referenzdaten_und_einstellungen(
    migrations_database_url: str,
) -> None:
    basis = _alembic(migrations_database_url, "downgrade", "base")
    assert basis.returncode == 0, basis.stderr
    vorher = _alembic(migrations_database_url, "upgrade", "4d43756aecd3")
    assert vorher.returncode == 0, vorher.stderr

    werk = create_engine(migrations_database_url)
    try:
        with werk.begin() as verbindung:
            verbindung.execute(
                text(
                    "INSERT INTO setpoint_mode "
                    "(code, name, sort_order, is_builtin) "
                    "VALUES ('migration-frost', 'Migration Frost', 0, false)"
                )
            )
            modus_id = verbindung.execute(
                text("SELECT id FROM setpoint_mode WHERE code = 'migration-frost'")
            ).scalar_one()
            verbindung.execute(
                text(
                    "INSERT INTO setting "
                    "(id, timezone, polling_interval_seconds, default_hysteresis_k, "
                    "default_min_on_seconds, default_min_off_seconds, "
                    "default_sensor_timeout_seconds, default_window_resume_delay_seconds, "
                    "frost_protection_mode_id, session_lifetime_seconds, updated_at) "
                    "VALUES (1, 'UTC', 30, 0.30, 300, 300, 1800, 120, :modus_id, "
                    "1209600, '2026-08-29 08:00:00')"
                ),
                {"modus_id": modus_id},
            )

        hoch = _alembic(migrations_database_url, "upgrade", "head")
        assert hoch.returncode == 0, hoch.stderr
        with werk.connect() as verbindung:
            faehigkeiten = set(
                verbindung.execute(text("SELECT code FROM device_capability")).scalars()
            )
            status = set(verbindung.execute(text("SELECT code FROM sensor_status")).scalars())
            einstellung = verbindung.execute(
                text(
                    "SELECT timezone, control_armed, measurement_retention_days, "
                    "shadow_interval_seconds FROM setting WHERE id = 1"
                )
            ).one()
        assert {
            "humidity", "illuminance", "occupancy", "link_quality", "power", "energy",
            "valve_position", "setpoint", "availability",
        } <= faehigkeiten
        assert status == {"ok", "veraltet", "keine_quelle"}
        assert tuple(einstellung) == ("UTC", False, 30, 60)

        runter = _alembic(migrations_database_url, "downgrade", "4d43756aecd3")
        assert runter.returncode == 0, runter.stderr
        with werk.connect() as verbindung:
            verblieben = set(
                verbindung.execute(text("SELECT code FROM device_capability")).scalars()
            )
        assert verblieben == {
            "temperature", "switch", "setpoint_display", "contact", "battery",
        }
        wieder_hoch = _alembic(migrations_database_url, "upgrade", "head")
        assert wieder_hoch.returncode == 0, wieder_hoch.stderr
    finally:
        werk.dispose()


@pytest.mark.migration
def test_umlaute_werden_in_bestehenden_bezeichnungen_nachgezogen(
    migrations_database_url: str,
) -> None:
    """Vier Bezeichnungen standen transliteriert in der Datenbank.

    Eine frisch eingerichtete Anlage bekommt die richtige Schreibweise schon beim
    Fuellen -- die Seed-Revision liest die Konstanten aus dem Code. Bestehende Anlagen
    tragen die alte Schreibweise aber in ihren Zeilen, und genau die stellt dieser Test
    her, bevor er die Revision darueber laufen laesst. Ohne den Umweg pruefte er nur,
    dass die Konstante richtig ist, und nie, dass die Revision etwas tut.
    """
    basis = _alembic(migrations_database_url, "downgrade", "base")
    assert basis.returncode == 0, basis.stderr
    vorher = _alembic(migrations_database_url, "upgrade", "c8e21a5f4d70")
    assert vorher.returncode == 0, vorher.stderr

    alte_schreibweise = [
        ("device_capability", "link_quality", "Verbindungsqualitaet", "Verbindungsqualität"),
        ("device_capability", "illuminance", "Beleuchtungsstaerke", "Beleuchtungsstärke"),
        ("device_role", "controller", "Bediengeraet", "Bediengerät"),
        ("actor_source", "web", "Weboberflaeche", "Weboberfläche"),
    ]
    werk = create_engine(migrations_database_url)
    try:
        with werk.begin() as verbindung:
            for tabelle, code, alt, _ in alte_schreibweise:
                verbindung.execute(
                    text(f"UPDATE {tabelle} SET label = :alt WHERE code = :code"),  # noqa: S608
                    {"alt": alt, "code": code},
                )
            # Eine von Hand vergebene Bezeichnung, die die Revision in Ruhe lassen muss.
            verbindung.execute(
                text("UPDATE device_role SET label = 'Mein Aktor' WHERE code = 'actuator'")
            )

        hoch = _alembic(migrations_database_url, "upgrade", "head")
        assert hoch.returncode == 0, hoch.stderr

        def bezeichnung(tabelle: str, code: str) -> str | None:
            with werk.connect() as verbindung:
                return verbindung.execute(
                    text(f"SELECT label FROM {tabelle} WHERE code = :code"),  # noqa: S608
                    {"code": code},
                ).scalar()

        for tabelle, code, _, neu in alte_schreibweise:
            assert bezeichnung(tabelle, code) == neu, code
        assert bezeichnung("device_role", "actuator") == "Mein Aktor"

        runter = _alembic(migrations_database_url, "downgrade", "c8e21a5f4d70")
        assert runter.returncode == 0, runter.stderr
        for tabelle, code, alt, _ in alte_schreibweise:
            assert bezeichnung(tabelle, code) == alt, code
        wieder_hoch = _alembic(migrations_database_url, "upgrade", "head")
        assert wieder_hoch.returncode == 0, wieder_hoch.stderr
    finally:
        werk.dispose()
