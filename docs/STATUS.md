# Stand

Letzte Aktualisierung: 2026-08-29

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
| Tests | 180 unter SQLite, 179 + 1 erwarteter Übersprung unter MariaDB |
| Testabdeckung | 99 %, Mindestschwelle 97 % in der CI |
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

### Nach dem Abschlussreview erledigt (2026-08-29)

Beim ersten Ausprobieren im Browser fielen zwei Fehler auf, die alle Tests und alle
Reviews passiert hatten:

- **Die Startseite fehlte.** Anmeldung, Abmeldung und die Navigationsleiste zeigten auf
  `/`, das es nicht gab — wer sich anmeldete, landete auf einer 404-Seite. Kein Test hatte
  es gefunden, weil alle Weiterleitungen mit `follow_redirects=False` abgeschnitten wurden:
  geprüft wurde, *dass* weitergeleitet wird, nie *wohin*.
- **Bootstrap war nirgends eingebunden**, obwohl der Rahmenentwurf es voraussetzt. Keine
  Aufgabe hatte es verlangt, also hat es niemand gebaut und kein Reviewer beanstandet.
- Eingabefehler endeten als `500` statt als Meldung im Formular.

Daraufhin ergänzt: `tests/test_rauchtest.py` (jede Seite antwortet, Weiterleitungen führen
irgendwohin, jeder Verweis in jeder Vorlage ist erreichbar) und
`tests/test_endpunktabdeckung.py` (jede Route muss in einem Test wirklich aufgerufen
werden). Beide wurden gegengeprüft: Entfernt man den Startseiten-Router beziehungsweise
ergänzt eine ungetestete Route, schlagen sie fehl.

Ebenfalls erledigt: globaler `Forbidden`-Handler, Testabdeckung von 93 auf 99 %, und die
Regel in CLAUDE.md, dass **jedes Review die Suite selbst ausführt**. Vorher stand dort das
Gegenteil — der Grund, warum nie jemand unabhängig nachgeprüft hat.

**Vor Teilprojekt 3 zu erledigen** (dort entstehen die Pflegeansichten):

- Eine gemeinsame Dependency für den CSRF-Schutz. Heute steht die Prüfung von Hand in der
  einen zustandsändernden Route; mit jeder weiteren müsste sie wiederholt werden, und
  irgendwann vergisst man sie.
- `PasswordTooShort` wird nur im Einrichtungsformular gefangen. Eine spätere
  Passwortänderung braucht dieselbe Behandlung erneut — ein generischer Handler geht nicht,
  weil die Meldung ins jeweilige Formular zurück muss.

**Vor Teilprojekt 2 zu erledigen:**

- Ein Test für das Startverhalten: dass genau ein Einmal-Token entsteht und beim zweiten
  Start keines weiteren. Das ist der einzige Kanal, über den ein Betreiber an dieses
  Geheimnis kommt. (Der Lifespan-Hook ist inzwischen von der Abdeckung erfasst, das
  Zusammenspiel über zwei Starts hinweg aber nicht.)

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
