/*
 * Geraete per Ziehen einer Stufe der Zone zuordnen.
 *
 * Dieselbe Haltung wie beim Zeitplan: Das Ziehen ist eine zweite Bedienart derselben
 * Aenderung, keine eigene Schnittstelle. Beim Loslassen geht dasselbe Formular hinaus,
 * das darunter von Hand ausgefuellt wird -- gleicher CSS-Schutz, gleiche Rechtepruefung,
 * gleiche Fehlerdarstellung. Ohne JavaScript bleiben die Formulare, und der Hinweis
 * "ziehen" wird gar nicht erst eingeblendet.
 *
 * Zwei Dinge, die beim Zeitplan Zeit gekostet haben und hier von vornherein anders sind:
 *
 *   * Das gezogene Element wird **nicht** im Baum umgehaengt, nur per `transform`
 *     verschoben. Ein appendChild waehrend des Ziehens loest die Zeigererfassung, und
 *     danach landen pointermove und pointerup auf einem anderen Element.
 *   * Die Kennungen stehen im Rumpf des Formulars, nicht im Pfad. hx-boost liest die
 *     `action` einmal beim Verarbeiten der Seite; ein spaeter umgeschriebener Pfad waere
 *     wirkungslos.
 */
(function () {
    "use strict";

    function zielUnter(x, y, ziele) {
        for (const ziel of ziele) {
            const rahmen = ziel.getBoundingClientRect();
            if (x >= rahmen.left && x <= rahmen.right && y >= rahmen.top && y <= rahmen.bottom) {
                return ziel;
            }
        }
        return null;
    }

    function absenden(geraetId, zielart) {
        if (zielart === "messquelle") {
            const formular = document.getElementById("zuordnung-messquelle");
            formular.elements.device_id.value = geraetId;
            formular.requestSubmit();
            return;
        }
        const eintrag = document.querySelector('[data-rollencode="' + zielart + '"]');
        const rollenId = eintrag ? eintrag.dataset.rollenid : null;
        if (!rollenId) {
            // Die Rolle gibt es in dieser Anlage nicht. Lieber nichts tun als eine
            // Anfrage schicken, die der Server als Formfehler zurueckweist.
            return;
        }
        const formular = document.getElementById("zuordnung-rolle");
        formular.elements.device_id.value = geraetId;
        formular.elements.role_id.value = rollenId;
        formular.requestSubmit();
    }

    function karteVerdrahten(karte, ziele) {
        karte.addEventListener("pointerdown", function (ereignis) {
            if (ereignis.button !== 0) {
                return;
            }
            ereignis.preventDefault();
            const startX = ereignis.clientX;
            const startY = ereignis.clientY;
            let bewegt = false;
            let ziel = null;

            karte.classList.add("tc-in-bewegung");
            karte.style.pointerEvents = "none";

            function bewegen(zweites) {
                if (Math.abs(zweites.clientX - startX) > 3
                    || Math.abs(zweites.clientY - startY) > 3) {
                    bewegt = true;
                }
                karte.style.transform = "translate(" + (zweites.clientX - startX) + "px, "
                    + (zweites.clientY - startY) + "px)";
                const getroffen = zielUnter(zweites.clientX, zweites.clientY, ziele);
                if (getroffen !== ziel) {
                    ziele.forEach(function (z) { z.classList.remove("tc-ziel-aktiv"); });
                    if (getroffen) {
                        getroffen.classList.add("tc-ziel-aktiv");
                    }
                    ziel = getroffen;
                }
            }

            function aufraeumen() {
                window.removeEventListener("pointermove", bewegen);
                window.removeEventListener("pointerup", loslassen);
                window.removeEventListener("pointercancel", aufraeumen);
                karte.classList.remove("tc-in-bewegung");
                karte.style.pointerEvents = "";
                karte.style.transform = "";
                ziele.forEach(function (z) { z.classList.remove("tc-ziel-aktiv"); });
            }

            function loslassen() {
                const getroffen = ziel;
                aufraeumen();
                if (bewegt && getroffen) {
                    absenden(karte.dataset.geraet, getroffen.dataset.ziel);
                }
            }

            window.addEventListener("pointermove", bewegen);
            window.addEventListener("pointerup", loslassen);
            window.addEventListener("pointercancel", aufraeumen);
        });
    }

    function einrichten() {
        const vorrat = document.getElementById("geraetevorrat");
        if (!vorrat || vorrat.dataset.verdrahtet) {
            return;
        }
        vorrat.dataset.verdrahtet = "ja";
        const ziele = Array.from(document.querySelectorAll("[data-ziel]"));
        if (!ziele.length) {
            return;
        }
        vorrat.querySelectorAll(".tc-ziehbar").forEach(function (karte) {
            karteVerdrahten(karte, ziele);
        });
        const hinweis = document.querySelector("[data-ziehhinweis]");
        if (hinweis) {
            hinweis.hidden = false;
        }
    }

    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
