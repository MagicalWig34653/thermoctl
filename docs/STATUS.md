# Stand

Letzte Aktualisierung: 2026-09-05, Freigabe `v0.7.0`.

## Zwei Fehler in der Homebridge-Konfiguration behoben

Aus dem echten Betrieb gemeldet: ein Wechsel von Aus auf Automatik kam bei thermoctl nie
an, und eine Zone ohne Messwert liess HomeKit mit `... number 0 exceeded minimum of 10`
abstürzen. Beide Ursachen lagen in der Beispielkonfiguration für `mqtt-thing`
(`thermoctl/domain/interfaces.py::homebridge_zone_configs`, `docs/homebridge.md`), nicht
im MQTT-Vertrag selbst:

- Die bisherigen `apply`-Funktionen für Ziel-Zustand rechneten mit HomeKit-Zahlen
  (0/1/2/3) — aber `mqtt-thing`s `multiCharacteristic` übergibt beim Setzen bereits den
  **Listenwert** aus `heatingCoolingStateValues` an `apply`, nicht die Zahl, und schlägt
  beim Lesen `apply`s Rückgabe in derselben Liste nach. Die Zuordnung stand deshalb auf
  keiner Seite je richtig. Behoben, indem `heatingCoolingStateValues` jetzt direkt
  thermoctls eigenes Vokabular trägt (`off`/`manual`/`auto`); Ziel-Zustand braucht dadurch
  gar kein `apply` mehr. Der Ist-Zustand (`would_heat`, `true`/`false`) bleibt die eine
  Ausnahme mit eigenem `apply` — und das ruft jetzt `message.toString()` auf, weil
  `apply` den rohen MQTT-`Buffer` bekommt, gegen den ein bloßes `=== 'true'` nie zutrifft.
- Eine Zone ohne Messwert veröffentlicht bewusst eine leere Nutzlast
  (`_as_text(None)`) — für Home Assistant richtig (dessen MQTT-Climate-Integration
  ignoriert eine leere Nutzlast ausdrücklich), aber `mqtt-thing`s Fliesskommaparser macht
  daraus `NaN`, was HomeKit unterhalb von `minTemperature` ablehnt. `publishing.py` bleibt
  deshalb unverändert; `getCurrentTemperature` bekommt stattdessen ein `apply`, das bei
  leerer Nutzlast `undefined` liefert.

Beide Befunde sind gegen den Quelltext des Plugins geprüft (dessen index.js und
libs/mqttlib.js, `arachnetech/homebridge-mqttthing`), nicht nur gegen dessen README. Vier neue
Wächtertests in `tests/test_homebridge_interface.py` prüfen jetzt zusätzlich, dass Ziel-
Zustand kein `apply` mehr trägt, dass `heatingCoolingStateValues` thermoctls Vokabular an
den richtigen Indizes führt und Index 2 keinen gültigen Modus ist, dass der Ist-Zustands-
`apply` in dieselbe Liste decodiert und `.toString()` aufruft, und dass der Temperatur-
`apply` eine leere Nutzlast auf `undefined` abbildet — die bisherigen Tests prüften nur die
Topic-*Pfade*, nie den Inhalt der `apply`-Funktionen oder `heatingCoolingStateValues`, und
hätten diese Fehlerklasse deshalb nicht gefunden.

## Passkeys und MCP-Token haben jetzt eigene Add-on-Felder

`docker/thermoctl_optionen.py`: `mcp_token`, `passkey_rp_id`, `passkey_rp_name` und
`passkey_origin` sind aus `BEWUSST_AUSGELASSEN` in `ABGEBILDETE_FELDER` gewandert —
bisher nur über das freie `env`-Feld erreichbar, jetzt mit eigener Beschriftung im
Add-on-UI. `tools/env_nach_addon.py::_SCHEMA_REIHENFOLGE` musste dieselben vier
Felder bekommen, sonst hätte `als_yaml` sie beim Umstieg von `.env` auf das Add-on
stillschweigend verworfen, statt sie auszugeben — ein neuer Test
(`test_jedes_dedizierte_feld_steht_in_der_schema_reihenfolge`) hält beide Listen
seither zusammen.

