/*
 * Kiosk: Panel-Ansicht (0.9.5).
 *
 * `kiosk.html` rendert beide Ebenen immer -- die 2x3-Übersicht und, je Zone,
 * einen flächendeckenden Detailbereich (`[data-kiosk-detail]`). Dieses Skript
 * entscheidet nur, welche gerade gilt, öffnet/schließt den Detailbereich und
 * springt nach 45 s ohne Bedienung zur Übersicht zurück.
 *
 * `data-ansicht-aktiv` an <body> ist ausschließlich das Ergebnis dieses
 * Skripts. Ohne JavaScript existiert das Attribut nie, keine Panel-Regel in
 * thermoctl.css greift, und die Seite bleibt bei der ursprünglichen, scrollenden
 * Tafel-Darstellung -- Sollwert, Boost und Übersteuerung-aufheben bleiben über
 * die gewöhnlichen Formulare bedienbar (siehe die Begründung in
 * `thermoctl/auth/kiosk.py::kiosk_csrf_protection`). "Panel vs. Tafel" ist reine
 * Anzeigeform, keine Sicherheitsgrenze.
 *
 * Zustand (welche Zone offen ist) lebt in diesem Modul, nicht im DOM: die
 * Selbstaktualisierung (`hx-trigger="every 20s"` auf `#kiosk-body`) *und* jedes
 * Formular darin (Sollwert, Boost, Übersteuerung aufheben) tauschen
 * `#kiosk-body` per `hx-swap="outerHTML"` komplett aus, wodurch jede am alten
 * Detailbereich gesetzte Klasse verloren geht. Dieser Abschluss wird nie neu
 * geladen (der Lader in `page_scripts.js` lädt jede Datei nur einmal je
 * Dokument), sein Zustand also schon.
 */
