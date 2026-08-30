# Bericht: Steuergeräte konfigurierbar

## Gebaut

- Zigbee2MQTT-`exposes` werden rekursiv als Gerätemerkmale mit Typ, Einheit,
  Wertebereich, Lese-/Schreibzugriff und normalisierten Auswahlwerten übernommen.
- Migration `e4b8a21c7f10` legt `device_property`, `device_property_value`,
  `channel_kind` und `controller_channel` an und füllt die Kanalarten.
- Lesekanäle setzen Zonensollwert oder Betriebsart; bereits verarbeitete Werte werden
  anhand ihres Messzeitpunkts nicht erneut angewendet.
- Schreibkanäle liefern Sensor-/Zonentemperatur, Zonensollwert oder einen festen Wert an
  `<mqtt_base_topic>/<external_id>/set`. Identische Werte werden nicht wiederholt und
  alle Nachrichten tragen `switches=False`.
- Die Domäne erlaubt Schreibkanäle ausschließlich auf Geräten mit der Rolle
  `controller`; der Veröffentlichungszyklus prüft diese Sicherheitsbedingung erneut.
- `/controllers` zeigt je Bediengerät eingehende Werte, ausgehende Belegungen und die
  Tastenbelegung. Navigation und Zonengeräteseite verweisen dorthin. Das bestehende
  `zuordnung.js` bedient auch das Ziehen einer Temperaturquelle; ohne JavaScript bleibt
  dasselbe Formular vollständig nutzbar.
- Neue Domänen-, Parser-, Versand- und Webtests prüfen insbesondere den Aktor-Riegel,
  Zugriff/Range/Auswahlwerte und das Unterdrücken unveränderter Sendewerte.

## Entscheidungen

- `device_property` speichert zusätzlich den letzten Wert und dessen Messzeitpunkt.
  Der Entwurf verlangt, dass die Seite den letzten Wert jedes lesbaren Merkmals zeigt
  und wiederholte Lesewerte erkennt, nennt dafür aber keine eigene Zustandstabelle.
  Die Zusatzspalten halten diese beiden Aussagen am Merkmal selbst fest, ohne JSON oder
  modellbezogene Sonderbehandlung.
- Für `operating_mode` wird der empfangene Text gegen die vorhandene
  `operating_mode`-Nachschlagetabelle aufgelöst. Es gibt keine fest codierte Übersetzung
  von Zigbee-Modellwerten; unbekannte Werte werden abgewiesen und protokolliert.
- Die Zonengeräteseite zeigt aus Kompatibilitätsgründen noch die gesehenen Tastennamen
  und den Hinweis auf den neuen Ort, bearbeitet wird die Belegung nur unter
  `/controllers`.

## Offene Blocker

Keine offenen Implementierungsblocker. Nicht automatisierbar in dieser Umgebung ist die
Abnahme am echten Bediengerät: `sensor: external` setzen, eine externe Temperatur auf dem
Display prüfen und einen am Gerät gedrehten Sollwert zurücklesen. Dieser Punkt steht auch
in `docs/STATUS.md`.

## Prüfergebnisse

### `ruff check .`

```text
All checks passed!
```

### `mypy thermoctl`

```text
Success: no issues found in 90 source files
```

### `pytest -q --cov-fail-under=97`

```text
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
........................................................................ [ 43%]
........................................................................ [ 50%]
........................................................................ [ 57%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 79%]
........................................................................ [ 86%]
........................................................................ [ 93%]
.................................................................        [100%]
TOTAL                                         5037    114    98%
Required test coverage of 97% reached. Total coverage: 97.74%
```

### MariaDB

Befehl:

```text
THERMOCTL_TEST_DATABASE_URL="mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test" .venv/bin/pytest -q --cov-fail-under=97
```

Ergebnis:

```text
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
........................................................................ [ 43%]
........................................................................ [ 50%]
........................................................................ [ 57%]
......................................s................................. [ 64%]
........................................................................ [ 71%]
........................................................................ [ 79%]
........................................................................ [ 86%]
........................................................................ [ 93%]
.................................................................        [100%]
TOTAL                                         5037    114    98%
Required test coverage of 97% reached. Total coverage: 97.74%
```

Die eine MariaDB-Auslassung ist der bestehende SQLite-spezifische Test. Die
Migrationstests liefen in beiden Suiten einschließlich Upgrade und Downgrade; der direkte
Kopfcheck meldet `e4b8a21c7f10 (head)`.
