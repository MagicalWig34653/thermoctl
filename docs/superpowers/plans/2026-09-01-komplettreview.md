# Komplettreview des Projekts

Geplant am 2026-09-01, nach der Freigabe von v0.2.2. **Nicht begonnen.**

Anlass: „Plane schon mal ein Komplett-Review des Projektes für die nächsten Tage. Also
Fehler finden, Tests überprüfen, Refactoring, Optimierung, …"

## Wovon dieser Plan ausgeht

Nicht von einem allgemeinen Bedürfnis nach Qualität, sondern von dem, was in den letzten
zwei Fassungen tatsächlich schiefgegangen ist. Jede Runde unten hat einen konkreten
Vorfall als Anlass. Was keinen hat, steht am Ende unter *Bewusst nicht in diesem Plan*.

Die Ausgangslage ist dabei nicht schlecht: 1390 Tests, 100 Prozent Abdeckung, zwei
Datenbanken, grüne CI. Genau das ist der Punkt — **all das war grün, während die Fehler
drin waren.** Ein Review, das dieselben Werkzeuge noch einmal anwendet, findet nichts.

## Die vier Befunde, die den Plan bestimmen

1. **Abdeckung sagt nichts über Prüfkraft.** Dreimal in einer Fassung: Ein verstümmeltes
   Formularfeld liess 177 Tests grün. Ein entfernter Sensor-Riegel liess alle 39
   Regelungstests grün. Ein Formular war ohne JavaScript unbedienbar, während seine
   Tests grün waren — sie setzten die Felder selbst, statt das gerenderte HTML zu nehmen.
2. **Zusicherungen in der Dokumentation gelten als wahr, bis jemand sie prüft.** Das
   Freigabe-Review fand fünf Aussagen in `CHANGELOG.md` und `docs/STATUS.md`, die der
   Code nicht hergab — darunter „geschaltet wird weiterhin nichts" und „der Malen-Editor
   ist nicht enthalten", während er enthalten war.
3. **Ein Muster wiederholt sich, wenn es niemand sucht.** Netzwartezeit innerhalb einer
   offenen Datenbanktransaktion gab es an *zwei* Stellen (Meross und Open-Meteo). Die
   zweite fand nur, wer nach der ersten gezielt gesucht hat.
4. **Die Regelkette ist gewachsen und niemand hat sie als Ganzes angesehen.** `decide()`
   hat inzwischen sieben Regeln. Zwei Kreuzreviews fanden dort Blocker, die beim
   Gegenlesen des Diffs durchgingen — weil ein Diff die Wechselwirkung zweier Regeln
   nicht zeigt.

---

## Runde 1 — Wie viel prüfen die Tests wirklich? (Mutationstest)

**Die wichtigste Runde. Wenn nur eine gemacht wird, dann diese.**

Ein Werkzeug (`mutmut` oder `cosmic-ray`) verändert den Quelltext systematisch —
Vergleiche umdrehen, Konstanten verschieben, Bedingungen entfernen — und führt jedes Mal
die Suite aus. Was grün bleibt, ist eine Zeile, die niemand prüft. Das ist die Zahl, die
100 Prozent Abdeckung vorgibt zu sein, aber nicht ist.

**Zuschnitt:** Nicht das ganze Projekt auf einmal, das läuft tagelang. In dieser
Reihenfolge, jede Stufe ein eigener Auftrag:

1. `domain/control_loop.py` und `services/shadow_run.py` — hier bewegt sich eine Heizung.
2. `domain/schedule.py` — hier entsteht, wann geheizt wird.
3. `domain/authz.py`, `auth/` — hier entscheidet sich, wer darf.
4. Der Rest der Domäne.

**Fertig, wenn** für die Stufen 1 bis 3 jede überlebende Mutation entweder mit einem Test
erschlagen oder mit einer Begründung als unerheblich abgelegt ist. Die Begründung gehört
in den Code, nicht in eine Tabelle, die niemand wiederfindet.

**Erwartung, damit die Zahl niemanden schockt:** Bei 100 Prozent Abdeckung überleben in
einem gut getesteten Projekt üblicherweise 10 bis 25 Prozent der Mutanten. Alles darunter
wäre erstaunlich, alles über 40 Prozent ein Alarmzeichen.

---

## Runde 2 — Stimmt, was wir behaupten? (Zusicherungs-Audit)

Jede Aussage in `README.md`, `CHANGELOG.md`, `docs/STATUS.md`, `docs/self-hosting.md`,
`docs/api.md`, `docs/mqtt.md`, `docs/mcp.md` und `docs/sicherheitsdurchsicht.md` wird
gegen den Code gehalten. Nicht gelesen — **geprüft**, mit einer Mutation oder einem
Aufruf.

