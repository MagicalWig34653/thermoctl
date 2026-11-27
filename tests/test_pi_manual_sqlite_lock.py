"""Ein Eingriff in den I-Anteil, während ein anderer Prozess dieselbe SQLite-Datei beschreibt
(Karte A391AABC).

SQLite öffnet Transaktionen mit einfachem `BEGIN`: Wer erst liest und dann schreibt, bekommt
sofort `database is locked` -- ohne Warten --, wenn dazwischen jemand anderes festgeschrieben
hat (im Browsertest als 500 auf „I setzen“ gesehen). Der Eingriff muss deshalb als erste
Anweisung einer frischen Transaktion schreiben; hält jemand die Schreibsperre länger als die
Wartezeit, kommt eine verständliche Absage statt eines 500, und nichts ist geändert.
"""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.helpers import source, user_with_permissions
from tests.test_shadow_run_pi import _pi_zone
from thermoctl.config import get_settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.state import PiManualRequest, ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain import pi_intervention
from thermoctl.domain.authz import principal_for_user
from thermoctl.domain.principal import Principal

# Eine echte Änderung: SQLite lässt ein inhaltsgleiches UPDATE die Seite nicht neu schreiben.
TOUCH = text("UPDATE zone_state SET pi_time_balance_seconds = pi_time_balance_seconds + 1")


@pytest.fixture
def file_database(tmp_path: Path) -> Iterator[tuple[Engine, Principal, int]]:
    settings = get_settings().model_copy(
        update={"database_url": f"sqlite:///{tmp_path / 'sperre.db'}"}
    )
    engine = create_engine_from_settings(settings)
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as setup:
            source(setup, "web")
            zone = _pi_zone(setup, "bad")
            user = user_with_permissions(
                setup, "verwalter", [("zone.read", None), ("zone.manage", None)]
            )
            principal = principal_for_user(setup, user)
            zone_id = zone.id
            setup.commit()
        yield engine, principal, zone_id
    finally:
        engine.dispose()


def test_a_commit_by_someone_else_between_read_and_write_does_not_fail_the_request(
    file_database: tuple[Engine, Principal, int],
) -> None:
    engine, principal, zone_id = file_database
    with Session(engine, expire_on_commit=False) as request, Session(engine) as other:
        zone = request.get(Zone, zone_id)  # die Anfrage hat gelesen (Anmeldung, Raum)
        assert zone is not None
        request.scalars(select(ZoneState)).all()
        other.execute(TOUCH)
        other.commit()

        pi_intervention.request_set(request, principal, zone, "0.3", source="web")
        request.commit()

    with Session(engine) as after:
        stored = after.scalars(select(PiManualRequest)).one()
        assert stored.integral == Decimal("0.3")
        assert [e.action for e in after.scalars(select(AuditEvent))] == ["pi.integral_set"]


def test_a_held_write_lock_is_a_refusal_not_a_crash(
    file_database: tuple[Engine, Principal, int],
) -> None:
    engine, principal, zone_id = file_database
    with Session(engine, expire_on_commit=False) as request, Session(engine) as other:
        request.execute(text("PRAGMA busy_timeout=50"))
        request.commit()
        zone = request.get(Zone, zone_id)
        assert zone is not None
        other.execute(TOUCH)  # Schreibsperre, nicht festgeschrieben

        with pytest.raises(pi_intervention.PiInterventionError) as refused:
            pi_intervention.request_reset(request, principal, zone, source="web")
        assert refused.value.code == "busy"
        other.rollback()

    with Session(engine) as after:
        assert after.scalars(select(PiManualRequest)).all() == []
        assert after.scalars(select(AuditEvent)).all() == []


def test_any_other_database_error_stays_an_error(
    file_database: tuple[Engine, Principal, int],
) -> None:
    """Nur „belegt“ wird zur Absage; ein echter Datenbankfehler bleibt sichtbar."""
    engine, principal, zone_id = file_database
    with Session(engine, expire_on_commit=False) as request:
        zone = request.get(Zone, zone_id)
        assert zone is not None
        request.execute(text("DROP TABLE pi_manual_request"))
        request.commit()

        with pytest.raises(OperationalError, match="no such table"):
            pi_intervention.request_reset(request, principal, zone, source="web")