(function () {
    "use strict";

    const BREITENSCHWELLE = "(max-width: 600px)";
    const RUECKSPRUNG_MS = 45000;

    let offeneZoneId = null;
    let rueckspringTimer = null;

    function seite() {
        return document.querySelector(".kiosk-page");
    }

    function kioskKoerper() {
        return document.getElementById("kiosk-body");
    }

    // "auto" entscheidet die Bildschirmbreite (Umschaltpunkt 600 px); "panel"
    // und "tafel" sind fest -- das ist die Adresszeilen-Umschaltung
    // (`?ansicht=panel`/`?ansicht=tafel`), an der die Breite nichts mehr ändert.
    function aufgeloesteAnsicht() {
        const angefordert = seite() ? seite().dataset.ansicht : null;
        if (angefordert === "panel" || angefordert === "tafel") {
            return angefordert;
        }
        return window.matchMedia(BREITENSCHWELLE).matches ? "panel" : "tafel";
    }

    function ansichtAnwenden() {
        const koerper = seite();
        if (koerper) {
            koerper.dataset.ansichtAktiv = aufgeloesteAnsicht();
        }
    }

    function timerLoeschen() {
        if (rueckspringTimer !== null) {
            window.clearTimeout(rueckspringTimer);
            rueckspringTimer = null;
        }
    }

    function timerNeuStarten() {
        timerLoeschen();
        rueckspringTimer = window.setTimeout(detailSchliessen, RUECKSPRUNG_MS);
    }

    function detailZuZone(zoneId) {
        return document.querySelector('[data-kiosk-detail="' + zoneId + '"]');
    }

    function detailOeffnen(zoneId) {
        const detail = detailZuZone(zoneId);
        if (!detail) {
            return;
        }
        document.querySelectorAll(".kiosk-detail-open").forEach(function (offen) {
            offen.classList.remove("kiosk-detail-open");
        });
        detail.classList.add("kiosk-detail-open");
        offeneZoneId = zoneId;
        timerNeuStarten();
        // Zurück-Knopf statt Kachel im Fokus: Ein Wandtablett wird per Finger
        // bedient, aber ein angeschlossenes Zeigegerät oder eine Tastatur soll
        // trotzdem sofort im richtigen Bereich landen.
        const zurueck = detail.querySelector("[data-kiosk-back]");
        if (zurueck) {
            zurueck.focus();
        }
    }

    function detailSchliessen() {
        timerLoeschen();
        if (offeneZoneId === null) {
            return;
        }
        const detail = detailZuZone(offeneZoneId);
        if (detail) {
            detail.classList.remove("kiosk-detail-open");
        }
        offeneZoneId = null;
    }

    // Zeigt bei einem gerade abgelehnten Formular (Sollwert außerhalb des
    // erlaubten Bereichs, Übersteuerung ohne Berechtigung, ...) automatisch den
    // Detailbereich, in dem die Fehlermeldung steht -- ohne das wäre sie in der
    // Panel-Übersicht unsichtbar, weil dort per CSS ausgeblendet
    // (`.kiosk-tile-full`).
    function fehlerZoneUebernehmen() {
        if (offeneZoneId !== null) {
            return;
        }
        const koerper = kioskKoerper();
        const fehlerZone = koerper ? koerper.dataset.kioskErrorZone : "";
        if (fehlerZone) {
            offeneZoneId = fehlerZone;
        }
    }

    const verdrahteteKacheln = new WeakSet();
    const verdrahteteDetails = new WeakSet();

    function kachelnVerdrahten() {
        document.querySelectorAll("[data-kiosk-tile]").forEach(function (kachel) {
            if (verdrahteteKacheln.has(kachel)) {
                return;
            }
            verdrahteteKacheln.add(kachel);
            const oeffnen = function () {
                // Wirkungslos außerhalb der Panel-Ansicht: In der Tafel-Ansicht
                // bleiben die Formulare in der Kachel selbst die einzigen
                // Bedienelemente, wie vor 0.9.5.
                if (aufgeloesteAnsicht() !== "panel") {
                    return;
                }
                detailOeffnen(kachel.dataset.kioskTile);
            };
            kachel.addEventListener("click", oeffnen);
            kachel.addEventListener("keydown", function (ereignis) {
                if (ereignis.key === "Enter" || ereignis.key === " ") {
                    ereignis.preventDefault();
                    oeffnen();
                }
            });
        });
    }

    function detailsVerdrahten() {
        document.querySelectorAll("[data-kiosk-detail]").forEach(function (detail) {
            if (verdrahteteDetails.has(detail)) {
                return;
            }
            verdrahteteDetails.add(detail);
            const zurueck = detail.querySelector("[data-kiosk-back]");
            if (zurueck) {
                zurueck.addEventListener("click", detailSchliessen);
            }
            // Jede Berührung im offenen Detailbereich zählt als Bedienung -- ein
            // zweimaliges Anheben des Sollwerts innerhalb von 45 s darf nicht
            // mitten in der zweiten Berührung zur Übersicht zurückspringen.
            detail.addEventListener("pointerdown", function () {
                if (offeneZoneId === detail.dataset.kioskDetail) {
                    timerNeuStarten();
                }
            });
        });
    }

    function nachAktualisierungWiederherstellen() {
        ansichtAnwenden();
        kachelnVerdrahten();
        detailsVerdrahten();
        fehlerZoneUebernehmen();
        if (offeneZoneId !== null && aufgeloesteAnsicht() === "panel") {
            detailOeffnen(offeneZoneId);
        }
    }

    function einrichten() {
        ansichtAnwenden();
        kachelnVerdrahten();
        detailsVerdrahten();
        fehlerZoneUebernehmen();
        if (offeneZoneId !== null && aufgeloesteAnsicht() === "panel") {
            detailOeffnen(offeneZoneId);
        }
    }

    // Nur für "auto" relevant (siehe `aufgeloesteAnsicht`), aber unschädlich
    // registriert, auch wenn die Ansicht fest steht -- die Anfrage danach ist
    // billig, und "auto" kann sich erst nach dem ersten Aufbau ergeben, wenn ein
    // Wandtablett zwischenzeitlich gedreht wird.
    window.matchMedia(BREITENSCHWELLE).addEventListener("change", ansichtAnwenden);

    // Ein verzögert geladenes Skript kann nach `DOMContentLoaded` ankommen.
    if (document.readyState !== "loading") {
        einrichten();
    }
    document.addEventListener("DOMContentLoaded", einrichten);
    document.addEventListener("htmx:load", nachAktualisierungWiederherstellen);
    document.addEventListener("htmx:historyRestore", nachAktualisierungWiederherstellen);
})();
