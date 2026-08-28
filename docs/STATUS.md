# Stand

Letzte Aktualisierung: 2026-08-28

## Wo wir stehen

Teilprojekt 1 — Fundament ist abgeschlossen. Datenmodell, Domänenlogik, Anmeldung und
Rechte, Einrichtungsassistent, Verwaltung, REST-Adapter, Container, CI und die Tests der
Architekturgrenzen sind umgesetzt.

`thermoctl` steuert noch keine Heizung. Teilprojekt 2 — Geräte-Anbindung im
Schattenbetrieb — ist als Nächstes vorgesehen und erhält einen eigenen Zyklus aus
Brainstorming, Spezifikation und Plan.

## Zahlen zum Abschluss

Vom Controller nachgeprüft, nicht aus Berichten übernommen:

| | |
|---|---|
| Tests | 142 unter SQLite, 141 + 1 erwarteter Übersprung unter MariaDB |
| Ruff, mypy strict | ohne Befund, 38 Quelldateien |
| Migrationskette | linear, genau ein Kopf |
| Container | baut, startet als Nicht-root (UID 10001), `/healthz` antwortet |
| Einrichtung | Einmal-Token erscheint beim ersten Start im Log |

## Abschlussreview

Über den gesamten Branch gelaufen. Empfehlung: **Merge mit Auflagen**.

**Behoben:** Ein Timing-Seitenkanal bei der Anmeldung. Pythons Kurzschlussauswertung
übersprang die Passwortprüfung, wenn der Benutzername nicht existierte — Argon2id ist
absichtlich langsam, wodurch die Antwortzeit verriet, welche Konten es gibt. Gleiche
Fehlermeldung und gleiche Wartezeit genügen dagegen nicht. Der vorhandene Test prüfte
Status und Text, nicht die Zeit.

**Vor Teilprojekt 3 zu erledigen** (dort entstehen die Pflegeansichten):

- Eine gemeinsame Dependency für den CSRF-Schutz. Heute steht die Prüfung von Hand in der
  einen zustandsändernden Route; mit jeder weiteren müsste sie wiederholt werden, und
  irgendwann vergisst man sie.
- Ein globaler `exception_handler` für `Forbidden`. Heute übersetzt jede Route selbst nach
  403; eine künftige, die es vergisst, liefert 500 statt 403.

**Vor Teilprojekt 2 zu erledigen:**

- Ein Test für das Startverhalten: dass genau ein Einmal-Token entsteht und beim zweiten
  Start keines weiteren. Das ist der einzige Kanal, über den ein Betreiber an dieses
  Geheimnis kommt.

**Kann warten:** CSRF-Cookie im Doppel-Submit-Muster (Standardpraxis, Token gibt nichts
preis), Referenzdaten-Fixture nur für `test_setup.py`, fehlende ändernde Routen in der
Verwaltung (gehören zu Teilprojekt 3).

## Offen

- Geräte anbinden und zunächst im Schattenbetrieb beobachten.
- Den eigentlichen Regelkreis implementieren.
- Die Pflegeoberfläche vervollständigen.
- Die Datenübernahme aus dem Altschema planen; insbesondere ist die Umwandlung des
  unregelmäßigen Stundenrasters in Schaltpunkte ungeklärt.
- In Teilprojekt 2 entscheiden, ob die alten MQTT-Topics übergangsweise zusätzlich bedient
  werden.
- `vm130-nginx` bis zum abgeschlossenen Cutover unverändert als Rückfallebene erhalten.

Tag, Veröffentlichung und Push bleiben eine gesonderte Freigabeentscheidung des
Projektinhabers.
