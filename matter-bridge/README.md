# thermoctl Matter-Bridge (optional)

Ein eigener, **optionaler** Node-Dienst, der thermoctl-Zonen über MQTT als Matter-Thermostate in Apple Home
(und, mit Einschränkungen, Google Home) sichtbar macht. thermoctl selbst braucht ihn nicht und enthält keinen
Node-Code; die Bridge läuft als eigener Container neben thermoctl und spricht nur MQTT.

**Nicht zertifiziert.** Die Bridge verwendet die Matter-Test-Hersteller-ID `0xFFF1` und behauptet keine
Matter-Zertifizierung. Apple Home fragt beim Hinzufügen deshalb nach („Trotzdem hinzufügen“ / *Add Anyway*);
Google Home verlangt für Test-Hersteller-IDs ein Entwicklerkonto und Googles Testverfahren
([Pairing und Test-VID](https://developers.home.google.com/matter/integration/pair)).
**Nicht an einer echten Apple-Home- oder Google-Home-Installation geprüft** -- siehe „Geräteprüfung“ unten.

## Was die Bridge tut

Je konfigurierter Zone entsteht unter einem Matter-Aggregator ein Thermostat, das nur **Aus** und **Heizen**
kennt. Es gibt kein Kühlen und kein Matter-„Auto“ (das bräuchte Kühl-Sollwert und Totband).

| Matter | MQTT (Präfix `thermoctl`, Standard) |
|---|---|
| Betriebsart **Aus** | `thermoctl/zones/<id>/command/operating_mode` = `off` |
| Betriebsart **Heizen** | `thermoctl/zones/<id>/command/operating_mode` = `auto` (fest, nicht einstellbar; in thermoctl verhalten sich `manual` und `auto` gleich) |
| Sollwertrad | `thermoctl/zones/<id>/command/temporary_setpoint`, Dezimalzahl in °C mit höchstens einer Nachkommastelle (`21.5`), QoS 1, kein Retain |
| Anzeige Ist | `state/current_temperature` |
| Anzeige Soll | `state/setpoint` |
| Anzeige Betriebsart | `state/operating_mode`: `off` wird Aus, `manual` und `auto` werden Heizen |
| Anzeige „heizt“ | `state/would_heat` |
| (nur gelesen, ohne Matter-Gegenstück) | `state/override_active` |

- **Das Sollwertrad ist eine befristete Übersteuerung.** Es ändert nie die dauerhafte Modus-Temperatur; danach
  gilt wieder der Zeitplan. Wie lange sie dauert (Standard: bis zur nächsten Schaltung), stellt man in thermoctl
  ein, nicht in der Bridge. Die Bridge sendet **nie** `command/setpoint`.
- **Voraussetzung auf der thermoctl-Seite:** der Befehl `command/temporary_setpoint` muss dort implementiert
  sein. Diese Bridge liefert nur die Matter-Seite; ohne ihn bleibt jeder Radschritt wirkungslos (das Rad springt
  nach 500 ms auf den gemeldeten Sollwert zurück).
- **Sollwert-Writes bei Matter-Modus Aus werden abgelehnt**, ebenso Werte außerhalb der Grenzen, Werte, die
  nicht auf einem Zehntelgrad liegen, Kühlmodi und Auto. Die Grenzen sind standardmäßig die von thermoctl
  (−20 bis 35 °C, `thermoctl/domain/modes.py`) und lassen sich nur **einengen**. Meldet thermoctl einen Sollwert
  außerhalb der eingestellten Grenzen, gilt die Zone als nicht erreichbar und die Bridge warnt.
- **Zustand kommt nur aus MQTT.** Nach einem Write vergleicht die Bridge nach 500 ms mit dem zuletzt empfangenen
  Zustand: Ein von thermoctl abgelehntes Rad springt zurück, ein angenommenes ist dann längst bestätigt. Es gibt
  kein Echo: Die Bridge sendet nur, was ein Matter-Controller geschrieben hat, nie, was sie über MQTT empfängt.
  Reihenfolge der empfangenen Zustände entscheidet; ein Zustand, der unmittelbar nach einem Write eintrifft,
  aber noch vor der Verarbeitung entstanden ist, kann das Rad für etwa eine Sekunde kurz zurückspringen lassen.
- **Entprellung (200 ms, letzter Wert gewinnt).** Apple Home schreibt je Radschritt einen Wert. Ohne Entprellung
  würde jeder Zwischenwert in thermoctl eine eigene befristete Übersteuerung mit Audit-Eintrag erzeugen. Betriebsart-
  Wechsel werden nicht entprellt.
- **Erreichbarkeit.** Verliert die Bridge die MQTT-Verbindung, wird der Cache geleert, alle Zonen melden
  *nicht erreichbar* (Matter `Reachable=false`, Temperatur leer), Writes werden abgelehnt, und nach der
  Wiederverbindung gleicht die Bridge neu ab. **Retained-Nachrichten gelten nicht als frisch:** Sie tragen keinen
  Zeitstempel und könnten aus der Zeit vor einem Ausfall von thermoctl stammen. Die Bridge wartet auf den
  nächsten live veröffentlichten Zustand.
- **Maximalalter (`MATTER_BRIDGE_MAX_AGE_SECONDS`, Standard 300).** thermoctl veröffentlicht alle Zustände **in
  jedem Regelzyklus** (Standard 60 s, Einstellung `shadow_interval_seconds`), auch unveränderte, nicht nur bei
  Änderung (`thermoctl/services/publishing.py`). Ein Wert, der länger als das Maximalalter nicht erneuert wurde,
  gilt deshalb als veraltet: Sollwert oder Betriebsart veraltet → Zone nicht erreichbar; Istwert oder Heizbedarf
  veraltet → nur dieser Wert fehlt. thermoctl veröffentlicht einen leeren Wert **nicht** (ein Fühlerausfall zeigt
  sich daher nur durch das Ausbleiben des Istwerts); so wird das Fehlen nach dem Maximalalter sichtbar. **Das
  Maximalalter muss mindestens das Dreifache des Regelzyklus betragen**, sonst flackern die Zonen. Nach einem
  Start der Bridge dauert es bis zu einen Zyklus, bis die Zonen erreichbar werden.
- **Ein Zonenfehler stört die anderen nicht.** Jede Zone hat eigenen Cache, eigene Entprellung und eigenen Abgleich.
- **Feindliche oder kaputte Nutzlasten** (`NaN`, `Infinity`, `21.00`, Hexadezimal, Exponent, Nicht-ASCII-Ziffern,
  Binärdaten, 10 MB) werden verworfen, nie umgerechnet. Ein ungültiger Sollwert oder eine ungültige Betriebsart
  macht die Zone nicht erreichbar, ein ungültiger Istwert zeigt „keine Temperatur“, ein ungültiger Heizbedarf
  „heizt nicht“. Je Übergang steht eine Warnung im Log, ohne den Inhalt zu nennen.

## Konfiguration

Umgebungsvariablen haben Vorrang vor der optionalen JSON-Datei (`MATTER_BRIDGE_CONFIG=/pfad/bridge.json`, Felder
in der Tabelle in Klammern). Ungültige Konfiguration bricht den Start mit einer klaren Meldung ab; das Passwort
steht in keiner Meldung.

| Variable (JSON-Feld) | Standard | Bedeutung |
|---|---|---|
| `MATTER_BRIDGE_MQTT_URL` (`mqttUrl`) | **Pflicht** | `mqtt://host:1883` oder `mqtts://host:8883`, ohne Zugangsdaten in der URL |
| `MATTER_BRIDGE_MQTT_USERNAME` (`mqttUsername`) | -- | Benutzer; die Bridge braucht ein eigenes Konto |
| `MATTER_BRIDGE_MQTT_PASSWORD` (`mqttPassword`) | -- | Passwort (nie in Logs); setzt einen Benutzer voraus |
| `MATTER_BRIDGE_TOPIC_PREFIX` (`topicPrefix`) | `thermoctl` | wie `THERMOCTL_MQTT_PREFIX` in thermoctl |
| `MATTER_BRIDGE_ZONES` (`zones`) | **Pflicht** | JSON-Liste `[{"id": 1, "name": "Wohnzimmer"}]`; `id` ist die thermoctl-Zonen-ID (positive Ganzzahl, eindeutig), `name` die Anzeige in Home (1 bis 32 UTF-8-Bytes) |
| `MATTER_BRIDGE_MIN_SETPOINT` / `_MAX_SETPOINT` (`minSetpoint`, `maxSetpoint`) | `-20` / `35` | Sollwertgrenzen in °C, Zehntelgrad, innerhalb −20 bis 35 |
| `MATTER_BRIDGE_MAX_AGE_SECONDS` (`maxAgeSeconds`) | `300` | Maximalalter eines Zustands, siehe oben |
| `MATTER_BRIDGE_MATTER_PORT` (`matterPort`) | `5540` | Matter-Port (TCP und UDP) |
| `MATTER_BRIDGE_STORAGE_DIR` (`storageDir`) | `./matter-storage` (im Abbild `/data`) | Speicherverzeichnis, siehe „Persistenz“ |
| `MATTER_BRIDGE_LOG_LEVEL` (`logLevel`) | `info` | `error`, `warn`, `info`, `debug`; Matter.js-Meldungen kommen bei `info` ab `warn` |

Namen sind keine Identität: Zonen werden über die ID erkannt. Eine Zone **umzubenennen** ändert nur den
angezeigten Namen (beim nächsten Start). Eine Zone aus der Liste zu **entfernen** macht ihren Thermostaten in
Home unerreichbar; ihre Endpoint-Nummer wird nie an eine andere Zone vergeben. Eine **neue** Zone bekommt eine neue
Nummer. Eine später wieder eingetragene Zone bekommt ihre alte Nummer und Identität zurück.

Meldungen gehen nach stderr, auf stdout steht ausschließlich der Kopplungscode (einmalig).

## Kopplung

Beim **ersten Start** (nicht gekoppelt) schreibt die Bridge **genau einmal** auf stdout:

```
Matter-Kopplungscode: 12345678901
```

Der Code steht danach nie wieder in Logs oder Fehlermeldungen (auch nicht bei `debug`; die Standardausgabe von
Matter.js dafür ist abgeschaltet). Im Container also einmal `docker logs <container>` lesen.

1. **Apple Home:** *Gerät hinzufügen* → *Weitere Optionen* → Code eingeben → bei der Warnung zu nicht
   zertifiziertem Zubehör *Trotzdem hinzufügen*. Ein Home-Hub (HomePod, Apple TV) wird für die Fernsteuerung
   außer Haus gebraucht.
2. **Google Home (zweiter Controller, Multi-Admin):** Der Standard-Kopplungscode gilt nur für die **erste**
   Kopplung. Für weitere Controller öffnet der erste Controller selbst ein Kopplungsfenster an der Bridge
   (je App-Version „Pairing-Modus“, „Zubehör teilen“ bzw. in Google Home „Verknüpfte Apps“; die genauen
   Bezeichnungen sind nicht geprüft). Das ist reines Matter (`OpenCommissioningWindow`); die Bridge gibt dafür keinen
   weiteren Code aus und braucht keine eigene Funktion. Im Test mit zwei Controllern an der Bridge nachgewiesen
   (`test/multiadmin.test.mjs`, virtuelles Netz); mit echtem Apple Home und Google Home nicht geprüft.
3. **Code verloren, noch nie gekoppelt:** Speicherverzeichnis leeren und neu starten (es gibt nichts zu
   verlieren; ein neuer Code erscheint). **Schon gekoppelt und aus allen Home-Apps entfernt:** ebenso.
   **Niemals** das Verzeichnis leeren, solange noch ein Controller gekoppelt ist -- dessen Fabric wäre danach
   tot, und das Zubehör müsste in Home neu angelegt werden.

## Persistenz und Backup

Das Speicherverzeichnis (`/data`) enthält Passcode, Discriminator, Fabrics (die Schlüssel der gekoppelten
Controller) und das Identitätsregister `instance.json` (Instanzschlüssel, Endpoint-Nummern). **Wie einen
privaten Schlüssel behandeln:** nur für den Benutzer lesbar (die Bridge legt es mit Modus 700 an), nicht in ein
öffentliches Repository, nicht ungeschützt sichern. Gesichert wird das **ganze** Verzeichnis im Ruhezustand (Container
anhalten); ein Teilstand aus `storage.db` ohne `instance.json` wird nicht übernommen.

Bei beschädigtem, fehlendem, fremdem oder nicht beschreibbarem Speicher **bricht die Bridge klar ab und ändert
nichts**. Sie legt nie stillschweigend einen neuen Speicher an, denn eine neue Kopplungsidentität würde jede
bestehende Fabric entwerten. Ein zweiter Prozess auf demselben Verzeichnis wird abgewiesen. Der Speicher nutzt
`node:sqlite` (in Node 22 als experimentell markiert; im Abbild ist die Warnung abgeschaltet).

## Netzwerk

Matter findet Geräte über mDNS (UDP 5353, Multicast) und spricht dann auf dem Matter-Port (Standard 5540,
TCP und UDP). Daraus folgt:

- **Host-Netz oder macvlan.** Ein Docker-Bridge-Netz leitet keinen Multicast weiter; Apple Home findet die Bridge
  dort nicht. Unter Linux `network_mode: host` (oder ein macvlan-Netz) verwenden.
- **Docker Desktop auf dem Mac ist nicht geeignet** (läuft in einer VM ohne Multicast ins LAN). Auf dem Mac nur
  zum Entwickeln der Tests.
- Bridge und die Home-Hubs/Apps müssen im selben Layer-2-Netz (VLAN) liegen oder mDNS wird dorthin gespiegelt.
- Firewall: UDP 5353 und der Matter-Port (UDP und TCP) eingehend vom Heimnetz.
- Kein Thread und kein Thread-Border-Router nötig (IP-Bridge über Ethernet/WLAN).

## Broker: Rechte

Die Bridge braucht ein **eigenes** MQTT-Konto, das nur lesen darf, was sie anzeigt, und nur die zwei Befehle
schreiben darf, die sie sendet. Alles andere, besonders `command/setpoint`, bleibt ihr verwehrt. Beispiel für die
dateibasierte EMQX-Autorisierung (Präfix `thermoctl`, Zugangsdaten gehören nicht in die Datei; zur Einordnung
siehe `docs/mqtt.md`, Abschnitt „Den Broker absichern“):

```erlang
{allow, {username, "matter-bridge"}, subscribe, [
  "thermoctl/zones/+/state/current_temperature",
  "thermoctl/zones/+/state/setpoint",
  "thermoctl/zones/+/state/operating_mode",
  "thermoctl/zones/+/state/would_heat",
  "thermoctl/zones/+/state/override_active"
]}.
{allow, {username, "matter-bridge"}, publish, [
  "thermoctl/zones/+/command/operating_mode",
  "thermoctl/zones/+/command/temporary_setpoint"
]}.
{deny, {username, "matter-bridge"}, all, ["#"]}.
```

Mosquitto (`acl_file`):

```
user matter-bridge
topic read thermoctl/zones/+/state/current_temperature
topic read thermoctl/zones/+/state/setpoint
topic read thermoctl/zones/+/state/operating_mode
topic read thermoctl/zones/+/state/would_heat
topic read thermoctl/zones/+/state/override_active
topic write thermoctl/zones/+/command/operating_mode
topic write thermoctl/zones/+/command/temporary_setpoint
```

Fehlt ein Leserecht, steht im Log „MQTT-Abonnement fehlgeschlagen. Broker-ACLs prüfen“. Die Bridge abonniert
nur diese Topics, nie Wildcards. Über das Netz gelten die Hinweise in `docs/mqtt.md` (TLS, getrennte Konten).

## Betrieb (Compose-Beispiel)

Ein Abbild ist **nicht veröffentlicht**; die Bridge wird lokal gebaut (`docker build -t thermoctl-matter-bridge
matter-bridge/`, Node 22, unprivilegierter Benutzer `node`).

```yaml
services:
  matter-bridge:
    build: ./matter-bridge          # oder ein selbst gebautes/eigenes Abbild
    network_mode: host               # mDNS braucht Multicast, siehe Abschnitt Netzwerk
    init: true
    restart: unless-stopped
    environment:
      MATTER_BRIDGE_MQTT_URL: mqtt://broker.example.lan:1883
      MATTER_BRIDGE_MQTT_USERNAME: matter-bridge
      MATTER_BRIDGE_MQTT_PASSWORD: ${MATTER_BRIDGE_MQTT_PASSWORD}   # aus .env, nicht ins Repository
      MATTER_BRIDGE_ZONES: '[{"id": 1, "name": "Wohnzimmer"}, {"id": 2, "name": "Bad"}]'
    volumes:
      - matter-bridge-data:/data
volumes:
  matter-bridge-data:
```

Der **Healthcheck** (`node healthcheck.mjs`, Docker `HEALTHCHECK`) meldet *gesund*, wenn der Matter-Knoten online
und der MQTT-Broker verbunden ist. Nicht erreichbare Zonen (thermoctl schweigt) zählen nicht: ein Neustart der
Bridge würde daran nichts ändern. Beenden mit SIGTERM schließt MQTT, Matter-Knoten und Speicher sauber.

## Tests

```
cd matter-bridge
npm ci
npm test               # Standardlauf, ohne OS-Netz
npm run test:network   # Stufe 2, nur macOS, nur Loopback unter sandbox-exec
```

**`npm test`** nutzt das virtuelle Matter-Netz von Matter.js (In-Memory-Transport; PASE, CASE, Subscriptions
laufen vollständig), eine **UDP-Sperre** (`dgram.createSocket` wirft, damit kein mDNS-Paket das Netz erreichen
kann) und einen echten MQTT-Broker (Aedes) auf `127.0.0.1` mit zufälligem Port. Der Broker zeichnet jede
Veröffentlichung und jedes Abonnement der Bridge auf; die Tests prüfen, dass nur die zwei erlaubten Befehle
(nie `command/setpoint`) gesendet und nur `state/*` abonniert wird.

**`npm run test:network`** startet `main.mjs` als echten Prozess mit echtem UDP-Stack, aber nur in einer
`sandbox-exec`-Hülle, die alles außer localhost verbietet (auch mDNS-Multicast); ohne Hülle (und auf anderen
Systemen) weigert sich der Test zu starten. Er belegt: Prozessstart und sauberes Beenden per SIGTERM, Kopplungscode
genau einmal auf stdout, Healthcheck, PASE, Attestierung, CSR und AddNOC über echte Loopback-UDP-Sockets, Neustart
mit demselben Speicher ohne zweiten Code. **Der operative Schritt (CASE) braucht mDNS und ist in der Hülle
gesperrt;** die Kopplung scheitert dort erwartungsgemäß. mDNS im Heimnetz wurde bewusst nicht versucht.

**Was die Tests nicht beweisen:**

- Der Simulator ersetzt thermoctl. Er bildet nur den hier beschriebenen Vertrag nach: **nicht** den Python-
  Befehlsweg (`commands.py`, `remote_control.py`), **nicht** die echte Domäne (Zeitplan, Übersteuerung, Frostschutz,
  Audit) und **nicht** den echten Broker (ACLs, Retained-Verhalten über Neustarts, Last Will).
- Kein Test mit Apple Home, Google Home oder einem anderen echten Controller, kein mDNS im echten Netz, keine
  Test-Hersteller-ID-Annahme durch die Apps.
- Das Zurückspringen des Rads beruht auf dem Abgleich nach 500 ms; ein *fachliches* Quittungssignal von thermoctl gibt
  es nicht (MQTT-PUBACK bestätigt nur den Transport zum Broker).
- Der Schreibpfad hängt an einer internen Matter.js-Schnittstelle (`openForWrite` des Protokoll-Clusters, nötig, damit
  auch ein Write mit unverändertem Wert, etwa „Heizen“ bei angezeigtem Heizen, einen Befehl auslöst). Matter.js ist
  deshalb exakt auf 0.18.0 gepinnt; bei einem Versionssprung brechen die Tests laut, nicht still.

## Geräteprüfung (offen)

Vor dem produktiven Einsatz mit einer **getrennten Testinstallation** (Test-Broker, Test-Home, keine
Produktivanlage) prüfen und festhalten (App-Version, UI-Modus, angebotene Grenzen, Meldungen zur Zertifizierung):

1. iPhone/Apple Home: Kopplung mit „Trotzdem hinzufügen“, Kachel, Aus/Heizen, Sollwertgrenzen, leere Temperatur,
   Änderungen aus beiden Richtungen, Neustart der Bridge, Umbenennen, zwei Zonen getrennt, Verhalten ohne MQTT.
2. Google Home: Test-VID/Entwicklerkonto, zweites Kopplungsfenster, Darstellung eines Heat-only-Thermostats unter
   einem Aggregator.
3. mDNS im echten Netz (Host-Netz/macvlan, VLAN, Firewall), Wiederfinden nach Neustart und IP-Wechsel.
4. Zusammenspiel mit der echten thermoctl-Domäne (`command/temporary_setpoint`, Dauer der Übersteuerung) und den
   echten Broker-ACLs.