**Befund zu Passkeys hinter Ingress:** Sie funktionieren, solange Home Assistant
selbst unter einem Hostnamen erreichbar ist — `passkey_rp_id`/`passkey_origin` müssen
dann auf *diesen* Hostnamen zeigen, nicht auf einen des thermoctl-Containers, den der
Browser unter Ingress nie sieht (Home Assistant Core reicht die Anfrage intern
weiter, `X-Ingress-Path` gesetzt, siehe Abschnitt oben zum Ingress-Präfix). Bei
reinem IP-Zugriff auf Home Assistant (kein Hostname) funktionieren Passkeys
grundsätzlich nicht — WebAuthn verlangt einen gültigen Domainnamen als
Relying-Party-Id, keine Einstellung kann das umgehen. Details in
`docs/self-hosting.md`, Abschnitte 6c und 8.

**Diese Datei sagt, was jetzt gilt — sonst nichts.** Wie es dazu kam, welche Fehler wie
gefunden wurden und warum etwas so entschieden ist, wird hier nicht mitgeführt; das
gehört in `git log` und die Auftragsberichte. Der Grund für diese Trennung: Diese Datei
ist zweimal auf über tausend Zeilen gewachsen und enthielt dabei gleichzeitig aktuelle und
längst überholte Angaben — zuletzt „nichts ist scharf", „1024 Tests, 98,55 %",
„`control_armed` wird nirgends gesetzt", „es gibt keine Geräteerkennung für Meross". Alle
vier stimmten einmal und standen noch da; ein Freigabe-Review konnte sie namentlich
widerlegen.

## Der Ingress-Präfix gilt jetzt pro Anfrage, nicht mehr pro Prozess

Als Home-Assistant-Add-on ist `thermoctl` sowohl über Ingress als auch — der
Container-Port ist freigegeben (`ports: 8000/tcp`) — direkt über einen eigenen
Reverse-Proxy erreichbar, beides gleichzeitig, aus demselben laufenden Prozess.
`thermoctl/app.py::create_app` entscheidet den Ingress-/Reverse-Proxy-Präfix dafür
pro Anfrage (Middleware `resolve_root_path`), nicht mehr einmal für den ganzen
Prozess über FastAPIs `root_path`-Konstruktorargument: Trägt die Anfrage die
Kopfzeile `X-Ingress-Path` mit exakt dem Wert, den dieser Prozess beim Start vom
Supervisor erfragt hat (`Settings.root_path`, gesetzt über
`docker/thermoctl_ingress.py`), gilt der Präfix für diese Anfrage — sonst nicht,
unabhängig davon, was konfiguriert ist (`thermoctl.app._ingress_header_prefix`).
Ohne konfigurierten Präfix wird die Kopfzeile gar nicht erst gelesen. Recherchiert
(Quelltext von `home-assistant/core`,
`homeassistant/components/hassio/ingress.py::_init_header`): Home Assistant Core
setzt diese Kopfzeile unbedingt auf jeder über Ingress weitergeleiteten Anfrage,
HTTP wie WebSocket, mit exakt dem Wert, den der Supervisor als `ingress_entry`
ausgibt — beide stimmen byteweise überein, wenn eine Anfrage tatsächlich über
Ingress kam. Fehlt die Kopfzeile trotzdem, wird die Anfrage wie eine direkte
behandelt: sichtbar unpräfigierte Navigation statt eines stillen
Sicherheitsproblems.

Sitzungscookies folgen demselben Präfix (`thermoctl/web/urls.py::cookie_path`) und
sind dadurch ebenfalls pro Zugangsweg getrennt, solange beide unter
unterschiedlichen Adressen erreichbar sind — der übliche Fall. Details, auch zum
Randfall gleicher Hostname/unterschiedlicher Port, und zum Befund bei Passkeys (an
den einen konfigurierten Hostnamen gebunden, nur untersucht, nicht gelöst) stehen in
`docs/self-hosting.md`, Abschnitt 6c.

## Eine per Boost ausgelöste Übersteuerung liess sich nicht aufheben

