# MCP-Server

Der dritte Adapter über derselben Domänenlogik, neben der Weboberfläche und der
[REST-Schnittstelle](api.md). Er stellt Zonen, Geräte, Zeitpläne, Sollwerte samt
Begründung und die Schattenentscheidungen bereit und kann übersteuern. `read_control()`
zeigt den gespeicherten ersten Riegel. Der MCP-Prozess teilt den prozesslokalen MQTT-Riegel
nicht mit dem Web-/MQTT-Prozess und liefert seinen Zustand deshalb ausdrücklich als
`unknown_from_mcp_process`. Im Trockenlauf gehen keine Sollwerte hinaus; nach dem
Scharfschalten bleibt der beim Start gebaute MQTT-Riegel bis zum Neustart zu. Erst danach
können Sollwerte an selbstregelnde Thermostatventile und Ein/Aus-Befehle an gewöhnliche
Aktoren gesendet werden.

Installation mit der optionalen Abhängigkeit:

```console
pip install 'thermoctl[mcp]'
```

Ein MCP-Client kann den Server beispielsweise so über stdio starten:

```json
{
  "mcpServers": {
    "thermoctl": {
      "command": "/pfad/zu/thermoctl-mcp",
      "env": {
        "THERMOCTL_DATABASE_URL": "sqlite:////pfad/zur/thermoctl.db",
        "THERMOCTL_SECRET_KEY": "<mindestens-32-zeichen-langes-geheimnis>",
        "THERMOCTL_MCP_TOKEN": "<ausgestelltes-api-token>"
      }
    }
  }
}
```

Das Token muss die Rechte der verwendeten Werkzeuge tragen. Ohne
`THERMOCTL_MCP_TOKEN` verweigert der Server den Start. Token und Datenbankadresse im
Beispiel sind ausschließlich Platzhalter. Dieses Verfahren (stdio) bleibt unverändert; der
HTTP-Zugang unten ist eine zusätzliche Möglichkeit.

## MCP über HTTP

Neben stdio kann der MCP-Server auch über HTTP erreichbar sein, mit Bearer-Token oder
OAuth. Die Werkzeuge und ihre Rechte sind dieselben; nur der Zugang ist ein anderer.
Vorgabe ist **aus** — ohne Einstellung ändert sich nichts.

### Betriebsarten

`THERMOCTL_MCP_MODE` wählt:

| Wert | Bedeutung |
|---|---|
| `off` (Vorgabe) | kein HTTP-Zugang zum MCP-Server |
| `path` | der MCP-Endpunkt liegt unter einem Pfad der Weboberfläche, im selben Prozess und auf demselben Port |
| `port` | der MCP-Server läuft als zweiter Prozess (`thermoctl-mcp --http`) auf eigenem Port; im Container startet `docker/entrypoint.sh` beide und beendet den Container, sobald einer endet |

Einstellungen und ihre Pflichtregeln:

| Variable | `path` | `port` |
|---|---|---|
| `THERMOCTL_PUBLIC_URL` | Pflicht — öffentliche Adresse der Weboberfläche, z. B. `https://heizung.example.org` | Pflicht — dorthin leitet die Zustimmungsseite um |
| `THERMOCTL_MCP_PATH` | Vorgabe `/mcp`; Segmente aus Buchstaben, Ziffern und `. _ ~ -`, ohne `/` am Ende | ohne Bedeutung (ein ungültiger Wert verhindert den Start nicht) |
| `THERMOCTL_MCP_PORT` | ohne Bedeutung | Pflicht, 1–65535 |
| `THERMOCTL_MCP_PUBLIC_URL` | optional; ersetzt `PUBLIC_URL` + Pfad als Adresse des MCP-Endpunkts | Pflicht — öffentliche Adresse des MCP-Listeners, z. B. `https://mcp.example.org` |

- Alle Adressen: `https`, nur für `localhost`, `127.0.0.1` und `::1` ist `http` erlaubt.
  Kein `/` am Ende, keine Zugangsdaten, kein Query, kein Fragment, nur ASCII (Umlaute als
  Punycode).
- Der Pfad darf keinen von thermoctl belegten Pfad treffen (OAuth-Endpunkte, `/api`,
  `/static`, `/.well-known`, die Seiten der Weboberfläche) und nicht mit
  `THERMOCTL_ROOT_PATH` kollidieren.
