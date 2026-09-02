# Komplettreview Runde 5: Messbericht

Gemessen am 2. September 2026 im Worktree `messen`. Zeiten sind Wandzeiten auf dem
lokalen Entwicklungsrechner (Apple Silicon, Docker Desktop 29.5.2, Python 3.14.6).
Alle Laufzeit-, Größen- und Nutzenmessungen in diesem Bericht sind SQLite-Messungen.
Für MariaDB wurde die Migration in beide Richtungen geprüft, aber keine eigene
Nutzenmessung des Index durchgeführt.

## Abfragen je Schattenzyklus

Das Werkzeug erzeugt 1, 5 und 20 Zonen mit Zustand, Frostsollwert und einer definierten
Schattenhistorie, zählt über SQLAlchemys `before_cursor_execute` jedes tatsächlich an
SQLite gesendete Statement und misst `services.shadow_run.cycle()` bis einschließlich
`flush()`.

| Zonen | Historie je Zone | SELECT | alle Statements | Laufzeit |
|---:|---:|---:|---:|---:|
| 1 | 30 | 12 | 16 | 5,56 ms |
| 5 | 30 | 52 | 72 | 8,80 ms |
| 20 | 30 | 202 | 282 | 25,99 ms |

Die Anzahl wächst linear: insgesamt `14 × Zonen + 2`, bei SELECTs `10 × Zonen + 2`.
Gemessene N+1-Gruppen sind je Zone zwei Sollwertabfragen sowie je eine Abfrage für
Zustand, Sensorstatus, Frostmodus, Übersteuerung, Zeitplan und Schattenhistorie. Bei
kleiner Historie sind 25,99 ms für 20 Zonen kein Laufzeitproblem bei 60 s Zykluszeit.

Die Vorher-Baseline wird nach `Base.metadata.create_all()` ausdrücklich hergestellt:
`--baseline-vor-indexmigration` droppt den neuen Composite-Index und stellt den vor der
Migration vorhandenen Einzelindex auf `decided_at` her. Danach liest das Werkzeug den
tatsächlichen Indexbestand aus `sqlite_master`, gibt ihn als `shadow_indexes` aus und
bricht ab, wenn er nicht exakt der erwarteten Variante entspricht. Gemessen wurden damit
die frühere Produktionsstruktur mit ausschließlich `ix_shadow_decision_decided_at` und
die endgültige Struktur mit ausschließlich `ix_shadow_decision_zone_decided_id`.

| Zonen | Historie je Zone | Baseline: `(decided_at)` | Final: `(zone_id, decided_at, id)` | weniger Zeit |
|---:|---:|---:|---:|---:|
| 1 | 43.200 | 39,56 ms | 38,24 ms | 3,4 % |
| 5 | 43.200 | 342,27 ms | 205,79 ms | 39,9 % |
| 20 | 43.200 | 3.655,96 ms | 890,69 ms | 75,6 % |

Bei 20 Zonen spart der Composite-Index damit 2.765,27 ms beziehungsweise 75,6 Prozent.
Die zuerst berichteten 79,4 Prozent entstanden vor dem Einbau des Index ins Modell und
wurden nicht mit dem Endzustand gegengeprüft; diese Zahl war deshalb nicht belastbar.
Die verbleibenden 890,69 ms entstehen vor allem dadurch, dass `_previous_state()`
weiterhin alle 43.200 Zeilen je Zone materialisiert. Eine Änderung daran würde
`services/shadow_run.py` berühren und ist in dieser Runde ausdrücklich gesperrt; das
bleibt ein Befund.

Wiederholung:

```console
.venv/bin/python -m tools.runde5_messen queries --history 30
.venv/bin/python -m tools.runde5_messen queries --history 43200
.venv/bin/python -m tools.runde5_messen queries --history 43200 \
  --baseline-vor-indexmigration
```

## Wachstum und Aufbewahrung

Die Zeilenzahl ist gerechnet, nicht gemessen: `365 × 24 × 60 × 10 = 5.256.000` Zeilen
je Tabelle und Jahr, falls jede Zone in jedem Minutenzyklus genau eine Zeile erzeugt.
Die Größe je Zeile wurde dagegen gemessen: je Tabelle 100.000 repräsentative Zeilen in
einer eigenen SQLite-Datei einschließlich der damaligen Produktionsindizes, anschließend
Dateigrößendifferenz geteilt durch 100.000.

| Tabelle | gemessene Byte/Zeile | Hochrechnung für 5.256.000 Zeilen |
|---|---:|---:|
| `measurement` | 94,49 | 496.664.248 Byte (473,66 MiB) |
| `shadow_decision` | 161,75 | 850.163.466 Byte (810,78 MiB) |
| `device_command` | 134,39 | 706.352.579 Byte (673,63 MiB) |

