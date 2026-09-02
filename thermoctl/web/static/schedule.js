/*
 * Dragging in the schedule's week view.
 *
 * The script is a second way to operate the same change, not an interface of its own: on
 * release it submits the very form a user without JavaScript would fill in by hand. So the
 * same CSRF protection, the same permission check and the same error display apply -- and
 * the schedule stays fully operable without JavaScript.
 *
 * After submitting, the page reloads instead of simply leaving the bar where it was. That
 * is deliberate: a moved point also changes the boundaries of the *neighbouring* bars, and
 * rebuilding that server-side split once more in the browser would be a second version of
 * the same logic -- exactly what principle 6 forbids.
 */
(function () {
    "use strict";

    // Welche Elemente schon verdrahtet sind -- als WeakSet, nicht als Attribut im
    // Markup. Der Unterschied ist nicht kosmetisch: htmx legt beim Navigieren eine
    // Momentaufnahme der Seite in seinen Verlaufsspeicher und stellt sie beim
    // Zurueckgehen daraus wieder her. Attribute ueberleben das, Ereignisbehandler
    // nicht. Ein `data-wired` im Markup kam also zurueck, ohne dass noch ein Behandler
    // daran hing -- die Marke sagte "schon verdrahtet", und die Seite reagierte auf
    // nichts mehr. Ein WeakSet kennt nur Elemente dieses Dokumentzustands; ein aus dem
    // Speicher geparstes Element ist ein neues und wird neu verdrahtet.
    const wired = new WeakSet();

    const MINUTES_PER_DAY = 1440;
    const GRID = 15; // Minutes. Anything finer cannot be hit reliably with a mouse.
    const PAINT_TOOL_KEY = "thermoctl.schedule.paint-tool";

    function rememberedPaintTool() {
        try {
            return window.sessionStorage.getItem(PAINT_TOOL_KEY);
        } catch (_error) {
            // Storage can be disabled by browser policy. The server-rendered default
            // remains usable, so persistence is an enhancement rather than a dependency.
            return null;
        }
    }

    function rememberPaintTool(value) {
        try {
            window.sessionStorage.setItem(PAINT_TOOL_KEY, value);
        } catch (_error) {
            // See rememberedPaintTool(): drawing and moving must still work without it.
        }
    }

    function twoDigits(number) {
        return String(number).padStart(2, "0");
    }

    function asTimeOfDay(minute) {
        return twoDigits(Math.floor(minute / 60)) + ":" + twoDigits(minute % 60);
    }

    function snapped(minute) {
        const rounded = Math.round(minute / GRID) * GRID;
        // There is no 24:00: the last reachable point is 23:45.
        return Math.min(Math.max(rounded, 0), MINUTES_PER_DAY - GRID);
    }

    function snappedBoundary(minute) {
        return Math.min(Math.max(Math.round(minute / GRID) * GRID, 0), MINUTES_PER_DAY);
    }

    function activePaintMode() {
        const selected = document.querySelector('input[name="paint_tool"]:checked');
        return selected && selected.value !== "move" ? Number(selected.value) : null;
    }

    function explainMoveTool() {
        const hint = document.querySelector("[data-paint-tool-hint]");
        if (hint) {
            hint.textContent = "Hier lässt sich nichts ziehen. Bitte einen Balken mit "
                + "Greifcursor wählen oder oben einen Modus zum Malen auswählen.";
        }
    }

    function setPaintForm(weekday, start, end) {
        const form = document.getElementById("schedule-paint");
        if (!form) {
            return false;
        }
        const weekdayField = form.elements.namedItem("weekday");
        const startField = form.elements.namedItem("start_time");
        const endField = form.elements.namedItem("end_time");
        const boundaryField = form.elements.namedItem("end_boundary");
        if (!weekdayField || !startField || !endField || !boundaryField) {
            return false;
        }
        weekdayField.value = String(weekday);
        startField.value = asTimeOfDay(start);
        // A pointer exactly at the lower edge means the last representable minute.
        endField.value = asTimeOfDay(Math.min(end, MINUTES_PER_DAY - GRID));
        boundaryField.checked = end === MINUTES_PER_DAY;
        return true;
    }

    function wirePainting(day) {
        day.addEventListener("pointerdown", function (event) {
            const modeId = activePaintMode();
            if (event.button !== 0 || event.target.closest("a, button")) {
                return;
            }
            // A bar is an unambiguous handle even while a paint mode is selected.
            // Leaving it to wireBar makes both common gestures work immediately;
            // the tool choice remains useful for forcing paint on everything else.
            if (event.target.closest(".schedule-draggable")) {
                return;
            }
            if (modeId === null) {
                if (!event.target.closest(".schedule-draggable")) {
                    explainMoveTool();
                }
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            const start = snappedBoundary(
                (event.clientY - day.getBoundingClientRect().top)
                / day.getBoundingClientRect().height * MINUTES_PER_DAY
            );
            let finish = Math.min(start + GRID, MINUTES_PER_DAY);
            const preview = document.createElement("div");
            preview.className = "schedule-paint-preview";
            day.appendChild(preview);

            function render(current) {
                finish = current === start ? Math.min(start + GRID, MINUTES_PER_DAY) : current;
                const low = Math.min(start, finish);
                const high = Math.max(start, finish);
                preview.style.top = (low / MINUTES_PER_DAY * 100) + "%";
                preview.style.height = ((high - low) / MINUTES_PER_DAY * 100) + "%";
                preview.textContent = asTimeOfDay(low) + "–"
                    + (high === MINUTES_PER_DAY ? "24:00" : asTimeOfDay(high));
            }
            render(finish);

            function onMove(moveEvent) {
                const box = day.getBoundingClientRect();
                render(snappedBoundary((moveEvent.clientY - box.top) / box.height * MINUTES_PER_DAY));
            }
            function finishGesture() {
                window.removeEventListener("pointermove", onMove);
                window.removeEventListener("pointerup", finishGesture);
                const low = Math.min(start, finish);
                const high = Math.max(start, finish);
                preview.remove();
                if (high <= low) {
                    return;
                }
                const form = document.getElementById("schedule-paint");
                if (form && setPaintForm(Number(day.dataset.weekday), low, high)) {
                    form.requestSubmit();
                }
            }
            window.addEventListener("pointermove", onMove);
            window.addEventListener("pointerup", finishGesture, { once: true });
        }, true);
    }

    /** The day the pointer is currently over -- or null outside the grid. */
    function dayUnder(x, y, days) {
        for (const day of days) {
            const box = day.getBoundingClientRect();
            if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
                return day;
            }
        }
        return null;
    }

    function minuteIn(day, y) {
        const box = day.getBoundingClientRect();
        const fraction = (y - box.top) / box.height;
        return snapped(fraction * MINUTES_PER_DAY);
    }

    function submit(pointId, weekday, minute) {
        const form = document.getElementById("schedule-move");
        if (!form) {
            return;
        }
        const pointField = form.elements.namedItem("point_id");
        const weekdayField = form.elements.namedItem("weekday");
        const timeField = form.elements.namedItem("time_of_day");
        if (!pointField || !weekdayField || !timeField) {
            return;
        }
        // The identifier as a field, not in the path: hx-boost reads a form's `action`
        // once while processing the page. A path rewritten here would have no effect --
        // the request would go to the path from before.
        pointField.value = String(pointId);
        weekdayField.value = String(weekday);
        timeField.value = asTimeOfDay(minute);
        // `requestSubmit()` and not `submit()`: only that fires a submit event, and only
        // through it does hx-boost take hold -- which is where the CSRF header is set
        // from the cookie. A bare submit() would go out without it and be rejected with
        // a 403.
        form.requestSubmit();
    }

    function wireBar(bar, days) {
        bar.addEventListener("pointerdown", function (event) {
            if (event.target.closest("a, button, input, select, label")) {
                return;
            }
            // Primary button only. A right-click should open the context menu, not move
            // a bar.
            if (event.button !== 0) {
                return;
            }
            event.preventDefault();

            const grabX = event.clientX;
            const grabY = event.clientY;
            const grabOffset = event.clientY - bar.getBoundingClientRect().top;
            const timeField = bar.querySelector(".schedule-time");
            const originalTime = timeField ? timeField.textContent : "";
            let target = null;
            let moved = false;

            // The bar is only moved visually, never relocated in the tree. An appendChild
            // during the drag releases pointer capture (the browser frees it implicitly
            // when the element leaves the tree), and pointermove and pointerup then land
            // on some *other* bar under the pointer. That is exactly what the first
            // version failed on: the drag looked right and submitted nothing.
            bar.classList.add("schedule-dragging");
            // Transparent to pointers during the drag, so the day column underneath is
            // hit and not the bar itself.
            bar.style.pointerEvents = "none";

            function onMove(secondEvent) {
                if (Math.abs(secondEvent.clientY - grabY) > 3
                    || Math.abs(secondEvent.clientX - grabX) > 3) {
                    moved = true;
                }
                bar.style.transform =
                    "translate(" + (secondEvent.clientX - grabX) + "px, "
                    + (secondEvent.clientY - grabY) + "px)";
                const day = dayUnder(secondEvent.clientX, secondEvent.clientY, days);
                if (!day) {
                    target = null;
                    return;
                }
                const minute = minuteIn(day, secondEvent.clientY - grabOffset);
                target = { weekday: Number(day.dataset.weekday), minute: minute };
                if (timeField) {
                    timeField.textContent = asTimeOfDay(minute);
                }
            }

            function cleanUp() {
                window.removeEventListener("pointermove", onMove);
                window.removeEventListener("pointerup", onRelease);
                window.removeEventListener("pointercancel", onCancel);
                bar.classList.remove("schedule-dragging");
                bar.style.pointerEvents = "";
                bar.style.transform = "";
            }

            function onRelease(secondEvent) {
                cleanUp();
                if (!moved || !target) {
                    if (timeField) {
                        timeField.textContent = originalTime;
                    }
                    // A click without movement is not a failed drag but the statement
                    // "something belongs here". It is handled here and not as a click
                    // event: `preventDefault()` on pointerdown suppresses the click on
                    // the bar entirely. Without that, pre-filling would only work on
                    // free areas -- and as soon as a schedule exists, the bars cover the
                    // day without gaps, so there would be almost none.
                    const day = dayUnder(
                        secondEvent.clientX, secondEvent.clientY, days
                    );
                    if (day) {
                        prefill(day, secondEvent.clientY);
                    }
                    return;
                }
                submit(bar.dataset.point, target.weekday, target.minute);
            }

            function onCancel() {
                cleanUp();
                if (timeField) {
                    timeField.textContent = originalTime;
                }
            }

            // On the window, not on the bar: the events should still arrive when the
            // pointer leaves the bar or something else covers it.
            window.addEventListener("pointermove", onMove);
            window.addEventListener("pointerup", onRelease);
            window.addEventListener("pointercancel", onCancel);
        });
    }

    /** Shows in the grid which time was just taken over.
     *
     *  The feedback sits where the pointer is, not in a form further down. Previously
     *  `scrollIntoView` pulled the form into view -- and tore the grid away from under
     *  the mouse in doing so: anyone wanting to set two points in a row clicked the same
     *  spot on screen the second time and hit a completely different time, because the
     *  page had scrolled several hundred pixels in between. Measured in the browser: one
     *  click shifted the grid by 377 px, which is about thirteen hours.
     */
    function mark(day, minute) {
        document.querySelectorAll(".schedule-marker").forEach(function (old) {
            old.remove();
        });
        const marker = document.createElement("div");
        marker.className = "schedule-marker";
        marker.style.top = (minute / MINUTES_PER_DAY * 100) + "%";
        marker.textContent = asTimeOfDay(minute);
        day.appendChild(marker);
    }

    function prefill(day, y) {
        // Takes day and time into the create form instead of creating a point right
        // away: a switch point without a chosen mode would be none.
        const weekdayField = document.querySelector('select[name="weekday"]');
        const timeField = document.getElementById("time_of_day");
        if (!weekdayField || !timeField) {
            return;
        }
        const minute = minuteIn(day, y);
        weekdayField.value = day.dataset.weekday;
        timeField.value = asTimeOfDay(minute);
        mark(day, minute);
        // Focus only when the field is in view anyway. `focus({preventScroll: true})` is
        // not enough for that: in Chromium the call still scrolled the page by 377 px --
        // measured, 377 with focus, 0 without. Focusing a field you cannot see achieves
        // nothing anyway; the feedback is the marker above.
        const box = timeField.getBoundingClientRect();
        const visible = box.top >= 0
            && box.bottom <= (window.innerHeight || document.documentElement.clientHeight);
        if (visible) {
            timeField.focus({ preventScroll: true });
        }
    }

    function setUp() {
        const grid = document.getElementById("schedule-grid");
        if (!grid || grid.dataset.editable !== "yes" || wired.has(grid)) {
            return;
        }
        wired.add(grid);

        const days = Array.from(grid.querySelectorAll(".schedule-day"));
        grid.querySelectorAll(".schedule-draggable").forEach(function (bar) {
            wireBar(bar, days);
        });
        days.forEach(function (day) {
            wirePainting(day);
            // Free areas: there the click arrives quite normally. Bars handle their own
            // click, see onRelease().
            day.addEventListener("click", function (event) {
                if (event.target === day) {
                    prefill(day, event.clientY);
                }
            });
        });

        document.querySelectorAll('input[name="paint_tool"]').forEach(function (tool) {
            tool.addEventListener("change", function () {
                rememberPaintTool(tool.value);
                const paintMode = activePaintMode();
                grid.classList.toggle("schedule-painting", paintMode !== null);
                const hint = document.querySelector("[data-paint-tool-hint]");
                if (hint) {
                    hint.textContent = paintMode === null
                        ? "Nur Balken mit Greifcursor lassen sich verschieben."
                        : "Ziehen im Raster malt mit dem gewählten Modus.";
                }
            });
        });

        // htmx replaces the server-rendered form after every gesture. Remembering the
        // choice in this tab avoids adding UI-only state to every schedule endpoint and
        // form. Restore before deriving classes and hints; an unavailable/deleted mode
        // simply leaves the valid server-rendered default in place.
        const remembered = rememberedPaintTool();
        if (remembered !== null) {
            const tool = Array.from(
                document.querySelectorAll('input[name="paint_tool"]')
            ).find(function (candidate) {
                return candidate.value === remembered;
            });
            if (tool) {
                tool.checked = true;
            }
        }
        const initialPaintMode = activePaintMode();
        grid.classList.toggle("schedule-painting", initialPaintMode !== null);

        // The hint sits hidden in the markup and only becomes visible here: whoever has
        // no JavaScript should not read that they can drag something that does not drag.
        const hint = document.querySelector("[data-schedule-hint]");
        if (hint) {
            hint.hidden = false;
        }
    }

    // Two hooks, as in passkey.js: `DOMContentLoaded` for the direct call, `htmx:load`
    // for every page swapped in by hx-boost.
    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
})();
