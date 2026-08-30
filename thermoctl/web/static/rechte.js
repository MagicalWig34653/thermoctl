/*
 * Ein Recht fuer die ganze Anlage deckt jede Zone ab -- die Zonenhaken daneben sind dann
 * gegenstandslos. Das Skript daempft sie, damit niemand einen Haken setzt, der nichts
 * bewirkt.
 *
 * Rein optisch: Der Server bildet die Differenz aus dem, was ankommt, und
 * `pointer-events: none` sorgt dafuer, dass die gedaempften Haken nicht mehr geaendert
 * werden koennen. Ohne JavaScript sind alle Haken bedienbar und die Domaene entscheidet
 * -- ein anlagenweites Recht macht die Zonenrechte dort schlicht ueberfluessig.
 */
(function () {
    "use strict";

    function nachfuehren(kaestchen) {
        const kennung = kaestchen.dataset.recht + "-" + kaestchen.dataset.gruppe;
        const zonen = document.querySelector('[data-zonen-fuer="' + kennung + '"]');
        if (zonen) {
            zonen.classList.toggle("tc-verdeckt", kaestchen.checked);
        }
    }

    function einrichten() {
        if (document.documentElement.dataset.rechteVerdrahtet) {
            return;
        }
        document.documentElement.dataset.rechteVerdrahtet = "ja";
        // Am `document`, einmal: Die Kaestchen werden bei jeder Navigation neu
        // eingesetzt, das `document` bleibt.
        document.addEventListener("change", function (ereignis) {
            const kaestchen = ereignis.target;
            if (kaestchen && kaestchen.dataset && kaestchen.dataset.recht) {
                nachfuehren(kaestchen);
            }
        });
    }

    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