- Eine unvollständige oder unzulässige Angabe **verhindert den Start** mit einer Meldung,
  die die Variable nennt; es gibt keine stille Rückfallstufe.
- Im Modus `path` braucht die Web-App das MCP-Paket (`thermoctl[mcp]`). Fehlt es, startet
  die Weboberfläche trotzdem; im Log steht `mcp.http.unavailable`. Das Container-Abbild
  bringt es mit.
- `THERMOCTL_MCP_MODE=port` nutzt `THERMOCTL_BIND_HOST`; `thermoctl-mcp --http` ohne
  diesen Modus bricht mit einer Meldung ab.

### Adressen

Der MCP-Endpunkt ist die Adresse aus `THERMOCTL_MCP_PUBLIC_URL`, sonst
`THERMOCTL_PUBLIC_URL` + `THERMOCTL_MCP_PATH` (z. B. `https://heizung.example.org/mcp`).
Die OAuth-Endpunkte hängen daran: `/authorize`, `/token`, `/register`, `/revoke`.

Die Beschreibungen für Clients (`/.well-known/oauth-protected-resource` und
`/.well-known/oauth-authorization-server`) liegen an beiden Orten, an denen ein Client
suchen darf:

- **Modus `path`:** an der Wurzel der Weboberfläche mit dem Pfad dahinter, also
  `https://heizung.example.org/.well-known/oauth-protected-resource/mcp`, und zusätzlich
  unter dem MCP-Pfad selbst (`…/mcp/.well-known/…`). Der Reverse Proxy muss
  `/.well-known/` nicht gesondert behandeln — es ist dieselbe Web-App.
- **Modus `port`:** an der Wurzel des MCP-Listeners; hat `THERMOCTL_MCP_PUBLIC_URL` einen
  Pfad, zusätzlich mit diesem Pfad als Endung. Ein Proxy, der den Pfad auf die Wurzel des
  Listeners abbildet, muss die `/.well-known/`-Pfade weiterreichen.

### Anbinden mit Claude Code

Mit einem Bearer-Token (Abschnitt unten):

```console
claude mcp add --transport http thermoctl https://heizung.example.org/mcp \
  --header "Authorization: Bearer <token>"
```

Per OAuth, ohne vorab ausgestelltes Token:

```console
claude mcp add --transport http thermoctl https://heizung.example.org/mcp
```

Danach im Client `/mcp` aufrufen und `thermoctl` anmelden. Der Client öffnet den Browser
mit der Zustimmungsseite von thermoctl (Ablauf unten). Die Adressen im Beispiel sind
Platzhalter.

### Bearer-Token

Unter `/tokens` in der Oberfläche ausstellen — wie jedes API-Token, siehe
[REST-Schnittstelle](api.md). Das Formular bietet eine **Vorauswahl** (Presets), die nur
Haken setzt; danach lässt sich jedes Recht einzeln ändern. Ein Bearer-Token gilt am
MCP-Endpunkt **und** in der REST-Schnittstelle. Kiosk-Token werden am MCP-Endpunkt
abgewiesen.

### Rechte-Presets

Presets stehen im Code (`thermoctl/domain/token_presets.py`), nicht in der Datenbank; eine
Änderung wirkt nie rückwirkend auf bereits ausgestellte Token.

| Preset | Rechte |
|---|---|
| Read-only | `zone.read`, `device.read`, `audit.read` |
| Read-write | zusätzlich `override.create`, `override.cancel`, `vacation.manage`, `zone.manage`, `schedule.manage`, `setting.manage`, `control.disarm` |

- **`control.arm` steckt in keinem Preset.** Scharfschalten bleibt eine Einzelentscheidung
  und gibt es über MCP ohnehin nicht (siehe unten).
- `control.disarm` ist das Recht für `force_dry_run` (zurück in den Trockenlauf). Es ist
  neu in 0.14.0; die Migration vergibt es an alle Gruppen und Token, die anlagenweit
  `control.arm` haben.
- Ebenfalls in keinem Preset: `user.manage`, `group.manage`, `token.manage`, `token.self`
  und `device.manage`. Wer bei `set_sensor_failure_policy` Ausgleichswerte setzen will,
  hakt `device.manage` zusätzlich an.
