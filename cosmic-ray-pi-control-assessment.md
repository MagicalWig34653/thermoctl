# Mutationstest — `domain/pi_control.py`

Werkzeug: Cosmic Ray 8.7.0. Konfiguration: `cosmic-ray-pi-control.toml`. Die
Sitzungsdatenbanken liegen nur unter `/tmp`.

## Vorgeschichte, kurz

Für dieses Modul war zuvor "662 Mutanten, null Überlebende" berichtet worden.
Das war falsch und ist von niemandem nachgerechnet worden. Die tatsächlichen
Rohdaten zweier cosmic-ray-Sitzungen:

| Stand | Mutanten | erschlagen | überlebt |
|---|---:|---:|---:|
| vor der letzten Änderung | 662 | 424 | 54 |
| vor dieser Bewertung | 664 | 427 | **53** |

53 Überlebende, nicht null und nicht vier. Diese Bewertung arbeitet die 53
einzeln ab.

Ein zusätzlicher Befund am Rande: Der erste eigene Nachlauf dieser Sitzung
meldete "0 Überlebende" bei 480 als `INCOMPETENT` markierten Läufen — die
`test-command`-Zeile in `cosmic-ray-pi-control.toml` referenziert
`.venv/bin/python` relativ, und dieser Worktree besitzt keine eigene
`.venv`. Ohne den erwartungsgemäßen Interpreter schlägt jeder Testlauf mit
`FileNotFoundError` fehl, cosmic-ray wertet das als "unfähig", nicht als
"überlebt", und ein oberflächlicher Blick auf `surviving mutants: 0` hätte
denselben Fehler wiederholt, den diese Bewertung gerade korrigiert. Ein
lokaler Symlink `.venv -> .../thermoctl/.venv` (von `.gitignore` ohnehin
ausgenommen) behebt das für den eigenen Lauf.

## Messung

| Stand | Mutanten | erschlagen | überlebt |
|---|---:|---:|---:|
| vor dieser Bewertung | 664 | 427 | 53 |
| nach dieser Bewertung | 664 | 472 | 8 |

184 Einträge sind mit dem gemeinsamen Operatorfilter (Kreuztyp-Vergleiche,
`BitOr`-Rauschen) ausgenommen, unverändert gegenüber der Vorsitzung.

Von den 53 Überlebenden sind **45 echte Testlücken** (geschlossen durch neue
fachliche Tests in `tests/test_pi_control.py`) und **8 gleichwertig**
(einzeln unten begründet, unverändert überlebend).

## Die drei interessantesten Lücken

**1. Die Anti-Windup-Freeze-Prüfung war nie beobachtbar von einem echten
Zwischenwert aus geprüft (Zeilen 136–139).** Jeder vorhandene Test ließ die
Integralkomponente exakt bei 0 oder 1 starten. An dieser Grenze klemmt
`_clamp01` das Ergebnis ohnehin auf denselben Wert — ob die Freeze-Logik
greift oder nicht, ist dort unsichtbar, weil beide Pfade zufällig dasselbe
liefern. Ein Mutant, der `gain_per_k * error_k` durch `gain_per_k ** error_k`
oder `gain_per_k - error_k` ersetzte, oder der die Sättigungsbedingung selbst
verfälschte (`u_before == 1` zu `== 0`, `stuck_high or stuck_low` zu `and`),
überlebte deshalb unbemerkt. Die neuen Tests starten die Integralkomponente
bewusst **innerhalb** von (0, 1) (z. B. 0,9) und lassen sie durch einen
Fehler sättigen, der die Ausgabe zwar auf 1 klemmt, den *inneren* Wert aber
nicht: "Die eingefrorene Integralkomponente bleibt bei ihrem echten
Vorwert stehen, statt unbeobachtet weiter Richtung Kappungsgrenze zu
wandern." Das ist die eigentliche fachliche Aussage von Abschnitt 2's
Anti-Windup-Schutz — nicht "u bleibt ≤ 1", was ohnehin immer gilt.

