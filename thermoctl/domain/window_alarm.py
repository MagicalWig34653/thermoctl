"""A forgotten open window with cold air outside.

Two conditions, both anlagenweit configurable, both have to hold at once:

* a zone's window has been open longer than `setting.window_alarm_open_minutes`
  (strictly longer -- "seit mehr als"), and
* the outdoor temperature is strictly under `setting.window_alarm_outdoor_threshold_c`
  ("unter", not "höchstens").

This module only ever answers the question "should an alarm be raised right now" --
`state.py`/`app.py::_window_alarm_notices` turns the answer into the same
"only the transition, with an all-clear" notice shape every other fault kind in this
codebase already uses (see `domain.fault_notice.stuck_sensor_notice`).

Deliberately **not** wired into `domain/control_loop.py::decide()`. The project
owner's own words: this is a message, not an intervention -- whatever the frost
protection and window-open rules there decide about the actual heating stays
entirely their own business.
"""

from datetime import datetime
from decimal import Decimal

from thermoctl.domain.fault import OK


def window_alarm_state(
    *,
    window_open: bool | None,
    window_open_since: datetime | None,
    now: datetime,
    open_after_minutes: int,
    outdoor_status: str,
    outdoor_temperature_c: Decimal | None,
    threshold_c: Decimal,
) -> bool | None:
    """Whether the window-forgotten alarm should currently be active for a zone.

    Returns `None` -- an unknown state, not a verdict -- whenever **either**
    half of the condition is not currently trustworthy:

    * the outdoor reading (`outdoor_status` is not `domain.fault.OK`, or,
      redundantly but defensively, the value itself is missing), or
    * the window's own contact state (`window_open` is `None` -- the contact
      went stale or missing this cycle).

    The second case is not a cosmetic addition: an alarm already active when the
    contact fails mid-cycle must not collapse into "no window open" just
    because the caller (`services/ingest.py::advance_zone_state`) rightly keeps
    `window_open_since` unchanged while the contact's current state is unknown.
    Without this check, a stale-but-previously-open `window_open_since` would
    read here as "confirmed still open" and the alarm would keep silently
    firing on a fact nobody can currently vouch for -- the opposite failure
    from the one this function exists to prevent, but the same root cause:
    treating "we don't know" as if it were a definite answer.

    `None` must never be treated as "no alarm" by a caller: comparing this
    against a previous `True` has to recognise it as "cannot currently say"
    rather than silently issuing an all-clear for a condition that, for all
    anyone can tell right now, still holds. See
    `domain.fault_notice.window_alarm_notice`.
    """
    if outdoor_status != OK or outdoor_temperature_c is None:
        return None
    if window_open is None:
        return None
    if not window_open or window_open_since is None:
        return False
    age_s = (now - window_open_since).total_seconds()
    return age_s > open_after_minutes * 60 and outdoor_temperature_c < threshold_c
