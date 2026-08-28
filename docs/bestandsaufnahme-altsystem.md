# Bestandsaufnahme Altsystem

Stand: 2026-08-28. Grundlage für den Neubau als `thermoctl`.

Dieses Dokument beschreibt das **abzulösende** System. Es existiert, damit spätere Sessions
nicht erneut zwei fremde Projekte durchsuchen müssen. Alles hier Beschriebene ist Ist-Zustand,
**kein** Zielbild — das steht in der Design-Spezifikation.

> **Keine Zugangsdaten in diesem Repo.** Das Altsystem enthält reale Passwörter und Tokens
> hartcodiert im Quelltext. Dieses Dokument benennt nur, *welche* Zugänge existieren und wofür.
> Die Werte werden bewusst nicht übernommen — `thermoctl` soll veröffentlichbar sein.

## 1. Wo das Altsystem liegt

| Ort | Inhalt | Versionskontrolle |
|---|---|---|
| `~/Documents/Code Projekte/PycharmProjects/python-script-runner` | Die vier Heizungs-Python-Services (plus neun fachfremde Services) | Git |
| `~/Documents/Code Projekte/PhpstormProjects/vm130-nginx` | PHP-Steuerungsoberfläche | **kein Git** |

Der `python-script-runner` ist ein Homelab-Repo mit einem zentralen Supervisor (`main.py`,
Docker SDK, Port 3250) und einem Container pro Skript. Die Heizung ist dort der einzige
Block, dessen Skripte sich gegenseitig aufrufen — alle übrigen Services sind unabhängig.
Die Ablösung entfernt also eine Ausnahme, statt ein Muster zu brechen.

## 2. Die vier aktiven Heizungs-Services

| Service | Port | Aufgabe |
|---|---|---|
| `heizungSteuerungV2.py` | – | Regelschleife. Pollt alle Räume mit `autoHeatControl='true'`, vergleicht Ist-/Soll-Temperatur, schaltet Meross-Ventile, stößt danach die MQTT-Neuveröffentlichung an. |
| `heizungZigbeeSensorConnector.py` | – | Abonniert `zigbee2mqtt/<sensorFriendlyName>`, schreibt Ist-Temperatur in `thermostate`, propagiert Sollwerte an gekoppelte Zigbee-Heizkörperventile. Einziger Service mit echtem Connection-Pool. |
| `heizungThermostatConnectorMQTT-HTTP.py` | 3253 | Bidirektionale Brücke DB ↔ MQTT für Home Assistant. Nimmt HTTP- *und* MQTT-Set-Befehle entgegen. |
| `heizungMerossConnector.py` | 3254 | Dünner Wrapper um die Meross-Cloud-API (`meross_iot`), schaltet MSS710-Steckdosen, die als Ventile fungieren. |

Kopplung untereinander: `heizungSteuerungV2` ruft die beiden anderen HTTP-Services über
Docker-Compose-Servicenamen auf (`heizungMerossConnector:3254`, `heizungThermostatConnectorMQTT-HTTP:3253`).
Alle vier teilen sich die MySQL-Datenbank `smartHome` als impliziten Vertrag — ohne Migrationswerkzeug.

Legacy und bereits abgelöst (Homebridge-Generation, in `main.py` auskommentiert):
`heizungSteuerung.py`, `heizungHomebridgeConnector.py`, `heizungHistory.py`,
`helper/reassignHomebridgeUIDs.py`. Für `thermoctl` irrelevant außer als Warnung, dass
das Repo Altlasten nicht entfernt, sondern nur deaktiviert.

## 3. Datenbankschema (MariaDB, Datenbank `smartHome`)

### `rooms`
```
ID                   int(8) PK auto_increment
name                 varchar(255) NOT NULL
friendlyName         varchar(255) NOT NULL
thermostatId         int(8)          -> FK thermostate(ID), ON UPDATE CASCADE
sensorFriendlyName   varchar(255) NOT NULL   -- Zigbee2MQTT-Gerätename des Temperatursensors
valveIdMeross        varchar(64)             -- Meross-Geräte-UUID (Steckdose als Ventil)
valveIdRadiatorList  varchar(255)            -- KOMMASEPARIERTE Liste Zigbee-Ventilnamen
autoHeatControl      set('true','false') NOT NULL default 'true'
```

