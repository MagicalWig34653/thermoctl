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

## Als gleichwertig abgelegt — 8, einzeln

Ausgelesen aus `/tmp/thermoctl-pi-control-new2.sqlite` (Abschlusslauf nach
den 45 neuen Tests):

```
operator_name                          start_pos_row  occurrence
core/ReplaceComparisonOperator_Gt_GtE  137            1
core/NumberReplacer                    137            25
core/ReplaceComparisonOperator_Lt_LtE  138            0
core/NumberReplacer                    138            28
core/ReplaceBinaryOperator_Mul_Div     190            9
core/ReplaceBinaryOperator_Mul_Div     235            10
core/ReplaceBinaryOperator_Mul_Div     318            13
core/ReplaceBinaryOperator_Mul_Div     509            14
```

**1. Zeile 137, Spalte 43, `ReplaceComparisonOperator_Gt_GtE`, occurrence 1
(`pi_arithmetic`).** `stuck_high = u_before == 1 and error_k > 0` wird zu
`error_k >= 0`. Die beiden Operatoren unterscheiden sich einzig bei
`error_k == 0`. Dort ist der Integral-Zuwachs `(gain_per_k / ti_seconds) *
error_k * dt_seconds` unabhängig vom Faktor `error_k = 0` immer exakt null
— ob die Bedingung an dieser Stelle "eingefroren" (`new_integral =
integral`) oder "normal fortgesetzt" (`new_integral =
clamp01(integral + 0) = integral`, weil die Integralkomponente laut
Invariante stets in [0, 1] bleibt) entscheidet, das Ergebnis ist in beiden
Zweigen identisch. Nachgerechnet für `error_k = 0`, jeden erreichbaren
`integral` und jedes `gain_per_k`, nicht nur für einen Beispielwert.

**2. Zeile 138, Spalte 42, `ReplaceComparisonOperator_Lt_LtE`, occurrence 0
(`pi_arithmetic`).** Spiegelbildlich zu Nr. 1: `stuck_low = u_before == 0
and error_k < 0` wird zu `error_k <= 0`. Dieselbe Begründung, gespiegelt:
bei `error_k == 0` ist der Zuwachs unabhängig vom Vorzeichen des Vergleichs
null.

**3. Zeile 137, Spalte 45, `NumberReplacer`, occurrence 25
(`pi_arithmetic`).** Dieselbe Zeile wie Nr. 1, aber die literale `0` in
`error_k > 0` wird zu `-1`, nicht zu `>=`. Diese Mutation wirkt sich nur im
Bereich `-1 < error_k <= 0` aus. Damit sie überhaupt beobachtbar wäre,
müsste in diesem Bereich gleichzeitig `u_before == 1` gelten — also
`gain_per_k * error_k + integral = 1` bei `error_k ≤ 0`. Mit
`integral ≤ 1` (Invariante der Funktion, siehe oben) und `gain_per_k ≥ 0`
(Datenbank-Check-Constraint `pi_gain_per_k BETWEEN 0.05 AND 0.50` in
`thermoctl/db/models/zone.py:41-43`, außerhalb dieses Moduls erzwungen, hier
nur vorausgesetzt) ist `gain_per_k * error_k ≤ 0`, also kann die Summe nur
dann exakt 1 erreichen, wenn `gain_per_k * error_k = 0` **und** `integral =
1` gilt. In diesem Fall ist aber der Zuwachs `(gain_per_k / ti_seconds) *
error_k * dt_seconds = 0` — derselbe Faktor, der die Summe auf 0 gebracht
hat, macht auch den Zuwachs zu null. Es gibt also keinen erreichbaren
Zustand, in dem diese Mutation zu einem anderen `new_integral` führt als
das Original. Einschränkung, offen benannt: Diese Herleitung hängt an der
Prämisse `gain_per_k ≥ 0`. `pi_arithmetic()` selbst prüft das Vorzeichen
nicht (reine Funktion, siehe Moduldocstring); die Prämisse kommt
ausschließlich aus der Schema-Constraint des einzigen produktiven Aufrufers
(`thermoctl/services/shadow_run.py:621`). Ein direkter, isolierter Aufruf
von `pi_arithmetic()` mit negativem `gain_per_k` — den kein Test und kein
produktiver Pfad je tut — würde die Äquivalenz aufheben.

