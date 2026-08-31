/*
 * The search field on the devices page.
 *
 * Purely presentational: it hides rows that do not match and sends nothing to the server.
 * A list of thirty devices is too long to skim and too short for a search mask with a
 * route of its own -- and a filter that triggers a request would, at this size, be slower
 * than the eye.
 *
 * The field is `hidden` in the HTML and only revealed here: without JavaScript it would
 * not filter, and an input field that does nothing is worse than none.
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

    function setUp() {
        const field = document.getElementById("device-search");
        if (!field || wired.has(field)) {
            return;
        }
        wired.add(field);
        field.hidden = false;
        const rows = Array.from(document.querySelectorAll("[data-device-row]"));
        const emptyNotice = document.getElementById("no-matches");

        field.addEventListener("input", function () {
            const search = field.value.trim().toLowerCase();
            let matches = 0;
            rows.forEach(function (row) {
                const fits = !search || row.dataset.searchText.includes(search);
                row.hidden = !fits;
                if (fits) {
                    matches += 1;
                }
            });
            // A heading with no rows underneath looks like a bug.
            document.querySelectorAll(".tc-panel").forEach(function (panel) {
                const visible = panel.querySelector("[data-device-row]:not([hidden])");
                panel.hidden = !visible;
                const heading = panel.previousElementSibling;
                if (heading && heading.classList.contains("t-section")) {
                    heading.hidden = !visible;
                }
            });
            if (emptyNotice) {
                emptyNotice.hidden = matches > 0;
            }
        });
    }

    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
})();
