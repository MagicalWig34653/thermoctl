# REST-Schnittstelle

`thermoctl` hat drei Adapter über derselben Domänenlogik: die Weboberfläche, diese
REST-Schnittstelle und den [MCP-Server](mcp.md). Gemeinsame Fähigkeiten benutzen dieselbe
Domänenlogik; die Adapter bieten aber nicht denselben Umfang. Beispielsweise lassen sich
Geräte derzeit in der Oberfläche, nicht über REST, anlegen und ändern.

Grundadresse: `/api/v1`.

## Zum Ausprobieren: `/docs`

Der laufende Dienst liefert die Beschreibung maschinenlesbar unter `/openapi.json` und als
Swagger-Oberfläche unter **`/docs`** — dort lässt sich jeder Weg anklicken und ausprobieren.

Oben rechts steht **Authorize**: Dort einmal das API-Token eintragen (nur den Token selbst,
ohne `Bearer `), danach schickt jeder Aufruf es mit.

Drei Dinge, die dabei bewusst so sind:

- **Die Oberfläche liegt vollständig im Dienst**, nicht in einem CDN. Sonst bliebe sie in
  einem Heimnetz ohne Internetzugang leer, und jeder Aufruf verriete einem Dritten, wann
  jemand die Heizungssteuerung öffnet. Herkunft und Prüfsummen der mitgelieferten Dateien
  stehen in `thermoctl/web/static/HERKUNFT.md`.
- **Beschrieben ist nur diese Schnittstelle**, nicht die HTML-Seiten der Oberfläche. Sonst
  stünde neben jedem echten Endpunkt ein Formularweg, dessen „Try it out" eine echte
  Änderung auslöst.
- **`/docs` selbst verlangt keine Anmeldung.** Die Beschreibung verrät, welche Wege es
  gibt, aber keinen einzigen Wert — und dasselbe steht ohnehin in dieser Datei. Ausprobieren
  lässt sich von dort nichts ohne Token; jeder Aufruf durchläuft dieselbe Prüfung wie sonst.

ReDoc (`/redoc`) gibt es nicht: dasselbe CDN-Problem, und `/docs` deckt dieselbe
Beschreibung ab.

## Anmeldung

Ausschließlich über ein API-Token im `Authorization`-Header:

```
Authorization: Bearer <token>
```

Ein Token hat die Form `tctl_<achtstelliges Präfix>_<Geheimnis>`. Der Klartext erscheint
**genau einmal**, beim Ausstellen; gespeichert wird nur der SHA-256-Hash des Geheimnisses.
Wer ihn verliert, stellt ein neues aus.

Ausgestellt und widerrufen werden Tokens unter `/tokens` in der Oberfläche.

Diese Schnittstelle nimmt **kein Sitzungscookie** an. Das ist Absicht und keine Lücke: Ein
Cookie würde bei jedem Aufruf aus dem Browser automatisch mitgeschickt und die
Schnittstelle damit von jeder fremden Seite aus aufrufbar machen. Weil nur der Header zählt,
braucht sie umgekehrt keinen CSRF-Schutz. Ein Test hält beides nach.

Ein Token trägt eigene Rechte, höchstens die seines Besitzers. Ein Token für ein
Anzeigetafel-Skript bekommt `zone.read` und sonst nichts — und kann dann auch nichts
anderes, selbst wenn sein Besitzer Verwalter ist.

## Antworten auf Fehler

| Status | Bedeutung |
|---|---|
| `401` | Kein, unbekanntes, abgelaufenes oder widerrufenes Token |
| `403` | Token gültig, Recht fehlt |
| `404` | Objekt gibt es nicht **oder** dieses Token darf es nicht sehen |
| `422` | Eingabe passt nicht zum Schema (Feld fehlt, Wert außerhalb der Grenzen) |

`404` statt `403` für fremde Zonen ist Absicht: Sonst verriete die Antwort, welche Zonen
es gibt.

