"""Die Startpruefung des Datenbankschemas.

Anlass ist ein echter Fehlstart: Nach dem Verschieben der Datenbankdatei startete der
Dienst gegen eine leere Datei und scheiterte mit einem sechzigzeiligen Traceback, dessen
Kern `no such table: user` war -- eine Meldung, die den fehlenden Migrationslauf weder
benennt noch den Befehl nennt, der hilft.
"""

import logging

import pytest
from sqlalchemy import Engine, create_engine, text

from thermoctl.db.base import Base
from thermoctl.db.schema_state import (
    COMMAND,
    SchemaPasstNicht,
    check_schema,
    database_state,
)


def _leere_datenbank(tmp_path, name: str = "leer.db") -> Engine:
    return create_engine(f"sqlite:///{tmp_path / name}")


def _gestempelte_datenbank(tmp_path, revision: str, name: str = "gestempelt.db") -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    with engine.begin() as verbindung:
        verbindung.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        verbindung.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision}
        )
    return engine


def test_leere_datenbank_nennt_den_befehl(tmp_path) -> None:
    """Die Meldung muss handlungsfaehig machen: Was fehlt, und was tut man dagegen."""
    with pytest.raises(SchemaPasstNicht) as errors:
        check_schema(_leere_datenbank(tmp_path))
    assert COMMAND in str(errors.value)
    assert "kein Schema" in str(errors.value)


def test_veralteter_stand_nennt_beide_revisionen(tmp_path, monkeypatch) -> None:
    """Der unangenehmere Fall: Das Schema ist da, aber alt. Ohne Pruefung faellt das
    erst spaeter auf, an einer beliebigen Spalte, die es noch nicht gibt."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: "neue_revision")
    with pytest.raises(SchemaPasstNicht) as errors:
        check_schema(_gestempelte_datenbank(tmp_path, "alte_revision"))
    notice = str(errors.value)
    assert "alte_revision" in notice
    assert "neue_revision" in notice
    assert COMMAND in notice


def test_aktueller_stand_laesst_den_start_durch(tmp_path, monkeypatch) -> None:
    """Gegenprobe zu den beiden Faellen oben. Ohne sie wuerden sie auch von einer
    Funktion erfuellt, die grundsaetzlich abbricht."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: "kopf")
    check_schema(_gestempelte_datenbank(tmp_path, "kopf"))


def test_ohne_ermittelbaren_kopf_kein_fehlalarm(tmp_path, monkeypatch) -> None:
    """Wer thermoctl ohne das Migrationsverzeichnis betreibt, soll starten koennen --
    lieber eine Pruefung weniger als eine, die im falschen Moment blockiert."""
    monkeypatch.setattr("thermoctl.db.schema_state._migration_head", lambda: None)
    check_schema(_gestempelte_datenbank(tmp_path, "irgendeine"))


def test_schema_ohne_stempel_ist_kein_startgrund(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """`Base.metadata.create_all()` hinterlaesst keinen Alembic-Stempel. Genau so baut
    die Testsuite ihr Schema; ein Abbruch daran wuerde den halben Lauf lahmlegen."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ohne_stempel.db'}")
    Base.metadata.create_all(engine)
    with caplog.at_level(logging.WARNING):
        check_schema(engine)
    assert "nicht ueber Alembic" in caplog.text


def test_stand_der_datenbank_meldet_leere_datei_als_unbekannt(tmp_path) -> None:
    assert database_state(_leere_datenbank(tmp_path, "blank.db")) is None


def test_kopf_der_migrationen_findet_die_echte_revision() -> None:
    """Kein Attrappen-Test: Er liest das echte Migrationsverzeichnis und belegt damit,
    dass der Vergleich im Betrieb ueberhaupt eine Grundlage hat."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from thermoctl.db.schema_state import _migration_head

    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert _migration_head() == expected[0]
