# Mutationstest Runde 1, Stufe 1

Werkzeug: Cosmic Ray 8.7.0. Die zwei TOML-Dateien daneben enthalten Ziel, Testbefehl,
Zeitlimit, lokalen Verteiler und den wiederholbaren Gesamtbefehl. Die Sitzungsdatenbanken
liegen absichtlich unter `/tmp` und gehören nicht ins Repository oder in die CI.

## Ungefilterter Ausgangslauf

| Datei | Mutanten | überlebt | Anteil |
|---|---:|---:|---:|
| `domain/control_loop.py` | 137 | 15 | 10,95 % |
| `services/shadow_run.py` | 367 | 180 | 49,05 % |
| **gesamt** | **504** | **195** | **38,69 %** |

Jeder Ausgangsüberlebende ist unten entweder einem fachlichen Test oder einer begründeten
Ablage zugeordnet. Mehrere Operatoren an derselben Quellstelle sind zusammengefasst; die
Zahl in Klammern nennt dabei weiterhin jeden einzelnen Mutanten.

## Durch Tests erschlagen

### `control_loop.py` — 11

- Zeilen 29 und 53, `frozen=True` entfernt (2), sowie Zeilen 48–50, sichere
  Standardwerte `False` auf `True` gesetzt (3):
  `test_control_inputs_and_outputs_are_immutable_and_safety_flags_default_off`.
- Zeilen 78 und 105, Statusvergleich durch `<` beziehungsweise `is` ersetzt (2), sowie
  Zeile 218, Modusvergleich durch `is not` ersetzt (1):
  `test_status_and_mode_codes_are_compared_by_value_not_object_identity`.
- Zeile 131, Wiederanlaufgrenze `<` auf `<=` (1):
  `test_resume_delay_ends_exactly_at_its_configured_boundary`.
- Zeile 158, im Begründungstext der tatsächlich gehaltene Zustand vertauscht (1):
  `test_minimum_duration_reason_names_the_state_that_is_actually_held`.
- Zeile 150, Mindestdauer des Ein- und Auszustands vertauscht (1):
  `test_minimum_duration_uses_the_limit_for_the_current_state`.

### `shadow_run.py` — 53

- Zeilen 49–53, Frost-Sollwert nach falscher Zone oder falschem Modus ausgewählt bzw.
  Fallback vertauscht (16):
  `test_frost_setpoint_is_selected_for_the_exact_zone_and_configured_mode` und
  `test_missing_zone_frost_setpoint_does_not_leak_from_neighbouring_rows`.
- Zeilen 76, 84–86, Verlauf aus falscher Zone, falschem Ende oder falscher
  Zustandssequenz (12):
  `test_previous_state_uses_only_the_target_zones_latest_uninterrupted_run` und
  `test_empty_previous_state_does_not_leak_from_lower_or_higher_zone_ids`.
- Zeilen 123 und 131, falsche Fensterflanke beziehungsweise falsche Untergrenze der
  Schließdauer (9): Erweiterungen in
  `test_closing_a_window_starts_a_growing_restart_delay`.
- Zeile 166, Solarabsenkung verworfen oder ohne Ergebnis dereferenziert (2): die bereits
  vorhandenen Wirkungstests in `test_shadow_run_solar.py`, die im endgültigen Testbefehl
  ausdrücklich enthalten sind.
- Zeilen 206–211, Override aus falscher Zone oder außerhalb seines Gültigkeitsfensters
  verwendet (10): `test_only_an_active_override_of_the_same_zone_blocks_valve_protection`
  und `test_override_is_active_at_its_start_and_inactive_at_its_end`.
- Zeilen 217 und 248, Ventilschutz nach verpasster Endfrist weiter aktiv oder am exakten
  Intervall noch nicht fällig (2):
  `test_expired_valve_protection_is_closed_even_after_missing_its_exact_deadline` und
  `test_valve_protection_becomes_due_at_the_exact_interval_boundary`.
- Zeile 329, nach Fehler der ersten Zone abgebrochen statt fortgesetzt (1):
  `test_a_failing_first_zone_does_not_prevent_later_zones`.