Das Freigabe-Review hat gezeigt, dass das geht und dass es sich lohnt. Es hat aber nur
`CHANGELOG.md` und `STATUS.md` erfasst, und nur den Teil, der 0.2.2 betrifft.

**Besonders zu prüfen**, weil es dort weh tut:
- `docs/sicherheitsdurchsicht.md` — die Tabelle „keine Secrets, keine Roh-SQL, keine
  Route ohne Rechteprüfung" ist eine Reihe von Zusicherungen. Gilt jede noch?
- `docs/api.md` und `docs/mcp.md` — jeder dokumentierte Endpunkt und jedes Werkzeug wird
  aufgerufen und mit der Beschreibung verglichen. Für MCP gibt es dafür schon einen
  Wächter, für REST nicht.
- `docs/mqtt.md` — der Themenbaum wird gegen das gemessen, was der Dienst wirklich
  veröffentlicht.

**Fertig, wenn** jede widerlegte Aussage berichtigt ist **und** dort, wo es geht, ein
Wächtertest entsteht, der sie künftig hält. Eine Berichtigung ohne Wächter verfällt.

---

## Runde 3 — Die Regelkette als Ganzes

`decide()` hat sieben Regeln mit einer Vorrangordnung. Bisher wurde jede Regel einzeln
geprüft und jede Änderung als Diff. **Die Wechselwirkung wurde nie systematisch
angesehen** — und genau dort lagen beide Blocker des Ventilschutzes.

**Vorgehen:** Eine vollständige Zustandstabelle über die Eingangsgrössen von `Situation`
(Sensorzustand × Fenster × Übersteuerung × Mindestdauer × Schutzlauf × Messwert relativ
zur Hysterese × Betriebsart), als generierter Test. Für jede Kombination die erwartete
Entscheidung — **von Hand festgelegt, nicht aus dem Code abgeleitet**, sonst prüft die
Tabelle wieder nur sich selbst. Wo die Erwartung unklar ist, ist das ein Befund und keine
Lücke im Test.

Dazu die Frage, die noch niemand gestellt hat: **Gibt es eine Kombination, in der die
Anlage weder heizt noch heizen darf, obwohl sie müsste?** Frostschutz bei Sensorausfall
ist gebaut; was passiert bei Sensorausfall *während* eines Fenster-Wiederanlaufs
innerhalb einer Übersteuerung?

**Fertig, wenn** die Tabelle grün ist und jede Zelle, die überrascht hat, entweder
behoben oder als bewusste Entscheidung in `docs/offene-entscheidungen.md` steht.

---

## Runde 4 — Muster statt Einzelfälle

Für jeden Fehler dieser Fassung wird gefragt: **Wo noch?** Als Suche über das ganze
Projekt, nicht als Erinnerung.

| Muster | Anlass | Wie zu suchen |
|---|---|---|
| Netzwartezeit in offener DB-Transaktion | Meross **und** Open-Meteo | Jedes `await` innerhalb eines `session_scope` |
| Formular, dessen Felder nur ein Skript füllt | Malen-Editor, `mode_id` | Jedes versteckte Feld ohne `value` in einer Vorlage |
| Test, der Felder selbst setzt statt sie zu rendern | dreimal | Jeder POST-Test ohne vorheriges GET |
| Zeit in UTC angezeigt statt lokal | Kiosk-Uhr | Jede absolute Zeitausgabe in Vorlagen |
| `data-`-Attribut als Verdrahtungsmerker | Drag-and-Drop | Alle fünf Skripte |
| Wächtertest, der die falsche Ebene prüft | MCP-Werkzeugnamen | Jeder Test mit „Wächter" im Namen |

**Fertig, wenn** jede Zeile entweder „keine weiteren Fälle" oder eine Liste behobener
Fundstellen hat.

---

## Runde 5 — Was der Dienst tut, wenn niemand hinsieht (Optimierung)

Bisher wurde nie gemessen, nur gebaut. Diese Runde misst zuerst und ändert erst danach.

- **Abfragen je Schattenzyklus.** Der Zyklus läuft alle 60 Sekunden über alle Zonen.
  Zählen, wie viele Abfragen das je Zone sind, und ob es N+1-Muster gibt (Verdacht:
  Sollwerte, Zonenzustand, Geräte je Zone einzeln geladen).
- **Wachstum.** `measurement` und `shadow_decision` wachsen dauerhaft. Bei einem Zyklus
  pro Minute und zehn Zonen sind das 5,2 Millionen Zeilen im Jahr. Aufbewahrung ist
  gebaut — greift sie, und sind die Indizes so, dass das Löschen nicht selbst zum
  Problem wird?
