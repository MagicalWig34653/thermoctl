# REST-Schnittstelle

`thermoctl` hat drei Adapter über derselben Domänenlogik: die Weboberfläche, diese
REST-Schnittstelle und — ab Phase 5 — einen MCP-Server. Eine Regel ist einmal
implementiert; die Schnittstellen führen sie nur aus. Was hier möglich ist, ist deshalb
genau das, was in der Oberfläche möglich ist, und umgekehrt.

Grundadresse: `/api/v1`. Eine maschinenlesbare Fassung dieser Seite liefert der laufende
Dienst selbst unter `/openapi.json`, zum Durchklicken unter `/docs`.

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
`DELETE /api/v1/zones/{zone_id}/schedule/{punkt_id}` mit demselben Recht.

```json
{"weekday": 1, "minute_of_day": 360, "mode_id": 2}
```

### Regelparameter

`GET /api/v1/zones/{zone_id}/parameters` benötigt `zone.read`,
`PUT /api/v1/zones/{zone_id}/parameters` benötigt `zone.manage`. `null` stellt die Vererbung des globalen Standards wieder her.

```json
{"hysteresis_k": "0.30", "min_on_seconds": 300, "min_off_seconds": null,
 "sensor_timeout_seconds": null, "temperature_offset_k": "0.00",
 "window_resume_delay_seconds": null}
```

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
{"temperature_c": 21.5, "dauer_minuten": 120}
```

| Feld | Bedeutung |
|---|---|
| `temperature_c` | Pflicht, 5 bis 35 °C, eine Nachkommastelle |
| `dauer_minuten` | Ende in Minuten ab jetzt |
| `bis_naechste_schaltung` | `true` = bis zum nächsten Punkt des Zeitplans |

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
     -d '{"temperature_c": 21.5, "bis_naechste_schaltung": true}' \
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
