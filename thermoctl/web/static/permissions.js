/*
 * A permission covering the whole plant covers every zone -- the zone checkboxes beside it
 * are then moot. This script dims them so nobody ticks a box that has no effect.
 *
 * Purely visual: the server works out the difference from what arrives, and
 * `pointer-events: none` makes sure the dimmed boxes can no longer be changed. Without
 * JavaScript every box is operable and the domain decides -- a plant-wide permission
 * simply makes the zone permissions redundant there.
 */
(function () {
    "use strict";

    function update(checkbox) {
        const key = checkbox.dataset.permission + "-" + checkbox.dataset.group;
        const zones = document.querySelector('[data-zones-for="' + key + '"]');
        if (zones) {
            zones.classList.toggle("tc-hidden", checkbox.checked);
        }
    }

    function setUp() {
        if (document.documentElement.dataset.permissionsWired) {
            return;
        }
        document.documentElement.dataset.permissionsWired = "yes";
        // On `document`, once: the checkboxes are replaced on every navigation, the
        // `document` stays.
        document.addEventListener("change", function (event) {
            const checkbox = event.target;
            if (checkbox && checkbox.dataset && checkbox.dataset.permission) {
                update(checkbox);
            }
        });
    }

    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
})();
