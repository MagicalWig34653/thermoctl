/*
 * Suchfeld der Glossarseite -- Quelle: tools/landingpage/glossar.js (nach site/ kopiert).
 *
 * Filtert nur im Browser: blendet nicht passende Einträge aus und sendet nichts. Das
 * Suchfeld ist im HTML `hidden` und wird erst hier eingeblendet; ohne Skript würde es
 * nichts filtern. Die Sprungmarken A-Z sind gewöhnliche Verweise und gehen auch ohne.
 * Kein Speicher, keine Cookies, keine Anfragen.
 */
(function () {
    "use strict";

    var field = document.getElementById("glossar-suche");
    if (!field) {
        return;
    }
    var box = document.getElementById("glossar-suchbox");
    var status = document.getElementById("glossar-status");
    var empty = document.getElementById("glossar-leer");
    var entries = Array.prototype.slice.call(document.querySelectorAll("[data-suche]"));
    var groups = Array.prototype.slice.call(document.querySelectorAll("[data-gruppe]"));
    var letters = Array.prototype.slice.call(document.querySelectorAll("[data-buchstabe]"));
    box.hidden = false;

    field.addEventListener("input", function () {
        var query = field.value.trim().toLowerCase();
        var hits = 0;
        entries.forEach(function (entry) {
            var fits = !query || entry.getAttribute("data-suche").indexOf(query) !== -1;
            entry.hidden = !fits;
            if (fits) {
                hits += 1;
            }
        });
        var visible = {};
        groups.forEach(function (group) {
            var any = group.querySelector("[data-suche]:not([hidden])") !== null;
            group.hidden = !any;
            if (any) {
                visible[group.getAttribute("data-gruppe")] = true;
            }
        });
        letters.forEach(function (link) {
            if (visible[link.getAttribute("data-buchstabe")]) {
                link.removeAttribute("aria-disabled");
                link.removeAttribute("tabindex");
            } else {
                link.setAttribute("aria-disabled", "true");
                link.setAttribute("tabindex", "-1");
            }
        });
        empty.hidden = hits > 0;
        status.textContent = query
            ? hits + " von " + entries.length + " Begriffen"
            : "";
    });
})();
