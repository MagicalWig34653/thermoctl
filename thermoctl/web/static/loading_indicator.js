/* Globale Ladeanzeige für htmx-Anfragen -- ein schmaler Balken über dem Inhalt
 * (Markup und Optik: base.html/base_plain.html bzw. thermoctl.css, Klasse
 * `.tc-loading-bar`).
 *
 * Anlass: Als Home-Assistant-Add-on läuft thermoctl hinter einem zusätzlichen
 * Proxy-Sprung (Browser -> HA-Frontend -> Supervisor -> Add-on) und reagiert
 * dadurch spürbar langsamer als im direkten Docker-Betrieb. Die Latenz lässt
 * sich nicht wegnehmen, wohl aber sichtbar machen.
 *
 * Am `document`, nicht an einzelnen Elementen: Die Seiten sind mit `hx-boost`
 * verdrahtet, auch ein Seitenwechsel ist eine htmx-Anfrage. Eine einzige globale
 * Anzeige deckt das ab, ohne dass jede Schaltfläche ihre eigene bräuchte.
 *
 * Verzögerung vor dem Anzeigen: 400 ms. Kürzer, und jede etwas trägere Antwort
 * (auch eine ganz gewöhnliche, ohne Proxy) lässt den Balken aufblitzen -- genau
 * das Zappeln, das hier vermieden werden soll. Länger, und die Anzeige käme für
 * eine wirklich langsame Anfrage spät. 400 ms liegt im üblichen Bereich für
 * "spürbar, aber noch keine Geduldsprobe" (Faustregel etwa 300-1000 ms).
 *
 * Ein Zähler statt eines Schalters: `hx-boost` kann mehr als eine Anfrage
 * gleichzeitig lostreten (zum Beispiel ein vorausgeladener Link neben einer
 * gerade laufenden Navigation). Der Balken darf erst verschwinden, wenn wirklich
 * keine Anfrage mehr läuft.
 */
(function () {
    "use strict";

    var VERZOEGERUNG_MS = 400;
    var laufende_anfragen = 0;
    var anzeige_timer = null;

    // Nicht zwischenspeichern: `hx-boost` tauscht den Inhalt von <body> bei jeder
    // Navigation aus, und `#tc-loading-bar` liegt dort als Geschwister von <nav>
    // und <main> -- ein einmal gemerktes Element wäre nach dem ersten
    // Seitenwechsel eine losgelöste Karteileiche, während im echten DOM längst
    // ein neues `#tc-loading-bar` steht. Dieselbe Falle wie beim `data-wired`-Marker
    // in anderen Skripten (siehe tests/test_smoke_test.py), nur über die
    // Elementreferenz statt über ein Attribut.
    function holeBalken() {
        return document.getElementById("tc-loading-bar");
    }

    function anzeigenPlanen() {
        if (anzeige_timer !== null) {
            return;
        }
        anzeige_timer = window.setTimeout(function () {
            anzeige_timer = null;
            if (laufende_anfragen > 0) {
                var element = holeBalken();
                if (element) {
                    element.classList.add("tc-loading-bar-sichtbar");
                }
            }
        }, VERZOEGERUNG_MS);
    }

    function anzeigenAbbrechen() {
        if (anzeige_timer !== null) {
            window.clearTimeout(anzeige_timer);
            anzeige_timer = null;
        }
        var element = holeBalken();
        if (element) {
            element.classList.remove("tc-loading-bar-sichtbar");
        }
    }

    document.addEventListener("htmx:beforeRequest", function () {
        laufende_anfragen += 1;
        anzeigenPlanen();
    });

    // `htmx:afterRequest` feuert als letzter Schritt jeder Anfrage -- bei Erfolg
    // genauso wie bei einem Fehlerstatus oder einem Netzwerkfehler (htmx räumt
    // darüber auch seine eigene Klasse `htmx-request` auf, unabhängig vom
    // Ausgang). Ein einziger Listener hier reicht deshalb, damit der Balken auch
    // nach einer gescheiterten Anfrage wieder verschwindet, statt ewig weiter zu
    // laufen und einen Ladezustand vorzutäuschen, der nicht mehr stimmt.
    document.addEventListener("htmx:afterRequest", function () {
        laufende_anfragen = Math.max(0, laufende_anfragen - 1);
        if (laufende_anfragen === 0) {
            anzeigenAbbrechen();
        }
    });
})();
