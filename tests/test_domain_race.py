"""The cases that only occur under concurrent requests.

Each of these functions checks first and writes afterward. Between the two, a
second request can claim the same name or the same time slot -- the pre-check
then says "free", and the database constraint catches it. That is exactly why
an `except IntegrityError` stands behind every pre-check.

These branches are almost impossible to hit over HTTP, because the pre-check
almost always holds. The test manufactures the race by making the pre-check
come up empty -- exactly what a concurrent request does.
"""

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_mode, operating_mode, source
from tests.helpers import create_zone as zone_helper
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain import zones as zone_modul
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import ScheduleError, create_schedule_point
from thermoctl.domain.zones import ZoneNameTaken, create_zone, update_zone


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def _principal() -> Principal:
    return Principal(user_id=None, token_id=None, grants=frozenset())


def test_a_zone_name_taken_concurrently_when_creating(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = operating_mode(session, "auto")
    create_zone(
        session, _principal(), name="besetzt", display_name="Besetzt",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    # The pre-check says 'free' — as it would for a second request that claims
    # the same name at the same moment.
    monkeypatch.setattr(zone_modul, "_name_taken", lambda *a, **k: False)
    with pytest.raises(ZoneNameTaken):
        create_zone(
            session, _principal(), name="besetzt", display_name="Zweiter",
            operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
        )


def test_a_zone_name_taken_concurrently_when_updating(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = operating_mode(session, "auto")
    create_zone(
        session, _principal(), name="schon-da", display_name="Schon da",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    other = create_zone(
        session, _principal(), name="andere", display_name="Andere",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    monkeypatch.setattr(zone_modul, "_name_taken", lambda *a, **k: False)
    with pytest.raises(ZoneNameTaken):
        update_zone(
            session, other, _principal(), name="schon-da", display_name="Andere",
            operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
        )
    assert other.name == "andere"


def test_a_time_slot_taken_concurrently(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = create_race_zone(session)
    mode = create_mode(session, "wettlauf-tag", "Tag")
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    import thermoctl.domain.schedule as schedule_modul

    # The occupancy check says 'free' — as it would for a second request at the same moment.
    monkeypatch.setattr(schedule_modul, "_moment_taken", lambda *a, **k: False)
    with pytest.raises(ScheduleError):
        create_schedule_point(
            session, zone, weekday=1, minute=360, mode_id=mode.id,
            user_id=None, token_id=None,
        )


def create_race_zone(session: Session):  # type: ignore[no-untyped-def]
    return zone_helper(session, "wettlauf-zone")


@pytest.mark.parametrize(
    ("weekday", "minute", "field"),
    [(0, 360, "weekday"), (8, 360, "weekday"), (1, -1, "time_of_day"), (1, 1440, "time_of_day")],
)
def test_schedule_point_boundaries_also_apply_in_the_domain(
    session: Session, weekday: int, minute: int, field: str
) -> None:
    """The view already checks -- the domain checks again anyway.

    REST and MCP call the same function, and a rule that lives only in the
    adapter does not apply to the others.
    """
    zone = create_race_zone(session)
    mode = create_mode(session, f"grenze-{weekday}-{minute}", "Tag")
    with pytest.raises(ScheduleError) as errors:
        create_schedule_point(
            session, zone, weekday=weekday, minute=minute, mode_id=mode.id,
            user_id=None, token_id=None,
        )
    assert errors.value.field == field


def test_an_unknown_mode_is_rejected_in_the_domain(session: Session) -> None:
    zone = create_race_zone(session)
    with pytest.raises(ScheduleError) as errors:
        create_schedule_point(
            session, zone, weekday=1, minute=360, mode_id=999999,
            user_id=None, token_id=None,
        )
    assert errors.value.field == "mode_id"


@pytest.mark.parametrize(
    "input_value", ["kein Doppelpunkt", "aa:bb", "6:00:00", "", "25:00", "6:60"]
)
def test_nonsensical_times_are_rejected(input_value: str) -> None:
    from thermoctl.domain.schedule import time_of_day_in_minutes

    with pytest.raises(ScheduleError):
        time_of_day_in_minutes(input_value)


def test_a_time_slot_taken_concurrently_while_moving(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race as when creating, on the other route into the same table.

    Dragging a bar checks the target minute first and writes afterwards; a second
    request can take that minute in between. The pre-check then says "free" and the
    unique constraint catches it -- and the error has to name `time_of_day`, because
    that is the field the form can mark. It said `uhrzeit` for a while, a name the
    form had not used since the endpoints were translated, so the message was
    rendered nowhere at all.
    """
    zone = create_race_zone(session)
    mode = create_mode(session, "wettlauf-verschieben", "Tag")
    moving = SchedulePoint(
        zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
    )
    session.add(moving)
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=420, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    import thermoctl.domain.schedule as schedule_modul

    monkeypatch.setattr(schedule_modul, "_moment_taken", lambda *a, **k: False)
    with pytest.raises(ScheduleError) as fehler:
        schedule_modul.move_schedule_point(
            session, zone, moving, weekday=1, minute=420, user_id=None
        )
    assert fehler.value.field == "time_of_day"
