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
- [`docs/superpowers/specs/2026-08-29-teilprojekt-2-geraete-schattenbetrieb-design.md`](docs/superpowers/specs/2026-08-29-teilprojekt-2-geraete-schattenbetrieb-design.md)
  und der [zugehörige Plan](docs/superpowers/plans/2026-08-29-teilprojekt-2-geraete-schattenbetrieb.md)
  — Teilprojekt 2 (**umgesetzt**): Geräte-Anbindung im Schattenbetrieb. Enthält, warum der
  Trockenlauf eine abgesicherte Eigenschaft ist und keine Absichtserklärung.
- [`docs/superpowers/specs/2026-08-29-teilprojekt-3-konfigurationsoberflaeche-design.md`](docs/superpowers/specs/2026-08-29-teilprojekt-3-konfigurationsoberflaeche-design.md)
  und der [zugehörige Plan](docs/superpowers/plans/2026-08-29-teilprojekt-3-konfigurationsoberflaeche.md)
  — Teilprojekt 3 (**umgesetzt**): die Konfigurations-Oberfläche.
- [`docs/offene-entscheidungen.md`](docs/offene-entscheidungen.md) — Entscheidungen, die
  ohne Rückfrage getroffen wurden, mit Begründung und verworfenen Alternativen. **Wer eine
  davon anders will, ändert sie** — sie sind dokumentiert, nicht in Stein.
- [`docs/technisches_konzept.md`](docs/technisches_konzept.md) — **unverbindlich.** Fachliches
  Zielbild für Bedienung und Gerätetypen aus anderem Kontext. Setzt Home Assistant als
  Einstiegspunkt voraus, was der Rahmenentwurf ausdrücklich verworfen hat. Bei Widerspruch
  gilt der Rahmenentwurf. Übernommen wurden daraus vier Punkte, siehe TP1-Spezifikation.

**Rahmenentwurf und Bestandsaufnahme vor der ersten Änderung lesen.** Sie ersparen es, zwei
fremde Projekte erneut zu durchsuchen.

## Stand

**Der aktuelle Stand steht in [`docs/STATUS.md`](docs/STATUS.md). Diese Datei zuerst lesen.**
Sie sagt ausschliesslich, was *jetzt* gilt. Die Chronik — wie es dazu kam, welche Fehler
wie gefunden wurden, warum etwas so entschieden ist — steht in
[`docs/verlauf.md`](docs/verlauf.md). Die Trennung gibt es, seit `STATUS.md` auf über
tausend Zeilen gewachsen war und gleichzeitig aktuelle und längst widerlegte Aussagen
enthielt; ein Freigabe-Review konnte vier davon namentlich widerlegen. **Neues gehört
oben in `STATUS.md`, Abgelöstes wandert nach `verlauf.md` — nicht beides in dieselbe
Datei.**
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

**Aufgaben gehen an Agents, nicht an die Hauptsession. Das ist eine Bedingung, keine
Empfehlung.** Grobe Zielverteilung: rund 60 % an Codex, der Rest an Claude-Code-Agents.
Codex bekommt scharf umrissene, testbare Einheiten — Modelle, Migrationen, CRUD,
Templates, Workflows. In der Hauptsession bleiben **nur** diese vier Dinge:

1. Auth- und Berechtigungslogik,
2. das Zusammenführen von Zweigen und das Auflösen von Sammeldateien,
3. das Gegenlesen von Sicherheitsrelevantem (Grundsatz 7),
4. das Zerlegen der Arbeit in Aufträge und das Verteilen.

**Alles andere wird verteilt, auch wenn es schneller ginge, es selbst zu tippen.** Genau
dieser Gedanke ist der Grund, warum die Regel überhaupt hier steht: Er kommt bei jeder
einzelnen Aufgabe, er stimmt bei jeder einzelnen Aufgabe, und in Summe landet trotzdem
das ganze Teilprojekt in einer Sitzung. Zweimal ist das passiert — zuletzt bei der
Meross-Anbindung in 0.3.0, die vollständig in der Hauptsession entstand und deren
Kreuzreview deshalb nachträglich beauftragt werden musste.