Boost gibt es an drei Stellen (Kiosk, MQTT/Home Assistant, REST/MCP); aufheben liess sich
zuvor nur an einer, der Startseite der angemeldeten Oberfläche. REST und MCP hatten
`cancel_override` bereits. Ergänzt: ein Kiosk-Knopf „Übersteuerung aufheben" (nur
sichtbar, wenn eine Übersteuerung läuft; Recht `override.cancel`, nicht `override.create`
-- er hebt womöglich die Übersteuerung eines anderen auf) und für Home Assistant der
Befehl `command/cancel_override` samt Discovery-Knopf sowie der neue Zustandswert
`state/override_active`, damit sichtbar ist, ob es dort überhaupt etwas aufzuheben gibt.
`domain/schedule.py::running_override` ist die neue, einmal implementierte Abfrage dafür.
Damit ein Kiosk-Token diesen Knopf je sehen kann, gehört `override.cancel` jetzt zu
`domain/kiosk.py::KIOSK_CONTROL_PERMISSIONS` -- ohne das hätte `issue_kiosk_token`
niemals ein Token damit ausgestattet, und der Knopf wäre für jedes Wandtablett
unerreichbar geblieben. Neu ausgestellte Kiosk-Token bekommen das Recht automatisch;
schon ausgestellte brauchen dafür eine erneute Ausstellung.

## Homebridge-Konfiguration direkt auf der Schnittstellen-Seite

`/interfaces` zeigt je sichtbarer Zone einen fertigen `mqtt-thing`-Block zum Kopieren.
Die Topics baut `domain/interfaces.py` über dieselben Funktionen wie die
Veröffentlichung selbst (`states_topics`/`command_topics`), mit dem tatsächlich
konfigurierten MQTT-Präfix — kein zweites Mal abgeschrieben, und ein Wächtertest hält
das gerenderte HTML gegen `publication.py`.

**Zugangsdaten stehen nie darin.** Homebridge bekommt Platzhalter und den Hinweis, dass
es einen eigenen Broker-Zugang mit engen Rechten braucht; die Erzeugung liest die
eigenen MQTT-Zugangsdaten gar nicht erst.

Die Seite verlangt weiterhin `setting.manage`, der Abschnitt zeigt aber nur Zonen, für
die zusätzlich `zone.read` vorliegt — dieselbe Filterung, mit der die Bediengeräteseite
ihren Befund von 2026-09-02 behoben hat.

Der Kopierknopf kommt ohne Bibliothek aus. Die Zwischenablage-Schnittstelle des Browsers
verlangt einen sicheren Kontext, den ein Heimnetz über schlichtes HTTP nicht bietet;
dort greift ein Rückfallweg, und scheitert auch der, sagt der Knopf es, statt stumm zu
bleiben.

## Wo das Projekt steht

Die Anlage des Projektinhabers läuft seit dem 2026-09-02 scharf mit `thermoctl`, das
Altsystem bleibt parallel als Rückfallebene. Das Repository ist seit `v0.6.1` öffentlich
(`github.com/MagicalWig34653/thermoctl`), unter der AGPL-3.0. Der Betrieb läuft heute in
zwei Formen: als eigener Docker-Container (`docker compose`) und als
Home-Assistant-Add-on — Letzteres war im ursprünglichen Rahmenentwurf nicht vorgesehen,
siehe [roadmap.md](roadmap.md).

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | gebaut; die Abnahme anhand echter Betriebsdaten ist geprüft und **nicht bestanden** (siehe unten) |
| 3 — Konfigurations-Oberfläche | abgeschlossen |
| 4 — Regelkreis und Cutover | schaltet scharf an der echten Anlage; Ablösung des Altsystems noch offen |
| 5 — Integrationen und Veröffentlichung | Repository öffentlich, Add-on-Betrieb dazugekommen |

Details je Phase, Aufgabenlisten und was nicht ursprünglich vorgesehen war stehen in
[roadmap.md](roadmap.md).

## Zahlen

Selbst nachgemessen für diese Freigabe (nicht aus einem früheren Bericht übernommen):