## Endpunkte

### `GET /api/v1/zones` — Zonen auflisten

Recht: `zone.read`. Liefert **nur** die für dieses Token sichtbaren Zonen; ein auf eine
Zone eingeschränktes Recht ergibt eine einelementige Liste.

```json
[{"id": 1, "name": "wohnzimmer", "display_name": "Wohnzimmer"}]
```

### `GET /api/v1/zones/{zone_id}` — eine Zone

Recht: `zone.read`. `404`, wenn es sie nicht gibt oder das Token sie nicht sehen darf.

### Zonen verwalten

`POST /api/v1/zones`, `PUT /api/v1/zones/{zone_id}` und
`DELETE /api/v1/zones/{zone_id}` benötigen `zone.manage`. Anlegen antwortet mit `201`,
Löschen mit `204`.

```json
{"name": "bad", "display_name": "Bad", "operating_mode_id": 1,
 "sort_order": 10, "temperature_source_device_id": null}
```

### Modi und Sollwerte

`GET /api/v1/modes` benötigt `zone.read`; `POST /api/v1/modes` benötigt `mode.manage`.

```json
{"code": "urlaub", "name": "Urlaub", "sort_order": 30}
```

`GET /api/v1/zones/{zone_id}/setpoints` benötigt `zone.read`,
`PUT /api/v1/zones/{zone_id}/setpoints` benötigt `setpoint.write`.

```json
{"setpoints": [{"mode_id": 2, "temperature_c": "20.5"}]}
```

### Zeitplan

`GET /api/v1/zones/{zone_id}/schedule` benötigt `zone.read`. Einen Punkt legt
`POST /api/v1/zones/{zone_id}/schedule` mit `schedule.manage` an; gelöscht wird er über
`DELETE /api/v1/zones/{zone_id}/schedule/{point_id}` mit demselben Recht.

```json
{"weekday": 1, "minute_of_day": 360, "mode_id": 2}
```

`PUT /api/v1/zones/{zone_id}/schedule/{point_id}` verschiebt einen vorhandenen Punkt
(`schedule.manage`) — das Gegenstück zum Ziehen in der Wochenansicht. Der Punkt **behält
seine Kennung**, damit ein Aufrufer ihn weiterverfolgen kann, und das Audit-Protokoll
zeigt eine Verschiebung statt eines Löschens mit anschließendem Anlegen.

```json
{"weekday": 4, "minute_of_day": 480}
```

Ein bereits belegter Zeitpunkt wird mit `422` abgelehnt.

### Steuerung

`GET /api/v1/control` (`zone.read`) liefert den Betriebszustand der Anlage und die
globalen Vorgaben, von denen jede Zone erbt.

```json
{"control_armed": false, "timezone": "Europe/Berlin", "shadow_interval_seconds": 60, "…": "…"}
```

`PUT /api/v1/control/armed` legt den Riegel um, den die Datenbank hält. Das braucht
**`control.arm`**, ein eigenes Recht — nicht `setting.manage`: Wer Zeitzone und
Aufbewahrungsdauer pflegen darf, soll die Heizung nicht nebenbei scharf schalten können.

```json
{"armed": true, "reason": "Vier Tage Schattenlauf gegen das Altsystem verglichen"}
```

`reason` ist beim Scharfschalten Pflicht und geht ins Audit-Protokoll; beim
Zurücknehmen in den Trockenlauf ist sie freiwillig, weil das der Weg ist, den jemand in
Eile geht. Fehlt sie beim Scharfschalten, antwortet der Dienst mit `422`.

**Scharfschalten hebt nur den Riegel, den die Datenbank hält.** Der zweite Riegel —
`MqttClient(switching_allowed=…)`, beim Bau des Clients gesetzt — bleibt davon unberührt.

`PUT /api/v1/control/defaults` (`setting.manage`) schreibt die globalen Vorgaben. Alle
Felder aus der Antwort von `GET /api/v1/control` außer `control_armed` sind Pflicht; die
Grenzen prüft die Domäne, ein Verstoß ist `422`.