- Zeile 101, nur einer der beiden Fenster-Lookups fehlt (1):
  `test_window_state_is_safe_when_only_one_required_lookup_exists`. Dieser Mutant wurde
  nach dem vollständigen Nachlauf zusätzlich isoliert mit `cosmic-ray mutate-and-test`
  ausgeführt; Ergebnis `TestOutcome.KILLED`.

## Begründet abgelegt

### Cosmic-Ray-Kreuztypoperatoren — 127 Ausgangsüberlebende

Der dokumentierte Operatorfilter legt diese Mutanten ausdrücklich ab. Er überspringt
insgesamt 262 von 504 erzeugten Mutanten; 127 davon gehörten zu den ungefiltert noch
überlebenden Mutanten, die übrigen waren ohnehin tot.

- Typannotationen mit `|` in Zeilen 58, 93, 94, 140, 179 und 307 von `shadow_run.py`
  werden je durch arithmetische Operatoren wie `/`, `**` oder `<<` ersetzt (77
  Überlebende). Annotationen ändern keine Laufentscheidung; diese Ersetzungen prüfen
  zudem nicht die Bedeutung der Union, sondern erzeugen fremde Typausdrücke.
- SQLAlchemy-Ausdrucksoperator `|` in Zeile 209 von `shadow_run.py` wird durch
  arithmetische Operatoren ersetzt (2 Überlebende). Das ist keine plausible Mutation
  der booleschen Fachbedingung, sondern ein Wechsel in eine andere SQLAlchemy-
  Operatorfamilie. Die fachlich sinnvolle Mutation `or` zu `and` bleibt ungefiltert.
- Vergleiche werden quer zu nicht benachbarten oder identitätsbasierten Operatoren
  ersetzt, etwa `==` durch `>=`/`is not` oder `!=` durch `<` (48 Überlebende: 4 in
  `control_loop.py`, 44 in `shadow_run.py`). Diese Mutanten hängen von Sortierreihenfolge,
  String-Internierung oder SQL-Abfrageplan ab und modellieren keinen einzelnen
  Grenzfehler. `==`↔`!=`, `<`↔`<=`, `>`↔`>=`, Negationen und `and`↔`or` bleiben aktiv.

### Aktive, aber äquivalente Mutanten — 4

- `shadow_run.py:84`, `rows[0]` zu `rows[-1]` beim Initialwert von `start` (1): Die
  unmittelbar folgende Schleife besucht zwingend zuerst `rows[0]` und überschreibt
  `start`, bevor sie bei einem Zustandswechsel abbrechen kann. Der Initialwert ist daher
  nicht beobachtbar.
- `shadow_run.py:96`, `state.window_open is not False` zu `!= False` (1): Der modellierte
  Wertebereich ist exakt `bool | None`; für `False`, `True` und `None` sind beide
  Ausdrücke gleich.
- `shadow_run.py:211`, `.limit(1)` zu `.limit(2)` (1): Das Ergebnis wird nur mit
  `session.scalar(...) is not None` auf Existenz geprüft. Jede positive Grenze liefert
  dieselbe Aussage.
- `shadow_run.py:238`, `.limit(1)` zu `.limit(2)` (1): `session.scalar(...)` liest wegen
  der unveränderten absteigenden Sortierung weiterhin genau den ersten, neuesten Wert.

## Endstand

Der vollständige gefilterte Nachlauf meldete `control_loop.py` mit 0 Überlebenden und
`shadow_run.py` zunächst mit 5. Der anschließend ergänzte Lookup-Test erschlug den fünften
isoliert; übrig sind die vier oben begründeten äquivalenten Mutanten.

- Roh bezogen auf alle 504 erzeugten Mutanten: **4 überlebt = 0,79 %**, davon alle vier
  begründet äquivalent.
- Bezogen auf die 242 nach dem dokumentierten Filter aktiven Mutanten: **4 überlebt =
  1,65 %**.
- Nicht abgelegte, fachlich relevante Überlebende: **0**.

Es wurde kein Fehler der Regellogik gefunden, sondern ausschließlich fehlende
Testaussagen. Die Regellogik selbst blieb unverändert.