**2. Ein widersprüchlicher persistierter Zustand (Fensterstart passt, Tastgrad
fehlt) ließ sich zum Absturz bringen, ohne dass ein Test das bemerkte
(Zeile 225).** `window_modulate()` fror nur dann einen neuen Tastgrad ein,
wenn entweder die Fenstergrenze gewechselt hat **oder** kein Tastgrad
vorliegt. Die Mutation kehrte die zweite Bedingung um; damit hätte ein
korrupter `ModulatorState` mit passendem `window_start`, aber
`frozen_duty=None` (z. B. nach einem unvollständigen Datenbank-Schreiben)
die nachfolgende `assert duty is not None` zum Absturz gebracht, statt sich
— wie der Rest des Moduls es an jeder anderen Stelle für beschädigte
Zustände tut — defensiv zu erholen. Der neue Test baut genau diesen
Zustand und verlangt, dass ein frischer Tastgrad eingefroren wird statt
eines Abbruchs.

**3. Die ±900-Sekunden-Grenze der Restzeitrechnung war nur über sich selbst
geprüft (Zeilen 40 f., 274).** Bestehende Tests verglichen das Ergebnis mit
dem importierten `REMAINDER_LIMIT_S` bzw. `WINDOW_SECONDS` — bei einer
mutierten Konstante ändert sich damit *beide* Seiten des Vergleichs
gemeinsam, und der Test bleibt grün, egal welchen Wert die Konstante trägt.
Die neuen Tests pinnen die Fensterlänge und die Restzeitgrenze mit
unabhängigen Literalen (900 Sekunden, `_at(minute=45)` statt einer aus
`WINDOW_SECONDS` abgeleiteten Uhrzeit) und prüfen zusätzlich exakt an der
Grenze selbst — bei 900 hält der Modulator noch, bei 901 erzwingt er den
Wechsel, symmetrisch auf beiden Vorzeichen. Das ist keine kosmetische
Fleißarbeit: Die Grenze existiert laut Moduldokumentation ausdrücklich, damit
eine blockierte Mindestlaufzeit keine unbegrenzte Heizzeit-Schuld
anhäufen kann ("Verworfen und schlecht lösbar", Abschnitt 3) — ein
verschobener Wert von 899 oder 901 hätte genau diese Schutzwirkung
unbemerkt verändert.

## Als gleichwertig abgelegt — 8

- **Zeilen 190, 235, 318, 509 (je 1):** Cosmic Ray ersetzt den
  Signaturtrenner `*` durch `/`. Wie schon in
  `cosmic-ray-domain-rest-assessment.md` und
  `cosmic-ray-stage2-assessment.md` festgehalten: Alle produktiven Aufrufer
  reichen diese Parameter bereits als Schlüsselwörter (geprüft per
  `grep` über `thermoctl/`); Funktionskörper und Ergebnis bleiben in jedem
  Fall gleich, nur zusätzlich erlaubte Aufrufsyntax ändert sich. Zeile 235
  betrifft zusätzlich `settle()`, eine private, nur intern mit festen
  Schlüsselwörtern aufgerufene Closure — von außen ohnehin nicht ansprechbar.
- **Zeile 137, Spalte 43 (`error_k > 0` → `>= 0`) und Zeile 138, Spalte 42
  (`error_k < 0` → `<= 0`):** Beide Vergleichsoperator-Mutationen können sich
  ausschließlich an der Stelle `error_k == 0` auswirken — dem einzigen Punkt,
  an dem `>` und `>=` (bzw. `<` und `<=`) verschiedene Wahrheitswerte liefern.
  Der Zuwachs der Integralkomponente ist aber `(gain_per_k / ti_seconds) *
  error_k * dt_seconds` — bei `error_k = 0` unabhängig vom gewählten Pfad
  exakt null. Ob die Bedingung an dieser einen Stelle "eingefroren" oder
  "normal fortgesetzt" entscheidet, das Ergebnis (`new_integral =
  integral`) ist in beiden Fällen identisch, weil die Integralkomponente laut
  Invariante immer in [0, 1] bleibt und `_clamp01` einer unveränderten Zahl
  nichts hinzufügt.
