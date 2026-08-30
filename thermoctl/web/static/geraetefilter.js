/*
 * Das Suchfeld auf der Geraeteseite.
 *
 * Rein in der Anzeige: Es blendet Zeilen aus, die nicht passen, und schickt nichts an den
 * Server. Eine Liste von dreissig Geraeten ist zu lang zum Ueberfliegen und zu kurz fuer
 * eine Suchmaske mit eigenem Weg -- und ein Filter, der eine Anfrage ausloest, waere bei
 * dieser Menge langsamer als das Auge.
 *
 * Das Feld ist im HTML `hidden` und wird erst hier eingeblendet: Ohne JavaScript filterte
 * es nicht, und ein Eingabefeld, das nichts tut, ist schlimmer als keines.
 */
(function () {
    "use strict";

    function einrichten() {
        const feld = document.getElementById("geraetesuche");
        if (!feld || feld.dataset.verdrahtet) {
            return;
        }
        feld.dataset.verdrahtet = "ja";
        feld.hidden = false;
        const zeilen = Array.from(document.querySelectorAll("[data-geraetezeile]"));
        const leermeldung = document.getElementById("ohne-treffer");

        feld.addEventListener("input", function () {
            const suche = feld.value.trim().toLowerCase();
            let treffer = 0;
            zeilen.forEach(function (zeile) {
                const passt = !suche || zeile.dataset.suchtext.includes(suche);
                zeile.hidden = !passt;
                if (passt) {
                    treffer += 1;
                }
            });
            // Eine Ueberschrift ohne Zeilen darunter sieht aus wie ein Fehler.
            document.querySelectorAll(".tc-tafel").forEach(function (tafel) {
                const sichtbar = tafel.querySelector("[data-geraetezeile]:not([hidden])");
                tafel.hidden = !sichtbar;
                const ueberschrift = tafel.previousElementSibling;
                if (ueberschrift && ueberschrift.classList.contains("t-abschnitt")) {
                    ueberschrift.hidden = !sichtbar;
                }
            });
            if (leermeldung) {
                leermeldung.hidden = treffer > 0;
            }
        });
    }

    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