| | |
|---|---|
| Tests | 4488 unter SQLite, unverändert unter MariaDB (Exit 0, keine Skips) |
| Testabdeckung | 100 %, Mindestschwelle 100 % in der CI |
| Ruff, mypy strict | ohne Befund, 109 Quelldateien |
| Migrationskette | linear, ein Kopf (`67e794059830`), vorwärts und rückwärts gegen beide Datenbanken geprüft; **keine neue Migration seit `v0.6.4`** |
| Container | baut (`docker build -f docker/Dockerfile`), Exit 0 |

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`. Wer
die zweite setzt, läuft unbemerkt gegen SQLite und bekommt trotzdem einen grünen Lauf.

## Was geschaltet wird — genau

- **`setting.control_armed`** ist der erste Riegel. Steht er auf `false`, geht an keinen
  Aktor etwas hinaus — das ist der Zustand einer neuen Anlage. `/control/arm` öffnet ihn,
  mit eigenem Recht `control.arm`.
- **Der MQTT-Client trägt einen zweiten, unabhängigen Riegel**, der beim Prozessstart
  gebaut wird. Scharfschalten wirkt deshalb erst nach einem Neustart.
- **Sind beide Riegel offen**, veröffentlicht der Dienst: Sollwerte an selbstregelnde
  Thermostatventile, Ein/Aus an gewöhnliche Zigbee2MQTT-Aktoren
  (`services/publishing.py::_send_actuator_switches`), Sollwert und `system_mode`
  (wo vorhanden) an Zigbee2MQTT-Thermostatventile ohne eigene Regelung
  (`Zigbee2MqttThermostat`), und Schaltbefehle an Meross-Steckdosen über eine
  zwischengespeicherte Cloud-Sitzung (`services/meross_session.py`), außerhalb jeder
  Datenbanktransaktion.
- **Ein gescheiterter Befehl wird jeden scharfen Zyklus erneut versucht** — unbegrenzt oft,
  bewusst ohne Backoff — und nur einmal pro Ausfallepisode geloggt; der
  Zwischenspeicher „nur bei Änderung senden" trägt das Ergebnis im Schlüssel, damit ein
  gescheiterter Befehl das Gerät nicht dauerhaft überspringt.
- **Das Schaltprotokoll** (`device_command`, `/device-commands`, Recht `audit.read`)
  zeichnet jeden Befehl auf, der hinausging oder im Trockenlauf unterdrückt oder
  verworfen wurde — Zeitpunkt, Zone, Gerät, Nutzlast, Ergebnis, Begründung, Auslöser.
  Ein Eintrag überlebt das Löschen oder Umbenennen seiner Zone oder seines Geräts
  (`SET NULL` plus Namens-Momentaufnahme) und unterliegt keiner automatischen
  Aufbewahrung — anders als Messwerte ist ein Schaltbefehl selten und jeder einzelne
  kann der Beleg sein, nach dem später jemand sucht. REST und MCP ziehen hier noch
  nicht nach (bewusste Entscheidung, keine übersehene Lücke).
- **`decide()` in `thermoctl/domain/control_loop.py`** berechnet `protection_allowed`
  einmal, oberhalb der Mindestschaltdauer-Regel; die Ausnahme von der Mindestschaltdauer
  gilt achsenabhängig — für einen gehaltenen Ein-Zustand reicht `valve_protection_active`
  allein, für den Aus-Timer zusätzlich `protection_allowed`. Ein Ventilschutzlauf, der
  seinen Vorrang mitten im Lauf verliert (Übersteuerung, „aus", Sensorausfall), hebt die
  Mindestschaltdauer nicht mehr auf — dieser Zusammenhang war einmal ein echter Fehler in
  der Regelkette (Taktschutz ausgehebelt für die Restdauer eines abgebrochenen
  Schutzlaufs) und ist die Eigenschaft, die `tests/test_control_loop_state_table.py`
  (2.376 erreichbare Kombinationen von 3.888 Rohkombinationen) mit erschöpft.

## Add-on-Betrieb

- **Optionsschema ist flach**: `secret_key`, `log_level`, `log_format`,
  `database_*` (fünf Felder), `mqtt_*` (neun Felder, inklusive `client_id` und
  `ca_cert`), `meross_email`/`meross_password`, `notify_webhook*`. Dazu ein freies Feld
  **`env`** (eine `NAME=WERT`-Zuweisung je Zeile) für jede `THERMOCTL_*`-Variable ohne
  eigenes Feld. Reihenfolge bei Überschneidung: dedizierte Felder, dann `env`, dann —
  gewinnt gegen beides — eine vom Betreiber tatsächlich gesetzte Umgebungsvariable.
  `docker/thermoctl_optionen.py` übersetzt; `ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN`
  dort sind gegen `Settings.model_fields` gewächtert
  (`test_every_settings_field_is_translated_or_deliberately_excluded`).
- **Das Abbild startet als root** und gibt die Rechte über `setpriv` an den
  unprivilegierten Benutzer `thermoctl` ab, bevor Migration und Dienst laufen — nötig,
  weil der Home-Assistant-Supervisor `/data/options.json` root:root anlegt. Der
  gewöhnliche `docker compose`-Betrieb mit explizit gesetztem `user:` bleibt unverändert
  unprivilegiert; ohne `user:`-Angabe läuft der Container kurz als root und fällt vor
  `alembic` zurück.
- **Ingress-Präfix**: `THERMOCTL_ROOT_PATH` (aus Konfiguration, nicht aus der
  `X-Ingress-Path`-Kopfzeile) setzt FastAPIs `root_path`; jeder lokale Verweis in den
  Vorlagen, Cookie-`path` und der `/static`-Mount respektieren ihn. `/healthz` bleibt
  bewusst unpräfigiert — ein Docker-Healthcheck erreicht den Container direkt.
- **Mehrarchitektur-Abbild**: `linux/amd64` und `linux/arm64`, `armv7` ausdrücklich
  nicht.
- **`tools/env_nach_addon.py`** übersetzt eine bestehende `.env` in die
  Add-on-YAML-Konfiguration — die Gegenrichtung von `docker/thermoctl_optionen.py`,
  aus denselben `ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN`-Konstanten abgeleitet statt
  doppelt gepflegt. `--ohne-datenbank` lässt alle `database_*`-Felder weg, für den Fall,
  dass die Datenbank im Add-on bereits eingetragen ist und nicht von einer
  `.env`-SQLite-Zeile aus der Entwicklungsumgebung überschrieben werden soll. Näheres in
  [self-hosting.md](self-hosting.md#6b-umstieg-von-docker-compose-auf-das-home-assistant-add-on).
- **Offen:** Ob und wie das Add-on Zugangsdaten des Home-Assistant-eigenen
  MQTT-Brokers automatisch übernimmt, ist nicht gebaut — `services: ["mqtt:want"]`
  gewährt nur Zugriff auf die Supervisor-API, füllt `options.json` nicht von selbst.

## Störungsmeldungen

Drei Arten lassen sich anlagenweit einzeln abschalten, unter „Einstellungen" —
**Sensorstörung** samt Entwarnung, **Brücke oder Broker weg**, und **Schaltbefehl
gescheitert**. Alle drei sind ab Werk an. Gemeldet wird nur der Übergang, samt
Entwarnung. Home Assistant bleibt entkoppelt: Wer den Webhook stilllegt, verliert den
Problemsensor dort nicht.

Ein **Testknopf** unter „Einstellungen" schickt eine gekennzeichnete Testmeldung über
denselben Weg wie eine echte und zeigt Statuscode, Dauer und im Fehlerfall den Grund
unmittelbar auf der Seite; ohne hinterlegten Webhook wird er nicht angeboten, gegen
wiederholtes Auslösen liegt ein Zeitabstand von zehn Sekunden davor. Daneben steht der
**Zustellzustand** — wann zuletzt versucht, mit welchem Ergebnis; „noch nie versucht"
ist ein eigener Zustand. Das Audit-Protokoll unterscheidet `sent` (Versuch ging los,
unabhängig vom Netzerfolg) von `suppressed` (durch einen abgeschalteten Schalter nie
versucht).

Migration `67e794059830` (sechs neue Spalten auf `setting`) ist vor `v0.6.4` gelandet,
in `v0.7.0` selbst kam keine neue Migration dazu.

## Kiosk, Ladeanzeige, Sprache

- **`kiosk.html`** trägt den AGPL-§13-Quelltextverweis knapp in der Kopfzeile
  (`target="_blank"`) statt einer Fußzeile — die einzige Ausnahme von `base.html`, weil
  das Wandtablett aus Distanz angesehen wird und die Fläche dem Zonenraster gehört.
- **Eine dezente Ladeanzeige** (`#tc-loading-bar`) läuft global auf jeder angemeldeten
  Seite und der Anmeldung/Einrichtung, gesteuert über die htmx-Ereignisse
  `htmx:beforeRequest`/`htmx:afterRequest`; erscheint erst nach 400 ms Verzögerung,
  respektiert `prefers-reduced-motion`. Bewusst nicht am Kiosk-Dashboard, dessen einzige
  htmx-Anfrage der selbsttätige 20-Sekunden-Nachlader ist.