- Ein Token trägt höchstens die Rechte seines Besitzers.

### OAuth-Ablauf

1. **Registrierung.** Der Client meldet sich selbst an (`POST /register`, JSON mit
   `client_name` und `redirect_uris`; nur öffentliche Clients, höchstens 10
   Redirect-Adressen). Das gibt ihm eine Client-Id, aber **kein einziges Recht**.
2. **Anfrage.** Der Client schickt den Browser an `/authorize` (Code-Verfahren mit
   PKCE `S256`; der Parameter `resource` ist Pflicht und bindet das Token an diesen
   MCP-Server). thermoctl merkt sich die Anfrage 10 Minuten und leitet auf
   `/oauth/consent` der Weboberfläche weiter.
3. **Anmeldung.** Wer nicht angemeldet ist, landet auf der Anmeldeseite und danach wieder
   hier. Nötig ist das Recht `token.self`.
4. **Zustimmung.** Die Seite nennt den Namen des Clients (frei gewählt, **nicht geprüft**)
   und die Adresse, an die zurückgeleitet wird. Für Adressen im Netz steht eine Warnung
   da. Eine Vorauswahl (Read-only oder Read-write) setzt die Haken; angeboten werden nur
   Rechte, die der angemeldete Nutzer selbst hat. Wer „Zulassen" wählt, vergibt genau die
   angehakten Rechte, mindestens eines; „Ablehnen" schickt `access_denied` zurück.
5. **Tausch.** Der Client tauscht den Code (gilt 5 Minuten, genau einmal) am `/token`
   gegen ein Zugriffs-Token und ein Refresh-Token.
6. **Gültigkeit.** Das Zugriffs-Token gilt **1 Stunde**, das Refresh-Token **30 Tage**.
   Bei jeder Erneuerung wird das Refresh-Token ersetzt (Rotation). Wird ein schon
   benutztes oder widerrufenes Refresh-Token noch einmal vorgelegt, **sperrt thermoctl die
   ganze Familie** — auch das gerade gültige Token dieser Sitzung. Eine Erneuerung
   behält nur die Rechte, die der Nutzer noch hat; bleibt keines, ist die Sitzung zu Ende.
7. **Einsicht und Widerruf.** OAuth-Token erscheinen unter `/tokens` mit Quelle `oauth` und
   dem Namen des Clients und lassen sich dort widerrufen (das sperrt auch die
   Refresh-Familie). `POST /revoke` widerruft ein Refresh- oder Zugriffs-Token (RFC 7009)
   und antwortet immer gleich.

### Debugging

- Jeder Werkzeugaufruf schreibt `event=mcp.tool` mit `tool`, `key_prefix` (die ersten
  Zeichen des Tokens, nie das Token), `outcome` und bei Fehlern `reason`. `outcome` ist
  `ok`, `denied` (Recht fehlt), `rejected` (die Domäne hat die Eingabe abgewiesen),
  `not_found` oder `error`.
- Die Zugangsprüfung am HTTP-Eingang schreibt `event=mcp.http.auth` mit `outcome` `ok` oder
  `denied` und dem Grund (kein Bearer-Header, Token ungültig/widerrufen/abgelaufen/nur
  Kiosk, Origin nicht erlaubt).
- Die OAuth-Vorgänge schreiben Ereignisse mit dem Namen `oauth.…` (u. a. `oauth.approve`,
  `oauth.token.refresh`, `oauth.revoke`, `oauth.purge`), jeweils mit `outcome`.
- Der Grund einer Ablehnung steht auch **im Ergebnis des Werkzeugs**, das das Modell
  liest — es kann ihn weitergeben, statt zu raten.
- `401` am Endpunkt heißt: kein oder ungültiges Token (die Antwort verweist per
  `WWW-Authenticate` auf die Beschreibung des Servers); `403 forbidden_origin`: der
  `Origin`-Header passt nicht zur MCP-Adresse; ein abgewiesener `Host`-Header kommt vom
  Transportschutz des MCP-Pakets (siehe Reverse Proxy in
  [self-hosting.md](self-hosting.md)).

### Grenzen und Sicherheit

