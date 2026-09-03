# Mutationstest Runde 1 — restliche Domäne

Werkzeug: Cosmic Ray 8.7.0. Die Sitzungsdatenbanken liegen nur unter `/tmp`.
Die Konfigurationen und Testbefehle stehen in den jeweiligen
`cosmic-ray-*.toml`-Dateien.

## `domain/device_assignment.py`

| Mutanten | überlebt vorher | erschlagen | abgelegt | relevant nachher |
|---:|---:|---:|---:|---:|
| 301 | 14 | 9 | 5 | 0 |

167 Mutanten wurden mit demselben Operatorfilter wie in den früheren Stufen
ausgenommen; der Abschlusslauf meldet 129 erschlagene und fünf überlebende,
gleichwertige Mutationen.

Die neun neuen fachlichen Tests prüfen:

- die konkrete Fähigkeitsangabe in der Fehlermeldung;
- Geräte- und Rollenname beim Lösen einer Zuordnung;
- die eindeutige Zuordnung von Audit-Zusammenfassung und -Detail zu beiden
  Richtungen der Selbstregelung;
- `assign` gegenüber `unassign` beim Setzen und Lösen der Messquelle;
- die Fähigkeitsprüfung der Messquelle vor einem Gerätetausch;
- das Zusammenführen einer Rolle, die das neue Gerät in derselben Zone bereits
  besitzt.

Als gleichwertig abgelegt sind je eine Mutation des Signaturtrenners `*` nach
`/` in den Zeilen 96, 134, 163, 217 und 244. Sämtliche produktiven Aufrufer
reichen die nachfolgenden Parameter bereits als Schlüsselwörter; nur zusätzlich
erlaubte Aufrufsyntax, nicht Funktionskörper oder Ergebnis, ändert sich.

Es wurde kein Fehler in der Produktionslogik nachgewiesen.

## `domain/deviation.py`

| Mutanten | überlebt vorher | erschlagen | abgelegt | relevant nachher |
|---:|---:|---:|---:|---:|
| 71 | 1 | 1 | 0 | 0 |

50 Mutanten wurden mit dem gemeinsamen Operatorfilter ausgenommen. Die einzige
überlebende Mutation hob die Unveränderlichkeit von `Comparison` auf. Ein neuer
Test prüft diese Zusicherung inhaltlich; die isolierte Wiederholung meldet die
Mutation als erschlagen. Es wurde kein Fehler in der Produktionslogik
nachgewiesen.

## `domain/statistics.py`

| Mutanten | überlebt vorher | erschlagen | abgelegt | relevant nachher |
|---:|---:|---:|---:|---:|
| 165 | 12 | 11 | 1 | 0 |

31 Mutanten wurden mit dem gemeinsamen Operatorfilter ausgenommen; der
Abschlusslauf meldet 133 erschlagene und eine überlebende, gleichwertige
Mutation.

Die elf neuen fachlichen Tests prüfen die Unveränderlichkeit beider
Ergebnisobjekte, die Untergrenze der Zyklusdauer bei 0 und 1 Sekunde, einen
inklusiven Zeitraum über genau zwei Kalendertage sowie die Darstellungsgrenzen
bei 1 Sekunde, 59 und 60 Minuten und bei 119 und 120 Minuten.

Als gleichwertig abgelegt ist die Mutation des Signaturtrenners `*` nach `/` in
Zeile 58. Sämtliche produktiven Aufrufer übergeben `cycle_seconds` und
`timezone_name` als Schlüsselwörter; Berechnung und Ergebnis bleiben gleich.

Es wurde kein Fehler in der Produktionslogik nachgewiesen.
