"""Ein Eingriff in den I-Anteil **während** eines laufenden Regelzyklus (Karte A391AABC).

Der Regelzyklus ist eine Transaktion über alle Räume. MariaDB ab 11.6 lehnt mit
`innodb_snapshot_isolation=ON` (Standard) das Schreiben einer Zeile ab, die seit dem Snapshot
geändert wurde (Fehler 1020) und rollt die ganze Transaktion zurück: Schriebe der Eingriff in eine
Zeile, die auch der Zyklus schreibt, fiele durch einen Klick der Zyklus **aller** Räume aus.
Deshalb darf der Eingriff keine Zeile anfassen, die der Zyklus ändert.

Das lässt sich nur mit echten, gleichzeitigen Transaktionen zeigen. SQLite lässt nur einen
Schreiber nach dem anderen zu; dort wird übersprungen.
"""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from tests.helpers import source, user_with_permissions
from tests.test_shadow_run_pi import NOW, _pi_zone
from thermoctl.db.base import Base
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.domain import pi_intervention
from thermoctl.domain.authz import principal_for_user
from thermoctl.services import shadow_run

SUFFIX = "_pi_race"


@pytest.fixture
def own_database(engine: Engine) -> Iterator[Engine]:
    """Eine eigene Datenbank: die Transaktionen hier werden wirklich festgeschrieben."""
    if engine.dialect.name == "sqlite":
        pytest.skip("SQLite kennt keine zwei gleichzeitigen Schreiber -- nur MariaDB")
    name = f"{engine.url.database}{SUFFIX}"
    with engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        connection.execute(text(f"CREATE DATABASE `{name}`"))
    race = create_engine(engine.url.set(database=name))
    try:
        Base.metadata.create_all(race)
        yield race
    finally:
        race.dispose()
        with engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))


@pytest.mark.parametrize("isolation", ["ON", "OFF"], ids=["snapshot-on", "snapshot-off"])
@pytest.mark.parametrize("earlier_request", [False, True], ids=["neu", "ersetzt"])
def test_an_intervention_during_a_cycle_costs_no_cycle_and_is_not_lost(
    own_database: Engine, isolation: str, earlier_request: bool
) -> None:
    with Session(own_database) as setup:
        source(setup, "web")
        zones = [_pi_zone(setup, name) for name in ("bad", "flur")]
        for zone in zones:
            state = setup.get(ZoneState, zone.id)
            assert state is not None
            state.pi_integral = Decimal("0.5")
        user = user_with_permissions(
            setup, "verwalter", [("zone.read", None), ("zone.manage", None)]
        )
        principal = principal_for_user(setup, user)
        if earlier_request:
            pi_intervention.request_set(setup, principal, zones[0], "0.9", source="web")
        zone_ids = [zone.id for zone in zones]
        setup.commit()

    with Session(own_database) as cycle_session, Session(own_database) as web:
        cycle_session.execute(text(f"SET SESSION innodb_snapshot_isolation={isolation}"))
        cycle_session.execute(select(ZoneState)).all()  # der Zyklus hat seinen Snapshot

        zone = web.get(type(zones[0]), zone_ids[0])
        assert zone is not None
        pi_intervention.request_reset(web, principal, zone, source="web")
        web.commit()

        rows = shadow_run.cycle(cycle_session, NOW)
        cycle_session.commit()
        assert sorted(row.zone_id for row in rows) == sorted(zone_ids)  # kein Raum fällt aus

    with Session(own_database) as after:
        rows = shadow_run.cycle(after, NOW + timedelta(seconds=60))
        after.commit()
        reasons = list(
            after.scalars(
                select(ShadowDecision.pi_reset_reason)
                .where(ShadowDecision.zone_id == zone_ids[0])
                .order_by(ShadowDecision.decided_at, ShadowDecision.id)
            )
        )
        # Der spätere Eingriff wirkt genau einmal, spätestens im Zyklus danach.
        assert reasons.count("manuell_reset") == 1
        view = pi_intervention.read_state(
            after, principal, after.get(type(zones[0]), zone_ids[0])
        )
        assert view.pending is None
