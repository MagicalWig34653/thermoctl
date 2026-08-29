# Implementierungsplan — Teilprojekt 2

Zur [Spezifikation](../specs/2026-08-29-teilprojekt-2-geraete-schattenbetrieb-design.md).
Stand: 2026-08-29.

## Global Constraints

Gilt für jede Aufgabe. Wer sie verletzt, wird im Review zurückgewiesen.

1. **Trockenlauf.** Kein `publish()`, kein schaltender Fremdaufruf. `setting.control_armed`
   bleibt `false` und wird in dieser Phase nirgends gesetzt.
2. **Nichts hart verdrahtet.** Keine Gerätenamen, Topics, Broker-Adressen, Zugangsdaten im
   Quelltext. Auch nicht in Tests als „Beispiel" — Testdaten kommen aus
   `tests/daten/anlage-beispiele.json` oder aus Fixtures.
3. **Datenbankagnostisch.** Kein ENUM, kein SET, keine JSON-Spalte, keine
   datenbankspezifische Funktion. `Numeric`, nicht `Float`.
4. **Genau eine Migration** für die ganze Phase, aus Aufgabe 1. Keine andere Aufgabe legt
   eine an oder fasst `down_revision` an.
5. **Domäne kennt keinen Adapter.** `thermoctl/domain/*` importiert kein `fastapi`, kein
   `aiomqtt`, kein `httpx`.
6. **Zu jedem Endpunkt und jeder Funktion ein Test**, der etwas belegt. Ein Test, der nur
   bestätigt, was der Code ohnehin tut, zählt nicht.
7. **Blocker melden, nicht raten.** Fast alle Blocker in Teilprojekt 1 waren Fehler im
   Plan, nicht in der Umsetzung. Wer einen vorgegebenen Test „passend macht", verdeckt sie.
8. Jede Aufgabe bekommt einen eigenen Worktree, einen eigenen Branch und eine eigene
   Testdatenbank.

## Aufgaben

### 1. Schema und Migration — *zuerst, blockiert 4, 5, 6, 8, 9*
Modelle `Measurement`, `ZoneState`, `DeviceHealth`, `ShadowDecision`, Nachschlagetabelle
`SensorStatus`; neue `device_capability`-Zeilen; `device.is_group`; drei neue
`setting`-Spalten. Eine Alembic-Migration, vorwärts und rückwärts lauffähig, gegen beide
Datenbanken. Anlegefunktionen in `tests/hilfen.py`. Tests je Modell für Bedingungen und
Fremdschlüssel.

### 2. Nutzlast-Auswertung — *unabhängig*
`thermoctl/domain/beobachtung.py`: eine Zigbee2MQTT-Nutzlast → Liste von `Beobachtung`
(Fähigkeitscode, Zahl- oder Textwert, Messzeitpunkt). Rein, ohne Datenbank.
Tests **gegen `tests/daten/anlage-beispiele.json`**: jede der zehn echten Nachrichten wird
ausgewertet, die erwarteten Werte stehen im Test. Dazu die Randfälle aus Abschnitt 2 der
Spezifikation: `null`, verschachtelte Objekte, fehlendes `last_seen`, unbekannte Felder,
kaputtes JSON.

### 3. Geräteklassifikation — *unabhängig*
`thermoctl/domain/geraeteklassen.py`: aus einem Eintrag von `bridge/devices`
(`definition.exposes`) die Fähigkeiten ableiten; Brücke und Gruppen aussortieren; Modell
und Hersteller mitnehmen. Muss Ventil (`current_heating_setpoint`) und Fensterkontakt
(`contact`) erkennen, **ohne** dass eine Zustandsnachricht davon vorliegt.

### 4. MQTT-Client — *unabhängig von 1*
`thermoctl/integrations/mqtt/client.py` und `zigbee2mqtt.py`: Verbindung, TLS,
Zugangsdaten aus `Settings`, Wiederverbindung mit wachsendem Abstand, Abonnements aus
Abschnitt 5 der Spezifikation, Zustellung an einen übergebenen Handler. Topic-Zuschnitt als
reine Funktion (Topic → Art der Nachricht und Gerätename), damit sie ohne Broker prüfbar
ist. `publish()` verweigert im Trockenlauf — mit Test.
Neue Abhängigkeit: `aiomqtt`.

### 5. Ingest — *nach 1, 2, 3*
`thermoctl/services/ingest.py`: Beobachtungen → `measurement`, `device_health`,
`device.last_seen_at`; unbekannte Geräte werden angelegt, nicht verworfen; Zuordnung zur
Zone über `zone.temperature_source_device_id` schreibt `zone_state`.
Dazu `services/aufbewahrung.py`: Messwerte älter als `measurement_retention_days`
löschen, in Blöcken, mit Protokollzeile.

### 6. Regelentscheidung — *unabhängig, sicherheitsrelevant*
`thermoctl/domain/regelung.py` nach Abschnitt 6 der Spezifikation. Reine Funktion,
erschöpfend getestet: jede der sechs Regeln einzeln, ihre Rangfolge gegeneinander, und
ausdrücklich der Fall, an dem das Altsystem scheitert (`ist == soll` schaltet nicht um).
**Wird in der Hauptsession gegengelesen** (Grundsatz 7).

### 7. Störungserkennung — *nach 1*
`thermoctl/domain/stoerung.py`: aus jüngstem Messzeitpunkt, Jetzt und Timeout den
Sensorzustand bestimmen. Rein. Grenzfälle: genau auf der Grenze, keine Messung, Messung
aus der Zukunft.

### 8. Aktoren im Trockenlauf — *nach 1, 4*
`thermoctl/integrations/aktoren.py`: Schnittstelle plus Zigbee-Ventil und Meross-Schalter,
vollständig gebaut, im Trockenlauf nur protokollierend. Test, dass ohne `control_armed`
nichts hinausgeht — auch dann nicht, wenn der Aufrufer es verlangt.

### 9. Schattenlauf — *nach 1, 5, 6, 7*
`thermoctl/services/schattenlauf.py`: je Zone Lage zusammenstellen, `aufgeloester_sollwert`
und `regelung.entscheiden` aufrufen, Ergebnis nach `shadow_decision` schreiben. Läuft als
Hintergrundaufgabe im Lifespan, Abstand aus `setting.shadow_interval_seconds`, abschaltbar
über `mqtt_enabled`/eigene Einstellung. Test über mehrere Zyklen, dass eine unveränderte
Lage `unveraendert` ergibt und keine Zeilenflut entsteht.

### 10. Oberfläche und API — *nach 1, 5*
`/geraete` lesend, Zonenzustand auf der Startseite, `GET /api/v1/devices` und
`GET /api/v1/zones/{id}/state`. Rechte: `device.read` beziehungsweise `zone.read`.
**Anwendung danach wirklich starten und die Seite öffnen.**

## Reihenfolge und Parallelität

```
1 ─┬─ 5 ─┬─ 9
   ├─ 7 ─┘
   ├─ 8
   └─ 10
2 ─┘
3 ─┘
4 ─┴─ 8
6 ─────── 9
```

Parallel startbar: 1, 2, 3, 4, 6. Danach 5, 7, 8, 10. Zuletzt 9.

## Review

Kreuzweise: Wer implementiert hat, reviewt nicht. Jedes Review führt die Testsuite **selbst**
aus — gegen beide Datenbanken, dazu Ruff und mypy — und berichtet das Ergebnis.
Aufgabe 6 wird zusätzlich in der Hauptsession gegengelesen.