- **Startzeit** des Containers, mit einer Datenbank realistischer Grösse.
- **Speicher** über 24 Stunden Laufzeit; die Publikationszustände und Caches wachsen
  potenziell unbegrenzt.

**Fertig, wenn** die Zahlen in `docs/STATUS.md` stehen — auch die unauffälligen. Geändert
wird nur, was die Messung als teuer ausweist. **Keine Optimierung ohne vorherige Zahl.**

---

## Runde 6 — Aufräumen (Refactoring)

Zuletzt, und ausdrücklich nach den Runden 1 bis 3: Ein Refactoring vor dem
Mutationstest ändert Code, dessen Prüfkraft unbekannt ist.

Kandidaten aus dieser Fassung:
- `services/shadow_run.py::_process_zone` ist lang geworden und mischt inzwischen
  Zustandsfortschreibung, Fälligkeitsrechnung und Entscheidung.
- `thermoctl/app.py` ist über 600 Zeilen und beherbergt Lifespan, Schattenzyklus,
  Fehlerbehandler und Zusammenbau.
- Fünf Skriptdateien im Frontend mit demselben `WeakSet`-Muster, fünfmal geschrieben.
- `web/daily_views.py` und `web/control_views.py` tragen Formularauswertung, die anderswo
  Domänensache wäre.

**Bedingung:** Jedes Refactoring wird durch den Mutationstest der betroffenen Datei
**vorher und nachher** abgesichert. Sinkt die Prüfkraft, war es keines.

---

## Runde 7 — Sicherheit, mit Abstand betrachtet

`docs/sicherheitsdurchsicht.md` stammt aus Teilprojekt 3 und ist mehrfach ergänzt worden.
Diese Runde prüft sie nicht fort, sondern **noch einmal von vorn**, mit dem, was seither
dazugekommen ist: Kiosk-Token, Meross-Zugangsdaten in der Umgebung, die gelockerte
CSRF-Behandlung, der MQTT-Weg zu einer fremden Cloud.

Konkrete Fragen, die aus dieser Fassung offen geblieben sind:
- Der Kiosk-Token steht im Cookie und in der Adresszeile. Wer das Tablett in der Hand
  hat, hat den Token. Reicht das Rechtemodell dafür?
- Meross-Zugangsdaten gehen stündlich an eine fremde Wolke. Was passiert, wenn die
  antwortet, aber falsch?
- Die CSRF-Lockerung erlaubt einer fremden Seite, einen Besucher abzumelden. Ist das
  weiterhin der richtige Tausch?

---

## Reihenfolge und Aufwand

| Tag | Runde | Warum dort |
|---|---|---|
| 1 | 1 (Stufen 1–2) | Alles Weitere hängt davon ab, ob die Tests etwas prüfen |
| 2 | 1 (Stufen 3–4) + 4 | Musterjagd läuft nebenher, sie braucht kein Werkzeug |
| 3 | 3 | Braucht ruhige Aufmerksamkeit, nicht viele Agents |
| 4 | 2 | Umfangreich, aber gut zerlegbar |
| 5 | 5 | Messen, dann entscheiden |
| 6 | 6 + 7 | Aufräumen zuletzt, Sicherheit mit frischem Blick |

**Verteilung** wie üblich: Aufträge an Agents, Kreuzreview über beide Anbieter, in der
Hauptsession nur Auth, Zusammenführen, Sicherheitsrelevantes und das Zerlegen selbst.
**Runde 3 ist die Ausnahme** — die Vorrangkette gehört in der Hauptsession gegengelesen,
weil dort zweimal etwas durchgegangen ist.

## Bewusst nicht in diesem Plan

- **Kein Typsystem-Ausbau, keine neue Architektur, kein Wechsel eines Bausteins.** Nichts
  davon hat einen Anlass in einem echten Fehler.
- **Keine Erhöhung der Abdeckungsschwelle.** Sie steht auf 100; höher geht nicht, und
  genau darin liegt ja das Problem.
- **Kein Umbau des Frontends.** Server-gerendert ohne Build-Schritt ist eine getroffene
  Entscheidung; die fünf Skripte aufzuräumen ist etwas anderes als sie zu ersetzen.
- **Keine neuen Funktionen.** Was auffällt, wird notiert, nicht gebaut.

## Was der Projektinhaber entscheiden sollte, bevor es losgeht

1. **Reicht Runde 1 allein?** Sie ist die aufwändigste und die mit dem grössten Ertrag.
   Wer nur einen Tag hat, macht sie und sonst nichts.
2. **Soll Runde 5 messen oder auch ändern?** Messen ist billig, Ändern nicht.
3. **Läuft das vor oder nach dem Schattenbetrieb an der echten Anlage?** Danach hätte es
   echte Daten und echte Lastzahlen — vorher wäre die Anlage sauberer, wenn sie startet.