- **OAuth-Token gelten nur am MCP-Endpunkt.** Die REST-Schnittstelle weist sie mit `401`
  ab, damit ein KI-Client, dem man MCP erlaubt hat, nicht die ganze REST-Fläche bekommt.
- **Die Zustimmungsseite lässt sich nicht einbetten** (`frame-ancestors 'none'`,
  `X-Frame-Options: DENY`).
- **Die Registrierung ist offen, aber ohne Rechte.** Rechte entstehen erst durch die
  Zustimmung eines angemeldeten Nutzers. Je Quelladresse sind 20 Registrierungen pro
  Stunde erlaubt. **Hinter einem Reverse Proxy ist das die Adresse des Proxys:** uvicorn
  wertet Weiterleitungsheader nur für Adressen aus `FORWARDED_ALLOW_IPS` aus. Wer den
  Proxy dort einträgt, bekommt die Adresse des Besuchers; sonst teilen sich alle die
  Grenze. Die Begrenzung selbst liest keine Header.
- **Wachstumsgrenzen**, weil `/register` und `/authorize` öffentlich sind: höchstens 1000
  nie benutzte Clients, nach einem Tag werden sie gelöscht; höchstens 100 offene
  Anfragen je Client.
- **Aufräumen** läuft täglich im Takt der Aufbewahrung: abgelaufene Anfragen, Codes und
  Refresh-Token verschwinden einen Tag nach Ablauf. Benutzte Clients und widerrufene
  Token bleiben zur Nachvollziehbarkeit.
- Ein Downgrade der Migration `b721e49c6a30` **löscht alle OAuth-Token**, weil sie sonst
  als dauerhafte Bearer-Token weitergälten.
- HTTP-Zugang braucht TLS (Abschnitt 8c in [self-hosting.md](self-hosting.md)).

## Die Werkzeuge

25 Stück, alle über dieselbe Domänenlogik wie Oberfläche und REST-Schnittstelle. Jedes
prüft dasselbe Recht wie der entsprechende REST-Endpunkt.