### Sonnenabsenkung

`PUT /api/v1/control/solar-location` (`setting.manage`) setzt Schalter und Standort für
die Sonnenprognose. Die Koordinaten sind **Text**, nicht Zahlen: Die Grenzen prüft die
Domäne, damit sie nur an einer Stelle stehen.

```json
{"enabled": true, "latitude": "52.520", "longitude": "13.405"}
```

Eine leere Koordinate ist eine gültige Antwort und heißt „kein Standort" — eine
Vorgabe gibt es hier bewusst nicht (Grundsatz 1). Die Antwort ist dieselbe wie bei
`GET /api/v1/control`; darin stehen `solar_forecast_enabled`,
`solar_forecast_latitude` und `solar_forecast_longitude`, dazu
`default_solar_setback_max_k` und `solar_setback_lookahead_hours`.

Wie stark eine einzelne Zone von Sonne profitiert, steht als `solar_gain_factor`
(0 bis 1) an der Zone selbst und wird über `POST`/`PUT /api/v1/zones` mitgeschrieben.
**Fehlt das Feld, gilt 0** — ein Aufrufer, der die Absenkung nicht kennt, schaltet sie
für diese Zone damit aus statt sie versehentlich ein.

### Regelparameter

`GET /api/v1/zones/{zone_id}/parameters` benötigt `zone.read`,
`PUT /api/v1/zones/{zone_id}/parameters` benötigt `zone.manage`. `null` stellt die Vererbung des globalen Standards wieder her.

```json
{"hysteresis_k": "0.30", "min_on_seconds": 300, "min_off_seconds": null,
 "sensor_timeout_seconds": null, "temperature_offset_k": "0.00",
 "window_resume_delay_seconds": null,
 "pi_enabled": false, "pi_gain_per_k": "0.25", "pi_integral_time_minutes": 180,
 "pi_min_on_seconds": 60, "pi_min_off_seconds": 60}
```

Die fünf `pi_*`-Werte gehören zur **PI-Regelung (Beta)**, seit 0.5.0. `pi_enabled` ist je
Zone aus als Vorgabe und lässt sich nur setzen, wenn die Zone dafür taugt. Die Prüfung ist
gerätegenau, nicht zonenweit: Ein Gerät mit der Fähigkeit `thermostat`, das nicht
selbstregelnd ist, schliesst PI aus — es bekäme die Entscheidung als Sollwertsprung
weitergereicht —, ebenso eine Zone ganz ohne gewöhnlichen Schaltaktor. Ein
**selbstregelndes** Ventil dagegen schliesst die Zone nicht aus: Es bekommt seinen
Sollwert über einen eigenen Weg und sieht die PI-Entscheidung nie, sodass es neben einem
gewöhnlichen Schaltaktor stehen darf — PI steuert dann nur diesen Schaltaktor an. Der
Versuch wird bei fehlender Eignung mit einer Begründung abgewiesen, statt still auf
Hysterese zurückzufallen.

`pi_min_on_seconds` und `pi_min_off_seconds` sind **weiche** Untergrenzen: Die Genauigkeit
des Tastgrads darf sie unterschreiten. Das ist eine bewusste Entscheidung und der Grund,
warum PI deutlich häufiger schaltet als die Hysterese — die Zahlen dazu stehen am Schalter
in der Oberfläche und im Eintrag zu 0.5.0 im [CHANGELOG](../CHANGELOG.md).

`PUT /api/v1/zones/{zone_id}/parameters/{name}` setzt **einen** Parameter und lässt die
übrigen, wie sie sind — auch die geerbten. Neben dem PUT auf alle, nicht statt seiner: Wer
nur die Hysterese ändern will, müsste sonst erst alle sechs lesen und wieder mitschicken
und schriebe dabei jeden geerbten Wert als Zonenabweichung fest. Recht: `zone.manage`.

