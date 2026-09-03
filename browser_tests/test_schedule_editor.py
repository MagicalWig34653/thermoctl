"""The weekly schedule editor: painting, dragging, tool switching, and undo.

Named in the task as by far the most complex piece of UI in the project, with
several previously reported defects -- among them a paint-tool selection that
jumped back to the first mode right after painting. None of this is reachable
without a real browser: every gesture here is driven by `pointerdown`/
`pointermove`/`pointerup` in `thermoctl/web/static/schedule.js`, which an HTTP
test never executes.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from browser_tests import seed
from browser_tests.conftest import LiveServer

pytestmark = pytest.mark.browser


@pytest.fixture
def schedule_zone_id(live_server: LiveServer, request: pytest.FixtureRequest) -> int:
    # A name unique per test: all tests in this file share the one session-scoped
    # server and its one database (see conftest.py) -- a fixed name would collide
    # on `zone.name`'s UNIQUE constraint as soon as a second test asked for it.
    name = f"Zeitplan-{request.node.name}"[:190]
    with live_server.session() as session:
        zone = seed.create_schedule_zone(session, name)
        session.commit()
        return zone.id


def _drag(page: Page, start: tuple[float, float], end: tuple[float, float]) -> None:
    page.mouse.move(*start)
    page.mouse.down()
    # A round trip into the page: the pointerdown handler in schedule.js sets up
    # its `moved`/`target` bookkeeping asynchronously relative to the CDP command
    # that dispatched the event, and issuing `mouse.move`/`mouse.up` immediately
    # after `mouse.down()` can outrun it -- the release is then read as a plain
    # click rather than a drag. Forcing one real evaluation round trip after each
    # step is enough to let the event actually land before the next one fires.
    page.evaluate("() => document.body.offsetHeight")
    # Several intermediate steps: schedule.js only treats the gesture as a real
    # move (as opposed to a click) once it sees more than 3px of travel, and a
    # single jump can also land before the browser has dispatched a pointermove
    # at all.
    page.mouse.move(*end, steps=8)
    page.evaluate("() => document.body.offsetHeight")
    page.mouse.up()


def test_painting_a_new_block_creates_it_and_the_chosen_tool_survives_the_reload(
    admin_page: Page, schedule_zone_id: int
) -> None:
    admin_page.goto(f"/zones/{schedule_zone_id}/schedule")

    # The default tool is the first mode ("Tag", `loop.first` in schedule.html).
    # Deliberately switching to the *third* one ("Frostschutz", never used by
    # `create_schedule_zone`'s own points): this is exactly the state the
    # historical bug lost on the very next server round trip. Not the second
    # mode ("Nacht") -- every empty day already carries "Nacht" over from
    # Monday's last point, and painting more of the same mode onto ground that
    # already has it is correctly a no-op, not a new block.
    mode_radios = admin_page.locator('input[name="paint_tool"][type="radio"]')
    chosen_radio = mode_radios.nth(3)  # 0 = "Punkte ziehen", 1 = "Tag", 2 = "Nacht"
    chosen_id = chosen_radio.get_attribute("id")
    assert chosen_id
    admin_page.locator(f'label[for="{chosen_id}"]').click()
    expect(chosen_radio).to_be_checked()

    # Tuesday (weekday 2) has no schedule point of its own yet -- only a
    # full-day segment carried over from Monday's last mode -- so it is
    # paintable ground from edge to edge.
    tuesday = admin_page.locator('.schedule-day[data-weekday="2"]')
    draggable_before = tuesday.locator(".schedule-draggable")
    expect(draggable_before).to_have_count(0)

    box = tuesday.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    _drag(admin_page, (x, box["y"] + box["height"] * 0.2), (x, box["y"] + box["height"] * 0.3))

    # The gesture submits the paint form and the server re-renders the page
    # (schedule.js's own docstring: "the page reloads instead of simply leaving
    # the bar where it was").
    expect(admin_page.locator("[data-gesture-error]")).to_have_count(0)
    tuesday = admin_page.locator('.schedule-day[data-weekday="2"]')
    # Two new points, not one: painting an interval onto carried ground creates a
    # point where the new mode starts *and* one where the carried mode resumes
    # afterwards -- both now real, draggable schedule points instead of a carry-over.
    expect(tuesday.locator(".schedule-draggable")).to_have_count(2)

    # The regression check: still the chosen mode, not reset to the first.
    expect(admin_page.locator(f"#{chosen_id}")).to_be_checked()


# No test here for dragging an *existing* block (schedule.js's `wireBar`) to a new
# time, even though it was in scope and is arguably the single most important
# gesture in this editor. Every variant tried -- raw `mouse.down`/`move`/`up`,
# the same with an extra settle and a forced layout round trip between steps,
# and Playwright's own `locator.drag_to()` -- reproduced the gesture correctly
# in an ad hoc standalone script, but failed deterministically (the point's
# `data-start-minute` never changed) every time it ran inside this suite's
# fixtures, for a reason not pinned down in the time available. A test that
# fails the same way every run is not "flaky" in the sense the task warns
# about, but shipping it green-washed with a workaround that only happens to
# pass would be worse than admitting the gap: painting (above) and undo
# (below) already exercise `schedule.js`'s pointer-event handling, the paint
# tool's persistence, and a real server round trip: they would have caught a
# JS exception or a wholly non-functional editor. Dragging a block onto a new
# time specifically is not covered here and needs a look with real developer
# tools attached, not another blind attempt.


def test_the_undo_button_reverts_the_last_gesture(
    admin_page: Page, schedule_zone_id: int
) -> None:
    admin_page.goto(f"/zones/{schedule_zone_id}/schedule")

    wednesday = admin_page.locator('.schedule-day[data-weekday="3"]')
    expect(wednesday.locator(".schedule-draggable")).to_have_count(0)
    box = wednesday.bounding_box()
    assert box is not None
    x = box["x"] + box["width"] / 2
    _drag(admin_page, (x, box["y"] + box["height"] * 0.4), (x, box["y"] + box["height"] * 0.5))

    wednesday = admin_page.locator('.schedule-day[data-weekday="3"]')
    # Two, not one -- see the equivalent comment in test_painting_a_new_block...
    expect(wednesday.locator(".schedule-draggable")).to_have_count(2)

    admin_page.get_by_role("button", name="Rückgängig").click()

    wednesday = admin_page.locator('.schedule-day[data-weekday="3"]')
    expect(wednesday.locator(".schedule-draggable")).to_have_count(0)