| Werkzeug | Recht | Was es liefert |
|---|---|---|
| `list_zones()` | `zone.read` | Name, Anzeigename, Betriebsart und Sonnenprofil je sichtbarer Zone |
| `zone_state(zone_id)` | `zone.read` | Ist-Temperatur, Messzeitpunkt, Sensorzustand |
| `explain_setpoint(zone_id)` | `zone.read` | Sollwert **und Begründung**, aus derselben Funktion, die auch regelt |
| `read_schedule(zone_id)` | `zone.read` | Schaltpunkte mit Wochentag, Minute im Tag und Modusnamen |
| `read_setpoints(zone_id)` | `zone.read` | die gesetzte Temperatur je Modus |
| `list_devices()` | `device.read` | Anbindung, Fähigkeiten, letzte Nachricht, Batterie |
| `shadow_decisions(zone_id, count=10)` | `zone.read` | die jüngsten Entscheidungen samt Grund |
| `device_commands(zone, outcome, from_at, to_at, limit=100)` | `audit.read` | das Schaltprotokoll — jeder gesendete, unterdrückte oder gescheiterte Befehl an ein Gerät, plus Notbetriebsentscheidungen (`entry_kind: "entscheidung"`) und Wechsel der Sonnenabsenkung (`entry_kind: "absenkung"`: Beginn, Betragsänderung, Ende je Zone; `outcome` `absenkung_beginn`/`absenkung_aenderung`/`absenkung_ende`, `reason` mit Sollwert vor der Absenkung und wirksamem Sollwert) |
| `temperature_history(zone_id, hours=24, from_at, to_at, max_points=300, log_limit=100)` | `zone.read` (Protokoll: zusätzlich `audit.read`) | die Messreihe einer Zone — gemessen, wirksamer Sollwert, Sollwert vor der Sonnenabsenkung, Sonnenabsenkung, Anteil der Messungen mit Heizanforderung — in gleich breite Zeitfächer verdichtet, mit Zusammenfassung, und im selben Aufruf die Einträge des Schaltprotokolls dieser Zone im Zeitraum |
| `read_emergency_state(zone_id)` | `zone.read` | Notbetrieb-Stufe, aktive Ersatzquelle, Rückkehrfortschritt, Takt-/Übergabestatus je Aktor, Ersatzquelle-Wandfühler-Vergleich |
| `override(zone_id, temperature_c, ends_at)` | `override.create` | legt eine Übersteuerung an |
| `cancel_override(zone_id)` | `override.cancel` | beendet die laufende Übersteuerung |
| `boost(zone_id)` | `override.create` | zieht die nächste Schaltung vor |
| `read_vacation()` | `zone.read` | laufenden oder geplanten Urlaub, oder nichts |
| `vacation(start_date, end_date, setback_temperature_c)` | `vacation.manage` | setzt den anlagenweiten Urlaub |
| `cancel_vacation()` | `vacation.manage` | beendet den Urlaub vorzeitig |
| `read_control_parameters(zone_id)` | `zone.read` | wirksame Regelparameter **samt ihrer Grenzen** |
| `set_control_parameters(zone_id, name, value)` | `zone.manage` | setzt einen Parameter, lässt die übrigen |
| `read_control()` | `zone.read` | gespeicherter Riegel, nicht feststellbarer MQTT-Riegel, globale Vorgaben und Sonnenabsenkung |
| `force_dry_run(reason)` | `control.disarm` | nimmt die Regelung in den Trockenlauf zurück |
| `move_schedule_point(zone_id, point_id, weekday, minute)` | `schedule.manage` | setzt einen Punkt auf einen anderen Zeitpunkt |
| `read_sensor_failure_defaults()` | `zone.read` | anlagenweites Notbetriebsprofil (Festtakt, Rückkehr, Kennlinie) und anlagenweiter Notsollwert |
| `set_sensor_failure_defaults(fixed_on_seconds, fixed_off_seconds, recovery_seconds, recovery_samples, warm_restart_hysteresis_k, emergency_setpoint_c, curve_points=None, profile_name=None)` | `setting.manage` | speichert Profil und Notsollwert zusammen |
| `read_sensor_failure_policy(zone_id)` | `zone.read` | wirksame und eigene Notbetriebs-Einstellungen einer Zone samt Ausgleichswerten ihrer Thermostat-Aktor-Zuordnungen |
| `set_sensor_failure_policy(zone_id, enabled, profile_id=None, emergency_setpoint_c=None, backup_offsets=None)` | `zone.manage` (zusätzlich `device.manage` für `backup_offsets`) | setzt Aktivierung, Profil-/Notsollwert-Herkunft, optional Ausgleichswerte |

Eine nicht sichtbare Zone wird wie eine unbekannte behandelt — die Antwort verrät nicht,
dass es sie gibt.

**`device_commands` prüft `audit.read`, nicht `zone.read`.** Wie die gleichnamige Ansicht
in der Oberfläche und der REST-Endpunkt ist das Schaltprotokoll ein Protokoll über die
ganze Anlage, nicht über eine einzelne Zone — ein auf eine Zone eingeschränktes Recht
genügt hier nicht. `zone` filtert gegen die Namens-Momentaufnahme der Zeile, nicht gegen
die aktuelle Zone, und findet deshalb auch Einträge einer inzwischen gelöschten Zone.
`limit` ist wie überall dort, wo eine Tabelle keiner Aufbewahrungsfrist unterliegt, auf
höchstens 500 begrenzt (Vorgabe 100) — ein Wert außerhalb löst einen Fehler aus. Jeder
Zeitpunkt trägt die Zeitzone ausdrücklich (`+00:00`), damit ein Eintrag auch Wochen
später noch eindeutig neben einem Vorfallsbericht aus einer anderen Zeitzone liegt.