### `thermostate`
```
ID                                     int(8) PK auto_increment
name                                   varchar(255) NOT NULL
friendlyName                           varchar(255) NOT NULL
type                                   enum('virtual-underfloor','physical-radiator') NOT NULL
temperatureTargetDay                   float NOT NULL default 28
temperatureTargetNight                 float NOT NULL default 28
temperatureTargetNightHours            text NOT NULL default '[[],[],[],[],[],[],[],[]]'
temperatureActual                      float NOT NULL default 15
thermostatTargetState                  enum('off','heat','cool','auto') NOT NULL default 'off'
thermostatActualState                  enum('off','heat','cool','auto') NOT NULL default 'off'
homeassistantSelectedPreset            enum('auto','Tag','Nacht','none') NOT NULL default 'auto'
homeassistantSelectedPresetLastChange  int(10) NOT NULL default 0
zigbeeFriendlyName                     varchar(255)
```

### `heizung_conf`
Schlüssel-Wert-Tabelle (`UUID`, `name`, `value` als Text). Tatsächlich genutzte Schlüssel:
`POLLING_RATE` (Sekunden zwischen Regelzyklen), `OFF_TARGET_TEMP` (Frostschutz-Sollwert,
Fallback 16.0), `lastSeen` (Unix-Zeitstempel, den die Regelschleife bei jedem Durchlauf schreibt —
dient als Lebenszeichen).

### Legacy-Tabellen
`heizung`, `heizung_bck`, `heizung_bck1224`, `history` — Homebridge-Generation plus zwei
Backup-Kopien. Werden von der aktiven Kette nicht gelesen.

## 4. Fallstricke im Ist-Schema

Diese Punkte sind der Grund, warum ein neues Schema nötig ist — nicht nur ein neues Repo:

1. **`temperatureTargetNightHours` ist ein positionell interpretierter JSON-Blob.**
   Ein Array mit acht Slots: Index 0 bleibt ungenutzt, Index 1–7 entsprechen `isoweekday()`
   (Montag = 1). Jeder Slot enthält Stunden als **Strings** (`"0"`–`"23"`), zu denen der
   Nacht-Sollwert gilt. Geprüft wird per `if str(current_hour) in down_temp_array[current_day]`.
   Geschrieben wird der Blob von der PHP-Oberfläche, die ihn aus Formularfeldnamen der Form
   `<roomID>-//-<tag>-//-<stunde>` zusammenbaut. Keinerlei Validierung auf beiden Seiten.
   Auflösung des Zeitplans: eine Stunde. Kein Konzept für Feiertage, Urlaub oder Ausnahmen.

2. **Keine Hysterese in der Regellogik.** `heizungSteuerungV2.process_rooms()` ist wörtlich
   `if ist_temp < soll_temp: set_valve('on') else: set_valve('off')`. Bei einer Ist-Temperatur
   am Sollwert schaltet das Ventil in jedem Zyklus (Standard 30 s) um. Das ist ein echter
   Defekt, kein Stilproblem — `thermoctl` braucht Hysterese und eine Mindestschaltdauer.

3. **`autoHeatControl` als `set('true','false')`** statt Boolean. Kann laut Typdefinition
   auch beides gleichzeitig oder nichts enthalten. Abgefragt wird per String-Vergleich.
   In SQLite ohnehin nicht abbildbar.

4. **`heizung_conf` als EAV-Tabelle** — jeder Wert ist Text, jede Nutzung castet selbst,
   jeder Fehler fällt erst zur Laufzeit auf.

5. **`valveIdRadiatorList` als kommaseparierter String** statt Zuordnungstabelle.

6. **`rooms` ↔ `thermostate` ist faktisch 1:1**, aber als FK mit zwei Tabellen modelliert.
   Die Trennung trägt keine erkennbare Bedeutung; Regellogik und Zeitplan hängen am
   Thermostat, Geräteanbindung am Raum.

7. **`thermostatTargetState` kennt `cool`**, was nirgends implementiert ist.

8. **`type` unterscheidet `virtual-underfloor` und `physical-radiator`**, was faktisch der
   Unterschied zwischen "Meross-Steckdose schaltet Fußbodenheizungskreis" und
   "Zigbee-Heizkörperthermostat" ist — also eine Geräteeigenschaft, die als Thermostat-Typ
   modelliert wurde.

## 5. Externe Schnittstellen, die beim Umstieg weiterlaufen müssen

