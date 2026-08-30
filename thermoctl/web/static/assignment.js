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
        return Boolean(karte.dataset.assignment || karte.dataset.source);
    }

    /** Passt diese Karte auf dieses Ziel?
     *
     *  Zwei Richtungen: Eine Karte aus dem Vorrat geht auf eine Stufe, sofern sie kann,
     *  was die Stufe verlangt -- derselbe Massstab wie in der Domaene, abgewiesen wird
     *  nur ein nachweislicher Widerspruch. Eine bereits zugeordnete Karte geht
     *  ausschliesslich zurueck in den Vorrat.
     */
    function passt(karte, ziel) {
        if (ziel.dataset.target === "entfernen") {
            return zugeordnet(karte);
        }
        if (zugeordnet(karte)) {
            return false;
        }
        const braucht = ziel.dataset.requires;
        const kann = (karte.dataset.can || "").split(" ").filter(Boolean);
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
        if (karte.dataset.source) {
            // Die Messquelle ist eine Spalte an der Zone, keine Zuordnungszeile. Ein
            // leeres Geraetefeld loescht sie -- derselbe Weg wie ueber das Formular.
            const formular = document.getElementById("assignment-source");
            formular.elements.device_id.value = "";
            formular.requestSubmit();
            return;
        }
        const formular = document.getElementById("assignment-detach");
        formular.elements.assignment_id.value = karte.dataset.assignment;
        formular.requestSubmit();
    }

    function absenden(geraetId, zielart, zielElement) {
        const ziel = zielElement && zielElement.dataset.form ? zielElement : null;
        if (ziel) {
            const formular = document.getElementById(ziel.dataset.form);
            if (formular && formular.elements.source_device_id) {
                formular.elements.kind.value = "sensor_temperature";
                formular.elements.source_device_id.value = geraetId;
                formular.requestSubmit();
            }
            return;
        }
        if (zielart === "messquelle") {
            const formular = document.getElementById("assignment-source");
            formular.elements.device_id.value = geraetId;
            formular.requestSubmit();
            return;
        }
        const eintrag = document.querySelector('[data-role-code="' + zielart + '"]');
        const rollenId = eintrag ? eintrag.dataset.roleId : null;
        if (!rollenId) {
            // Die Rolle gibt es in dieser Anlage nicht. Lieber nichts tun als eine
            // Anfrage schicken, die der Server als Formfehler zurueckweist.
            return;
        }
        const formular = document.getElementById("assignment-role");
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

            karte.classList.add("tc-dragging");
            karte.style.pointerEvents = "none";
            // Unpassende Ziele treten waehrend des Ziehens zurueck, statt erst beim
            // Loslassen mit einer Fehlermeldung zu antworten.
            ziele.forEach(function (z) {
                z.classList.toggle("tc-target-unfit", !passt(karte, z));
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
                    ziele.forEach(function (z) { z.classList.remove("tc-target-active"); });
                    if (getroffen) {
                        getroffen.classList.add("tc-target-active");
                    }
                    ziel = getroffen;
                }
            }

            function aufraeumen() {
                window.removeEventListener("pointermove", bewegen);
                window.removeEventListener("pointerup", loslassen);
                window.removeEventListener("pointercancel", aufraeumen);
                karte.classList.remove("tc-dragging");
                karte.style.pointerEvents = "";
                karte.style.transform = "";
                ziele.forEach(function (z) {
                    z.classList.remove("tc-target-active");
                    z.classList.remove("tc-target-unfit");
                });
            }

            function loslassen() {
                const getroffen = ziel;
                aufraeumen();
                if (!bewegt || !getroffen) {
                    return;
                }
                if (getroffen.dataset.target === "entfernen") {
                    loesen(karte);
                } else {
                    absenden(karte.dataset.device, getroffen.dataset.target, getroffen);
                }
            }

            window.addEventListener("pointermove", bewegen);
            window.addEventListener("pointerup", loslassen);
            window.addEventListener("pointercancel", aufraeumen);
        });
    }

    function einrichten() {
        const vorrat = document.getElementById("device-pool");
        if (!vorrat || vorrat.dataset.wired) {
            return;
        }
        vorrat.dataset.wired = "ja";
        const ziele = Array.from(document.querySelectorAll("[data-target]"));
        if (!ziele.length) {
            return;
        }
        // Beide Richtungen: die Karten im Vorrat und die bereits zugeordneten im
        // Flussbild.
        document.querySelectorAll(".tc-draggable").forEach(function (karte) {
            karteVerdrahten(karte, ziele);
        });
        const hinweis = document.querySelector("[data-drag-hint]");
        if (hinweis) {
            hinweis.hidden = false;
        }
    }

    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