- **Zeile 137, Spalte 45 (die Variante `error_k > -1`) und Zeile 138, Spalte
  44 (die Variante `error_k < 1`):** `stuck_high` verlangt zusätzlich
  `u_before == 1`. Mit `integral ≤ 1`, `gain_per_k ≥ 0` (eine
  Reglerverstärkung ist nach Auslegung dieses Moduls nie negativ — das
  Vorzeichen prüft dieses Modul selbst nicht, sondern setzt es als
  Konfigurationsinvariante voraus, ähnlich der Datenbank-Check-Constraint in
  `cosmic-ray-stage2-assessment.md`) kann `gain_per_k * error_k + integral`
  nur dann exakt die Sättigungsgrenze 1 erreichen, wenn `error_k ≥ 0` ist —
  ein negativer Fehler kann die Summe nur nach unten drücken. Die Bedingung
  `u_before == 1 and error_k > -1` unterscheidet sich deshalb im gesamten
  erreichbaren Zustandsraum nicht von `u_before == 1 and error_k > 0`.
  Symmetrisch für `u_before == 0 and error_k < 1` gegenüber `< 0`.

## Befunde in der Rechenlogik

Es wurde **kein Fehler** in der Rechenlogik von `pi_control.py` nachgewiesen.
Die einzige auffällige Beobachtung ist keine Falschberechnung, sondern die
Testschwäche selbst: mehrere bestehende Tests verglichen ein Ergebnis mit
einer aus derselben (mutierten) Quelle importierten Konstante statt mit
einem unabhängigen Literal (`WINDOW_SECONDS`, `REMAINDER_LIMIT_S`,
`MAX_CONTROL_CYCLE_SECONDS`) — siehe Lücke 3 oben. Das ist ein
Testmuster-Fehler, kein Produktionsfehler, aber einer, der sich in dieser
Datei besonders leicht wiederholen lässt, weil die Konstanten dort bewusst
prominent stehen ("nicht ein dritter Tuning-Parameter").

## Neue Tests — Übersicht

`tests/test_pi_control.py`, neuer Abschnitt 6, 21 neue Testfälle in 12
Klassen:

- feste Konstanten und neutrale Startwerte, mit unabhängigen Literalen
  statt Re-Import (`WINDOW_SECONDS`, `REMAINDER_LIMIT_S`,
  `MAX_CONTROL_CYCLE_SECONDS`, `NEUTRAL_PI_STATE.integral`,
  `NEUTRAL_MODULATOR_STATE.on`);
- `pi_dt`: ein Ein-Sekunden-Zeitschritt wird noch integriert;
- Anti-Windup-Freeze mit einer Integralkomponente strikt innerhalb (0, 1),
  auf beiden Sättigungsseiten und mit einem Fehler nur knapp über der
  Auslöseschwelle;
- `window_modulate`-Validierung an ihren echten Grenzen (negativer Tastgrad,
  ein Sekunde `dt`);
- Wiederherstellung nach einem widersprüchlichen persistierten Zustand
  (Fenstergrenze passt, Tastgrad fehlt);
- die Mindestdauer-Grenze exakt bei Gleichheit (nicht mehr blockiert);
- der Stichentscheid bei einem Bruchteil-Rest zwischen 0 und 1;
- die Restzeitgrenze exakt bei ±900 und ±901 Sekunden, auf beiden
  Vorzeichen;
- die Verweildaueraddition über mehrere Zyklen (kein Floor-Divide, Shift
  oder Bitoperator);
- `pi_eligible` an der Ein-Sekunden- und der Ein-und-sechzig-Sekunden-Grenze,
  mit unabhängigen Literalen;
- die Unveränderlichkeit aller sieben Zustands- und Ergebnis-Dataclasses,
  begründet über die im Moduldocstring zugesicherte Reinheit (kein Mutieren
  der Argumente), nicht als generischer `frozen=True`-Check.

## Prüfergebnisse

Siehe Bericht der Hauptsession (Prüflauf-Protokoll `/tmp/pm_sqlite.log`,
`/tmp/pm_maria.log`): SQLite und MariaDB je grün, 0 `FAILED`, 100 % Abdeckung
für `pi_control.py`. `git status --short` und die Prüfsumme der Datei vor
und nach dem Mutationslauf ebenfalls dort.
