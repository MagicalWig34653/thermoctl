# Die Umstellung des Codes auf Englisch

Diese Tabellen haben die einmalige Umbenennung gesteuert. Sie stehen hier, damit
nachvollziehbar bleibt, **was** wozu wurde — nicht, weil sie noch einmal laufen sollen.

| Datei | |
|---|---|
| `abbildung.txt` | die Bezeichner, 847 Zeilen |
| `zu_pruefen.tsv` | die 147 davon, die von Hand nachgebessert wurden |
| `module.tsv`, `testdateien.tsv` | die umbenannten Dateien |
| `formularfelder.tsv` | die Formularfelder — Vorlage, Ansicht und Test zugleich |
| `zeichenketten_global.tsv` | Namen, die als Zeichenkette vorkommen (Modulpfade, `monkeypatch`) |
| `zeichenketten_datei.tsv` | dieselben Wörter, aber nur in bestimmten Dateien — anderswo sind es Formularfelder |

Hier liegen die **Tabellen**, nicht die Skripte: Was umbenannt wurde, ist der Teil, der
überdauert; das Werkzeug war Einweg und ist nicht gepflegt. Die Umstellung lief in elf
Schritten von einem sauberen `HEAD` aus. Die Trennung ist der Kern: Dieselben deutschen Wörter stecken gleichzeitig in

* **Bezeichnern** (Schritt 1) — nur `NAME`-Token, nie Zeichenketten,
* **Modul- und Testdateinamen** (Schritt 2),
* **Vorlagenausdrücken und Kontextschlüsseln** (5, 6),
* **Web-Pfaden** (7),
* **MQTT-Topics und Befehlsarten** (8, 8b),
* **Formularfeldern** (8c, 8d) — Vorlage, Ansicht und Test müssen zusammen wechseln,
* **Befundarten** (8e).

Ein erster Anlauf hat alle diese Ebenen in einem Zug ersetzt und dabei Formular- und
Datenverträge mit verändert; er wurde verworfen. Die Reihenfolge und die Trennung hier
sind das Ergebnis daraus.

**Was deutsch geblieben ist:** jeder Text, den ein Mensch liest — Vorlagen, Fehlermeldungen,
Audit-Zusammenfassungen, die `label`-Spalten der Nachschlagetabellen, `docs/` und
`CLAUDE.md`. Ebenso das Datenbankschema (es war schon englisch, bis auf
`user_passkey.bezeichnung`) und die fremden Verträge von Zigbee2MQTT und Home Assistant.

**Was noch aussteht:** Kommentare, Docstrings und die 707 Testnamen sind weiterhin
deutsch. Das ist Sprache und kein Refactoring — ein Skript hilft dort nicht.
