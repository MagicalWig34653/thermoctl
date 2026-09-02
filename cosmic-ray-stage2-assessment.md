# Mutationstest Runde 1, Stufe 2 — Zwischenstand

Werkzeug: Cosmic Ray 8.7.0. Die Konfiguration steht in
`cosmic-ray-schedule.toml`; die Sitzungsdatenbank liegt nur unter `/tmp`.

## Ausgangslauf

Der erste Pilotlauf nur mit `tests/test_domain_schedule.py` war als Messung
ungeeignet: weitere direkte Schedule-Tests liegen in `test_schedule_views.py`,
`test_domain_race.py` und `test_remote_control.py`. Der endgültige Testbefehl
enthält deshalb alle vier Dateien (vor den Ergänzungen 109 Tests).

Nach dem aus Stufe 1 übernommenen Operatorfilter ergab der belastbare
Ausgangslauf:

| Datei | Mutanten | überlebt | Anteil |
|---|---:|---:|---:|
| `domain/schedule.py` | 1.853 | 237 | 12,79 % |

765 der 1.853 Einträge wurden vom unveränderten Stufe-1-Filter als
Kreuztypoperatoren abgelegt. Der Filter betrifft Typannotationen,
SQLAlchemy-Ausdrucksoperatoren und nicht benachbarte Vergleichsoperatoren.

## Durch Tests erschlagen — 99

- Unveränderlichkeit und dokumentierte Defaults von `Setpoint` und
  `DaySegment`.
- Die reale Wochenlänge sowie Uhrzeitparsing mit nicht trivialer Stunde und
  Minute.
- Wochensegmentierung außerhalb Montags einschließlich Eigentümerschaft des
  Umschaltpunkts.
- Aktueller und nächster Punkt an einem Freitag sowie der Sprung über das
  Wochenmaximum.
- Malen eines Intervalls an einem Mittwoch mit nicht vollen Stunden.
- Kopieren zwischen nicht benachbarten Tagen bei vorhandenen Punkten vor und
  nach dem Zieltag.
- Kalenderhilfen für einen Donnerstag sowie die kanonische Reduktion eines
  Wochenrings.
- Isolierte ungültige Wochentage und Intervallgrenzen.

Der Nachlauf meldet 138 Überlebende, also **7,45 % aller 1.853 Mutanten**.

## Noch offen — 138

Dieser Stand ist absichtlich nicht als fertige Bewertung ausgegeben. Unter den
138 Mutanten befinden sich sowohl begründbar unerhebliche als auch fachlich
relevante Mutanten:

- unerheblich: Kreuztypmutationen in SQLAlchemy-Ausdrücken an den Zeilen 157,
  161, 171, 431–433 und 784 sowie die äquivalente Lockerung von
  Keyword-only-Parametern an den Zeilen 180, 266, 336, 443, 491, 546, 581,
  607 und 686;
- noch als echte Testlücken zu behandeln: Grenzwerte und Arithmetik beim
  Malen (Zeilen 195–234), Kopieren (274–313), Segmentieren (377–411),
  Punktanlage/-verschiebung (451–509) sowie einzelne Grenzfälle bei
  Sollwertauflösung (769, 802, 807, 826–827);
- noch auf Äquivalenz zu prüfen: gekoppelte Intervallgrenzen, bei denen eine
  Mutation einer Teilbedingung weiterhin zwingend von einer anderen
  Teilbedingung zurückgewiesen wird.

Stufe 2 ist damit **nicht fertig**. Stufen 3 und 4 wurden wegen der verbindlichen
Reihenfolge noch nicht begonnen. Es wurde keine Produktionslogik geändert und
kein fachlicher Fehler nachgewiesen.
