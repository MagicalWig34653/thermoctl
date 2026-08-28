# CLAUDE.md

Arbeitsanweisung für Claude Code in diesem Repository.

## Was das hier ist

`thermoctl` ist eine eigenständige, self-hostbare Heizungssteuerung: sensorbasierte
Raumregelung mit Zeitplänen, konfiguriert über eine Weboberfläche, ansprechbar zusätzlich
über REST-API und MCP-Server.

**Das Projekt ist ein Neubau, kein Refactoring.** Es ersetzt vier gewachsene Python-Skripte
und eine PHP-Oberfläche aus zwei anderen Projekten. Der Stand der Planung ist:

- [`docs/superpowers/specs/2026-08-28-thermoctl-neubau-design.md`](docs/superpowers/specs/2026-08-28-thermoctl-neubau-design.md)
  — Rahmenentwurf: Ziele, getroffene Entscheidungen samt Begründung, verworfene Alternativen,
  Zerlegung in fünf Teilprojekte.
- [`docs/bestandsaufnahme-altsystem.md`](docs/bestandsaufnahme-altsystem.md) — das
  abzulösende System: Services, vollständiges Ist-Schema, MQTT-Topic-Vertrag, bekannte Defekte.
- [`docs/superpowers/specs/2026-08-28-teilprojekt-1-fundament-design.md`](docs/superpowers/specs/2026-08-28-teilprojekt-1-fundament-design.md)
  — Spezifikation von Teilprojekt 1 (**umgesetzt**): Datenmodell, Auth- und Rechtemodell,
  Konfiguration, Logging, Container, CI.
- [`docs/superpowers/plans/2026-08-28-teilprojekt-1-fundament.md`](docs/superpowers/plans/2026-08-28-teilprojekt-1-fundament.md)
  — Implementierungsplan dazu, 22 Aufgaben. Enthält in den *Global Constraints* die
  Eigenheiten, die beide Datenbanken unterscheiden — wer am Schema arbeitet, liest sie zuerst.
- [`docs/technisches_konzept.md`](docs/technisches_konzept.md) — **unverbindlich.** Fachliches
  Zielbild für Bedienung und Gerätetypen aus anderem Kontext. Setzt Home Assistant als
  Einstiegspunkt voraus, was der Rahmenentwurf ausdrücklich verworfen hat. Bei Widerspruch
  gilt der Rahmenentwurf. Übernommen wurden daraus vier Punkte, siehe TP1-Spezifikation.

**Rahmenentwurf und Bestandsaufnahme vor der ersten Änderung lesen.** Sie ersparen es, zwei
fremde Projekte erneut zu durchsuchen.

## Stand

**Der aktuelle Stand steht in [`docs/STATUS.md`](docs/STATUS.md). Diese Datei zuerst lesen.**
Sie sagt, welches Teilprojekt läuft, was zuletzt fertig wurde und was als Nächstes ansteht.

Git allein reicht dafür nicht: Commits sagen, was getan wurde, aber nicht, was als Nächstes
dran ist und warum. `STATUS.md` wird deshalb **im selben Commit** wie die Änderung
nachgezogen, die sie beschreibt — nicht hinterher, sonst verfällt sie.

## Technischer Rahmen (entschieden, nicht neu verhandeln)

| | |
|---|---|
| Backend | Python, FastAPI |
| Persistenz | SQLAlchemy + Alembic |
| Datenbank | Nutzerwahl beim Setup: SQLite (Standard) oder MariaDB |
| Frontend | Jinja-Templates, HTMX, Bootstrap — server-gerendert, kein Build-Schritt, kein npm |
| Schnittstellen | Drei dünne Adapter über gemeinsamer Domänenlogik: HTMX-Views, REST-API, MCP-Server |
| Betrieb | Eigener Docker-Container |
| Home Assistant | Optionale Integration per MQTT, **keine** Voraussetzung |

Begründungen und verworfene Alternativen stehen im Rahmenentwurf. Wenn eine dieser
Entscheidungen im Weg steht, das ansprechen — nicht stillschweigend anders bauen.

## Grundsätze

1. **Nichts hart verdrahtet.** Keine Geräte-IDs, Raumnamen, Broker-Adressen oder Zugangsdaten
   im Quelltext. Alles kommt aus Konfiguration oder Datenbank. Das ist der Hauptgrund für
   den Neubau — nicht ein Detail.
2. **Keine Secrets im Repo.** Auch nicht als Fallback-Wert, auch nicht in Beispielen, auch
   nicht zu Debug-Zwecken in Logs. Das Repo soll veröffentlichbar sein. Zugangsdaten des
   Altsystems wurden bewusst nicht übernommen.
3. **Datenbankagnostisch.** Kein `ENUM`, kein `SET`, keine JSON-Spalten als Datenmodell,
   keine datenbankspezifischen Funktionen. Jede Schemaänderung als Alembic-Migration.
4. **Authentifizierung ist verpflichtend.** Im Altsystem war fehlende Auth eine akzeptierte
   Heimnetz-Eigenschaft. Hier ausdrücklich nicht mehr.
