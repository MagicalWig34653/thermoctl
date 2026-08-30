import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7


@dataclass(frozen=True)
class SchedulePointDraft:
    weekday: int
    minute_of_day: int
    night: bool


def read_night_hours(blob: str) -> dict[int, frozenset[int]]:
    """Reads the unvalidated hour grid, without adopting unreadable parts."""
    result: dict[int, frozenset[int]] = {
        weekday: frozenset() for weekday in range(1, DAYS_PER_WEEK + 1)
    }
    try:
        roh: Any = json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        log.warning("Nachtstunden sind kein gueltiges JSON und werden als leer behandelt")
        return result

    if not isinstance(roh, list):
        log.warning("Nachtstunden sind kein Array und werden als leer behandelt")
        return result
    if len(roh) != DAYS_PER_WEEK + 1:
        log.warning(
            "Nachtstunden haben %d statt acht Slots; lesbare Wochentage werden uebernommen",
            len(roh),
        )

    for weekday in range(1, DAYS_PER_WEEK + 1):
        if weekday >= len(roh):
            continue
        slot = roh[weekday]
        if not isinstance(slot, list):
            log.warning("Nachtstunden-Slot %d ist keine Liste und wird verworfen", weekday)
            continue
        hours: set[int] = set()
        for value in slot:
            stunde = _read_hour(value)
            if stunde is None or stunde in hours:
                log.warning(
                    "Ungueltige oder doppelte Nachtstunde in Slot %d wird verworfen",
                    weekday,
                )
                continue
            hours.add(stunde)
        result[weekday] = frozenset(hours)
    return result


def _read_hour(value: object) -> int | None:
    # A bool is technically an integer, but was never a possible value of the PHP form.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        stunde = value
    elif isinstance(value, str) and value in {str(stunde) for stunde in range(HOURS_PER_DAY)}:
        stunde = int(value)
    else:
        return None
    return stunde if 0 <= stunde < HOURS_PER_DAY else None


def schedule_points_from_night_hours(
    night_hours: dict[int, frozenset[int]],
) -> list[SchedulePointDraft]:
    """Condenses an hour grid into the state transitions of the weekly ring."""
    week_picture = [
        stunde in night_hours.get(weekday, frozenset())
        for weekday in range(1, DAYS_PER_WEEK + 1)
        for stunde in range(HOURS_PER_DAY)
    ]
    wechsel = [
        index
        for index, night in enumerate(week_picture)
        if night != week_picture[index - 1]
    ]
    if not wechsel:
        return [SchedulePointDraft(weekday=1, minute_of_day=0, night=week_picture[0])]
    return [
        SchedulePointDraft(
            weekday=index // HOURS_PER_DAY + 1,
            minute_of_day=index % HOURS_PER_DAY * 60,
            night=week_picture[index],
        )
        for index in wechsel
    ]