- **Benutzersichtbarer Text schreibt echte Umlaute** (`ä`/`ö`/`ü`/`ß`), nicht mehr
  `ae`/`oe`/`ue`/`ss`. Bezeichner (Funktions-, Variablen-, Klassen-, Feld-,
  Spaltennamen), maschinell gelesene Schlüssel (YAML/JSON, Umgebungsvariablen,
  Migrationskennungen) und Dateinamen bleiben ASCII — das ist die Konvention für alles
  Neue. Der Wirkungswächter (`tests/test_user_visible_effect_texts.py`) prüft jede
  geänderte benutzersichtbare Zeile gegen `approved_physical_vocabulary.json` und fragt
  dafür `git`, nicht das Dateisystem — wichtig, falls je wieder eine Datei aus dem
  Repository entfernt wird, ohne aus der Versionsverfolgung zu verschwinden.

## Homebridge

`docs/homebridge.md` beschreibt die Einbindung über den `mqtt-thing`-Zusatz gegen
dieselben MQTT-Topics wie Home Assistant — keine eigene thermoctl-Integration, reine
Dokumentation mit Wächtertests gegen den Topic-Vertrag.

## Was nur der Projektinhaber entscheiden kann

- **Phase 2 wirklich abschließen.** Ein Auszug der Produktivdatenbank wurde am
  2026-09-04 gegen die drei Abnahmekriterien geprüft
  ([phase-2-abnahme.md](phase-2-abnahme.md)) — **nicht abnahmereif**: Fünf der sechs
  Zonen wurden erst nach dem Scharfschalten angelegt und liefen nie im
  Schattenbetrieb; Kriterium 3 (Altsystemvergleich) entfällt ersatzlos, weil der
  Vergleichsbetrieb übersprungen wurde. Eine Zone braucht mehrere Tage Schattenbetrieb
  ab ihrer Anlage, bevor das Kriterium für sie erfüllbar ist.