```json
{"value": "0.40"}
```

Gültige Namen sind die sechs Felder oben. Ein anderer ergibt `404` samt der Liste der
gültigen; ein Wert außerhalb der Grenzen `422`. Die Grenzen stehen in
`domain/zone_settings.PARAMETER` und gelten für jeden Weg gleich.

### `POST /api/v1/zones/{zone_id}/boost` — die nächste Schaltung vorziehen

Recht: **`override.create`** — es *ist* eine Übersteuerung, nur eine, deren Wert und Ende
der Zeitplan bestimmt statt der Aufrufer. Was als Nächstes ohnehin käme, gilt ab sofort und
genau bis zu dem Zeitpunkt, an dem es planmäßig gekommen wäre; danach läuft der Plan weiter,
als wäre nichts gewesen.

```json
{"zone_id": 1, "mode_code": "nacht", "temperature_c": "18.0",
 "valid_until": "2026-08-31T20:00:00"}
```

Ohne Zeitplan oder ohne hinterlegte Temperatur für den nächsten Modus gibt es nichts
vorzuziehen: `409` mit dem Grund im Klartext. Der Body ist leer — es gibt nichts zu wählen.

Bei allen zonenbezogenen Wegen ergibt eine fremde Zone `404` statt `403`, damit die
Antwort nicht verrät, dass diese Zone existiert.

### `GET /api/v1/zones/{zone_id}/state` — Zonenzustand

Recht: `zone.read`. Liefert Ist-Temperatur, Messzeitpunkt, Sensorzustand,
Fensterzustand und Aktualisierungszeitpunkt. Fehlende Werte sind `null`; insbesondere
bedeutet eine fehlende Temperatur nicht 0 °C. `404`, wenn die Zone nicht sichtbar ist
oder noch keinen abgeleiteten Zustand hat.

```json
{"zone_id": 1, "temperature_c": "20.25", "measured_at": "2026-08-29T08:15:00",
 "sensor_status": "ok", "window_open": false, "updated_at": "2026-08-29T08:15:02"}
```

### `GET /api/v1/devices` — Geräte auflisten

Recht: `device.read`. Liefert Anzeigename, externe Kennung, Anbindung, Modell,
Gruppenkennzeichen, Fähigkeiten, Lebenszeichen und zugeordnete Zonen. Noch nicht
gemeldete Gesundheitswerte sind `null`, Zuordnungen und Fähigkeiten gegebenenfalls leer.

```json
[{"id": 4, "external_id": "geraet-a", "display_name": "Gerät A",
  "integration": "zigbee2mqtt", "model": null, "is_group": false,
  "capabilities": ["temperature"], "last_payload_at": null,
  "battery_percent": null, "link_quality": null, "availability": null,
  "zones": ["zone-a"]}]
```

### `GET /api/v1/device-commands` — Schaltprotokoll lesen

Recht: `audit.read` — dasselbe wie unter „Einstellungen → Schaltprotokoll" in der
Oberfläche. Beide sind Protokolle über die ganze Anlage, nicht über eine einzelne
Zone; ein auf eine Zone eingeschränktes Token sieht hier nichts zusätzlich und nichts
weniger als über dieses eine Recht bereits erlaubt ist. **Nur lesend** — ein
Protokoll, das sich über eine Schnittstelle ändern liesse, wäre keins.

```json
[{"id": 42, "sent_at": "2026-08-29T08:15:02Z", "source": "system",
  "zone": "wohnzimmer", "device": "ventil-wohnzimmer", "command": "setpoint",
  "payload": "{\"occupied_heating_setpoint\": 21.0}", "outcome": "executed",
  "error": null, "reason": "Zeitplan"}]
```

| Parameter | Bedeutung |
|---|---|
| `zone` | Zonenname, gegen die Namens-Momentaufnahme geprüft — findet auch Einträge einer inzwischen gelöschten Zone |
| `outcome` | `executed`, `suppressed` oder `failed` |
| `from_at`, `to_at` | Zeitpunkte, ISO 8601; ohne Zeitzonenangabe als UTC gelesen |
| `limit` | höchstens 500, Vorgabe 100 |

