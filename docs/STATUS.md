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

### Phase 1a abgeschlossen (2026-08-29)

- **Gemeinsame CSRF-Abhängigkeit.** `csrf_schutz` hängt an jedem Router der Oberfläche,
  greift nur bei zustandsändernden Methoden und nur bei Cookie-Anfragen. Die Handprüfung in
  `logout` ist entfallen. `tests/test_csrf.py` zählt alle zustandsändernden Routen auf und
  wird rot, sobald eine ohne Schutz dazukommt. Begründung und Ausnahme für die REST-API
  stehen in [offene-entscheidungen.md](offene-entscheidungen.md).
- **Startverhalten über zwei Starts.** `tests/test_startverhalten.py` fährt den Dienst
  zweimal hoch: der erste Start meldet genau ein Einmal-Token, der zweite keines mehr, und
  es bleibt genau eine unverbrauchte Marke in der Datenbank.
- **Zwei Wächtertests waren blind.** Seit FastAPI 0.141 verschachtelt `include_router()`
  die Routen (`_IncludedRouter.original_router`); beide Wächter fanden dadurch nur noch
  `/healthz` und waren grün, weil sie nichts prüften. Zusätzlich wertete die
  Endpunktabdeckung ihre Mitschrift mitten im Lauf aus. Beides behoben, beides mit
  Gegenprobe belegt. Das ist die dritte Fehlerklasse dieser Art — Einzelheiten in
  [offene-entscheidungen.md](offene-entscheidungen.md).
- Der ganze Ablauf wurde gegen einen wirklich laufenden Dienst durchgespielt: Einrichtung,
  Anmeldung, Startseite, Benutzerliste, Abmeldung ohne und mit CSRF-Token (403 / 303).

**Weiterhin offen, vor Teilprojekt 3:**

- `PasswordTooShort` wird nur im Einrichtungsformular gefangen. Eine spätere
  Passwortänderung braucht dieselbe Behandlung erneut — ein generischer Handler geht nicht,
  weil die Meldung ins jeweilige Formular zurück muss.

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

**Veröffentlicht am 2026-08-29:** `main` gepusht, Tag `v0.1.0` gesetzt. Die Registry führt
`ghcr.io/magicalwig34653/thermoctl` in den Marken `latest`, `0.1.0` und `0.1`. Das
Repository bleibt privat; öffentlich geschaltet wird es erst in Phase 5.

Der erste Tag-Lauf schlug fehl: Ein handgeschriebener Schritt setzte die `latest`-Marke mit
`github.repository`, das die Originalschreibweise des Kontos trägt — Docker verlangt
Kleinbuchstaben. Der Schritt ist ersatzlos entfallen; die metadata-action setzt die Marke
jetzt selbst. Genau dafür war es richtig, vor dem Tag die CI abzuwarten.
