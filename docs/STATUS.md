# Stand

Letzte Aktualisierung: 2026-09-06, Freigabe `v0.7.0`.

## Betrieb unter Docker Swarm und Kubernetes dokumentiert

Zwei neue Anleitungen für den Aktiv-Bereitschafts-Verbund (Abschnitt 6d unten):
[`docs/docker-swarm.md`](docker-swarm.md) und [`docs/kubernetes.md`](kubernetes.md), je
mit lauffähigen Beispieldateien (`docker/swarm.compose.beispiel.yml`,
`docker/swarm.migrate.compose.beispiel.yml`, `k8s/*.beispiel.yaml`). Zwei Dateien statt
einer gemeinsamen, weil die Beispielmanifeste beider Systeme sonst dieselbe Anleitung mit
zwei unvereinbaren YAML-Dialekten überladen hätten.

**Offener Punkt, kein Dokumentationsfehler:** `docker/entrypoint.sh` führt
`alembic upgrade head` unbedingt aus, ohne Sperre gegen eine zweite, gleichzeitig
migrierende Nachbildung. Unter einem Orchestrierer starten zwei Nachbildungen leicht
gleichzeitig (Swarm: `docker stack deploy` ignoriert `depends_on`; Kubernetes: ein
Rolling Update lässt alte und neue Nachbildung kurz nebeneinander laufen). Beide
Anleitungen umschiffen das über einen vorgeschalteten, einmaligen Migrations-Job
(Swarm: `mode: replicated-job`; Kubernetes: `Job`, mit `kubectl wait` vor dem
`StatefulSet`) — das behebt nicht, dass der Entrypoint selbst ungesichert ist. Eine
Absicherung in `migrations/env.py` (z. B. eine Datenbank-Sperre) ist noch offen.

Letzte Aktualisierung: 2026-09-06.

## Zeitplan-Vorschau: nächste 24 Stunden je Zone

Die Zeitplanseite (`/zones/{id}/schedule`) zeigt jetzt oberhalb des Wochenrasters eine
Leiste mit der Vorschau der nächsten 24 Stunden, sichtbar bereits mit `zone.read`
(keine Änderungsrechte nötig). Die Berechnung sitzt in der Domäne
(`thermoctl.domain.schedule.schedule_forecast`), nicht in der Ansicht: sie reicht
über `resolved_setpoint`s eigene Rangfolge (Betriebsart Aus schlägt alles, dann eine
laufende Übersteuerung bis zu ihrem Ende, dann der Zeitplan, zuletzt Frostschutz) und
kann daher nie etwas zeigen, was zur Laufzeit nicht tatsächlich einträte. Sie rechnet
in echten UTC-Instanzen statt in Ortszeit-Arithmetik und bleibt deshalb auch über
Mitternacht, einen Wochentagswechsel und beide Sommerzeit-Umstellungen (23- bzw.
25-Stunden-Tag) exakt — mit Tests, die diese Grenzfälle einzeln mit von Hand
abgeleiteten Uhrzeiten belegen (`tests/test_domain_schedule.py`). REST und MCP bieten
die Vorschau noch nicht an; das ist eine bewusste Auslassung dieser Aufgabe, keine
technische Grenze — die Domänenfunktion ist adapterunabhängig nutzbar.

Letzte Aktualisierung: 2026-09-06.

## Liveaktualisierung der Startseite

Ist-Wert, Sollwert samt Begründung, Sensorzustand und letzte Entscheidung je Zone
zeigten sich bisher nur beim Neuladen. `start.html` fragt die Startseite jetzt im
Stil des Kiosks periodisch selbst ab (`hx-get`/`hx-select`/`hx-swap="outerHTML"` auf
`#tc-live`) — kein neues Werkzeug, HTMX war schon da.

Das Intervall ist keine feste Zahl, sondern kommt aus `setting.shadow_interval_seconds`
(`poll_interval_seconds` in `start_views.py`) — häufiger abzufragen als sich ein Wert
ändern kann, wäre verschwendete Last, gerade hinter dem Ingress-Proxy. `[!document.hidden]`
im `hx-trigger` pausiert den Abruf, solange der Tab im Hintergrund liegt.