Der separate Aufbau einer vollständig migrierten Datenbank mit exakt 5.256.000
Messwerten dauerte 6,86 s und ergab 510.099.456 Byte (486,47 MiB). Das bestätigt die
Stichprobe einschließlich übriger Schema-Grundkosten.

`delete_old_measurements()` greift: Bei 30 Tagen Aufbewahrung löschte ein echter Lauf
4.838.410 Zeilen und ließ 417.590 stehen. Er dauerte 11,80 s. `EXPLAIN QUERY PLAN`
meldete `SCAN measurement`, weil der vorhandene Index mit `device_id, capability_id`
beginnt. Ein probeweise angelegter Index nur auf `measured_at` kostete 2,82 s Aufbau,
wurde wegen `ORDER BY id` ebenfalls nicht gewählt und verkürzte den Lauf nur von 11,80
auf 11,58 s (1,8 Prozent). Deshalb wurde dieser Index nicht übernommen.

Zum Zeitpunkt dieser Messrunde griff die Aufbewahrung ausschließlich für `measurement`;
die daraus entstandene offene Entscheidung ist inzwischen getroffen und umgesetzt:
`shadow_decision_retention_days` hält standardmäßig 365 Tage, und die tägliche Bereinigung
löscht ältere Schattenentscheidungen in Blöcken. Der dafür ergänzte Index
`ix_shadow_decision_retention (decided_at, id)` gehört nicht zu den unten dokumentierten
historischen Indexvergleichen. `device_command` hat weiterhin bewusst keine Aufbewahrung.
Die 674-MiB-Jahreszahl ist eine Obergrenze unter der
ausdrücklich genannten Annahme eines Befehls je Zone und Minute; im vorhandenen Code
unterdrückt `PublicationState` unveränderte, bereits erfolgreiche Befehle.

Nach der Umsetzung wurde die erste Schattenbereinigung mit genau 5.256.000 vollständig
fälligen Zeilen und der Blockgröße 5000 auf derselben SQLite-Umgebung gemessen. Sie löschte
alle Zeilen in 11,12 s; `EXPLAIN QUERY PLAN` meldete dabei
`SEARCH shadow_decision USING COVERING INDEX ix_shadow_decision_retention (decided_at<?)`.
Ein normaler täglicher Folgelauf hat bei zehn Zonen nur rund 14.400 statt einer ganzen
Jahresmenge zu entfernen.

Wiederholung (Zielverzeichnis muss leer oder neu sein):

```console
.venv/bin/python -m tools.runde5_messen growth --sample-rows 100000 \
  --directory /tmp/thermoctl-runde5-neu
THERMOCTL_DATABASE_URL=sqlite:////tmp/thermoctl-runde5-jahr.sqlite \
  THERMOCTL_SECRET_KEY=01234567890123456789012345678901 \
  .venv/bin/alembic upgrade head
.venv/bin/python -m tools.runde5_messen build-year \
  --database /tmp/thermoctl-runde5-jahr.sqlite --zones 10
```

## Containerstart

Das Docker-Abbild wurde aus `docker/Dockerfile` gebaut. Die 510.099.456-Byte-Datenbank
wurde in `/data` eines Containers kopiert. Gemessen wurde fünfmal von `docker start`
bis zur ersten HTTP-200-Antwort von `/healthz`: 1,889/1,952/1,882/1,905/1,871 s;
Median 1,889 s, Minimum 1,871 s, Maximum 1,952 s. Das ist kein Problem.

## Speicher und Publikationszustand

Ein beschleunigter 24-Stunden-Lauf aktualisierte 1.440-mal dieselben zehn Kennungen in
den drei mengenabhängigen Wörterbüchern von `PublicationState`. Danach enthielten
`controller_values`, `valve_commands` und `switch_commands` jeweils exakt zehn Einträge;
`tracemalloc` maß zusammen 2.720 zusätzlich belegte Byte. Bei stabilen Geräten wachsen
diese Caches also nicht mit der Laufzeit.

Der Gegenversuch mit 14.400 stets neuen Kennungen (zehn ersetzte Geräte pro Minute)
ließ alle drei Wörterbücher auf je 14.400 Einträge wachsen und belegte 3.948.792 Byte,
274,22 Byte je Kennung über alle drei Caches. `registered` wird beim Löschen einer Zone
bereinigt; die drei geräte-/kanalbezogenen Wörterbücher werden nicht bereinigt. Das ist
ein begrenztes Konfigurationswechselproblem, kein Wachstum durch normale 24-Stunden-
Laufzeit. Wegen der künstlich extremen Wechselrate und nur 3,77 MiB wurde nichts geändert.

```console
.venv/bin/python -m tools.runde5_messen cache --cycles 1440 --devices-per-cycle 10
```

## Änderungen

Aufgrund der gemessenen 3,656 s pro 20-Zonen-Zyklus wurde der Index
`ix_shadow_decision_zone_decided_id` ergänzt. Die Vergleichsmessung weist 75,6 Prozent
weniger Zykluszeit auf SQLite nach.

