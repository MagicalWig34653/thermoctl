# Mutationstest Runde 1, Stufe 4 — Zwischenstand nach `administration.py`

Werkzeug: Cosmic Ray 8.7.0. Die vier TOML-Dateien daneben enthalten Ziel,
Testbefehl, Zeitlimit, lokalen Verteiler und denselben Operatorfilter wie die
vorherigen Stufen. Die Sitzungsdatenbanken liegen nur unter `/tmp` und gehören
weder ins Repository noch in die CI.

Die Reihenfolge des Auftrags wurde eingehalten. Nach `administration.py` endet
dieser Durchgang bewusst: Vier Dateien vollständig zu bewerten war verlässlicher,
als die folgenden Dateien nur anzulaufen. Offen bleiben damit
`device_assignment.py`, `statistics.py`, `deviation.py` und die danach übrigen
Domänendateien. `authz.py` und `auth/` gehörten ausdrücklich zu einem parallelen
Auftrag und wurden nicht berührt.

## Ausgangsläufe

Die belastbare Ausgangszahl ist jeweils der Lauf nach dem in Stufen 1 und 2
dokumentierten Kreuztypfilter. Zum Nachweis des Werkzeugrauschens wurde zusätzlich
jeweils ein vollständig ungefilterter Lauf ausgeführt.

| Datei | Mutanten | ungefiltert überlebt | nach Filter überlebt | Anteil |
|---|---:|---:|---:|---:|
| `domain/self_regulating.py` | 88 | 25 | 6 | 6,82 % |
| `domain/switch_commands.py` | 191 | 73 | 14 | 7,33 % |
| `domain/solar_setback.py` | 130 | 32 | 15 | 11,54 % |
| `domain/administration.py` | 577 | 266 | 42 | 7,28 % |
| **gesamt** | **986** | **396** | **77** | **7,81 %** |

## `self_regulating.py`

### Durch Tests erschlagen — 6

- `ValveCommand` bleibt nach der Entscheidung unveränderlich.
- Sollwerte exakt auf der vom Ventil deklarierten Unter- und Obergrenze sind
  gültig; ein fehlendes Maximum bedeutet unbeschränkt.
- Fehlende Thermostat-Capability-Metadaten verhindern vorsorglich jeden
  Ventilbefehl.
- Ein unbrauchbares erstes Ventil verdeckt kein späteres gültiges Ventil. Das ist
  physisch relevant: ein einzelner fehlerhafter Eintrag darf nicht die übrigen
  Heizkörper eines Raums ungeregelt lassen.

### Endstand

**0 von 88 überlebt = 0,00 %.** Es gibt weder abgelegte aktive Mutanten noch
einen Befund in der Produktionslogik.

## `switch_commands.py`

### Durch Tests erschlagen — 10

- `SwitchCommand` und `ThermostatCommand` bleiben unveränderlich.
- Capability-Verknüpfungen gehören exakt zum ausgewählten Gerät; insbesondere
  erschlagen die Tests die fachlich relevante SQLAlchemy-Mutation `&` zu `|` in
  beiden Joins.
- Ein Gerät mit Switch- und Thermostat-Capability wird nicht zusätzlich als
  Thermostat angesteuert und verdeckt kein späteres reines Thermostat.
- Die Geräte- und Capability-IDs des Ausschlusses werden exakt verglichen.

### Begründet abgelegt — 4

An den beiden SQLAlchemy-Join-Ausdrücken ersetzt Cosmic Ray `&` je durch `*` und
`/`. Das sind vier Kreuztypmutationen in eine andere SQLAlchemy-Operatorfamilie,
keine plausiblen Änderungen der booleschen Fachbedingung. `&` zu `|` blieb aktiv
und wird durch einen Test erschlagen.

### Endstand

**4 von 191 überlebt = 2,09 %**, alle vier begründet unerheblich. Fachlich
relevante Überlebende: **0**.

## `solar_setback.py`

### Durch Tests erschlagen — 6

- Ein Vorhersagepunkt exakt für den Entscheidungszeitpunkt gehört zum Fenster.
- Die physische Sonnenschwelle bleibt exakt und einschließlich 120 W/m²; 119 W/m²
  reicht nicht.
