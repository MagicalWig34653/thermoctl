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

    /** Ist diese Karte bereits einer Stelle zugeordnet? Dann kann sie nur heraus. */
    function zugeordnet(karte) {
        return Boolean(karte.dataset.zuordnung || karte.dataset.messquelle);
    }

    /** Passt diese Karte auf dieses Ziel?
     *
     *  Zwei Richtungen: Eine Karte aus dem Vorrat geht auf eine Stufe, sofern sie kann,
     *  was die Stufe verlangt -- derselbe Massstab wie in der Domaene, abgewiesen wird
     *  nur ein nachweislicher Widerspruch. Eine bereits zugeordnete Karte geht
     *  ausschliesslich zurueck in den Vorrat.
     */
    function passt(karte, ziel) {
        if (ziel.dataset.ziel === "entfernen") {
            return zugeordnet(karte);
        }
        if (zugeordnet(karte)) {
            return false;
        }
        const braucht = ziel.dataset.braucht;
        const kann = (karte.dataset.kann || "").split(" ").filter(Boolean);
        return !braucht || kann.length === 0 || kann.includes(braucht);
    }

    function zielUnter(x, y, ziele) {
        for (const ziel of ziele) {
            const rahmen = ziel.getBoundingClientRect();
            if (x >= rahmen.left && x <= rahmen.right && y >= rahmen.top && y <= rahmen.bottom) {
                return ziel;
            }
        }
        return null;
    }

    function loesen(karte) {
        if (karte.dataset.messquelle) {
            // Die Messquelle ist eine Spalte an der Zone, keine Zuordnungszeile. Ein
            // leeres Geraetefeld loescht sie -- derselbe Weg wie ueber das Formular.
            const formular = document.getElementById("zuordnung-messquelle");
            formular.elements.device_id.value = "";
            formular.requestSubmit();
            return;
        }
        const formular = document.getElementById("zuordnung-loesen");
        formular.elements.zuordnung_id.value = karte.dataset.zuordnung;
        formular.requestSubmit();
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
            // Unpassende Ziele treten waehrend des Ziehens zurueck, statt erst beim
            // Loslassen mit einer Fehlermeldung zu antworten.
            ziele.forEach(function (z) {
                z.classList.toggle("tc-ziel-unpassend", !passt(karte, z));
            });

            function bewegen(zweites) {
                if (Math.abs(zweites.clientX - startX) > 3
                    || Math.abs(zweites.clientY - startY) > 3) {
                    bewegt = true;
                }
                karte.style.transform = "translate(" + (zweites.clientX - startX) + "px, "
                    + (zweites.clientY - startY) + "px)";
                const unter = zielUnter(zweites.clientX, zweites.clientY, ziele);
                const getroffen = unter && passt(karte, unter) ? unter : null;
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
                ziele.forEach(function (z) {
                    z.classList.remove("tc-ziel-aktiv");
                    z.classList.remove("tc-ziel-unpassend");
                });
            }

            function loslassen() {
                const getroffen = ziel;
                aufraeumen();
                if (!bewegt || !getroffen) {
                    return;
                }
                if (getroffen.dataset.ziel === "entfernen") {
                    loesen(karte);
                } else {
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
        // Beide Richtungen: die Karten im Vorrat und die bereits zugeordneten im
        // Flussbild.
        document.querySelectorAll(".tc-ziehbar").forEach(function (karte) {
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