- **Die Ablösung des Altsystems** (Host, vier Skripte) ist noch nicht vollzogen; es
  läuft weiter als Rückfallebene.
- **Automatische Übernahme der Home-Assistant-eigenen MQTT-Broker-Zugangsdaten** ins
  Add-on ist nicht gebaut (siehe oben).

Der Sicherheitsstand steht in
[sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md), am
2026-09-04 gegen den aktuellen Code nachgeprüft (siehe deren einleitender Nachtrag):
Von acht Hoch-/Mittel-Befunden sind sechs behoben oder teilweise behoben
(`device.manage` zonenübergreifend, Kiosk-Token als Bearer-Ersatz, Login-Blockade des
Regelzyklus, Meross-Bestätigung, Webhook-Weiterleitung, Passwortwechsel/Sitzungswiderruf;
das Einrichtungs-Token zusätzlich befristet). **Weiterhin offen:** der MQTT-Befehlspfad
(dokumentiert, im Code nicht erzwingbar — Frage der Broker-Konfiguration), unbegrenzte/
unmaskierte Meross-Cloud-Antworten, das unbegrenzt wachsende Schaltprotokoll, die
HTTP-Netzwerkvorgabe (`0.0.0.0` ohne TLS-Erzwingung), und die CSRF-Ausnahme für
Login/Logout.