Prüffrage vor jeder Änderung an einer Quelldatei: **Steht diese Datei unter den vier
Punkten oben?** Wenn nein, gehört die Änderung in einen Auftrag, nicht in die
Hauptsession. Eine Ausnahme wird angesagt und begründet, nicht stillschweigend genommen.

**Modelle:** Claude-Agents laufen auf Sonnet, Codex auf seinem Standardmodell. **Opus nur nach
ausdrücklicher Genehmigung des Nutzers** — vorher fragen, nicht danach.

**Review kreuzweise.** Wer implementiert hat, reviewt nicht. Codex-Arbeit prüft ein
Claude-Agent und umgekehrt. Sicherheitsrelevantes (Auth, Rechteprüfung, Regellogik) wird
zusätzlich in der Hauptsession gegengelesen — Grundsatz 7.

**Jedes Review führt die Testsuite selbst aus** und berichtet das Ergebnis — gegen beide
Datenbanken, dazu Ruff und mypy. Der Bericht des Umsetzenden ist eine unbelegte Behauptung,
bis sie jemand unabhängig nachvollzogen hat. (Früher galt hier das Gegenteil, aus
Sparsamkeit: Der Umsetzende habe die Tests ja schon ausgeführt. Die Folge war, dass niemand
unabhängig prüfte.)

**Ein Worktree je Aufgabe**, eigener Branch, Merge nach bestandenem Review.

**Jede abgeschlossene Änderung wird committet**, zusammen mit dem nachgezogenen `STATUS.md`
und den Haken im Implementierungsplan. Keine Sammelcommits über mehrere Aufgaben.

**Die CI muss grün sein**, bevor etwas nach `main` geht: Ruff, Typprüfung, Tests gegen SQLite
**und** MariaDB, Alembic vorwärts und rückwärts, Docker-Image-Build, Testabdeckung über der
Mindestschwelle.

**Zu jedem Endpunkt und jeder Funktion gehört ein Test.** Ein Test, der nur bestätigt, was
der Code ohnehin tut, zählt nicht — er hebt die Prozentzahl und suggeriert eine Sicherheit,
die es nicht gibt. Wo eine Zeile nur durch eine künstliche Konstruktion erreichbar wäre, ist
eine begründete Ausnahme mit `# pragma: no cover` die ehrlichere Antwort.

**Was Tests nicht leisten, muss jemand ansehen.** Zweimal sind grundlegende Fehler durch alle
Tests und Reviews gerutscht — eine fehlende Startseite, auf die Anmeldung und Navigation
zeigten, und eine Oberfläche ohne eingebundenes Stylesheet. Beide Male fand es der
Projektinhaber beim ersten Öffnen der Seite. Der Grund ist strukturell: Das Verfahren prüft,
ob das Gebaute dem Plan entspricht, nie ob der Plan vollständig war. `tests/test_rauchtest.py`
fängt inzwischen die häufigsten Fälle — jede Seite antwortet, Weiterleitungen führen
irgendwohin, jeder Verweis in einer Vorlage ist erreichbar. Ersetzt aber nicht, die Anwendung
nach einem sichtbaren Teilschritt einmal wirklich zu öffnen.

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
  Urheberschaft. **Das gilt für jeden git-Schreibvorgang**, auch `git merge main` — wer
  einen Worktree auf den neuesten Stand bringen will, tut das vorher aus der Hauptsession.
  Ein Auftrag, der „hol dir main" verlangt, scheitert an `ORIG_HEAD.lock`.
- **`pkill -f <muster>` trifft auch laufende Agents.** Deren Kommandozeile enthält den
  ganzen Auftragstext; ein Muster wie `thermoctl.cli` steht darin und beendet den Agenten
  mitten in der Arbeit. Prozesse gezielt über ihre PID beenden.