**4. Zeile 138, Spalte 44, `NumberReplacer`, occurrence 28
(`pi_arithmetic`).** Spiegelbildlich zu Nr. 3: `error_k < 0` wird zu
`error_k < 1`, wirkt sich nur im Bereich `0 <= error_k < 1` aus. Für
`u_before == 0` bei `error_k ≥ 0` und `gain_per_k ≥ 0` gilt symmetrisch:
`gain_per_k * error_k + integral = 0` mit `integral ≥ 0` und
`gain_per_k * error_k ≥ 0` erzwingt `gain_per_k * error_k = 0` und
`integral = 0` — wieder macht derselbe Faktor auch den Zuwachs null.
Dieselbe Einschränkung wie bei Nr. 3 gilt hier ebenso.

**5. Zeile 190, Spalte 4, `ReplaceBinaryOperator_Mul_Div`, occurrence 9
(`window_modulate`).** Der Signaturtrenner `*,` wird zu `/,`; `state` wäre
dann positionsonly statt die nachfolgenden Parameter schlüsselwortonly zu
erzwingen. Geprüft per `grep -rn "window_modulate("
--include="*.py" .`: Der einzige produktive Aufruf steht in
`pi_control.py:446` selbst und reicht `now`, `u_raw`, `dt_seconds`,
`pi_min_on_seconds`, `pi_min_off_seconds` bereits als Schlüsselwörter; jeder
der zwölf Testaufrufe in `tests/test_pi_control.py` ebenso. Kein
bestehender oder denkbarer Aufruf im Repository nutzt Positionsargumente
für diese Parameter.

**6. Zeile 318, Spalte 4, `ReplaceBinaryOperator_Mul_Div`, occurrence 13
(`reset_pi_state`).** Dieselbe Mutation an `reset_pi_state(reason, *, now=
None, await_next_boundary=False)`. Die drei produktiven Aufrufe
(`pi_control.py:416,426`, `shadow_run.py:574,598`) und alle zwölf
Testaufrufe reichen `now` / `await_next_boundary` durchweg als
Schlüsselwörter; `reason` bleibt so oder so das einzige Positionsargument.

**7. Zeile 509, Spalte 4, `ReplaceBinaryOperator_Mul_Div`, occurrence 14
(`pi_eligible`).** Dieselbe Mutation an `pi_eligible(actuators, *,
control_cycle_seconds, pi_min_on_seconds, pi_min_off_seconds)`. Der einzige
produktive Aufruf (`shadow_run.py:557`) und alle neun Testaufrufe reichen
die drei Zahlenparameter als Schlüsselwörter — bei drei gleichartig
typisierten `int`-Parametern in Folge ist das zudem der Grund, warum die
Schlüsselwortpflicht überhaupt im Quelltext steht (Vertauschungsschutz);
nur die zusätzlich erlaubte, nirgends genutzte Aufrufsyntax ändert sich.

**8. Zeile 235, Spalte 25, `ReplaceBinaryOperator_Mul_Div`, occurrence 10
(`settle`).** Dieselbe Mutation an der verschachtelten Closure
`settle(on: bool, *, reason: str)` innerhalb von `window_modulate`. `settle`
ist nicht modulweit sichtbar (definiert und ausschließlich aufgerufen
innerhalb von `window_modulate`, sieben Aufrufstellen, alle mit
`reason=...`); es gibt keinen Aufrufer außerhalb dieser einen Funktion und
keine Möglichkeit, sie von einem Test aus isoliert mit Positionsargumenten
anzusprechen. Von den acht Überlebenden ist dies der am wenigsten
interessante Fall: eine reine Implementierungsdetail-Closure, kein Teil
irgendeiner Schnittstelle.

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
