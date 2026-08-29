# Stand

Letzte Aktualisierung: 2026-08-29

## Wo wir stehen

**Teilprojekt 1 (Fundament) ist abgeschlossen und als `v0.1.0` veröffentlicht.
Teilprojekt 2 (Geräte-Anbindung im Schattenbetrieb) ist gebaut.** Teilprojekt 3
(Konfigurations-Oberfläche) läuft. Aus Teilprojekt 5 wurde vorgezogen, was nicht auf den
Regelkreis wartet.

`thermoctl` liest jetzt Sensoren, führt eine Messwert-Historie, erkennt ausgefallene
Sensoren und schreibt für jede Zone auf, **was es schalten würde und warum** — ohne je
etwas zu schalten. Der Regelkreis selbst (Phase 4) ist gebaut und erschöpfend getestet,
aber nicht scharf.

### Was Phase 2 gebracht hat

| | |
|---|---|
| Nutzlast-Auswertung | gegen die echten Anlagendaten gebaut, nicht gegen Vermutungen |
| Geräteerkennung | aus `bridge/devices`, erkennt Ventile und Fensterkontakte ohne Zustandsnachricht |
| MQTT-Client | TLS, Zugangsdaten aus der Umgebung, Wiederverbindung mit wachsendem Abstand |
| Ingest | Messwerte, Gerätezustand, Zonenzustand; unbekannte Geräte werden angelegt |
| Regelentscheidung | Hysterese, Mindestschaltdauer, Fensterpause, Frostschutz — rein und erschöpfend getestet |
| Störungserkennung | ein ausbleibender Messwert ist ein Zustand, kein alter Wert |
| Aktoren | vollständig gebaut, hinter **zwei** unabhängigen Riegeln |
| Schattenprotokoll | je Zone und Zyklus eine begründete Entscheidung |
| Oberfläche | `/geraete` lesend, Zonenzustand auf der Startseite |

### Der Trockenlauf ist abgesichert, nicht zugesagt

- `setting.control_armed` steht auf `false` und wird nirgends gesetzt.
- Jeder Aktor prüft ihn als Erstes.
- Der MQTT-Client verweigert das Veröffentlichen zusätzlich, solange er nicht scharf
  gebaut wurde — auch wenn ein Aufrufer es ausdrücklich verlangt.
- Tests belegen beides, **und** den Gegenbeweis: Ein scharf gebauter Client sendet
  wirklich. Ohne den belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn
  das Senden gar nicht gebaut wäre.

## Zahlen

Vom Controller selbst nachgeprüft, nicht aus Berichten übernommen:

| | |
|---|---|
| Tests | grün unter SQLite **und** MariaDB |
| Testabdeckung | 99 %, Mindestschwelle 97 % in der CI |
| Ruff, mypy strict | ohne Befund |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken |
| Echte Anlagendaten | zehn Zustandsnachrichten durch den Ingest: 10 Geräte, 37 Messwerte, in der Oberfläche sichtbar |

## Was in dieser Runde gefunden wurde

Vier Fehler, die alle Tests und Reviews passiert hatten:

1. **Zwei Wächtertests prüften nichts.** Seit FastAPI 0.141 verschachtelt
   `include_router()` die Routen; beide Wächter fanden nur noch `/healthz` und waren grün,
   weil sie leer liefen. Behoben, mit Gegenprobe.
2. **Der Testlauf hing an einer zufällig vorhandenen `.env`.** In der CI wäre der nächste
   Lauf rot geworden. Nachgestellt in einem frischen Worktree.
3. **Die Startseite baute eine eigene Vorlagen-Umgebung mit relativem Pfad.** Im Container
   liegt das Paket in `site-packages` und das Arbeitsverzeichnis ist `/app` — dort hätte
   genau die Seite gefehlt, auf die Anmeldung und Navigation zeigen. Ein Wächter in
   `test_architektur.py` verhindert die Wiederholung.
4. **Ein zu kurzes Passwort hinterließ eine halb angelegte Einrichtung**, weil die Prüfung
   erst nach den ersten Schreibzugriffen kam. Der zweite, korrekte Versuch scheiterte dann.

Dazu zwei sicherheitsrelevante Korrekturen aus der Gegenlesung in der Hauptsession:

- **Bei ausgefallenem Sensor** schaltete die Regelung dauerhaft ab. Das ist die
  gefährlichere Antwort — genau so friert im Januar eine Leitung ein. Jetzt gilt der
  Frostschutz-Sollwert. Begründung und Restrisiko in
  [offene-entscheidungen.md](offene-entscheidungen.md).
- **Der MQTT-Wiederverbindungsabstand** wuchs über die Lebensdauer des Dienstes monoton
  weiter; eine Verbindung, die nach Tagen einmal abreißt, hätte eine Minute gewartet.

## Offen

**Was nur der Projektinhaber kann:**

- **Phase 2 wirklich abschließen.** Die Anlage muss über mehrere Tage laufen:
  `THERMOCTL_MQTT_ENABLED=true` samt Zugangsdaten in `.env`, dann Geduld. Erst dann steht
  fest, dass plausible Ist-Temperaturen aller Zonen einlaufen.
- **Die Meross-Zugangsdaten** hinterlegen. Der Adapter ist gebaut, sein Nutzlastaufbau ist
  aber eine begründete Annahme und nie gegen ein echtes Konto gelaufen — vor dem
  Scharfschalten zu prüfen.
- **Das Repository öffentlich schalten** (Phase 5, Aufgabe 9). Ausdrücklich seine
  Entscheidung.
- **Die Entscheidung zum Frostschutz bei Sensorausfall bestätigen**, bevor Phase 4 scharf
  schaltet.

**Was als Nächstes ansteht:**

- Teilprojekt 3 zu Ende bringen: Zeitplan-Editor, Gerätezuordnung samt Tausch,
  Regelparameter je Zone, Rechte- und Tokenverwaltung, Übersichtsseite.
- Danach Phase 4: Vergleichsbetrieb gegen das Altsystem, Datenübernahme aus dem Altschema,
  Scharfschalten hinter einem Schalter.
- `vm130-nginx` bleibt bis zum abgeschlossenen Cutover unverändert die Rückfallebene.