**`temperature_history` beantwortet „warum war es gestern Abend im Wohnzimmer kalt" in einem
Aufruf.** Es liefert die Messreihe, mit der auch die Verlaufskachel zeichnet, als Zahlen und
legt die Schaltvorgänge derselben Zone daneben. Das Zeitfenster kommt aus `from_at`/`to_at`
(ohne Zeitzone gilt UTC) oder aus den letzten `hours` (1 bis 168); höchstens 7 Tage, wie in
der Oberfläche. Die Messungen werden in Zeitfächer von `bucket_minutes` Minuten verdichtet
(Spanne geteilt durch `max_points`, aufgerundet auf ganze Minuten, mindestens eine); je Fach
steht der Mittelwert samt kleinstem und größtem Wert, der zuletzt gültige Sollwert und der
größte Absenkungsbetrag. **Ein Fach ohne Messung fehlt in `points`** — das ist eine Lücke, es
wird weder interpoliert noch mit 0 gefüllt; `summary.buckets_without_data` zählt sie.
`null` heißt unbekannt (etwa Zeilen aus der Zeit vor der Aufzeichnung der Absenkung), `0`
heißt belegt „keine Absenkung". `heat_share` ist der Anteil der Messungen, bei denen die
Regelung eine Heizanforderung ausgab (`would_heat`); das ist eine Aussage über die
Entscheidung der Regelung, nicht über den Raum — was ein Gerät daraufhin tat, steht im
Schaltprotokoll. Dieses gehört nur dazu, wenn das Token `audit.read` hat (aus demselben
Grund wie bei `device_commands`); sonst ist `switching_log` `null`, `switching_log_note`
nennt das fehlende Recht, und die Messreihe kommt trotzdem. Das Protokoll wird über den
Anzeigenamen der Zone gefiltert, unter dem es sie führt; wurde die Zone umbenannt, tragen
ältere Einträge den früheren Namen. Werte außerhalb der Grenzen (`hours`, Fensterlänge,
`max_points` 10 bis 1000, `log_limit` 1 bis 500) lehnt das Werkzeug mit einer Meldung ab.

Beispiel (gekürzt), Anfrage `temperature_history(zone_id=3, hours=6, max_points=12)`:

```json
{
  "zone": "wohnzimmer", "display_name": "Wohnzimmer",
  "from": "2026-10-10T16:00:00+00:00", "to": "2026-10-10T22:00:00+00:00",
  "bucket_minutes": 30,
  "summary": {"sample_count": 350, "first_at": "2026-10-10T16:00:00+00:00",
              "last_at": "2026-10-10T21:59:00+00:00", "measured_min_c": 17.9,
              "measured_avg_c": 19.2, "measured_max_c": 20.8, "heat_share": 0.41,
              "buckets_without_data": 1},
  "points": [{"at": "2026-10-10T16:00:00+00:00", "measured_c": 20.4, "measured_min_c": 20.1,
              "measured_max_c": 20.8, "setpoint_c": 20.5, "scheduled_setpoint_c": 20.5,
              "solar_setback_k": 0.0, "heat_share": 0.2}],
  "switching_log": [{"sent_at": "2026-10-10T17:42:00+00:00", "zone": "Wohnzimmer",
                     "entry_kind": "befehl", "outcome": "executed", "...": "..."}],
  "switching_log_note": null
}
```

**`boost` ist für ein Sprachmodell die verlässlichere Form von „mach es hier wärmer".** Es
muss weder eine Temperatur noch eine Dauer raten, und nach dem Schaltpunkt räumt sich der
Eingriff selbst weg. `override` bleibt daneben für den Fall, dass jemand eine bestimmte
Temperatur nennt.

**`vacation` verlangt `vacation.manage`, nicht `override.create`.** Ein Urlaub wirkt wie
eine Übersteuerung, aber über alle Zonen gleichzeitig statt über eine benannte — das
zonenbezogene `override.create` passt weder in der Zone noch in der Bedeutung. Für ein
Sprachmodell ist das der natürliche Weg für „ich bin ab dem 23. bis Dreikönig weg, stell
die Heizung auf 15 Grad runter": eine einzige, plant-weite Angabe statt einer Übersteuerung
je Zone. `start_date`/`end_date` sind lokale Kalendertage, beide eingeschlossen; die
Zeitzone der Anlage entscheidet, wann genau das lokal beginnt und endet. Eine bereits
laufende, von Hand gesetzte Übersteuerung einer einzelnen Zone bleibt davon unberührt, bis
sie selbst endet — siehe `domain/schedule.py`.

**`read_control_parameters` liefert die Grenzen mit.** Ohne sie wäre jeder Schreibversuch ein
Versuch: „0,05 Kelvin Hysterese" sieht für ein Modell so plausibel aus wie „0,5".
`set_control_parameters` verlangt `zone.manage` und nicht `override.create` — ein
Regelparameter wirkt dauerhaft und auf jede künftige Entscheidung, eine Übersteuerung nur
bis zum nächsten Schaltpunkt.

