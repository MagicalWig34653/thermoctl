# Homebridge: eine Zone als Thermostat (`mqtt-thing`)

Dieses Dokument beschreibt, wie eine thermoctl-Zone über das Homebridge-Plugin
[`mqtt-thing`](https://github.com/arachnetech/homebridge-mqttthing) als HomeKit-Thermostat
erscheint. Es setzt voraus, dass **[mqtt.md](mqtt.md) vollständig gelesen ist**, besonders
Abschnitt 2 („Senden: die eigene Struktur") und „Den Broker absichern: Er ist eine
Vertrauensgrenze" — hier steht nur, wie ein bestimmter Client, `mqtt-thing`, auf den dort
beschriebenen Vertrag aufsetzt. Ein eigenes Dokument statt eines weiteren Abschnitts in
`mqtt.md`, weil dort der Topic-*Vertrag* steht, den jeder Abonnent gleichermaßen nutzt —
Plugin-spezifische Konfigurationssyntax gehört nicht dorthin und würde die Übersicht des
Vertrags nur verwässern. `self-hosting.md` und `mqtt.md` sind schon nach demselben Schnitt
getrennt: Betrieb hier, Protokoll dort.

## Die Zonenkennung finden

Im Topic steht die **Kennung** der Zone, nicht ihr Name (siehe mqtt.md, Abschnitt 2) — der
Name darf sich ändern, ohne dass ein Abonnement bricht. Zwei Wege, sie nachzusehen:

- In der Weboberfläche: *Zonen* → einen der Verweise **Geräte**, **Zeitplan** oder
  **Bearbeiten** einer Zone anklicken (oder in der Statuszeile des Browsers antippen/mit der
  Maus darüberfahren) — die Zahl am Ende der Adresse (`.../zones/<id>/...`) ist die Kennung.
  Die Übersichtstabelle selbst zeigt sie nicht als eigene Spalte an.
- Über die REST-API: `GET /api/v1/zones` liefert jede Zone mit ihrer `id` (siehe
  [api.md](api.md)).

Im Folgenden steht `1` als Beispielkennung; sie ist durch die eigene zu ersetzen.

## Was sich gegenüber der Altsystem-Konfiguration ändert

Die eingangs gezeigte Konfiguration für das abgelöste System läuft **nicht unverändert
weiter**. Das war eine bewusste Entscheidung beim Neubau (mqtt.md, Abschnitt 2), keine
Nachlässigkeit:

- **Kein `/get`-Suffix.** Aus `heizung/thermostate/1/temperatureActual/get` wird
  `thermoctl/zones/1/state/current_temperature` — ohne `/get` am Ende.
- **Getrennte Bäume für Zustand und Befehl.** Statt `.../temperatureTarget/get` und
  `.../temperatureTarget/set` unter demselben Ast gibt es `state/...` und `command/...` als
  eigene Teilbäume.
- **Die Zonenkennung sitzt an anderer Stelle im Pfad** (`zones/<id>/...` statt
  `thermostate/<id>/...`) und das Präfix heißt standardmäßig `thermoctl` statt `heizung`.
- **Der HomeKit-Zustand „automatisch" (3) hat jetzt eine echte Entsprechung.** Das
  Altsystem kannte in `heatingCoolingStateValues` nur `["off", "heat"]` und
  `restrictHeatingCoolingState: [0, 1]` — automatischer Betrieb war für HomeKit unsichtbar.
  thermoctls `auto` ist der normale Zeitplanbetrieb und bekommt unten einen eigenen Platz.
- **Es gibt kein Kühlen und wird auch keins vorgetäuscht.** thermoctl kennt keinen
  Kühlbetrieb; die Konfiguration unten schließt den HomeKit-Zustand „Kühlen" (2) bewusst
  aus, statt ihn unbenutzt offenzulassen (Begründung weiter unten).
- **Neue Zugangsdaten und ein neuer Broker-Zugang.** Die alten Zugangsdaten des Altsystems
  wurden beim Neubau nicht übernommen (CLAUDE.md, Grundsatz 2). Für Homebridge wird ein
  eigenes Konto mit eigens zugeschnittenen Rechten angelegt, siehe unten.

## Vollständige `mqtt-thing`-Konfiguration

```json
{
    "accessory": "mqttthing",
    "type": "thermostat",
    "name": "Wohnzimmer Thermostat",
    "url": "mqtts://<broker>:8883",
    "username": "homebridge",
    "password": "<geheim>",
    "mqttOptions": { "protocolVersion": 5 },
    "topics": {
        "getCurrentTemperature": {
            "topic": "thermoctl/zones/1/state/current_temperature"
        },
        "getTargetTemperature": {
            "topic": "thermoctl/zones/1/state/setpoint"
        },
        "setTargetTemperature": {
            "topic": "thermoctl/zones/1/command/setpoint"
        },
        "getCurrentHeatingCoolingState": {
            "topic": "thermoctl/zones/1/state/would_heat",
            "apply": "return (message === 'true') ? 1 : 0;"
        },
        "getTargetHeatingCoolingState": {
            "topic": "thermoctl/zones/1/state/operating_mode",
            "apply": "return {auto: 3, manual: 1, off: 0}[message];"
        },
        "setTargetHeatingCoolingState": {
            "topic": "thermoctl/zones/1/command/operating_mode",
            "apply": "return {0: 'off', 1: 'manual', 3: 'auto'}[message];"
        }
    },
    "restrictHeatingCoolingState": [0, 1, 3],
    "minTemperature": 10,
    "maxTemperature": 30,
    "temperatureDisplayUnitsValues": "CELSIUS"
}
```

Erklärung der Felder, die sich gegenüber der Altsystem-Konfiguration ändern oder eine
Begründung brauchen:

- **`topics.*`** — sechs eigene Einträge statt der bisherigen sechs, mit den oben
  hergeleiteten Pfaden. `getCurrentTemperature`, `getTargetTemperature` und
  `setTargetTemperature` übertragen reine Zahlen (thermoctl schreibt sie als Text ohne
  Einheit, z. B. `21.5`) und brauchen deshalb keine Umformung — die einfache
  `{"topic": "..."}`-Form reicht.
- **`apply`** — die drei Zustands-Topics tragen andere Werte als die numerischen
  HomeKit-Zustände (0/1/2/3) und brauchen deshalb `mqtt-thing`s erweiterte Themenform mit
  einer Umrechnungsfunktion statt der einfachen `heatingCoolingStateValues`-Liste, die nur
  **eine** gemeinsame Wertliste für Ist- und Sollzustand kennt — hier sprechen Ist- und
  Sollzustand aber unterschiedliche Sprachen (`true`/`false` gegen `auto`/`manual`/`off`,
  siehe Begründung unten). `message` ist in `mqtt-thing`s `apply`-Funktionen der
  eingehende (bei `get...`) bzw. der von HomeKit gesetzte (bei `set...`) Wert. Die genaue
  Schreibweise ist Sache der installierten `mqtt-thing`-Version; vor der ersten Nutzung
  gegen deren [README](https://github.com/arachnetech/homebridge-mqttthing#configuration)
  prüfen — diese Anleitung wurde nicht gegen ein laufendes Homebridge getestet.
- **`restrictHeatingCoolingState: [0, 1, 3]`** — schränkt ein, was in HomeKit **auswählbar**
  ist: Aus, Heizen, Automatik. Der Zustand 2 (Kühlen) fehlt bewusst, siehe unten. Anders als
  in der Altsystem-Konfiguration (`[0, 1]`) fehlt hier **nicht** mehr die Automatik — sie ist
  bei thermoctl der Normalbetrieb (Begründung unten) und muss auswählbar sein.
- **`heatingCoolingStateValues` entfällt.** Es wird durch die beiden `apply`-Funktionen
  ersetzt; ein zusätzlicher Eintrag würde nur eine ignorierte zweite Wahrheit anlegen.
- **`minTemperature` / `maxTemperature`** unverändert bei 10/30 belassen — ein für Wohnräume
  sinnvoller Ausschnitt. thermoctl selbst akzeptiert −20 bis 35 °C (mqtt.md, Abschnitt 4);
  wer Frostschutzwerte in HomeKit sehen oder setzen will, weitet diesen Bereich entsprechend.
- **`mqttOptions.protocolVersion: 5`** unverändert aus der Altsystem-Konfiguration
  übernommen — betrifft die Verbindung zum Broker, nicht die Topics, und thermoctls eigener
  MQTT-Vertrag setzt keine bestimmte Protokollversion voraus.

## Die Wertzuordnung — und warum genau so

HomeKit kennt vier feste Heiz-/Kühlzustände: 0 Aus, 1 Heizen, 2 Kühlen, 3 Automatik.
thermoctl kennt drei Betriebsarten (`off`, `manual`, `auto`) und einen booleschen
Ist-Zustand (`would_heat`). Die Zuordnung:

| HomeKit-Zustand | thermoctl |
|---|---|
| 0 — Aus | `operating_mode = off` |
| 1 — Heizen | `operating_mode = manual` |
| 3 — Automatik | `operating_mode = auto` |
| 2 — Kühlen | **nicht vergeben** — kein Weg dorthin, kein Weg von dort |

**„Heizen" (1) ist `manual`, nicht `auto`.** In thermoctl bedeutet die manuelle
Betriebsart, dass ein fester Sollwert gilt statt eines Zeitplans — das deckt sich mit dem
Bild, das HomeKits „Heizen" nahelegt: ein Rad, an dem ich drehe und das genau diesen Wert
hält. Diese Deckungsgleichheit ist auch schon in `publication.py` angelegt: Die
Home-Assistant-Discovery übersetzt dort exakt so (`mode_command_template`:
`'manual' if value == 'heat' else value`, `mode_state_template`:
`'heat' if value == 'manual' else value`) — dieselbe Übersetzung, hier für `mqtt-thing`
nachvollzogen, nicht neu erfunden.

**„Automatik" (3) ist `auto` — der eigentliche Normalbetrieb.** Zeitpläne laufen, der
Sollwert wechselt nach Plan. Ohne diesen Eintrag bliebe der Normalbetrieb der Anlage in
HomeKit unsichtbar und unwählbar — genau das Loch, das die Altsystem-Konfiguration mit
`restrictHeatingCoolingState: [0, 1]` hatte. Deshalb steht die 3 jetzt in
`restrictHeatingCoolingState`.

**„Kühlen" (2) bleibt unvergeben, statt einen Platz offenzulassen.** thermoctl regelt
ausschließlich Heizung; es gibt keine Betriebsart, die „Kühlen" auch nur näherungsweise
entspräche. Zwei Stellen setzen das durch, nicht nur eine:

- `restrictHeatingCoolingState` lässt 2 gar nicht erst zur Wahl — in der Home-App
  erscheint kein Kühlen-Regler.
- Die `apply`-Funktion von `setTargetHeatingCoolingState` kennt in ihrer Zuordnung
  `{0: 'off', 1: 'manual', 3: 'auto'}` keinen Schlüssel `2`; träfe dennoch eine `2` ein
  (ein Skript, ein Automatisierungsfehler außerhalb der Home-App), ergäbe der Zugriff
  `undefined`, und `mqtt-thing` veröffentlichte keine sinnvolle Nachricht statt eine
  geratene. Ein zusätzlicher Eintrag `2: 'auto'` oder `2: 'off'` hätte diesen Fall
  stillschweigend auf einen Zustand abgebildet, den niemand angefordert hat — genau die Art
  Platzhalter, vor der die Aufgabenstellung warnt.

Für den **Ist-Zustand** (`getCurrentHeatingCoolingState`) gibt es ohnehin keinen
Kühl-Ausgang: `would_heat` ist `"true"` oder `"false"`, die `apply`-Funktion bildet das auf
1 (Heizen) oder 0 (Aus/Bereit) ab. HomeKit zeigt bei „Bereit" denselben Zustand wie bei
„Aus", weil die Anzeige „heizt / bereit" (mqtt.md, Abschnitt 4) selbst keine dritte Kategorie
kennt, die davon zu unterscheiden wäre.

## Was der Sollwert bedeutet, wenn man am Rad dreht

`setTargetTemperature` veröffentlicht auf `thermoctl/zones/1/command/setpoint`. Das
verstellt **die Solltemperatur des gerade geltenden Modus**, nicht „ab jetzt für eine
Stunde" — ausführlich begründet in mqtt.md, Abschnitt 2. Wer in HomeKit am Thermostatrad
dreht, während die Zone auf Automatik steht, ändert damit den Sollwert des aktuell aktiven
Zeitplanpunkts; der nächste Schaltpunkt überschreibt ihn wie geplant wieder. Wer eine
Übersteuerung mit festem Wert erwartet, findet die in der Weboberfläche oder über die
REST-API (`POST /api/v1/zones/{zone_id}/override`), nicht über diesen Thermostat-Regler.

## Retained: Zeigt Homebridge nach einem Neustart sofort etwas an?

Ja. Alle sechs gelesenen Zustands-Topics (`state/current_temperature`, `state/setpoint`,
`state/operating_mode`, `state/would_heat`) gehen mit gesetztem retain-Flag hinaus
(mqtt.md, Abschnitt 2: „Alles Bleibende geht mit dem retain-Flag hinaus"). Der Broker
liefert den zuletzt bekannten Wert daher sofort bei der ersten Verbindung aus, ohne auf den
nächsten Regelzyklus zu warten. Für die beiden Befehls-Topics gilt das Gegenteil: **Sie
sollten nicht** mit retain veröffentlicht werden — kein retain in dieser Beispiel-
Konfiguration ist Absicht, nicht Auslassung. Ein behaltener Befehl würde bei jeder
Neuverbindung von `mqtt-thing` erneut zugestellt und erneut ausgeführt (mqtt.md,
Abschnitt 2); `mqtt-thing` setzt für Befehls-Topics standardmäßig kein retain, das sollte
so bleiben.

## Broker-Rechte: nur was Homebridge wirklich braucht

Der Broker ist eine Vertrauensgrenze — ausführlich in mqtt.md, „Den Broker absichern:
Er ist eine Vertrauensgrenze", samt EMQX-Beispiel. Ein Homebridge-Zugang ist **kein**
Home-Assistant-Zugang und braucht auch nicht dessen komplette Rechte. Diese Konfiguration
nutzt genau vier Topics lesend und zwei schreibend:

```erlang
{allow, {username, "homebridge"}, subscribe, [
  "thermoctl/zones/1/state/current_temperature",
  "thermoctl/zones/1/state/setpoint",
  "thermoctl/zones/1/state/operating_mode",
  "thermoctl/zones/1/state/would_heat"
]}.
{allow, {username, "homebridge"}, publish, [
  "thermoctl/zones/1/command/setpoint",
  "thermoctl/zones/1/command/operating_mode"
]}.
{deny, all}.
```

Bewusst **nicht** vergeben: Leserechte auf `thermoctl/zones/1/state/#` (der Zugang sieht
dann auch `sensor_state`, `last_switch`, `next_switch`, jeden Modus- und Regelparameterwert
— nichts, was diese Konfiguration liest) und Schreibrechte auf `command/boost`,
`command/mode/+` oder `command/parameter/+` — dieses Thermostat löst keinen Boost aus und
verstellt keine Regelparameter, ein weiter gefasster Zugang könnte es aber. Ein zweiter
Homebridge-Zugang mit derselben engen Zuschneidung entsteht pro Zone, die dort ebenfalls
erscheinen soll — mit `2`, `3`, … statt `1` in den Topic-Pfaden. Die allgemeine Regel steht
in mqtt.md: getrennte Konten für getrennte Aufgaben, kein gemeinsamer Benutzer über
Zwecke hinweg.
