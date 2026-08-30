/*
 * Kleinigkeiten der Oberflaeche, die ohne Server auskommen.
 *
 * Bewusst eine eigene Datei und kein Skript in der Vorlage: hx-boost tauscht den Rumpf
 * aus und fuehrt Skripte darin erneut aus -- ein Umschalter waere danach doppelt
 * verdrahtet und wuerde jeden Klick zweimal ausfuehren, also gar nichts.
 */
(function () {
    "use strict";

    const SCHLUESSEL = "thermoctl-schema";

    function umschalten() {
        const wurzel = document.documentElement;
        const dunkel = wurzel.getAttribute("data-bs-theme") !== "dark";
        wurzel.setAttribute("data-bs-theme", dunkel ? "dark" : "light");
        try {
            localStorage.setItem(SCHLUESSEL, dunkel ? "dunkel" : "hell");
        } catch (fehler) {
            // Privates Fenster oder gesperrter Speicher: Die Wahl gilt dann nur fuer
            // diese Seite. Das ist besser als ein Umschalter, der gar nichts tut.
        }
    }

    function einrichten() {
        // Am `document` und nur einmal: Die Schaltflaeche selbst wird bei jeder
        // Navigation neu eingesetzt, das `document` bleibt.
        if (document.documentElement.dataset.schemaVerdrahtet) {
            return;
        }
        document.documentElement.dataset.schemaVerdrahtet = "ja";
        document.addEventListener("click", function (ereignis) {
            const knopf = ereignis.target.closest("[data-schema-umschalten]");
            if (knopf) {
                ereignis.preventDefault();
                umschalten();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
