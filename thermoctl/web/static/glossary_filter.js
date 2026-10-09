/*
 * The search field on the glossary page.
 *
 * Purely presentational, like `device_filter.js`: it hides entries that do not match and
 * sends nothing to the server. About fifty short entries are too few for a search route
 * of their own, and a request per keystroke would be slower than the eye.
 *
 * The field is `hidden` in the HTML and only revealed here: without JavaScript it would
 * not filter, and an input field that does nothing is worse than none. The A-Z jump
 * marks are ordinary links and work without this file.
 */
(function () {
    "use strict";

    // Wired elements are tracked in a WeakSet, not as a marker attribute: htmx restores a
    // snapshot of the page from its history cache, and attributes survive that while event
    // handlers do not (see `device_filter.js`).
    const wired = new WeakSet();

    function setUp() {
        const field = document.getElementById("glossary-search");
        if (!field || wired.has(field)) {
            return;
        }
        wired.add(field);
        field.hidden = false;
        const entries = Array.from(document.querySelectorAll("[data-glossary-entry]"));
        const groups = Array.from(document.querySelectorAll("[data-glossary-group]"));
        const letters = Array.from(document.querySelectorAll("[data-glossary-letter]"));
        const emptyNotice = document.getElementById("glossary-empty");

        field.addEventListener("input", function () {
            const search = field.value.trim().toLowerCase();
            let matches = 0;
            entries.forEach(function (entry) {
                const fits = !search || entry.dataset.searchText.includes(search);
                entry.hidden = !fits;
                if (fits) {
                    matches += 1;
                }
            });
            // A letter heading with no entries underneath looks like a bug, and a jump
            // mark to a hidden section would do nothing.
            const visibleLetters = new Set();
            groups.forEach(function (group) {
                const visible = group.querySelector("[data-glossary-entry]:not([hidden])");
                group.hidden = !visible;
                if (visible) {
                    visibleLetters.add(group.querySelector("h2").textContent.trim());
                }
            });
            letters.forEach(function (link) {
                const active = visibleLetters.has(link.dataset.glossaryLetter);
                link.classList.toggle("tc-glossary-letter-off", !active);
                if (active) {
                    link.removeAttribute("aria-disabled");
                    link.removeAttribute("tabindex");
                } else {
                    link.setAttribute("aria-disabled", "true");
                    link.setAttribute("tabindex", "-1");
                }
            });
            if (emptyNotice) {
                emptyNotice.hidden = matches > 0;
            }
        });
    }

    // A lazy-loaded script can arrive after DOMContentLoaded and htmx:load.
    setUp();
    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
    document.addEventListener("htmx:historyRestore", setUp);
})();
