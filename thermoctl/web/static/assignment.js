/*
 * Assign devices to a slot of a zone by dragging.
 *
 * Same stance as in the schedule: dragging is a second way to operate the same change,
 * not an interface of its own. On release the very same form goes out that is filled in
 * by hand below it -- same CSRF protection, same permission check, same error display.
 * Without JavaScript the forms remain, and the "drag" hint is never shown in the first
 * place.
 *
 * Two things that cost time in the schedule and are done differently here from the start:
 *
 *   * The dragged element is **not** moved in the tree, only shifted via `transform`. An
 *     appendChild during the drag releases pointer capture, and pointermove and pointerup
 *     then land on a different element.
 *   * The identifiers live in the form's body, not in the path. hx-boost reads the
 *     `action` once while processing the page; a path rewritten later would have no
 *     effect.
 */
(function () {
    "use strict";

    /** Is this card already assigned to a slot? Then it can only go back out. */
    function isAssigned(card) {
        return Boolean(card.dataset.assignment || card.dataset.source);
    }

    /** Does this card fit this target?
     *
     *  Two directions: a card from the pool goes onto a slot provided it can do what the
     *  slot requires -- the same yardstick as in the domain, only a demonstrable
     *  contradiction is rejected. A card that is already assigned goes exclusively back
     *  into the pool.
     */
    function fits(card, target) {
        if (target.dataset.target === "detach") {
            return isAssigned(card);
        }
        if (isAssigned(card)) {
            return false;
        }
        const required = target.dataset.requires;
        const abilities = (card.dataset.can || "").split(" ").filter(Boolean);
        return !required || abilities.length === 0 || abilities.includes(required);
    }

    function targetUnder(x, y, targets) {
        for (const target of targets) {
            const box = target.getBoundingClientRect();
            if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
                return target;
            }
        }
        return null;
    }

    function detach(card) {
        if (card.dataset.source) {
            // The temperature source is a column on the zone, not an assignment row. An
            // empty device field clears it -- the same route as through the form.
            const form = document.getElementById("assignment-source");
            form.elements.device_id.value = "";
            form.requestSubmit();
            return;
        }
        const form = document.getElementById("assignment-detach");
        form.elements.assignment_id.value = card.dataset.assignment;
        form.requestSubmit();
    }

    function submit(deviceId, targetKind, targetElement) {
        const target = targetElement && targetElement.dataset.form ? targetElement : null;
        if (target) {
            const form = document.getElementById(target.dataset.form);
            if (form && form.elements.source_device_id) {
                form.elements.kind.value = "sensor_temperature";
                form.elements.source_device_id.value = deviceId;
                form.requestSubmit();
            }
            return;
        }
        if (targetKind === "temperature_source") {
            const form = document.getElementById("assignment-source");
            form.elements.device_id.value = deviceId;
            form.requestSubmit();
            return;
        }
        const entry = document.querySelector('[data-role-code="' + targetKind + '"]');
        const roleId = entry ? entry.dataset.roleId : null;
        if (!roleId) {
            // This plant has no such role. Better to do nothing than to send a request
            // the server would reject as a form error.
            return;
        }
        const form = document.getElementById("assignment-role");
        form.elements.device_id.value = deviceId;
        form.elements.role_id.value = roleId;
        form.requestSubmit();
    }

    function wireCard(card, targets) {
        card.addEventListener("pointerdown", function (event) {
            if (event.button !== 0) {
                return;
            }
            event.preventDefault();
            const startX = event.clientX;
            const startY = event.clientY;
            let moved = false;
            let target = null;

            card.classList.add("tc-dragging");
            card.style.pointerEvents = "none";
            // Unsuitable targets step back during the drag instead of answering with an
            // error message only on release.
            targets.forEach(function (t) {
                t.classList.toggle("tc-target-unfit", !fits(card, t));
            });

            function onMove(second) {
                if (Math.abs(second.clientX - startX) > 3
                    || Math.abs(second.clientY - startY) > 3) {
                    moved = true;
                }
                card.style.transform = "translate(" + (second.clientX - startX) + "px, "
                    + (second.clientY - startY) + "px)";
                const under = targetUnder(second.clientX, second.clientY, targets);
                const hit = under && fits(card, under) ? under : null;
                if (hit !== target) {
                    targets.forEach(function (t) { t.classList.remove("tc-target-active"); });
                    if (hit) {
                        hit.classList.add("tc-target-active");
                    }
                    target = hit;
                }
            }

            function cleanUp() {
                window.removeEventListener("pointermove", onMove);
                window.removeEventListener("pointerup", onRelease);
                window.removeEventListener("pointercancel", cleanUp);
                card.classList.remove("tc-dragging");
                card.style.pointerEvents = "";
                card.style.transform = "";
                targets.forEach(function (t) {
                    t.classList.remove("tc-target-active");
                    t.classList.remove("tc-target-unfit");
                });
            }

            function onRelease() {
                const hit = target;
                cleanUp();
                if (!moved || !hit) {
                    return;
                }
                if (hit.dataset.target === "detach") {
                    detach(card);
                } else {
                    submit(card.dataset.device, hit.dataset.target, hit);
                }
            }

            window.addEventListener("pointermove", onMove);
            window.addEventListener("pointerup", onRelease);
            window.addEventListener("pointercancel", cleanUp);
        });
    }

    function setUp() {
        const pool = document.getElementById("device-pool");
        if (!pool || pool.dataset.wired) {
            return;
        }
        pool.dataset.wired = "yes";
        const targets = Array.from(document.querySelectorAll("[data-target]"));
        if (!targets.length) {
            return;
        }
        // Both directions: the cards in the pool and the ones already assigned in the
        // flow diagram.
        document.querySelectorAll(".tc-draggable").forEach(function (card) {
            wireCard(card, targets);
        });
        const hint = document.querySelector("[data-drag-hint]");
        if (hint) {
            hint.hidden = false;
        }
    }

    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
})();