Zwei Dinge mussten dafür ausdrücklich abgesichert werden, nicht nur der Abruf selbst:

- Ein aufgeklappter Übersteuern-Bereich und eine schon begonnene Eingabe dürfen den
  Austausch nicht verlieren. Gelöst über `hx-preserve` auf dem Formular und dem
  Auf/Zu-Knopf (beide mit stabiler `id`) — htmx lässt diese Elemente beim Swap
  unangetastet stehen, statt sie durch eine frische, leere Fassung zu ersetzen.
- Der globale Ladebalken (`loading_indicator.js`) darf für diesen Abruf nicht
  aufblitzen — er würde jede Minute von selbst erscheinen, ohne dass irgendjemand auf
  ihn wartet. Das auslösende Element trägt `data-tc-quiet-poll`; das Skript prüft
  genau dieses Element (`ereignis.detail.elt`), nicht dessen Vorfahren, damit ein POST
  aus einem Formular *innerhalb* des Bereichs (Übersteuern, Sollwert stellen) den
  Balken weiterhin zeigt.

Nachgewiesen in `browser_tests/test_start_page_live.py` (vier Tests: abgeleitetes
Intervall, ein geänderter Wert aktualisiert sich ohne Zutun, ein aufgeklappter Bereich
samt begonnener Eingabe übersteht eine Aktualisierung, der Ladebalken bleibt dabei
stumm — auch unter einer künstlich verzögerten Antwort).

Letzte Aktualisierung: 2026-09-06.

## Aktiv-Bereitschafts-Verbund: zwei Instanzen, eine Datenbank, ein Broker

Zwei `thermoctl`-Instanzen können jetzt dieselbe Datenbank und denselben MQTT-Broker
teilen — eine regelt, die andere steht bereit und übernimmt, wenn die erste ausfällt.
Neu: `thermoctl/services/cluster.py` (die einzige Stelle, die die Tabelle
`cluster_claim` liest oder schreibt), Migration `bb4a0ff63b2d` (legt die Tabelle an,
mit einer unbeanspruchten Seed-Zeile, und `setting.cluster_takeover_cycles`, Vorgabe 5).

**Der Anspruch wechselt durch eine einzige atomare `UPDATE`-Anweisung, nicht durch
Lesen-Prüfen-Schreiben.** Zwei Instanzen, die gleichzeitig um einen abgelaufenen
Anspruch konkurrieren, stellen beide dieselbe Anweisung — die Zeilensperre der
Datenbank serialisiert sie, und nur wer als Erster drankommt, sieht seine
`WHERE`-Bedingung noch zutreffen; der Verlierer betrifft null Zeilen und misslingt
dadurch, nicht durch eine zusätzliche Prüfung. `tests/test_cluster.py::
test_two_processes_racing_for_a_stale_claim_only_one_wins` lässt zwei Threads mit
zwei echten, unabhängigen Datenbankverbindungen über eine `threading.Barrier`
tatsächlich gleichzeitig antreten — nicht zwei Aufrufe nacheinander, die auch eine
falsche Umsetzung bestehen würde. Läuft gegen SQLite **und** MariaDB grün; unter
MariaDB ist es der Ernstfall (`REPEATABLE READ` verhält sich unter echter
Nebenläufigkeit anders als SQLites Dateisperre).

**Die Uhr, die zählt, ist die der Datenbank, nie die eines Hosts.** Jeder Vergleich
und jede Erneuerung von `expires_at` geht über `sqlalchemy.func.now()` — ausgewertet
von der Datenbank selbst. Zwei Maschinen stimmen ihre Uhren nie so genau ab, dass sie
sicher entscheiden könnten, wer einen Heizkörper schalten darf; die Datenbank, die
beide ohnehin teilen, ist die eine Uhr, auf die sich beide ohne Weiteres einigen.