### MQTT-Topics (Vertrag mit Home Assistant)
Veröffentlicht von `heizungThermostatConnectorMQTT-HTTP.py`:
```
heizung/thermostate/<id>/preset_mode/get
heizung/thermostate/<id>/thermostatTargetState/get
heizung/thermostate/<id>/thermostatActualState/get
heizung/thermostate/<id>/thermostatActualStateHA/get
heizung/thermostate/<id>/temperatureTarget/get
heizung/thermostate/<id>/temperatureActual/get
heizung/thermostate/<id>/availability/get
heizung/config/POLLING_RATE/get        (retained)
heizung/config/OFF_TARGET_TEMP/get     (retained)
heizung/config/lastSeen/get            (retained)
```
Abonniert: `heizung/#` (Set-Befehle unter `heizung/thermostate/<id>/<attribut>/set` und
`heizung/config/<schlüssel>/set`, dazu `heizung/thermostate/refresh` mit Payload `force`)
sowie `homeassistant/status` (bei `online` wird alles neu veröffentlicht).

Zigbee2MQTT: abonniert `zigbee2mqtt/<sensorFriendlyName>`, publiziert nach
`zigbee2mqtt/<ventilname>/set`.

**Wichtig:** Diese Topic-Struktur ist gewachsen, nicht entworfen (`.../get` als Suffix für
State-Topics ist unüblich). `thermoctl` sollte eine saubere eigene Struktur mit echter
Home-Assistant-MQTT-Discovery definieren und die Alt-Topics höchstens übergangsweise
zusätzlich bedienen.

### HTTP-Routen
`heizungThermostatConnectorMQTT-HTTP.py` (3253): `/init_thermostats`,
`/init_thermostat/<id>`, `/get/<id>/temperatureTarget/no-preset`,
`/update_thermostat/<id>/<topic>`.
`heizungMerossConnector.py` (3254): `/devices`, `/devices/<uuid>/status`, `/devices/<uuid>/<action>`.

Keine dieser Routen hat Authentifizierung. Im Altsystem war das eine bewusst akzeptierte
Heimnetz-Eigenschaft; für `thermoctl` ist es explizit nicht mehr akzeptabel.

### Externe Systeme
MariaDB (`192.168.0.130`), MQTT-Broker über TLS auf Port 8883, Zigbee2MQTT, Home Assistant
(als MQTT-Konsument), Meross-Cloud-API (Login mit E-Mail/Passwort), Telegram (Statusmeldungen
der Regelschleife).

## 6. Die PHP-Oberfläche (`vm130-nginx`)

Kein Framework, kein Git, kein Build. Relevante Teile:

- `index.php` — die eigentliche Heizungs-Oberfläche. Öffnet eine PDO-Verbindung **als
  `root`**, liest `rooms WHERE autoHeatControl = true`, rendert ein Stundenraster und
  schreibt beim Absenden `temperatureTargetNightHours` direkt per `UPDATE` in `thermostate`.
  Keine Authentifizierung, keine Validierung, kein CSRF-Schutz.
- `heizung-history/`, `history/`, `API/heizung_getTemp.php`, `API/checkHeizung.php`,
  `getIstTemp.php`, `hbr_thermostat/istTemp.php` — Verlaufsansichten und kleine Endpunkte,
  teils noch gegen das Homebridge-Altschema.
- `zeiten.php`, `ALT/zeiten.php` — ältere Varianten der Zeitplan-Oberfläche.
- Bootstrap 5 und jQuery liegen als Dateien in `src/`, PWA-Manifest und Service Worker
  sind vorhanden (die Oberfläche wird offenbar mobil als installierte Web-App genutzt).

Der Rest des Projekts (Tibber-Preise, Mond-Beleuchtung, Roku, Lautsprecher, App-Übersicht)
gehört **nicht** zur Heizung und bleibt dort bestehen. Abgelöst wird nur der Heizungsteil.

## 7. Was die Ablösung am Ende umfasst

1. Heizungsteil aus `vm130-nginx` entfernen (nach Cutover).
2. Die vier Heizungsskripte aus `python-script-runner` entfernen, samt ihrer
   `manager.add_script(...)`-Registrierungen in `main.py` und ihrer Compose-Services.
3. Legacy-Heizungsskripte und Legacy-Tabellen bewerten und abräumen.
4. Datenübernahme aus `rooms`/`thermostate`/`heizung_conf` ins neue Schema.

Punkt 1 und 2 sind explizit **nicht** Teil der ersten Teilprojekte — sie hängen am
erfolgreichen Cutover und sind dort eingeplant.
