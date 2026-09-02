# Mutationstest Runde 1, Stufe 2 — Abschlussbewertung

Werkzeug: Cosmic Ray 8.7.0. Die Konfiguration steht in
`cosmic-ray-schedule.toml`; die Sitzungsdatenbank liegt nur unter `/tmp`.

## Messung

Der Testbefehl umfasst `test_domain_schedule.py`, `test_schedule_views.py`,
`test_domain_race.py` und `test_remote_control.py`. Das ist nötig, weil alle vier
Dateien die Zeitplan-Domäne direkt prüfen.

| Stand | Mutanten | erschlagen | überlebt |
|---|---:|---:|---:|
| belastbarer Ausgangslauf | 1.853 | 851 | 237 (12,79 %) |
| Zwischenstand des ersten Auftrags | 1.853 | 950 | 138 (7,45 %) |
| Abschlusslauf | 1.853 | 1.044 | 44 (2,37 %) |

765 der 1.853 Einträge sind wie in Stufe 1 als Kreuztypoperatoren gefiltert.
Von den zuletzt 138 Überlebenden erschlagen neue fachliche Tests 94. Die
verbleibenden 44 sind unten einzeln nach Mutation und gemeinsamer Begründung
abgelegt. Damit bleiben **null fachlich relevante Überlebende**.

## Durch die abschließenden Tests erschlagen — 94

- Wochenring-Normalisierung mit einem, zwei und drei Punkten sowie die echte
  Tagesumrechnung eines Punkts genau an einer Tagesgrenze.
- Kalenderarithmetik für jeden Wochentag und mehrere Uhrzeiten, einschließlich
  der nicht durch bitweise Operatoren nachbildbaren Kombinationen.
- Uhrzeitbeschriftung bei Minute 1, 60, 1.439 und 1.440 sowie Eingabe 23:59.
- Malen der kleinsten Intervalle an beiden Tagesrändern, Übermalen vorhandener
  Start-, Innen- und Endpunkte, Erhalt des lokalen Endmodus gegenüber dem
  Wochenendmodus und der Wochenringsprung Sonntag/Montag.
- Kopieren mit einem Schaltpunkt bei Minute 1, über den Wochenring, auf
  aufeinanderfolgende Tage, auf gerade und ungerade Tage, mit einem Schaltpunkt
  bei Minute 1.439 sowie mit einem nur fortgetragenen Modus am Folgetag.
- Segmentierung bei Mitternacht und Minute 1 sowie an einem Donnerstag.
- Kollisionsprüfung mit abweichender Zone, abweichendem Tag und abweichender
  Minute; Anlage und Verschiebung an allen vier gültigen und ungültigen
  Kalendergrenzen.
- Der nächste Punkt bei einem exakt aktuellen sowie mehreren späteren Punkten.
- Konfigurierter Frostsollwert ungleich 16 °C samt Modusidentität, Ablauf einer
  Übersteuerung exakt an ihrer Endzeit, fehlender Zonen-Sollwert eines
  Zeitplanmodus und Begründung eines Schaltpunkts zu einer nicht vollen Stunde.

## Als gleichwertig abgelegt — 44

Jede Zeile nennt alle zugehörigen Überlebenden; kein Überlebender ist in mehr
als einer Zeile bewertet.

- **Zeilen 157 (4), 161 (4), 171 (5):** SQLAlchemy überlädt `&` für boolesche
  SQL-Ausdrücke. Die Mutationen nach `+`, `|`, `/`, `//` oder `*` wechseln in
  eine andere SQLAlchemy-Operatorfamilie. SQLite und MariaDB normalisieren die
  entstehenden booleschen Ausdrücke hier zur selben Treffermenge. Es sind
  Kreuztypmutationen des Werkzeugs, keine alternative fachliche Bedingung.
- **Zeilen 180, 266, 336, 443, 491, 546, 581, 607 und 686 (je 1):** Cosmic Ray
  ersetzt den Signaturtrenner `*` durch `/`. Alle produktiven Aufrufer reichen
  diese Parameter bereits als Schlüsselwörter. Der Funktionskörper und jedes
  Ergebnis bleiben gleich; nur zusätzlich erlaubte Aufrufsyntax ändert sich.