**Ein fehlender Anspruch-Datensatz heisst „Verbund nie eingerichtet"** und lässt jeden
Aufrufer als Alleinbetreiber gelten (`cluster.is_leader`/`try_become_leader`, beide
mit derselben Begründung dokumentiert). Genau dieser Fall gilt für die gesamte
Testsuite: sie baut ihr Schema über `Base.metadata.create_all()`, nicht über die
Migration, die die Seed-Zeile anlegt — ohne diesen bewussten Fehlschlag-offen-Fall
hätte jeder bestehende Schalttest im Projekt einen Anspruch-Datensatz gebraucht, von
dem er nie etwas wusste.

**Drei Riegel jetzt, nicht mehr zwei**, alle an derselben Stelle
(`integrations/actuators.py::switching_allowed`): `setting.control_armed`, der beim
Prozessstart eingefrorene Bolzen (`MqttClient`/`meross_switching_allowed`), und jetzt
`cluster.is_leader` — geprüft bei jedem einzelnen Schaltversuch, nicht nur einmal am
Zyklusanfang, damit eine Instanz, die ihren Anspruch mitten in einem langen Zyklus
verliert, den nächsten Aktor trotzdem nicht mehr anfasst. Erreicht damit sowohl den
Zigbee2MQTT- als auch den Meross-Pfad, weil beide durch dieselbe Funktion gehen.

**Der Regelzyklus selbst läuft auf der Bereitschaft gar nicht erst**
(`app.py::_shadow_loop`): jeder Durchlauf versucht zuerst atomar, den Anspruch zu
werden oder zu erneuern; nur wer gerade führt, macht mit dem restlichen Durchlauf
weiter (Sensorzustand, Schattenentscheidungen, Veröffentlichung, Meross-Abgleich,
Aufbewahrung). Eine Bereitschaft schreibt dadurch weder Schattenentscheidungen noch
Zustände an Home Assistant — zwei Instanzen, die dieselben zurückbehaltenen
Zustands-Topics beschreiben, wäre genau die Unschönheit, die dieses Verhalten
vermeidet (die MQTT-Client-Kennung muss je Instanz ohnehin verschieden sein, siehe
`docs/self-hosting.md`). Ein eingehender MQTT-Befehl (z. B. ein Moduswechsel aus Home
Assistant) darf auf beiden Instanzen angewendet werden — eine Konfigurationsschreibung,
kein Schaltvorgang, und ohnehin deckungsgleich, egal welche Instanz sie ausführt —,
aber nur die führende bestätigt ihn zurück an Home Assistant.

**Ein geordnetes Herunterfahren gibt den Anspruch sofort frei**
(`cluster.release`, aufgerufen aus `_lifespan`s `finally`), statt die
Bereitschaft die vollen fünf Zyklen warten zu lassen — der häufigste Fall ist ein
Neustart der aktiven Instanz, und die weiss beim Beenden bereits, dass sie geht.

**Sichtbar gemacht:** Die Startseite zeigt einen eigenen "Bereitschaft"-Chip, sobald
diese Instanz nicht führt — unabhängig vom sonstigen scharf/Trockenlauf-Zustand.

**Bewusst nicht gebaut:** Der Bereitschafts-MQTT-Client bleibt verbunden und nimmt
weiter Messwerte auf (beide Instanzen müssen ihre Datenbank aktuell halten, damit,
wer übernimmt, sofort mit frischen Daten arbeitet) — nur die Rückbestätigung eines
Befehls und jede Veröffentlichung sind an die Führungsrolle gebunden. Eine
gleichzeitige Bearbeitung derselben eingehenden Bruecken-/Störungsmeldung durch beide
Instanzen (mit je einem eigenen Webhook-Versand) ist ein bekannter, dokumentierter
Grenzfall und nicht gelöst — selten genug (die Brücke fällt nicht oft aus), dass er
bewusst zurückgestellt wurde, statt die Befehlsverarbeitung selbst zu verkomplizieren.

Details, inklusive Einrichtung je Instanz, in `docs/self-hosting.md`, Abschnitt 6d.

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