**`read_control()` liefert unter anderem `assumed_relay_lifetime_operations`** — die
angenommene Relais-Lebensdauer, Vorgabe 500.000, seit 0.6.0 unter
`PUT /api/v1/control/defaults` einstellbar (nicht über MCP schreibbar, `read_control()`
liest sie nur mit). Es ist eine Annahme, keine Herstellerangabe: öffentliche Meross-Daten
nennen keine Relaislebensdauer.

**Zu den `pi_*`-Parametern (Beta, seit 0.5.0):** `pi_enabled` ist je Zone aus als Vorgabe.
Ein Modell kann es über `set_control_parameters` einschalten, aber nur für eine Zone, die
dafür taugt. Die Prüfung ist gerätegenau, nicht zonenweit — ein Gerät mit der Fähigkeit
`thermostat`, das nicht selbstregelnd ist, schließt PI aus, ebenso eine Zone ohne
gewöhnlichen Schaltaktor. Ein **selbstregelndes** Ventil schließt die Zone dagegen nicht
aus: Es bekommt seinen Sollwert über einen eigenen Weg und sieht die PI-Entscheidung nie,
sodass es neben einem gewöhnlichen Schaltaktor stehen darf — PI steuert dann nur diesen
Schaltaktor an. Der Versuch wird bei fehlender Eignung mit einer Begründung abgewiesen.
Das ist Absicht: Ein Modell soll den
Rückfall nicht stillschweigend bekommen, sondern den Grund lesen können.

PI (Beta) kann je nach Einstellung mehr Schaltspiele erzeugen als die Hysterese. Wer es über
MCP aktiviert, umgeht den Warnhinweis, den die Oberfläche am Schalter zeigt — eine
Modellrechnung steht dort und im Eintrag zu 0.5.0 im [CHANGELOG](../CHANGELOG.md), und die
tatsächlich gezählten Schaltspiele sind in der Oberfläche unter „Relaisverschleiß" ablesbar.

**Der eigentliche Gewinn sind `explain_setpoint` und `shadow_decisions`.** Sie
beantworten „warum ist es hier kalt?" in einem Aufruf, statt Ist-Wert, Zeitplan und
Regelentscheidung von Hand zusammenzusuchen.

## Was es bewusst nicht gibt

**Kein Werkzeug schaltet die Anlage scharf.** `force_dry_run` gibt es, die
Gegenrichtung nicht — obwohl die Oberfläche und die REST-Schnittstelle sie können.

Der Grund: Die Domäne verlangt beim Scharfschalten eine Begründung, und für ein
Sprachmodell ist das keine Hürde, sondern genau die Sorte Text, die es mühelos erzeugt. Die
Sperre wäre hier eine Formalie statt einer Entscheidung. Zurück in den Trockenlauf ist
dagegen immer die sichere Richtung und soll jedem offenstehen, der die Anlage bedienen darf.
Wer scharf schalten will, tut das dort, wo ein Mensch am Knopf steht.

Schreibende Werkzeuge für Zonen und Sollwerte gibt es nicht. Regelparameter sind die
bewusste Ausnahme: `set_control_parameters` setzt genau einen benannten Parameter innerhalb
der von der Domäne vorgegebenen Grenzen und verlangt `zone.manage`. Wer eine Zone umbaut
oder Sollwerte pflegt, tut das in der Oberfläche oder über die REST-Schnittstelle, wo eine
Person daneben sitzt.

Beim Zeitplan gibt es eine Ausnahme: `move_schedule_point` ändert einen **schon
vorhandenen** Punkt, legt aber keinen an und löscht keinen. „Verschieb die Morgenheizung
im Bad eine halbe Stunde nach hinten" ist eine alltägliche Bitte, und der Umfang des
Schadens ist auf einen Zeitpunkt begrenzt.

Die Grenzen für Übersteuerungen (−20 bis 35 °C, eine Nachkommastelle) prüft die Domäne, nicht
der Adapter. Ein Werkzeug, das `temperature_c=99` schickt, bekommt einen Fehler — und zwar
denselben wie die Oberfläche.