- `HourlyForecast` und `SetbackResult` bleiben unveränderlich.

### Begründet abgelegt — 9

- Ein Mutant ersetzt den Keyword-only-Stern von `apply()` durch einen
  Positional-only-Schrägstrich. Alle Aufrufer verwenden die Parameter namentlich;
  Berechnung und Ergebnis ändern sich nicht.
- Acht Mutanten lockern oder entfernen frühe Prüfungen für Faktor, maximale
  Absenkung und Abstand zum Frostschutz. Für sämtliche dadurch zusätzlich
  durchgelassenen Werte ist `raw` nicht positiv; die unveränderte abschließende
  Prüfung `reduction <= 0` liefert daher ebenfalls `None`. Auch die Verknüpfung
  der frühen Prüfungen mit `and` statt `or` hat aus demselben Grund kein anderes
  beobachtbares Ergebnis.

### Endstand

**9 von 130 überlebt = 6,92 %**, alle neun begründet gleichwertig oder
signaturbezogen. Fachlich relevante Überlebende: **0**.

## `administration.py`

### Durch Tests erschlagen — 19

- Der letzte aktive Verwalter darf in eine andere Gruppe wechseln, wenn diese das
  anlagenweite `user.manage` tatsächlich trägt.
- Eine Verwaltungsgruppe darf gelöscht werden, wenn eine zweite aktive Quelle des
  Verwaltungsrechts verbleibt.
- Das Entziehen eines anderen anlagenweiten Rechts aus der einzigen
  Verwaltungsgruppe löst den Aussperrschutz nicht fälschlich aus.
- Aktivierung und Deaktivierung werden mit der tatsächlich eingestellten Richtung
  protokolliert.
- Zonengebundene Grants für zwei Zonen bleiben getrennte Zeilen und nennen im
  Audit die richtige Zone.
- Die Sammeländerung vergibt und entzieht exakt die Mengendifferenz und meldet die
  richtigen Zähler. Damit werden insbesondere Vereinigung/XOR statt Differenz und
  sämtliche Zählermutationen erschlagen.

### Begründet abgelegt — 23

- Zehn Mutanten ersetzen Keyword-only durch Positional-only. Kein fachlicher Wert,
  keine Berechtigung und keine Datenbankänderung ändert sich; alle Aufrufer nutzen
  die benannten Argumente.
- Zwei Mutanten lockern `.limit(1)` zu `.limit(2)` bei reinen Existenzabfragen über
  `scalar()`. Weiterhin wird nur die erste vorhandene Zeile betrachtet.
- Ein Mutant lockert ein internes Assert von `group is not None and permission is
  not None` zu `or`. Die Fremdschlüssel der übergebenen `GroupPermission` sichern
  beide Zeilen; unterscheidbar wäre er nur mit einem bereits ungültigen
  Datenbankzustand außerhalb des Funktionsvertrags.
- Zehn Mutanten ändern ausschließlich Sortierschlüssel innerhalb der Menge neuer
  Grants beziehungsweise alter Revokes. Alle Grants werden weiterhin vollständig
  vor allen Revokes ausgeführt — die für den Aussperrschutz entscheidende
  Reihenfolge bleibt also erhalten. Endzustand, Zähler und Sicherheitsprüfung sind
  identisch; nur die Reihenfolge voneinander unabhängiger Auditzeilen könnte sich
  ändern.

### Endstand

**23 von 577 überlebt = 3,99 %**, alle 23 begründet gleichwertig oder
unerheblich. Fachlich relevante Überlebende: **0**. Insbesondere wurde kein Defekt
des Riegels gegen das Aussperren des letzten Verwalters gefunden.

## Gesamtstand dieses Durchgangs

Von 77 gefilterten Ausgangsüberlebenden wurden **41 durch inhaltliche Tests
erschlagen** und **36 begründet abgelegt**. Danach bleiben **36 von 986 = 3,65 %**
Rohmutanten übrig, sämtlich abgelegt; fachlich relevante Überlebende: **0**.

Es wurde kein Fehler in der Produktionslogik gefunden und keine Produktionslogik
geändert. Alle Änderungen betreffen Tests, wiederholbare Cosmic-Ray-Konfigurationen
und diese Bewertung.
