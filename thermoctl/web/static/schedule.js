/*
 * Ziehen in der Wochenansicht des Zeitplans.
 *
 * Das Skript ist eine zweite Bedienart derselben Aenderung, keine eigene Schnittstelle:
 * Beim Loslassen schickt es dasselbe Formular ab, das ein Benutzer ohne JavaScript von
 * Hand ausfuellen wuerde. Damit gilt derselbe CSRF-Schutz, dieselbe Rechtepruefung und
 * dieselbe Fehlerdarstellung -- und der Zeitplan bleibt ohne JavaScript vollstaendig
 * bedienbar.
 *
 * Nach dem Absenden laedt die Seite neu, statt den Balken einfach stehen zu lassen. Das
 * ist Absicht: Ein verschobener Punkt aendert die Grenzen der *benachbarten* Balken mit,
 * und diese Zerlegung serverseitig noch einmal im Browser nachzubauen waere eine zweite
 * Fassung derselben Logik -- genau das, was Grundsatz 6 verbietet.
 */
(function () {
    "use strict";

    const MINUTEN_PRO_TAG = 1440;
    const RASTER = 15; // Minuten. Feiner ist mit der Maus nicht zuverlaessig treffbar.

    function zweistellig(zahl) {
        return String(zahl).padStart(2, "0");
    }

    function alsUhrzeit(minute) {
        return zweistellig(Math.floor(minute / 60)) + ":" + zweistellig(minute % 60);
    }

    function gerastert(minute) {
        const gerundet = Math.round(minute / RASTER) * RASTER;
        // 24:00 gibt es nicht: Der letzte erreichbare Punkt ist 23:45.
        return Math.min(Math.max(gerundet, 0), MINUTEN_PRO_TAG - RASTER);
    }

    /** Der Tag, ueber dem der Zeiger gerade steht -- oder null ausserhalb des Gitters. */
    function tagUnter(x, y, tage) {
        for (const tag of tage) {
            const rahmen = tag.getBoundingClientRect();
            if (x >= rahmen.left && x <= rahmen.right && y >= rahmen.top && y <= rahmen.bottom) {
                return tag;
            }
        }
        return null;
    }

    function minuteIn(tag, y) {
        const rahmen = tag.getBoundingClientRect();
        const anteil = (y - rahmen.top) / rahmen.height;
        return gerastert(anteil * MINUTEN_PRO_TAG);
    }

    function absenden(punktId, wochentag, minute) {
        const formular = document.getElementById("schedule-move");
        if (!formular) {
            return;
        }
        // Die Kennung als Feld, nicht im Pfad: hx-boost liest die `action` eines
        // Formulars einmal beim Verarbeiten der Seite. Ein hier umgeschriebener Pfad
        // waere wirkungslos -- die Anfrage ginge an den Pfad von vorhin.
        formular.elements.point_id.value = String(punktId);
        formular.elements.weekday.value = String(wochentag);
        formular.elements.time_of_day.value = alsUhrzeit(minute);
        // `requestSubmit()` und nicht `submit()`: Nur das feuert ein submit-Ereignis,
        // und nur darueber greift hx-boost -- das ist die Stelle, an der der
        // CSRF-Kopf aus dem Cookie gesetzt wird. Ein nacktes submit() ginge ohne ihn
        // hinaus und wuerde mit 403 abgewiesen.
        formular.requestSubmit();
    }

    function balkenVerdrahten(balken, tage) {
        balken.addEventListener("pointerdown", function (ereignis) {
            // Nur die primaere Taste. Ein Rechtsklick soll das Kontextmenue oeffnen,
            // nicht einen Balken verschieben.
            if (ereignis.button !== 0) {
                return;
            }
            ereignis.preventDefault();

            const griffX = ereignis.clientX;
            const griffY = ereignis.clientY;
            const griffversatz = ereignis.clientY - balken.getBoundingClientRect().top;
            const uhrzeitfeld = balken.querySelector(".schedule-time");
            const urspruenglicheZeit = uhrzeitfeld ? uhrzeitfeld.textContent : "";
            let ziel = null;
            let bewegt = false;

            // Der Balken wird nur optisch verschoben, nie im Baum umgehaengt. Ein
            // appendChild waehrend des Ziehens loest die Zeigererfassung aus (der
            // Browser gibt sie beim Entfernen aus dem Baum implizit frei), und danach
            // landen pointermove und pointerup auf irgendeinem *anderen* Balken unter
            // dem Zeiger. Genau daran ist die erste Fassung gescheitert: Das Ziehen sah
            // richtig aus und schickte nichts ab.
            balken.classList.add("schedule-dragging");
            // Waehrend des Ziehens durchlaessig, damit die Tagesspalte darunter
            // getroffen wird und nicht der Balken selbst.
            balken.style.pointerEvents = "none";

            function bewegen(zweitesEreignis) {
                if (Math.abs(zweitesEreignis.clientY - griffY) > 3
                    || Math.abs(zweitesEreignis.clientX - griffX) > 3) {
                    bewegt = true;
                }
                balken.style.transform =
                    "translate(" + (zweitesEreignis.clientX - griffX) + "px, "
                    + (zweitesEreignis.clientY - griffY) + "px)";
                const tag = tagUnter(zweitesEreignis.clientX, zweitesEreignis.clientY, tage);
                if (!tag) {
                    ziel = null;
                    return;
                }
                const minute = minuteIn(tag, zweitesEreignis.clientY - griffversatz);
                ziel = { wochentag: Number(tag.dataset.weekday), minute: minute };
                if (uhrzeitfeld) {
                    uhrzeitfeld.textContent = alsUhrzeit(minute);
                }
            }

            function aufraeumen() {
                window.removeEventListener("pointermove", bewegen);
                window.removeEventListener("pointerup", loslassen);
                window.removeEventListener("pointercancel", abbrechen);
                balken.classList.remove("schedule-dragging");
                balken.style.pointerEvents = "";
                balken.style.transform = "";
            }

            function loslassen(zweitesEreignis) {
                aufraeumen();
                if (!bewegt || !ziel) {
                    if (uhrzeitfeld) {
                        uhrzeitfeld.textContent = urspruenglicheZeit;
                    }
                    // Ein Klick ohne Bewegung ist kein misslungenes Ziehen, sondern die
                    // Ansage "hier soll etwas hin". Er wird hier behandelt und nicht als
                    // click-Ereignis: `preventDefault()` auf pointerdown unterdrueckt den
                    // Klick auf dem Balken vollstaendig. Ohne das griffe das Vorbelegen
                    // nur auf freien Flaechen -- und sobald ein Plan existiert, decken
                    // die Balken den Tag lueckenlos ab, es gaebe also fast keine.
                    const tag = tagUnter(
                        zweitesEreignis.clientX, zweitesEreignis.clientY, tage
                    );
                    if (tag) {
                        vorbelegen(tag, zweitesEreignis.clientY);
                    }
                    return;
                }
                absenden(balken.dataset.point, ziel.wochentag, ziel.minute);
            }

            function abbrechen() {
                aufraeumen();
                if (uhrzeitfeld) {
                    uhrzeitfeld.textContent = urspruenglicheZeit;
                }
            }

            // Am Fenster, nicht am Balken: Die Ereignisse sollen ankommen, auch wenn
            // der Zeiger den Balken verlaesst oder etwas anderes ueberdeckt.
            window.addEventListener("pointermove", bewegen);
            window.addEventListener("pointerup", loslassen);
            window.addEventListener("pointercancel", abbrechen);
        });
    }

    /** Zeigt im Gitter, welche Zeit gerade uebernommen wurde.
     *
     *  Die Rueckmeldung steht dort, wo der Zeiger ist, und nicht in einem Formular
     *  weiter unten. Vorher holte `scrollIntoView` das Formular heran -- und riss dabei
     *  das Gitter unter der Maus weg: Wer zwei Punkte nacheinander setzen wollte, klickte
     *  beim zweiten Mal auf dieselbe Bildschirmstelle und traf eine voellig andere
     *  Uhrzeit, weil die Seite in der Zwischenzeit um mehrere hundert Pixel gescrollt
     *  war. Im Browser nachgemessen: ein Klick verschob das Gitter um 377 px, das sind
     *  rund dreizehn Stunden.
     */
    function markieren(tag, minute) {
        document.querySelectorAll(".schedule-marker").forEach(function (alte) {
            alte.remove();
        });
        const marke = document.createElement("div");
        marke.className = "schedule-marker";
        marke.style.top = (minute / MINUTEN_PRO_TAG * 100) + "%";
        marke.textContent = alsUhrzeit(minute);
        tag.appendChild(marke);
    }

    function vorbelegen(tag, y) {
        // Uebernimmt Tag und Uhrzeit ins Anlege-Formular, statt sofort einen Punkt
        // anzulegen: Ein Schaltpunkt ohne gewaehlten Modus waere keiner.
        const wochentagfeld = document.querySelector('select[name="weekday"]');
        const uhrzeitfeld = document.getElementById("time_of_day");
        if (!wochentagfeld || !uhrzeitfeld) {
            return;
        }
        const minute = minuteIn(tag, y);
        wochentagfeld.value = tag.dataset.weekday;
        uhrzeitfeld.value = alsUhrzeit(minute);
        markieren(tag, minute);
        // Fokus nur, wenn das Feld ohnehin im Blick ist. `focus({preventScroll: true})`
        // reicht dafuer nicht: In Chromium scrollte der Aufruf die Seite trotzdem um
        // 377 px -- nachgemessen, mit Fokus 377, ohne 0. Ein Feld, das man nicht sieht,
        // zu fokussieren bringt ohnehin nichts; die Rueckmeldung ist die Marke oben.
        const rahmen = uhrzeitfeld.getBoundingClientRect();
        const sichtbar = rahmen.top >= 0
            && rahmen.bottom <= (window.innerHeight || document.documentElement.clientHeight);
        if (sichtbar) {
            uhrzeitfeld.focus({ preventScroll: true });
        }
    }

    function einrichten() {
        const gitter = document.getElementById("schedule-grid");
        if (!gitter || gitter.dataset.editable !== "ja" || gitter.dataset.wired) {
            return;
        }
        gitter.dataset.wired = "ja";

        const tage = Array.from(gitter.querySelectorAll(".schedule-day"));
        gitter.querySelectorAll(".schedule-draggable").forEach(function (balken) {
            balkenVerdrahten(balken, tage);
        });
        tage.forEach(function (tag) {
            // Freie Flaechen: Dort kommt der Klick ganz normal an. Balken behandeln
            // ihren Klick selbst, siehe loslassen().
            tag.addEventListener("click", function (ereignis) {
                if (ereignis.target === tag) {
                    vorbelegen(tag, ereignis.clientY);
                }
            });
        });

        // Der Hinweis steht im Markup ausgeblendet und wird erst hier sichtbar: Wer kein
        // JavaScript hat, soll nicht lesen, er koenne etwas ziehen, das sich nicht zieht.
        const hinweis = document.querySelector("[data-schedule-hint]");
        if (hinweis) {
            hinweis.hidden = false;
        }
    }

    // Zwei Aufhaenger, wie in passkey.js: `DOMContentLoaded` fuer den direkten Aufruf,
    // `htmx:load` fuer jede per hx-boost eingetauschte Seite.
    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", einrichten);
})();