5. **Debuggbarkeit ist ein Ziel, kein Nebenprodukt.** Strukturiertes Logging, nachvollziehbare
   Regelentscheidungen (warum wurde geschaltet oder nicht), aussagekräftige Fehlermeldungen.
6. **Domänenlogik gehört nicht in Adapter.** Eine Regel wird einmal implementiert und von
   UI, API und MCP gleichermaßen benutzt.
7. **Sicherheitsrelevant, weil physisch.** Der Dienst steuert eine echte Heizung. Fehler in
   der Regellogik haben reale Folgen. Änderungen daran besonders sorgfältig prüfen.

## Beim Umstieg zu beachten

Der Wechsel läuft als Parallelbetrieb: `thermoctl` entscheidet erst im Schattenbetrieb ohne
zu schalten, wird gegen das Altsystem verglichen, und erst dann scharf geschaltet — mit dem
Altsystem als Rückfallebene. Solange das nicht abgeschlossen ist, darf nichts am Altsystem
abgeschaltet oder gelöscht werden.

Zwei Defekte des Altsystems ausdrücklich **nicht** übernehmen:
- Die Regelschleife dort hat **keine Hysterese** (`if ist < soll: an, sonst aus`) und schaltet
  am Sollwert in jedem Zyklus um. `thermoctl` braucht Hysterese und Mindestschaltdauer.
- Zeitpläne liegen dort als positionell interpretierter JSON-Blob mit acht Slots. Hier werden
  sie als echte Zeilen modelliert.

## Arbeitsweise

Verbindlich für alle Sessions.

**Aufgaben gehen an Agents, nicht an die Hauptsession.** Grobe Zielverteilung: rund 60 % an
Codex, der Rest an Claude-Code-Agents. Codex bekommt scharf umrissene, testbare Einheiten —
Modelle, Migrationen, CRUD, Templates, Workflows. In der Hauptsession bleiben Auth- und
Berechtigungslogik sowie das Zusammenführen.

**Modelle:** Claude-Agents laufen auf Sonnet, Codex auf seinem Standardmodell. **Opus nur nach
ausdrücklicher Genehmigung des Nutzers** — vorher fragen, nicht danach.

**Review kreuzweise.** Wer implementiert hat, reviewt nicht. Codex-Arbeit prüft ein
Claude-Agent und umgekehrt. Sicherheitsrelevantes (Auth, Rechteprüfung, Regellogik) wird
zusätzlich in der Hauptsession gegengelesen — Grundsatz 7.

**Ein Worktree je Aufgabe**, eigener Branch, Merge nach bestandenem Review.

**Jede abgeschlossene Änderung wird committet**, zusammen mit dem nachgezogenen `STATUS.md`
und den Haken im Implementierungsplan. Keine Sammelcommits über mehrere Aufgaben.

**Die CI muss grün sein**, bevor etwas nach `main` geht: Ruff, Typprüfung, Tests gegen SQLite
**und** MariaDB, Alembic vorwärts und rückwärts, Docker-Image-Build.

### Praktisches zur Agentenarbeit

Aus der Umsetzung von Teilprojekt 1, damit es niemand erneut herausfinden muss:

- **Codex startet man direkt, nicht über den Rescue-Weiterleiter** — der startet ihn im
  aufrufenden Repo, wodurch ein Schwester-Worktree nicht beschreibbar ist:
  ```
  codex exec -C "<worktree>" --add-dir "<hauptrepo>" -s workspace-write     -c sandbox_workspace_write.network_access=true < /dev/null
  ```
  Netzzugriff muss ausdrücklich an, sonst scheitert `pip install`; `< /dev/null` ist Pflicht,
  sonst wartet Codex ohne Terminal endlos auf eine Eingabe.
- **Codex kann im Worktree nicht committen** (seine Sandbox schützt `.git`, und der
  Worktree-Index liegt im Hauptrepo). Den Commit führt die Hauptsession aus und vermerkt die
  Urheberschaft.
- **Bei parallelen Aufgaben bekommt jede eine eigene Testdatenbank.** Sonst legen mehrere
  Läufe dasselbe Schema an und räumen es einander weg; die Fehlschläge sind dann zufällig.
- **Migrationen vertragen keine echte Parallelität.** Zweigen zwei Aufgaben vom selben Stand
  ab, tragen beide dieselbe Vorgängerrevision, und die Historie hat zwei Köpfe. Die
  Hauptsession ordnet sie beim Zusammenführen; die Agents lassen `down_revision` in Ruhe.
- **Sammeldateien nach jedem Merge von Hand prüfen.** `tests/hilfen.py` und
  `db/models/__init__.py` werden von jeder Aufgabe ergänzt und kollidieren zuverlässig. Eine
  automatisch aufgelöste Fassung kann doppelte Definitionen enthalten und trotzdem grüne
  Tests liefern.
- **Agents melden Blocker, statt zu raten** — das ist die wichtigste Regel im Auftragstext.
  Fast alle Blocker in Teilprojekt 1 waren Fehler im Plan, nicht der Umsetzung. Ein Agent,
  der einen vorgegebenen Test „passend macht", verdeckt sie.