Ohne Aufbewahrungsfrist wächst diese Tabelle unbegrenzt (`docs/offene-entscheidungen.md`)
— `limit` ist deshalb keine Bequemlichkeit, sondern eine Obergrenze je Abfrage. Ein Wert
ausserhalb von 1 bis 500 ergibt `422`.

`sent_at` trägt immer die Zeitzone (`Z`, also UTC), obwohl intern alles naives UTC ist:
ein naiver Wert würde bei einem Aufrufer in einer anderen Zeitzone als Ortszeit gelesen.

### `GET /api/v1/me` — das eigene Token

Recht: `token.self`. Nützlich, um in einem Skript zu prüfen, ob ein Token noch gilt und
wann es abläuft.

```json
{"id": 3, "name": "Anzeigetafel", "prefix": "a1b2c3d4", "user_id": 1,
 "expires_at": "2027-01-01T00:00:00"}
```

`prefix` ist der mittlere, öffentliche Teil des Tokens — genug, um zwei Tokens
auseinanderzuhalten, und für sich genommen wertlos: Geprüft wird nur der dritte Teil.

### `POST /api/v1/zones/{zone_id}/override` — übersteuern

Recht: `override.create` (für diese Zone). Antwort `201`.

```json
{"temperature_c": 21.5, "duration_minutes": 120}
```

| Feld | Bedeutung |
|---|---|
| `temperature_c` | Pflicht, −20 bis 35 °C, eine Nachkommastelle |
| `duration_minutes` | Ende in Minuten ab jetzt |
| `until_next_switch` | `true` = bis zum nächsten Punkt des Zeitplans |

Ohne beides gilt die Übersteuerung **dauerhaft**, bis jemand sie aufhebt.

Das Ende wird beim Anlegen als Zeitpunkt ausgerechnet und abgelegt, nicht als Regel. Eine
spätere Änderung des Zeitplans verschiebt eine laufende Übersteuerung deshalb nicht
rückwirkend.

### `DELETE /api/v1/zones/{zone_id}/override` — Übersteuerung aufheben

Recht: `override.cancel`. Antwort `204`. Beendet die jüngste noch laufende Übersteuerung.
Gab es keine, ist der Aufruf trotzdem erfolgreich — er stellt einen Zustand her, keine
Aktion.

Übersteuerungen werden nie gelöscht, nur beendet: Sie sind die Historie, und ohne sie ist
Wochen später nicht mehr zu klären, warum ein Raum in einer bestimmten Nacht warm war.

## Beispiel

```bash
TOKEN=<das ausgestellte Token>
BASIS=http://127.0.0.1:8000/api/v1

curl -sH "Authorization: Bearer $TOKEN" "$BASIS/zones"

curl -sX POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"temperature_c": 21.5, "until_next_switch": true}' \
     "$BASIS/zones/1/override"

curl -sX DELETE -H "Authorization: Bearer $TOKEN" "$BASIS/zones/1/override"
```

## Was noch fehlt

Die Schnittstelle wächst mit den Phasen. Solange eine Fähigkeit in der Domänenlogik fehlt,
fehlt sie hier ebenfalls — das ist der Preis dafür, dass es keine zweite Umsetzung derselben
Regel gibt.

Noch nicht vorhanden: Messwerte sowie Geräte über REST anlegen und ändern. Der Stand steht
in der [Roadmap](roadmap.md).

## Stabilität

`v1` im Pfad ist eine Zusage: Was dokumentiert ist, verschwindet innerhalb von `v1` nicht
und ändert seine Bedeutung nicht. Neue Felder in Antworten können hinzukommen — ein
Aufrufer sollte unbekannte Felder deshalb überspringen statt daran zu scheitern.