- **Bei parallelen Aufgaben bekommt jede eine eigene Testdatenbank.** Sonst legen mehrere
  Läufe dasselbe Schema an und räumen es einander weg; die Fehlschläge sind dann zufällig.
- **Die Testsuite liest `THERMOCTL_TEST_DATABASE_URL`, nicht `THERMOCTL_DATABASE_URL`.**
  Wer die zweite Variable setzt, läuft gegen SQLite und merkt nichts davon — der Lauf ist
  grün und beweist nichts. Genau so ist eine ganze Sitzung lang „gegen beide Datenbanken
  geprüft" berichtet worden, während jeder dieser Läufe SQLite war; aufgefallen ist es
  einem Agenten, nicht der Hauptsession. Der MariaDB-Lauf lautet:
  ```
  THERMOCTL_TEST_DATABASE_URL="mysql+pymysql://root:pruefen@127.0.0.1:3306/<eigene_db>" \
    .venv/bin/python -m pytest -q
  ```
  Die CI benutzt die richtige Variable und war nie betroffen.
- **Migrationen vertragen keine echte Parallelität.** Zweigen zwei Aufgaben vom selben Stand
  ab, tragen beide dieselbe Vorgängerrevision, und die Historie hat zwei Köpfe. Die
  Hauptsession ordnet sie beim Zusammenführen; die Agents lassen `down_revision` in Ruhe.
- **Sammeldateien nach jedem Merge von Hand prüfen.** `tests/hilfen.py` und
  `db/models/__init__.py` werden von jeder Aufgabe ergänzt und kollidieren zuverlässig. Eine
  automatisch aufgelöste Fassung kann doppelte Definitionen enthalten und trotzdem grüne
  Tests liefern.
- **`cosmic-ray exec` niemals im Vordergrund unter einem Werkzeug-Timeout.** Cosmic Ray
  schreibt jede Mutation in die Datei und stellt sie nur über ein `finally` wieder her.
  Ein harter Abbruch überspringt das und hinterlässt **mutierten Produktionscode ohne
  Fehlermeldung**; ein Folgelauf misst dann gegen die kaputte Datei und liefert ein
  vollständiges, aber ungültiges Ergebnis. In der Regelkette ist das der schlimmste
  denkbare Ausgang. Im Hintergrund starten und die Prüfsumme der Datei vor und nach dem
  Lauf vergleichen.
- **`pgrep -f <muster>` findet auch die eigene Warteschleife.** Eine Schleife wie
  `while pgrep -f "cosmic-ray exec"; do sleep 30; done` trägt das Muster in ihrer eigenen
  Kommandozeile, findet sich selbst und wartet ewig. Dasselbe Problem wie bei `pkill -f`,
  nur stiller. Auf den Programmpfad prüfen (`pgrep -fl "bin/cosmic-ray"`) oder den
  Prozess über seine PID verfolgen.
- **Das Projekt liegt in einem iCloud-Ordner.** `~/Documents` ist ein Symlink nach iCloud
  Drive, und der Dienst legt bei schnellen Schreibvorgängen Konfliktkopien der Form
  `deviation 2.py` an — git-Operationen und Mutationsläufe erzeugen sie zuverlässig. Sie
  sind alte Stände. `.gitignore` hält sie aus dem Repository, `--ignore-glob` in
  `pyproject.toml` aus dem Testlauf, und `.venv` liegt als `.venv.tmp` ausserhalb des
  Abgleichs. Wenn Tests an Zusicherungen scheitern, die vor Wochen gestimmt haben:
  zuerst `find . -name "* 2.*"` prüfen, bevor man den Fehler im Code sucht.
- **Agents melden Blocker, statt zu raten** — das ist die wichtigste Regel im Auftragstext.
  Fast alle Blocker in Teilprojekt 1 waren Fehler im Plan, nicht der Umsetzung. Ein Agent,
  der einen vorgegebenen Test „passend macht", verdeckt sie.
