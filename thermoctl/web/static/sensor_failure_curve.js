/*
 * Hinzufügen/Entfernen von Kennlinienzeilen auf der Regelvorgaben-Seite
 * (Auftrag 8a). Jede Zeile ist ein echtes, serverseitig validiertes Trio aus
 * `curve_outdoor_c`/`curve_on_seconds`/`curve_off_seconds` -- das Skript ist
 * reine Bedienhilfe, keine eigene Validierung: eine ungültige oder
 * unvollständige Zeile geht genauso ab wie jede andere ans Formular und wird
 * dort (`domain.sensor_failure_policy.validate_profile`) geprüft. Ohne
 * JavaScript bleibt die Seite bedienbar -- eine leere Zeile lässt sich nicht
 * über die Schaltfläche anlegen, aber serverseitig gerenderte Zeilen lassen
 * sich weiterhin ändern und (durch Leeren aller drei Felder) entfernen.
 */
(function () {
    "use strict";

    const wired = new WeakSet();

    function wireRemoveButtons(container) {
        container.querySelectorAll("[data-sfc-remove]").forEach(function (button) {
            if (wired.has(button)) {
                return;
            }
            wired.add(button);
            button.addEventListener("click", function () {
                const row = button.closest("[data-sfc-row]");
                if (row) {
                    row.remove();
                }
            });
        });
    }

    function wireAddButton(container) {
        const addButton = container.querySelector("[data-sfc-add]");
        const template = container.querySelector("template[data-sfc-template]");
        const list = container.querySelector("[data-sfc-list]");
        if (!addButton || !template || !list || wired.has(addButton)) {
            return;
        }
        wired.add(addButton);
        addButton.addEventListener("click", function () {
            const fragment = template.content.cloneNode(true);
            list.appendChild(fragment);
            wireRemoveButtons(list);
        });
    }

    function setUp() {
        document.querySelectorAll("[data-sensor-failure-curve]").forEach(function (container) {
            wireRemoveButtons(container);
            wireAddButton(container);
        });
    }

    // A lazy-loaded script can arrive after DOMContentLoaded and htmx:load already
    // fired (same reasoning as `device_filter.js`): on a plain, unboosted
    // navigation this is the common case, not the exception -- `page_scripts.js`
    // only injects this file once the feature selector is already present in the
    // DOM, which is after `DOMContentLoaded` by construction. Without this
    // fallback, the add-row button would silently do nothing on a direct, bookmarked
    // visit to `/settings` and only ever work after an htmx-boosted navigation
    // happened to land on it first.
    if (document.readyState !== "loading") {
        setUp();
    }
    document.addEventListener("DOMContentLoaded", setUp);
    document.addEventListener("htmx:load", setUp);
    document.addEventListener("htmx:historyRestore", setUp);
})();
