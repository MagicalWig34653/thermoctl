# Steuergeräte: Werte hinschicken und entgegennehmen

**Stand: Entwurf.** Baut auf der Tastenbelegung aus `590b408` auf und erweitert sie um die
zweite Hälfte: Ein Steuergerät ist nicht nur ein Knopf, sondern eine Anzeige und ein Regler.

## Warum

Ein Aqara W100 (Zigbee2MQTT-Modell `TH-S04D`) hat ein Display und einen Sollwertsteller.
Beides ist heute tot: thermoctl liest seine Tasten, schickt ihm aber nichts, und der Wert,
den jemand am Gerät einstellt, geht ins Leere.

Der [Zigbee2MQTT-Eintrag](https://www.zigbee2mqtt.io/devices/TH-S04D.html) nennt die
Merkmale, um die es geht — nachgeschlagen, nicht geraten:

| Merkmal | lesbar | schreibbar | Typ | Bereich |
|---|---|---|---|---|
| `external_temperature` | nein | **ja** | numerisch | −100 … 100 °C |
| `external_humidity` | nein | **ja** | numerisch | 0 … 100 % |
| `sensor` | ja | **ja** | Auswahl | `internal`, `external` |
| `occupied_heating_setpoint` | **ja** | **ja** | numerisch | 5 … 30 °C |
| `system_mode` | **ja** | **ja** | Auswahl | `off`, `heat`, `cool`, `auto` |
| `local_temperature`, `temperature`, `humidity`, `battery` | ja | nein | numerisch | — |
| `action` | ja | nein | Auswahl | `single_plus`, `hold_center`, … (bereits umgesetzt) |

`external_temperature` ist **nur schreibbar**: Das Gerät zeigt an, was man ihm schickt.
Damit es das überhaupt tut, muss `sensor` auf `external` stehen — das ist keine Nebensache,
sondern die Voraussetzung, und deshalb muss es sich in derselben Oberfläche einstellen
lassen.

## Was gebaut wird

Eine eigene Unterseite. Sie beantwortet je Steuergerät eine Frage pro Merkmal: **Was steht
hier drauf, und was macht thermoctl damit?**

### Datenmodell

**`device_property`** — was ein Gerät laut Brücke kann. Bis hierher wirft
`geraeteklassen.py` die Merkmalsliste aus `bridge/devices` weg und behält nur eine Handvoll
abgeleiteter Fähigkeiten. Für diese Seite wird sie gebraucht, und zwar mit Zugriffsart und
Wertebereich — sonst müsste die Oberfläche raten, was ein Gerät annimmt.

| Spalte | |
|---|---|
| `device_id` | Fremdschlüssel, CASCADE |
| `name` | der Merkmalsname aus Zigbee2MQTT, z. B. `external_temperature` |
| `value_type` | `numeric`, `binary`, `enum`, `text` |
| `unit`, `min_value`, `max_value` | nullbar |
| `is_readable`, `is_writable` | aus dem `access`-Bitfeld: 1 = lesbar, 2 = schreibbar |

**`device_property_value`** — die erlaubten Werte eines Auswahlmerkmals
(`property_id`, `value`, `sort_order`). Eine eigene Tabelle statt einer
kommagetrennten Spalte: Grundsatz 3 verbietet JSON-Spalten als Datenmodell, und eine
Trennzeichenliste ist dasselbe in schlechter.

**`controller_channel`** — die eigentliche Einstellung, je Gerät und Merkmal genau eine.

| Spalte | |
|---|---|
| `device_id`, `property_name` | worauf sich der Kanal bezieht (eindeutig zusammen) |
| `direction` | `write` (thermoctl → Gerät) oder `read` (Gerät → thermoctl) |
| `kind_id` | Nachschlagetabelle `channel_kind`, siehe unten |
| `zone_id` | nullbar — nur für zonenbezogene Arten |
| `source_device_id` | nullbar — nur für `sensor_temperature` |
| `fixed_text`, `fixed_number` | nullbar — nur für `fixed` |

**`channel_kind`** — was in den Kanal fließt:

*Schreibend:*
- `sensor_temperature` — die zuletzt gemessene Temperatur eines beliebigen Geräts
  (`source_device_id`). **Das ist der Fall des Nutzers:** ein Multisensor speist das
  Display des W100.
- `zone_temperature` — die Ist-Temperatur der Zone, also das, was die Regelung benutzt.
- `zone_setpoint` — der aufgelöste Sollwert der Zone.
- `fixed` — eine Konstante. Damit stellt man `sensor` auf `external`, ohne dafür einen
  Sonderweg zu bauen.

*Lesend:*
- `zone_setpoint` — was am Gerät eingestellt wird, wird zum Sollwert der Zone. Genau wie
  der Thermostat in Home Assistant: über `fernbedienung.set_setpoint`, also den Modus, der
  gerade gilt, und nicht als Übersteuerung.
- `operating_mode` — `system_mode` des Geräts auf die Betriebsart der Zone.

### Senden

Ein Kanal mit `direction = write` wird im Veröffentlichungszyklus auf
`<mqtt_base_topic>/<external_id>/set` als `{"<property_name>": <wert>}` geschickt.

**Nur bei Änderung.** Ein Display, das jede Minute denselben Wert bekommt, hält ein
Batteriegerät wach; gesendet wird, wenn sich der Wert seit dem letzten Mal geändert hat
oder die Verbindung neu aufgebaut wurde.

**Der Riegel.** Diese Nachrichten gehen mit `schaltet=False` hinaus, und das ist eine
Aussage, keine Bequemlichkeit: Ein Wert auf einem Display bewegt kein Ventil. Deshalb gilt
die Regel **nur für Geräte, die in einer Zone die Rolle `controller` haben** — ein Kanal auf
ein Gerät mit der Rolle `actuator` wäre ein Schaltbefehl und muss durch beide Riegel. Die
Oberfläche bietet Schreibkanäle deshalb ausschließlich für Bediengeräte an, und die Domäne
weist alles andere ab.

### Entgegennehmen

Ein Kanal mit `direction = read` wirkt beim Verarbeiten der Gerätenachricht — dort, wo
schon die Tastendrücke ausgewertet werden. Es gilt **derselbe Wiederholungsschutz**: Eine
behaltene Nachricht wird bei jeder Neuverbindung erneut zugestellt, und ein Sollwert, den
niemand gedreht hat, wäre genauso falsch wie ein Boost, den niemand gedrückt hat.

### Die Seite

`/controllers` (verlinkt aus der Kopfleiste unter Einstellungen und von
*Zone → Geräte*). Je Steuergerät eine Tafel:

1. **Was ankommt** — die lesbaren Merkmale mit ihrem letzten Wert. Beantwortet „redet das
   Gerät überhaupt mit mir".
2. **Was hingeschickt wird** — je schreibbarem Merkmal ein Ablegeziel. Ein Gerät aus dem
   Vorrat daraufziehen bedeutet `sensor_temperature` mit diesem Gerät als Quelle; für
   `zone_temperature`, `zone_setpoint` und `fixed` gibt es daneben eine Auswahl. Dasselbe
   Ziehen wie bei der Gerätezuordnung, mit demselben Skript (`assignment.js`) — und wie
   dort funktioniert ohne JavaScript weiterhin das Formular darunter.
3. **Die Tastenbelegung**, die es schon gibt, zieht von *Zone → Geräte* hierher um. Sie
   gehört zum selben Gerät und beantwortet dieselbe Frage.

Ein Merkmal, das das Gerät gar nicht kann, wird nicht angeboten. Wertebereiche kommen aus
`device_property` und nicht aus einer zweiten Liste im Quelltext: Was das Gerät annimmt,
weiß die Brücke.

## Was ausdrücklich nicht gebaut wird

- **Keine Voreinstellung je Modell.** Kein „wenn Modell == TH-S04D, dann …". Die Merkmale
  kommen aus `bridge/devices`, die Belegung macht der Betreiber. Das ist Grundsatz 1, und
  es ist der Grund, warum die Seite mit jedem Gerät funktioniert, das Zigbee2MQTT kennt.
- **Kein Schreiben auf Aktoren.** Solange Phase 4 nicht abgeschlossen ist, geht über diese
  Seite nichts hinaus, was ein Ventil bewegt.

## Prüfung

Zu jedem Kanaltyp ein Test mit Gegenprobe. Dazu drei, die über die Mechanik hinausgehen:

- Ein Schreibkanal auf ein Gerät mit der Rolle `actuator` wird abgewiesen.
- Derselbe Wert wird nicht zweimal gesendet.
- Ein gelesener Sollwert aus einer wiederholten Nachricht verstellt die Zone nur einmal.

Dazu ein Lauf gegen einen echten Mosquitto-Broker: `sensor: external` setzen, eine
Temperatur schicken, den Sollwert am Gerät zurücklesen. Ob das Display sie **anzeigt**,
kann nur der Projektinhaber am Gerät prüfen — das gehört in `STATUS.md` unter „Was nur der
Projektinhaber kann".