Die erschöpfende Suche nach `ShadowDecision`, `shadow_decision` und `decided_at` über
Produktionscode, Tests, MCP-, Web- und REST-Schichten ergab folgende lesende Stellen:
`shadow_run.py` liest bisherigen Zustand und letzten regulären Heizzeitpunkt;
`publishing.py` liest letzte Entscheidung, letzten Wechsel und Heizstatus;
`start_views.py`, `control_views.py` und `kiosk_views.py` lesen für sichtbare Zonen;
`mcp/server.py` liest genau eine Zone; `domain/statistics.py` filtert Zeitraum und eine
`zone_id.in_(zone_ids)`-Liste; `domain/zones.py` zählt Abhängigkeiten einer konkreten
Zone. Jede damals vorhandene Abfrage, die nach `decided_at` filterte oder sortierte,
schränkte zugleich über `zone_id` ein. Einige Tests zählten oder lasen die ganze Tabelle
ohne Zeitfilter; dafür war der Einzelindex ebenfalls nutzlos. Direkte REST-Abfragen auf
die Tabelle gab es nicht. Deshalb wurde `ix_shadow_decision_decided_at` aus Modell und
bestehender, uncommitteter Migration entfernt; `downgrade()` stellt ihn wieder her. Die
später beschlossene globale Aufbewahrung ist eine neue Abfrage ohne Zoneneinschränkung
und begründet den neuen Retention-Index auf `(decided_at, id)`.

Der Schreibaufwand wurde isoliert mit 500.000 Zeilen und sieben Wiederholungen gemessen.
Der Median lag mit dem damals finalen Composite-Index allein bei 313,32 ms, mit zusätzlichem
alten Einzelindex bei 365,20 ms: ohne den ungenutzten Index 14,2 Prozent weniger
INSERT-Zeit. Das sind auf 20 Zeilen eines normalen Schattenzyklus umgerechnet nur rund
0,002 ms; für den gesamten Zyklus wird deshalb keine merkliche Beschleunigung behauptet.

Das Messwerkzeug ist nicht stillschweigend aus der Abdeckung gefallen. Ein knapper Test
ruft alle Unterbefehle (`queries` in beiden Indexvarianten, `inserts`, `cache`, `growth`
und `build-year`) mit winzigen Parametern auf und prüft parsebares JSON sowie den
tatsächlichen Indexbestand. Diese Variante wurde statt einer Ausnahme gewählt, weil das
dauerhaft im Repository bleibende Werkzeug schnell ausführbar ist und seine kritische
Baseline-Annahme direkt geprüft werden kann.

Die Migration entfernt beim erneuten MariaDB-Upgrade auch den für das vorherige
Downgrade notwendigen Hilfsindex `ix_shadow_decision_zone_id_fk`, sobald der Composite-
Index den Fremdschlüssel wieder abdeckt.

## Prüfung

- `.venv/bin/ruff check .`: `All checks passed!`
- `.venv/bin/mypy thermoctl`: `Success: no issues found in 107 source files`
- SQLite, `COLUMNS=200 .venv/bin/python -m pytest -p no:cacheprovider`: Exit 0,
  `FAILED=0`, `TOTAL 6687 0 100%`; wörtliche Schlusszeile:
  `1618 passed, 9 warnings in 43.67s`.
- MariaDB, mit der vorgegebenen `THERMOCTL_TEST_DATABASE_URL`:
  Exit 0, `FAILED=0`, `TOTAL 6687 0 100%`; wörtliche Schlusszeile:
  `1617 passed, 1 skipped, 10 warnings in 68.14s (0:01:08)`.

Die geänderte Migration lief mit
`THERMOCTL_SECRET_KEY=01234567890123456789012345678901` auf beiden Datenbanken als
`upgrade head`, `downgrade -1`, `upgrade head`; alle sechs Befehle hatten Exit 0. Die
wörtlichen Schlusszeilen waren jeweils entweder
`Running upgrade 3a3e44c560fb -> e8c21f4a9d70, Index fuer zonenweise Abfragen der Schattenhistorie.`
oder
`Running downgrade e8c21f4a9d70 -> 3a3e44c560fb, Index fuer zonenweise Abfragen der Schattenhistorie.`
SQLite hatte danach ausschließlich `ix_shadow_decision_zone_decided_id`. Auf MariaDB
lagen beim Downgrade der wiederhergestellte Einzelindex und der notwendige
`ix_shadow_decision_zone_id_fk`; nach dem zweiten Upgrade blieben ausschließlich
Composite-Index und Primärschlüssel. Das beschreibt bewusst den Stand jener
Indexmigration; das heutige Schema ergänzt daneben `ix_shadow_decision_retention` für
die globale Löschabfrage.