- **Zeile 197 (4):** `start_minute < 1440` nach `<= 1440` beziehungsweise
  `< 1441` lässt nur Start 1.440 durch die erste Bedingung; anschließend weist
  `end_minute <= start_minute` ihn zwingend ab. `end_minute > 0` nach `>-1`
  beziehungsweise `>=0` lässt nur Ende 0 weiter; dieselbe zweite Bedingung
  weist es bei jedem zulässigen Start zwingend ab.
- **Zeile 222 (1):** Das Einbeziehen des Startpunkts in die No-op-Prüfung ändert
  nichts: Wenn der äußere `_mode_at`-Vergleich erfüllt ist, hat ein vorhandener
  Punkt genau am Start bereits denselben Modus.
- **Zeile 228 (2):** Ein Punkt genau am Start wird unmittelbar danach mit dem
  gemalten Modus überschrieben. Ein Punkt genau am Ende wird unmittelbar danach
  mit dem zuvor ermittelten Endmodus wiederhergestellt. Ob einer der beiden vor
  dem Überschreiben gelöscht wird, ändert den gespeicherten Plan nicht.
- **Zeile 258 (1):** Für Minute 1.440 erzeugt auch die allgemeine Formatierung
  exakt `24:00`; die Mutation der Sonderfallgrenze nach 1.441 ist daher
  ausgabegleich. Andere Minuten einschließlich 1.439 sind separat geprüft.
- **Zeile 284 (2):** Der Fallback 0 nach 1 beziehungsweise -1 ist unerreichbar:
  `copy_schedule_day` beendet sich vor `day_pattern`, wenn keine Punkte
  existieren; mit mindestens einem Punkt benutzt `_mode_at` keinen Fallback.
- **Zeile 288 (2):** Ein Mitternachtspunkt ist bereits die Quelle des initialen
  Mustereintrags `(0, _mode_at(...))`. Ihn zusätzlich in der Schleife zu
  betrachten fügt wegen der unmittelbar folgenden Modusgleichheit nichts an.
- **Zeile 289 (1):** Der falsche Vergleich mit dem Offset statt dem Modus kann
  lediglich redundante Punkte in das Zwischenmuster aufnehmen. Die abschließende
  kanonische Ringnormalisierung entfernt genau diese wieder; der effektive und
  gespeicherte Plan bleibt identisch.
- **Zeile 295 (2):** Das Hinzufügen des Quelltags zu den Zieltagen kopiert dessen
  Muster auf sich selbst. Bei einem No-op bleibt die Vorabprüfung erfüllt; bei
  einer echten Kopie erzeugt die Kanonisierung wieder denselben Quelltag.
- **Zeile 301 (1):** Eine um eins zu große exklusive Tagesgrenze kann zusätzlich
  nur den Punkt bei 00:00 des Folgetags erfassen. Die anschließende
  Folgetagsbehandlung stellt genau diesen Modus wieder her, oder der Folgetag ist
  selbst Ziel und schreibt sein kopiertes 00:00-Muster.
- **Zeile 302 (2):** Der Zielpunkt bei 00:00 wird anschließend durch den ersten
  Mustereintrag überschrieben. Der Punkt bei 00:00 des Folgetags wird wie bei
  Zeile 301 anschließend wiederhergestellt oder vom kopierten Folgetag ersetzt.
- **Zeile 313 (2):** Wie bei Zeile 284 ist der Fallback unerreichbar, weil diese
  Schleife nur bei einem nicht leeren Ausgangsplan läuft.
- **Zeile 807 (2):** Ein gültiger `ZoneOverride` besitzt aufgrund der
  Datenbank-Check-Constraint genau eine feste Temperatur oder eine Modus-ID.
  Der Ausdruck `running.setpoint_mode_id or 0` erreicht in diesem Zweig deshalb
  nie den Fallback; 0, 1 und -1 ergeben für jeden gültigen Datensatz dieselbe
  Modus-ID.

## Befunde

Es wurde kein Fehler in der Produktionslogik nachgewiesen. Sämtliche 94
fachlich wirksamen Überlebenden waren Testlücken; die Tests wurden verschärft,
ohne Produktionslogik oder CI-Konfiguration zu ändern.
