/*
 * Kiosk: Panel-Ansicht.
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

    // 599.98px, nicht 600px: die Vorgabe lautet "ab 600 px die Tafel", und
    // `max-width: 600px` schließt 600px selbst noch ins Panel ein -- am
    // Grenzwert lief das der Zusage zuwider. Dieselbe Schreibweise benutzt das
    // Projekt schon anderswo (thermoctl.css, `767.98px`/`991.98px`, die
    // Bootstrap-Konvention für einen Haltepunkt "bis ausschließlich").
    const BREITENSCHWELLE = "(max-width: 599.98px)";
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

    // Nur die sichtbare Klasse setzen -- ohne den Rücksprung-Timer anzufassen.
    // Getrennt von `detailOeffnen` (unten), das eine *Bedienung* ist: Diese
    // Funktion läuft auch nach jedem `#kiosk-body`-Tausch (Selbstaktualisierung
    // alle 20 s, jedes Formular), um den DOM-Zustand wiederherzustellen -- ein
    // neuer DOM-Knoten hat die Klasse "kiosk-detail-open" nie gesehen. Bis
    // hierher rief genau diese Wiederherstellung `detailOeffnen` auf, das den
    // Timer jedes Mal neu startete -- bei laufender Selbstaktualisierung lief
    // der 45-s-Rücksprung dadurch nie ab (im Betrieb widerlegt: Detail 67 s
    // offen gelassen, drei Abrufe der Selbstaktualisierung dazwischen, Detail
    // weiterhin offen). Der Ablaufzeitpunkt darf sich nur bei echter Bedienung
    // ändern, nicht beim bloßen Wiederherstellen eines schon offenen Zustands.
    function detailWiederherstellen(zoneId) {
        const detail = detailZuZone(zoneId);
        if (!detail) {
            return;
        }
        document.querySelectorAll(".kiosk-detail-open").forEach(function (offen) {
            offen.classList.remove("kiosk-detail-open");
        });
        detail.classList.add("kiosk-detail-open");
    }

    function detailOeffnen(zoneId) {
        detailWiederherstellen(zoneId);
        const detail = detailZuZone(zoneId);
        if (!detail) {
            return;
        }
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
                // Bedienelemente, wie vor der Panel-Ansicht.
                if (aufgeloesteAnsicht() !== "panel") {
                    return;
                }
                detailOeffnen(kachel.dataset.kioskTile);
            };
            kachel.addEventListener("click", oeffnen);
            kachel.addEventListener("keydown", function (ereignis) {
                // `keydown` bubbelt von jedem Formularknopf innerhalb der Kachel
                // hoch (Sollwert, Boost -- in der Tafel-Ansicht dort die einzigen
                // Bedienelemente). Ohne diese beiden Prüfungen *vor* dem
                // `preventDefault` unten schluckte dieser Behandler Eingabetaste
                // und Leertaste auf einem fokussierten „+"-Knopf auch in der
                // Tafel-Ansicht -- eine Barrierefreiheits-Regression an einer
                // Ansicht, die unverändert bleiben soll. Erst wenn wirklich die
                // Kachel selbst das Ziel ist (nicht eines ihrer Kinder) *und* die
                // Panel-Ansicht überhaupt gilt, greift diese Tastaturaktivierung.
                if (ereignis.target !== kachel) {
                    return;
                }
                if (aufgeloesteAnsicht() !== "panel") {
                    return;
                }
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
            const alsBedienungZaehlen = function () {
                if (offeneZoneId === detail.dataset.kioskDetail) {
                    timerNeuStarten();
                }
            };
            detail.addEventListener("pointerdown", alsBedienungZaehlen);
            // `pointerdown` allein erfasst nur Maus/Finger/Stift -- eine
            // Tastaturaktivierung (Eingabetaste/Leertaste auf einem fokussierten
            // Knopf) feuert kein `pointerdown`. Seit die Wiederherstellung nach
            // einem Swap den Timer korrekt *nicht* mehr neu startet (siehe
            // `detailWiederherstellen` oben), fiel das auf: Eine echte
            // Tastaturbedienung schloss das Detail trotzdem nach dem nächsten
            // regulären Ablauf, weil sie nirgends als Aktivität ankam (in echter
            // Zeit gemessen: Detail nach einer erfolgreichen Eingabetaste-Bedienung
            // schon fünf Sekunden später zu). Nur Aktivierungstasten zählen, nicht
            // jeder Tastendruck (z. B. Tab beim Durchwandern der Knöpfe).
            detail.addEventListener("keydown", function (ereignis) {
                if (ereignis.key === "Enter" || ereignis.key === " ") {
                    alsBedienungZaehlen();
                }
            });
        });
    }

    function nachAktualisierungWiederherstellen() {
        ansichtAnwenden();
        kachelnVerdrahten();
        detailsVerdrahten();
        // Reihenfolge wichtig: `fehlerZoneUebernehmen` setzt `offeneZoneId` nur,
        // wenn noch keine Zone offen ist (z. B. ein per Formular ohne
        // JavaScript-Umweg -- sprich per htmx -- gerade abgelehnter Sollwert).
        // War schon eine Zone offen, bleibt es bei deren Wiederherstellung statt
        // eines neuen "Öffnens" mit neu laufendem Timer.
        fehlerZoneUebernehmen();
        if (offeneZoneId !== null && aufgeloesteAnsicht() === "panel") {
            detailWiederherstellen(offeneZoneId);
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
