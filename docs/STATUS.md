# Stand

Letzte Aktualisierung: 2026-09-04

## Störungsmeldungen: drei Arten, einzeln abschaltbar, mit Testknopf

Bisher gingen Meldungen ungefragt hinaus, und ob der Webhook sie annahm, stand nur im
Log. Drei Arten lassen sich jetzt anlagenweit einzeln abschalten — **Sensorstörung**
samt Entwarnung, **Brücke oder Broker weg**, und neu **Schaltbefehl gescheitert**.
Alle drei sind ab Werk an: Wer heute Meldungen bekommt, bekommt sie weiter.

Die neue Art schliesst eine Lücke, die dieses Projekt schon einmal bezahlt hat: Ein
gescheiterter Befehl landete ausschliesslich im Schaltprotokoll. An einer kalten Zone
bedeutet das im Januar ein eingefrorenes Rohr. Gemeldet wird nur der **Übergang**, samt
Entwarnung — die Unterscheidung dafür wird getrennt vom Log-Schlüssel geführt, damit
sie auch greift, wenn sich Nutzlast oder Riegel gleichzeitig ändern.

**Das Tor ist eine reine Funktion in der Domäne**, und die Meldungsart ist ein
Pflichtfeld von `FaultNotice` — optional wäre eine Meldung ohne Art still daran
vorbeigerutscht. Solange es keine `setting`-Zeile gibt, fällt es offen: Vor Abschluss
der Einrichtung wird nichts unterdrückt, was niemand konfigurieren konnte.
**Home Assistant bleibt entkoppelt**: Wer den Webhook stilllegt, verliert den
Problemsensor dort nicht.

**Der Testknopf** unter „Einstellungen" schickt eine gekennzeichnete Testmeldung über
denselben Weg wie eine echte und zeigt Statuscode, Dauer und im Fehlerfall den Grund
unmittelbar auf der Seite. Ohne hinterlegten Webhook wird er nicht angeboten; gegen
wiederholtes Auslösen liegt ein Zeitabstand von zehn Sekunden davor. Daneben steht der
**Zustellzustand** — wann zuletzt versucht, mit welchem Ergebnis, wobei „noch nie
versucht" ein eigener Zustand ist und nicht wie ein Fehlschlag aussieht.

Die Testmeldung trägt die Art `test` und geht bewusst **nicht** durch das Tor: Wer auf
„Testen" drückt, will diese eine Meldung hinausschicken. `notice_enabled` wirft für
diese Art weiterhin — landet eine Testmeldung je am Tor, ist das ein Fehler und soll
auffallen.

**Beim Gegenlesen gefunden und behoben:** Das Auditprotokoll schrieb
`notification.sent` auch dann, wenn das Tor unterdrückt hatte. Wer später sucht, warum
eine Störung niemanden erreichte, hätte dort „gesendet" gefunden. Es unterscheidet
jetzt `sent` von `suppressed`. Scheitert die Zustellung am **Netz**, bleibt es bei
`sent` — der Versuch ging los, und ob er ankam, beantwortet `notify_last_ok`.

Sechs neue Spalten auf `setting`, Migration `67e794059830`, vorwärts und rückwärts
gegen beide Datenbanken geprüft. Der Zustellzustand wird ausserhalb jeder
Schreibtransaktion notiert; ein Test prüft die Eigenschaft selbst, nach dem Vorbild des
Meross-Tests. Eine unterdrückte Meldung fasst ihn nicht an — es gab keinen Versuch.

## Kiosk: der AGPL-§13-Hinweis, wie ein Wandtablett ihn verträgt

`kiosk.html` erbt weder `base.html` noch `base_plain.html` und blieb beim
Quelltextverweis aussen vor. Statt einer Fusszeile — ein Wandtablett wird aus Distanz
angesehen, nicht gelesen, und die Fläche gehört dem Zonenraster — sitzt ein knapper
Verweis „Quelltext (AGPL-3.0)" in der Kopfzeile neben der Uhr. `target="_blank"` hält
die Anzeige geladen; ein Antippen führte sonst vom Kiosk weg, ohne einfachen Weg
zurück ausser einem Neustart des Browsers. Der Verweis öffnet nichts, was das Kiosk
nicht ohnehin kann — seine enge Bedienfläche ist eine Sicherheitseigenschaft und
bleibt unberührt.

## `env_nach_addon.py`: Datenbank in der `.env` schlaegt eine bereits im Add-on eingetragene nicht mehr blind

Der Projektinhaber betreibt seine Anlage mit MariaDB, deren Zugangsdaten schon im
Add-on stehen; seine `.env` enthaelt daneben noch eine
`THERMOCTL_DATABASE_URL=sqlite:///...`-Zeile aus der Entwicklungsumgebung. Das
Werkzeug haette diese SQLite-Zeile unveraendert uebernommen und damit die im Add-on
eingetragene MariaDB-Verbindung beim Einfuegen ueberschrieben. Zwei Wege dagegen:

- Neuer Schalter `--ohne-datenbank` laesst alle `database_*`-Felder in der Ausgabe
  komplett weg -- der uebliche Fall, wenn die Datenbank im Add-on bereits eingetragen
  ist und so bleiben soll. SQLite bleibt dabei unveraendert die Vorgabe des Add-ons
  selbst; der Schalter aendert nur, was `env_nach_addon.py` ausgibt.
- Ohne den Schalter: Liegt neben einer aktiven SQLite-`THERMOCTL_DATABASE_URL` eine
  **auskommentierte** zweite Zeile mit `mysql+pymysql://...` (die uebliche Art, sich
  beim Wechseln zwischen SQLite und MariaDB die jeweils andere Verbindung
  aufzubewahren), gewinnt diese gegen SQLite. Reicht sie nicht fuer eine vollstaendige
  Verbindung, faellt das Werkzeug auf SQLite zurueck und sagt auf der Fehlerausgabe
  warum -- kein stilles Zurueckfallen.

Gibt das Werkzeug SQLite aus, nennt der zugehoerige Hinweis auf der Fehlerausgabe
jetzt auch `--ohne-datenbank`, falls die Zieldatenbank tatsaechlich eine andere ist.
Rundlauf-Test um beide Faelle ergaenzt (`tests/test_tools_env_nach_addon.py`), fuer
`--ohne-datenbank` als eigener, ausdruecklicher Fall statt eines Rundlauf-Vergleichs
-- die Datenbank fehlt dort naturgemaess.

## Werkzeug fuer den Umstieg: `.env` -> Add-on-Konfiguration

`tools/env_nach_addon.py` liest eine bestehende `.env` (`docker compose`-Betrieb) und
erzeugt daraus die YAML-Konfiguration fuer das Home-Assistant-Add-on -- die
Gegenrichtung von `docker/thermoctl_optionen.py`. Die Abbildung ist bewusst nicht
zweimal aufgeschrieben: Das Skript importiert `ABGEBILDETE_FELDER` und
`BEWUSST_AUSGELASSEN` aus `thermoctl_optionen.py` und kehrt sie um, statt sie
abzuschreiben. Einzige Ausnahme ist `THERMOCTL_DATABASE_URL`, die dort aus fuenf
Optionen *zusammengesetzt* wird -- die Ruecksrichtung *zerlegt* sie wieder, in einer
eigenen Funktion (`datenbank_optionen_aus_url`), erkennt genau die beiden Formen, die
die Vorwaertsrichtung erzeugen kann (SQLite unter `/data/thermoctl.db`,
`mysql+pymysql://...` fuer MariaDB), und meldet jede andere URL als Fehler statt sie
still zu verwerfen.

Werte, die das Add-on nicht braucht (Bind-Adresse, Port, `secure_cookies`,
Pfadpraefix), werden uebersprungen; jede uebrige, nicht dediziert abgebildete
`THERMOCTL_*`-Variable landet im freien `env`-Feld des Add-ons. Ein
Rundlauf-Test (`tests/test_tools_env_nach_addon.py`) belegt fuer jede dokumentierte
Einstellung: `.env` -> Add-on-Optionen -> YAML -> zurueckgelesen -> durch
`thermoctl_optionen.translate()` -> dieselben `THERMOCTL_*`-Variablen wie am Anfang.
Naeheres in [`docs/self-hosting.md`](self-hosting.md#6b-umstieg-von-docker-compose-auf-das-home-assistant-add-on).

## Add-on-Optionsschema jetzt flach, plus freies `env`-Feld

Der Projektinhaber kam als Home-Assistant-Add-on-Betreiber dreimal nicht durch die
Konfiguration: `Missing option 'notify' in root`, obwohl die Gruppe im Schema mit
leerem Standardwert vorbelegt war. Ursache: der Supervisor prüft die *abgeschickte*
Konfiguration, und die Add-on-Oberfläche lässt eine Gruppe, in der niemand etwas
ausgefüllt hat, beim Speichern einfach weg. Verschachtelte Options-Gruppen sind damit
grundsätzlich eine Falle, keine Add-on-spezifische Eigenheit.

`docker/thermoctl_optionen.py` liest deshalb jetzt ein **flaches** Optionsschema:
`secret_key`, `log_level`, `log_format`, `database_type`/`database_host`/
`database_port`/`database_user`/`database_password`/`database_name`, `mqtt_enabled`/
`mqtt_host`/`mqtt_port`/`mqtt_tls`/`mqtt_ca_cert`/`mqtt_username`/`mqtt_password`/
`mqtt_client_id`/`mqtt_base_topic`/`mqtt_prefix`, `meross_email`/`meross_password`,
`notify_webhook`/`notify_webhook_token`. `meross_api_base` hat kein eigenes Feld mehr
(zu selten gebraucht) und steht jetzt in `BEWUSST_AUSGELASSEN`.

Dazu ein neues, freies Feld **`env`**: der Inhalt einer `.env`-Datei, eine Zuweisung je
Zeile (`NAME=WERT`), Kommentare und Leerzeilen übersprungen, `export ` am Zeilenanfang
geduldet, umschließende Anführungszeichen entfernt, ein ungültiger Name verworfen.
Damit erreicht ein Betreiber jede `THERMOCTL_*`-Variable ohne eigenes Add-on-Feld, ohne
auf eine neue Add-on-Fassung zu warten. Reihenfolge: dedizierte Felder, dann `env`,
dann — gewinnt gegen beides — eine echte, vom Betreiber gesetzte Umgebungsvariable.
Weder ein Wert noch eine verworfene Zeile geraten ins Log.

`ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN` und der Wächtertest
(`test_every_settings_field_is_translated_or_deliberately_excluded`) sind auf die
flache Form nachgezogen; `env` ist bewusst kein `Settings`-Feld und taucht in keinem
der beiden Dicts auf, damit der Wächter dadurch nicht aufgeweicht wird. Das
ausgelieferte Add-on-Abbild (v0.6.1) kennt `thermoctl_optionen.py` noch gar nicht, es
muss ohnehin neu gebaut werden — Rücksicht auf die alte, verschachtelte Form war daher
nicht nötig.

## Ingress-Präfix: thermoctl läuft jetzt unter einem beliebigen Pfadpräfix

thermoctl ist als Home-Assistant-Add-on gedacht, das dort per Ingress unter einem
zufälligen Pfad wie `/api/hassio_ingress/<token>/` eingebunden wird. Vorher kannte die
Anwendung keinen Präfix: jeder absolute Verweis, jede Weiterleitung, jedes Formularziel
und jede HTMX-Nachladung zeigte auf `/…` und landete damit außerhalb des Ingress-Pfads.

Der Präfix kommt **ausschließlich aus Konfiguration** (`THERMOCTL_ROOT_PATH`,
`Settings.root_path`, siehe `.env.example`), nicht aus der `X-Ingress-Path`-Kopfzeile —
die wäre nicht vertrauenswürdige Eingabe von außen. Der Wert wird als FastAPIs eigenes
ASGI-`root_path` gesetzt (`thermoctl/app.py::create_app`); von dort liest ihn alles
andere zurück: `thermoctl/web/urls.py` (`prefixed()`, `cookie_path()`) für
Weiterleitungen und Cookie-Gültigkeit, und ein Jinja-Kontextprozessor
(`thermoctl/web/__init__.py`) stellt ihn allen Vorlagen als `url_prefix` bereit — jeder
lokale `href`/`action`/`src`/`hx-*`-Wert in `thermoctl/web/templates/` ist jetzt
`{{ url_prefix }}/…` statt hartem `/…`. `thermoctl/web/static/passkey.js` liest denselben
Wert zur Laufzeit aus `<body data-url-prefix="…">`. Cookies tragen jetzt ein explizites
`path` (Präfix statt `/`) — sonst würden zwei Add-ons hinter demselben Host sich
gegenseitig die Sitzungscookies lesen.

**Eigener Befund unterwegs:** Starlettes `Mount` (`app.mount("/static", StaticFiles(...))`)
rechnet den `root_path` des Kind-Scopes als `root_path + gefundenes_präfix` — richtig nur,
wenn der Proxy den vollen externen Pfad weiterreicht. Home-Assistant-Ingress entfernt
seinen Präfix aber, bevor die Anfrage den Container erreicht (der ASGI-Definition von
`root_path` entsprechend); jede statische Datei lieferte dann 404, obwohl der gerenderte
Link im HTML korrekt aussah. Behoben durch einen eigenen, kleinen ASGI-Wrapper
(`thermoctl/app.py::_serve_static`), der das Präfix selbst abschneidet statt sich auf
Starlettes Akkumulation zu verlassen — nur beim `/static`-Mount betroffen, jede normale
Route bleibt unberührt.

Browsertests unter `browser_tests/test_ingress_prefix.py` laufen gegen eine Instanz
hinter einem lokalen Gegenstück zum Ingress-Proxy (`browser_tests/_ingress_proxy.py`,
entfernt den Präfix, bevor die Anfrage den echten Server erreicht) — Anmeldung,
Abmeldung, eine geboostete Navigation, das Stylesheet, das Kiosk und eine Zeigergeste im
Zeitplan-Editor, alles unter Präfix und mit derselben Konsolen-Fehler-Prüfung wie die
übrige Suite. `tests/test_ingress_prefix.py` belegt auf HTTP-Ebene Weiterleitungsziele,
Cookie-`Path`, gerenderte Verweise und die statische Datei selbst — je einmal mit und
ohne Präfix, um zu zeigen, dass sich ohne Präfix nichts ändert.

**Nicht angefasst:** die Anmeldung selbst (Grundsatz 4 gilt unverändert — Home Assistant
authentifiziert nicht für thermoctl mit), `docker/`, `.github/workflows/docker.yml` und
der Entrypoint (parallele Aufgabe). `/healthz` bleibt bewusst unpräfigiert — ein
Docker-Healthcheck erreicht den Container direkt, nicht über Ingress.

**Offen:** Der verpflichtende MariaDB-Lauf (`THERMOCTL_TEST_DATABASE_URL`) konnte nicht
durchgeführt werden — der lokale MariaDB-Server meldet `errno 28 "No space left on
device"` beim Anlegen jeder neuen Testdatenbank, unabhängig von dieser Änderung (rund
100 verwaiste `thermoctl_*`/`tc_*`-Testdatenbanken früherer Sitzungen liegen auf dem
Server). Die Änderungen selbst sind datenbankagnostisch — `root_path` betrifft
ausschließlich Request-Verarbeitung und Linkerzeugung, keine Schemaänderung, keine
datenbankspezifische Abfrage — ein Nachlauf gegen MariaDB ist trotzdem nachzuholen.

## Add-on-Optionsübersetzung: MQTT-Client-ID, CA-Zertifikat und Log-Format nachgezogen

Im echten Betrieb als Add-on fehlte die Möglichkeit, die MQTT-Client-ID zu setzen — an
einem EMQX-Broker mit Rechteverwaltung ohne die kein Verbindungsaufbau. `docker/thermoctl_optionen.py`
übersetzt jetzt zusätzlich `mqtt.client_id` → `THERMOCTL_MQTT_CLIENT_ID`,
`mqtt.ca_cert` → `THERMOCTL_MQTT_CA_CERT` und `log_format` → `THERMOCTL_LOG_FORMAT` (die
zweite Lücke, beim Durchsehen der gesamten Abbildung gegen `thermoctl/config.py`
gefunden). Neu: `ABGEBILDETE_FELDER` und `BEWUSST_AUSGELASSEN` als Konstanten im Skript,
gegen `Settings.model_fields` geprüft von
`test_every_settings_field_is_translated_or_deliberately_excluded` — eine neue,
nirgends eingeordnete Einstellung lässt diesen Test künftig fehlschlagen, statt
stillschweigend zu fehlen. `.env.example` war bereits vollständig (eigener Wächter
`test_every_setting_is_listed_in_the_example_file`), keine Änderung dort nötig.

## Home-Assistant-Add-on: Mehrarchitektur-Abbild und Optionsübersetzung

`.github/workflows/docker.yml` baut jetzt für `linux/amd64` **und** `linux/arm64`
(`docker/setup-qemu-action` neben dem vorhandenen buildx) — die vom Projektinhaber
entschiedenen Zielarchitekturen für das Add-on, `armv7` ausdrücklich nicht. Die
`latest`-Markenlogik (ausschliesslich aus einem `v*`-Tag) ist unverändert.

`docker/entrypoint.sh` versteht jetzt `/data/options.json`, wie der Home-Assistant-
Supervisor sie ablegt: liegt sie vor, übersetzt `docker/thermoctl_optionen.py` (reine
Standardbibliothek, kein `jq` im Abbild) ihre Felder in die `THERMOCTL_*`-Umgebungs-
variablen aus `.env.example`/`thermoctl/config.py`. Eine vom Betreiber bereits gesetzte
Umgebungsvariable gewinnt immer gegen die Optionsdatei. Ohne die Datei — der gewöhnliche
`docker compose`-Betrieb — ändert sich nichts. Getestet in `tests/test_docker_addon_options.py`
(reine Übersetzungslogik, per Unterprozess) und `tests/test_docker_entrypoint.py`
(das Shell-Skript selbst, mit Fake-`alembic`/-`thermoctl` auf dem PATH), dazu ein
manueller Rauchtest im echten Abbild. **Offen:** ob und wie das Add-on Zugangsdaten des
Home-Assistant-eigenen MQTT-Brokers automatisch übernimmt (`services: ["mqtt:want"]`
gewährt nur den Zugriff auf die Supervisor-API `/services/mqtt`, füllt aber nicht von
selbst `options.json` — eine Anbindung dafür ist hier nicht gebaut). Der Entwurf des
Add-on-Repositorys selbst (`config.yaml`, `DOCS.md`, …) liegt ausserhalb dieses
Repositorys, siehe Auftragsbericht.

## v0.6.1 — die erste Fassung, die veröffentlicht wird

Der Projektinhaber veröffentlicht das Repository mit dieser Fassung. Sie bringt deshalb
vor allem, was dafür fehlte: eine Lizenz, und die Beseitigung von Spuren aus der eigenen
Entwicklung, die für einen fremden Betreiber ohne Vorgeschichte sinnlos bis irreführend
gewesen wären. Dazu ein Begründungstext-Fix in der Regelkette und die Erkenntnis aus einer
ersten Auswertung der echten Anlage, dass Teilprojekt 2 formal noch nicht als abgenommen
gilt. **Keine Schemaänderung** — diese Fassung fügt keine Migration hinzu.

### thermoctl steht unter der AGPL-3.0

`thermoctl/web/templates/base.html` und `base_plain.html` tragen jetzt eine Fußzeile mit
Lizenzangabe (AGPL-3.0) und der Repository-Adresse
(`https://github.com/MagicalWig34653/thermoctl`) — sie erscheint auf jeder Seite, die
über diese beiden Grundvorlagen läuft, auch der Anmeldeseite. Der Verweis nennt die vom
Projektinhaber freigegebene, korrekte Adresse; das Repository wird mit dieser Fassung
öffentlich. **Offen:** Das eigenständige Kiosk-Dashboard (`thermoctl/web/templates/kiosk.html`)
erbt keine der beiden Grundvorlagen — bewusst schmale Bedienfläche fürs Wandtablet. Ob und
wie es den §13-Hinweis bekommen soll, ohne die Fläche zu überladen, ist nicht entschieden;
hier nicht angefasst. `LICENSE` ist der unveränderte Wortlaut von gnu.org; `pyproject.toml`
trägt die Lizenzangabe nach PEP 639, und das gebaute Paket weist `License-Expression:
AGPL-3.0-only` nach.

### Bauprozess-Dokumentation und Altsystem-Bestandsaufnahme aus dem Repository entfernt

Verlauf, getroffene Entscheidungen, Spezifikationen, Pläne und das interne
Umbenennungswerkzeug beschreiben ausschliesslich den eigenen Bauprozess; die
Altsystem-Bestandsaufnahme beschreibt ein fremdes, reales System mit vollständigem Schema,
MQTT-Topic-Vertrag und einer privaten IP-Adresse. Beides eignet sich nicht für ein
öffentliches Repository. Die Dateien bleiben lokal liegen und stehen in `.gitignore`; nur
die Versionsverfolgung gibt sie auf, ihre Inhalte werden anschliessend aus der gesamten
Historie getilgt. Rund fünfzig Verweise darauf im übrigen Repository sind aufgelöst — wo
ein Kommentar im Quelltext auf `offene-entscheidungen.md` zeigte, weil dort die Begründung
stand, steht die Begründung jetzt an Ort und Stelle. Der Quelltext des
Altsystem-Vergleichs selbst (`legacy_system.py`, `deviation.py`) ist unangetastet.

Außerdem: Der reale Rechnername `vm130-nginx` des Altsystems ist an allen fünf
Fundstellen in `docs/roadmap.md`, `docs/inbetriebnahme-schattenbetrieb.md` und
`docs/veroeffentlichung-durchsicht.md` durch eine neutrale Umschreibung („der Host des
Altsystems") ersetzt.

### Falsche Begründung bei `unveraendert` in `decide()` korrigiert

Die beiden Zweige mit Ergebniscode `unveraendert` in `thermoctl/domain/control_loop.py`
protokollierten unabhängig vom tatsächlichen Abstand denselben Satz „... innerhalb der
Hysterese um Soll ... ± hK ... — Zustand bleibt.". Erreicht wurden sie aber nicht nur, wenn
der Messwert wirklich im Band lag, sondern immer dann, wenn nur die *gegenüberliegende*
Bandkante nicht überschritten war — an der tatsächlichen Kante konnte der Messwert beliebig
weit entfernt sein. Gemessen an 18.527 echten `unveraendert`-Entscheidungen lag **keine
einzige** tatsächlich im Band (≤ 0,5K); mittlerer Abstand 5,90K, grösster 11,40K
(Extremfall: Ist 27.40 °C, Soll 16.0 °C ± 0.10K als „innerhalb" protokolliert). Die
Entscheidung selbst war in jedem Fall richtig (korrekte Hysterese, nur an den Kanten
umschalten) — falsch war ausschliesslich der protokollierte Text.

Jeder der beiden Zweige ist jetzt in zwei Fälle aufgeteilt: echt im Band vs. jenseits der
gegenüberliegenden Kante bei bereits laufendem bzw. bereits ausgeschaltetem Zustand. Kein
`heating`-Ergebnis und kein `reason_code` hat sich geändert — belegt durch die unveränderte
2.376-Kombinationen-Tabelle in `tests/test_control_loop_state_table.py` sowie drei neue
Tests in `tests/test_control_loop.py`, die den gefundenen Fehlerfall nachbilden (weit über
Soll bei Aus, weit unter Soll bei Heizen) und den echten Im-Band-Fall als Gegenprobe. Die
18.527 alten, falschen Begründungssätze in der Produktivdatenbank bleiben unverändert
stehen — sie sind Protokoll dessen, was war, siehe `CHANGELOG.md`.

`thermoctl/domain/pi_control.py` hat einen eigenen Begründungsweg und trägt diesen Satz
nicht — geprüft, keine Änderung nötig. Die übrigen Begründungstexte in `decide()`
(Fensterzustand, Mindestschaltdauer, Sensorausfall, Ventilschutz, reguläres Heizen/Aus an
den Kanten) wurden durchgesehen: Jeder benennt nur, was im jeweils erreichten Zweig
zwingend gilt — kein weiterer Fund derselben Fehlerklasse.

### Oberfläche von Umstiegs-Jargon bereinigt

Eine Durchsicht vor der Veröffentlichung fand vier Stellen, die den Entwicklungsstand oder
den Umstieg vom Altsystem des Projektinhabers durchscheinen liessen — für einen fremden
Betreiber ohne Altsystem sinnlos bis verwirrend. Auf `Betrieb` (`control.html`) verweist der
Trockenlauf-Absatz nicht mehr auf den Vergleich gegen das Altsystem, sondern allgemeingültig
darauf, dass sich Entscheidungen beobachten lassen, bevor sie etwas schalten; die
Checkliste vor dem Scharfschalten nennt den Zustand jetzt durchgehend „Trockenlauf" statt an
einer Stelle „Schattenbetrieb", und der Punkt zum Altsystem als Rückfallebene ist entfallen.
Der nie erreichbare Schnittstellen-Zustand `not_built` (Marke „Noch nicht gebaut") ist aus
`thermoctl/domain/interfaces.py`, `interfaces.html` und `tests/test_interfaces.py` entfernt —
`overview()` gab ihn für keine der sechs Gegenstellen je zurück, geprüft vor dem Entfernen.
`docs/scharfschalten.md` bleibt unverändert: Sie ist ausdrücklich für den Projektinhaber und
beschreibt genau den Umstieg, den sie im Titel trägt.

**Diese Datei sagt, was jetzt gilt — sonst nichts.** Wie es dazu kam, welche Fehler wie
gefunden wurden und warum etwas so entschieden ist, wird hier nicht mitgeführt. Der
Grund: Diese Datei war einmal auf über tausend Zeilen gewachsen und enthielt gleichzeitig
aktuelle und längst überholte Angaben — „nichts ist scharf", „1024 Tests, 98,55 %",
„`control_armed` wird nirgends gesetzt", „es gibt keine Geräteerkennung für Meross". Alle
vier stimmten einmal und standen noch da; ein Freigabe-Review konnte sie namentlich
widerlegen.

### Teilprojekt 2 — Auswertung der echten Betriebsdaten: nicht abnahmereif

Ein Auszug der Produktivdatenbank wurde gegen die drei Abnahmekriterien aus Abschnitt 4
von `docs/inbetriebnahme-schattenbetrieb.md` geprüft; Bericht in
[`docs/phase-2-abnahme.md`](phase-2-abnahme.md). Zentraler Befund: Fünf der sechs Zonen
wurden erst **nach** dem Scharfschalten (`control_armed`, 2026-09-01 19:20 Uhr) angelegt
und liefen deshalb nie im Schattenbetrieb — drei von vier Zonen mit Aktor hatten null
Minuten Schattenphase, bevor ihr erster Befehl den echten Aktor erreichte. Die
Regelentscheidungen selbst waren in allen geprüften Fällen korrekt, aber die
Begründungstexte bei „unverändert" (`control_loop.py`) sind in 99,76 % aller bisher
geschriebenen Entscheidungen sachlich ungenau — sie behaupten „innerhalb der Hysterese",
obwohl die Ist-Temperatur im Mittel 5,9 K vom Sollwert entfernt liegt. Kriterium 3
(Altsystemvergleich) entfällt ersatzlos, weil der Vergleichsbetrieb bewusst übersprungen
wurde. **Teilprojekt 2 gilt auf dieser Grundlage nicht als abgenommen.**

### Wirkungswächter erkennt Komposita ohne Trennzeichen und fragt jetzt git statt das Dateisystem

**Die Lücke aus dem letzten Stand ist geschlossen.** `PHYSICAL_VOCABULARY` in
`tests/test_user_visible_effect_texts.py` benutzte durchgängig eine führende Wortgrenze
(`\b`), wodurch deutsche Komposita wie „Zirkulationspumpe" oder „Ölbrenner" durchrutschten,
obwohl „pumpe" und „brenner" im Vokabular stehen. Entscheidung des Projektinhabers: Die
führende Wortgrenze fällt bei den Substantiven, die als Zweitglied in Komposita auftreten
(`ventil`, `aktor`, `heizkörper`, `heizkreis`, `fußbodenheizung`, `stellantrieb`, `boiler`,
`brenner`, `pumpe`, `heizung`, `heiz`, `wärm`/`waerm`, `warm`), sodass ein Teilstring-Treffer
jedes Kompositum fängt statt nur eine benannte Handvoll. Fehltreffer werden dafür in Kauf
genommen. Ausdrücklich **nicht** angefasst: `schalt` (die führende Grenze bleibt, weil
`Schaltfläche` sonst als physische Schaltbehauptung durchginge — die Zeichenkette kommt in
`thermoctl/web/templates/login.html` und `docs/self-hosting.md` tatsächlich vor) und
`geschaltet`. Ein Test belegt, dass „Zirkulationspumpe" und „Ölbrenner" jetzt gefangen
werden, sowie dass „warm" nicht auf „vorwarnen"/„Warnung" anschlägt. Die dadurch neu
gemeldeten 27 Fundstellen wurden persönlich durchgesehen und in
`tests/approved_physical_vocabulary.json` eingetragen — durchweg bestehende, bereits
zutreffende Aussagen zu `Schaltaktor`/`Heizungsaktor`, keine falsche Wirkbehauptung
darunter.

**Zweiter Fund, in derselben Fassung:** Der Wächter durchsuchte bisher das Dateisystem
statt die Versionsverfolgung. Als die Bauprozess-Dokumentation und die
Altsystem-Bestandsaufnahme das Repository verliessen (siehe oben), meldete er deren
Vorkommen fälschlich als ungeprüft — in einem frischen CI-Klon ohne diese Dateien lief die
Suite grün, auf der Platte des Projektinhabers scheiterte sie: genau die falsche Richtung
für einen Wächter. Er fragt jetzt `git`, was tatsächlich verfolgt wird, und fällt ohne
`git` darauf zurück, alles zu prüfen statt stillschweigend nichts. Die bisherige
Sonderbehandlung für `verlauf.md` entfällt damit — sie war der erste Fall derselben
Fehlerklasse.

## v0.6.0 — Nacharbeiten an PI, aus einer echten Anlage heraus

**Die PI-Eignungsprüfung ist gerätegenau geworden.** Ein selbstregelndes Thermostatventil
schliesst PI nicht mehr für die ganze Zone aus, sondern darf neben einem Schaltaktor
stehen — PI steuert dann nur den Schaltaktor. Das ist im Code belegt und nicht bloss
angenommen: `switch_commands()` und `thermostat_commands()` filtern beide mit
`ZoneDevice.self_regulating.is_(False)`, ein selbstregelndes Ventil taucht in keiner der
beiden Listen auf und bekommt seinen Sollwert über einen eigenen Weg. Ausgeschlossen
bleibt ein Thermostatventil **ohne** eigene Regelung — das bekommt die Entscheidung als
Sollwertsprünge und wäre von der schnellen Taktung betroffen.

**Die angenommene Relaislebensdauer ist einstellbar**, Vorgabe 500.000 statt fest
verdrahteter 100.000. Das ändert die Einschätzung des PI-Verschleisses erheblich: Aus rund
2,6 Relaislebensdauern im Jahr im ungünstigsten Fall werden rund 0,53. Die Zahl bleibt
ausdrücklich eine **Annahme** — auch und gerade, weil sie jetzt einstellbar ist.

### Browsertests, ausschliesslich örtlich

Dreizehn Playwright-Tests unter `browser_tests/`, nicht in der CI und nicht in der
gewöhnlichen Suite. Sie prüfen, was ein HTTP-Test nicht sehen kann: ob das Stylesheet
wirklich wirkt, ob die Browserkonsole fehlerfrei bleibt, ob eine echte Zeigergeste im
Zeitplan-Editor ankommt. Der Anlass steht in [CLAUDE.md](../CLAUDE.md) — zweimal sind
grundlegende Fehler durch alle Tests gerutscht und erst beim Öffnen der Seite aufgefallen.

**Die JavaScript-Abdeckung ist gemessen und bewusst nicht auf 100 Prozent getrieben:**

| Datei | Zeilen | abgedeckt |
|---|---:|---:|
| `schedule.js` | 412 | 16 % |
| `passkey.js` | 329 | 28 % |
| `assignment.js` | 243 | 32 % |
| `device_filter.js` | 63 | 51 % |
| `permissions.js` | 45 | 71 % |
| **gesamt** | **1.092** | **28 %** |

Der Weg auf 100 Prozent wurde geschätzt (zwei bis drei Wochen, das meiste davon in
WebAuthn-Fehlerzweigen und im Zeitplan-Editor) und vom Projektinhaber verworfen. Die
Messung selbst geht ohne npm über die V8-Abdeckung von Chromium; sie ist nicht fest
eingebaut. **Achtung, falls jemand sie nachbaut:** V8 liefert verschachtelte Bereiche, und
der äusserste umspannt die ganze Datei. Wer nur die Bereiche mit `count > 0` zählt, misst
100 Prozent und nichts — gezählt werden müssen die mit `count == 0`.

## v0.5.0 — PI-Regelung als Beta, je Zone einschaltbar

**Wer nichts einschaltet, bekommt exakt das Verhalten von 0.4.0.** Für eine Zone ohne den
Schalter wird die PI-Entscheidung nicht einmal berechnet: `_pi_outcome()` gibt die
Hysterese-Entscheidung zurück, bevor irgendetwas anderes geschieht, und das
Neutralisieren fasst ausschliesslich `pi_*`-Spalten an. Das ist strukturell so, nicht nur
getestet — und zusätzlich gemessen, indem dieselben Zyklusfolgen gegen den alten und den
neuen Stand liefen und Entscheidung wie Zonenzustand verglichen wurden.

PI gilt nur für gewöhnliche Schaltaktoren. Eine ungeeignete Zone sagt vor dem Einschalten
warum. Die sieben Vorrangregeln behalten absoluten Vorrang, und ein Wächtertest zwingt
jeden Ergebniscode der Regelkette zu einer ausdrücklichen Einordnung — vorher war das Tor
erlaubend per Vorgabe.

**Der Preis steht am Schalter**, mit der Rechnung hinter einem Info-Zeichen: bis zu
262.800 Schaltspiele im Jahr gegenüber höchstens 52.560 bei Hysterese. Der tatsächliche
Verschleiss ist unter [/relay-wear](relay-wear) je Gerät ablesbar — neue Seite, auch ohne
PI nützlich.

### Was diese Fassung über das Messen gelehrt hat

Über `domain/pi_control.py` wurde zuerst berichtet, sie habe 664 Mutanten und **null**
Überlebende. Tatsächlich waren es 53. Die Ursache ist systemisch: Alle
`mutation/cosmic-ray-*.toml` verweisen relativ auf `.venv/bin/python`, das es in einem Worktree
nicht gibt — jeder Mutant wird `INCOMPETENT`, und cosmic-ray meldet daraufhin null
Überlebende. **Ein vollständig gescheiterter Lauf sieht aus wie ein perfektes Ergebnis.**
Jede Konfiguration warnt jetzt davor, und `CLAUDE.md` verlangt, nach jedem Lauf die
`test_outcome`-Verteilung anzusehen. Nach dem Schliessen von 45 echten Lücken sind 8
Überlebende übrig, jeder einzeln als gleichwertig begründet.

## v0.4.0 — die Fassung nach dem Komplettreview

**Wer 0.3.0 scharf betreibt, sollte aktualisieren.** Sie behebt die ersten beiden echten
Fehler in der Regelkette, dazu vier Rechtefehler und sechs weitere Sicherheitsbefunde.
Der vollständige Eintrag steht im [CHANGELOG](../CHANGELOG.md).

Zwei Migrationen laufen im Container beim Start von selbst. **Der Rückweg nicht** —
`alembic upgrade head` geht nur vorwärts, und der alte Code startet danach nicht mehr,
weil `check_schema` das weitergewanderte Schema erkennt. Wer zurück will, downgraded
einmal von Hand mit dem *neuen* Abbild, bevor er das alte startet; die Reihenfolge steht
in [self-hosting.md](self-hosting.md).

## v0.3.0 — die Fassung, in der thermoctl schaltet

Alle vier Aktorwege sind verdrahtet: Zigbee2MQTT-Schalter, Zigbee-Thermostatventile,
Meross-Steckdosen, selbstregelnde Ventile. Bis 0.2.2 landete jede Regelentscheidung im
Schattenprotokoll und sonst nirgends.

**Der Trockenlauf bleibt die Vorgabe**, und das Scharfschalten wirkt erst nach einem
Neustart — der zweite Riegel wird beim Prozessstart gebaut. Die Anleitung dafür steht in
[scharfschalten.md](scharfschalten.md).

**Kein Vergleichsbetrieb.** Der mehrtägige Schattenbetrieb gegen das Altsystem wurde auf
Wunsch des Projektinhabers übersprungen. Dieser Code läuft als Erstes an einer echten
Heizung.

### Mutationstest, Runde 1

Erstmals gemessen, was die Tests wirklich prüfen, statt nur welche Zeilen sie ausführen —
auf `domain/control_loop.py` und `services/shadow_run.py`:

| | Mutanten | überlebt |
|---|---:|---:|
| `control_loop.py` | 137 | 15 = 11 % |
| `shadow_run.py` | 367 | **180 = 49 %** |

Die 49 Prozent sind die Zahl, wegen der diese Runde gemacht wurde. Sie ist nicht so
schlimm, wie sie aussieht — 127 der Überlebenden sind Mutationen an Typangaben und
SQLAlchemy-Ausdrücken, die kein Verhalten ändern —, aber **64 waren echte Testlücken**
und sind jetzt mit inhaltlichen Tests geschlossen. Übrig bleiben vier nachweislich
gleichwertige Mutanten, jeder einzeln begründet abgelegt.

Bezogen auf die 242 tatsächlich wirksamen Mutanten: 4 überleben, also 1,7 Prozent.
**Echte Fehler in der Regellogik: keine.** Alle Befunde waren Lücken in den Tests, nicht
im Code — das ist die beruhigende Hälfte des Ergebnisses.

Wiederholbar über `mutation/cosmic-ray-control-loop.toml` und `mutation/cosmic-ray-shadow-run.toml`; die
Einzelbewertung steht in `mutation/cosmic-ray-stage1-assessment.md`. Bewusst nicht in der CI — zu
langsam für jeden Lauf.

## Wo das Projekt steht

Die Hauptnavigation zeigt angemeldeten Benutzern nur Ziele, die sie tatsächlich öffnen
können. Zonenfilternde Übersichten erscheinen bereits bei einem passenden Recht für eine
einzelne Zone; anlagenweite Seiten erst bei einem anlagenweiten Recht. Die Zuordnung von
Ziel und Recht steht zentral in `web/navigation.py`, und ein Wächtertest vergleicht sie mit
der tatsächlichen Rechteprüfung der Zielansicht. Das Kiosk hat weiterhin seine getrennte
Navigation und sein eigenes Rechtemodell.

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen, veröffentlicht als `v0.1.0` |
| 1a — Nacharbeiten | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | gebaut; der Nachweis über mehrere Tage braucht die echte Anlage |
| 3 — Konfigurations-Oberfläche | abgeschlossen |
| 4 — Regelkreis und Cutover | Alle sieben Aktortypen der echten Anlage verdrahtet: Zigbee2MQTT-Aktoren, Meross-Steckdosen, Zigbee2MQTT-Thermostatventile. Kreuzreview der Verdrahtung fand drei Befunde, alle behoben — siehe unten |
| 5 — Integrationen und Veröffentlichung | Meross und Zeitplan-Bedienung erledigt, Freigabe von 0.2.2 offen |

## Was geschaltet wird — genau

Diese Frage ist mehrfach zu grob beantwortet worden, in beide Richtungen. Der Stand:

- **`setting.control_armed` steht in einer neuen Anlage auf `false`.** Solange bleibt
  jeder Weg zu einem Aktor zu.
- **`/control/arm` kann es öffnen**, mit eigenem Recht `control.arm`. Es ist also nicht
  wahr, dass „weiterhin nichts geschaltet wird" — das war eine Zusicherung ohne Deckung.
- **Der MQTT-Client trägt einen zweiten Riegel, der beim Start gebaut wird.** Wer scharf
  schaltet, muss den Dienst neu starten, bevor überhaupt etwas hinausgeht.
- **Sind beide Riegel offen**, bekommen selbstregelnde Thermostatventile ihren Sollwert
  veröffentlicht, **und jetzt auch ein gewöhnlicher (nicht selbstregelnder) Aktor an
  Zigbee2MQTT sein Ein/Aus** (`services/publishing.py::_send_actuator_switches`, neu):
  Rolle `actuator`, ohne `self_regulating`, mit der Fähigkeit `switch` — er bekommt, was
  `shadow_run.cycle()` zuletzt für seine Zone entschieden hat, nur bei Änderung, mit
  einem Eintrag im Schaltprotokoll für jeden Versuch (ausgeführt, unterdrückt oder
  gescheitert).
- **Ein Meross-Aktor bekommt jetzt ebenfalls seinen Befehl.** Das Schalten dort braucht
  eine Anmeldung gegen die Meross-Cloud; die läuft jetzt zwischengespeichert und
  außerhalb jeder Datenbanktransaktion (`services/meross_session.py`,
  `app.py::_shadow_loop`) — genau die Stelle, an der eine frühere Fassung die ganze
  SQLite-Datei bis zu 40 Sekunden gesperrt hatte. Lehnt die Cloud die Anmeldung ab, oder
  ist keine hinterlegt, schreibt jeder betroffene Zyklus stattdessen einen
  `failed`-Eintrag ins Schaltprotokoll, ohne den Zyklus selbst anzuhalten. Die Sitzung
  gilt sechs Stunden, bevor sie sich von selbst erneuert — eine bewusste Schätzung, keine
  gemessene Zahl, weil Meross keine Token-Lebensdauer dokumentiert: lang genug, dass ein
  gesundes Konto sich nur selten anmeldet, kurz genug, dass eine widerrufene Sitzung
  binnen desselben Tages auffällt statt unbegrenzt an einer toten Verbindung
  festzuhalten.
- **Ein Zigbee2MQTT-Thermostatventil ohne `self_regulating`** (Fähigkeit `thermostat`
  statt `switch`, von thermoctls eigener Hysterese statt eigener Regelung gesteuert)
  bekommt jetzt ebenfalls seinen Befehl, über `Zigbee2MqttThermostat`: den aufgelösten
  Zonensollwert und, wo das Gerät `system_mode` als beschreibbar meldet, `heat`/`off`
  dazu. Ein Gerät ohne `system_mode` (Bosch BTH-RA) wird stattdessen auf seinen
  niedrigsten Sollwert gefahren — der frühere Blocker dazu vom 2026-09-01 ist behoben.

## Kreuzreview der Aktorverdrahtung — drei Befunde, alle behoben

Der Projektinhaber hat den mehrtägigen Schattenbetrieb übersprungen; die Verdrahtung
läuft als Erstes an einer echten Heizung (vier Meross-Steckdosen, drei
Zigbee2MQTT-Thermostatventile). Ein Kreuzreview prüfte deshalb vor dem ersten
scharfen Betrieb noch einmal gezielt, ob geschaltet wird, wenn es soll — mit
„Nein" beantwortet. Alle drei Befunde sind jetzt behoben:

- **Ein gescheiterter Befehl wurde nie wiederholt** (schwer). Der Zwischenspeicher
  „nur bei Änderung senden" schrieb bei jedem Ausgang, auch bei einem gescheiterten
  — der nächste Zyklus mit unveränderter Entscheidung übersprang das Gerät dann
  komplett, kein neuer Versuch, kein neuer Log-Eintrag. Bei einer kalten, dauerhaft
  unterversorgten Zone (deren Entscheidung sich gerade *nicht* ändert) hätte das im
  Januar ein eingefrorenes Rohr bedeutet. Jetzt trägt der Zwischenspeicher das
  Ergebnis im Schlüssel: ein gescheiterter Befehl wird jeden scharfen Zyklus erneut
  versucht, aber nur einmal pro Ausfallepisode geloggt — unbegrenzt oft, bewusst ohne
  Backoff mit steigendem Abstand, weil ein Aktor an einer echten Heizung die Chance zur
  Erholung nicht verlieren soll, nur weil ein Backoff-Zähler noch wartet.
- **Die Entwertung der Meross-Sitzung war nie verdrahtet** (schwer). `app.py` übergab
  `meross_transport`, aber nicht `meross_session_cache`, an den
  Veröffentlichungszyklus — `invalidate_meross_session()` war damit im echten
  Betrieb unerreichbar, eine tote Meross-Verbindung blieb bis zu sechs Stunden als
  „gültig" im Zwischenspeicher stehen. Jetzt durchgereicht, mit einem Test, der den
  tatsächlichen Aufruf in `app.py` prüft, nicht nur die Funktion isoliert.
- **Der Meross-Weg hatte nur einen Riegel** (mittel). `MqttClient` friert seinen
  eigenen Riegel beim Start ein; der Meross-Weg (`MerossSwitch`) prüfte nur den
  Laufzeit-Riegel. Jetzt bekommt `MerossSwitch` denselben eingefrorenen Riegel
  (`app.state.sending_allowed`, wiederverwendet statt eines zweiten unabhängigen
  Werts) — beide Riegel beantworten dieselbe Frage („war die Anlage scharf, als dieser
  Prozess startete") und sollen deshalb denselben Wert tragen, statt als zwei
  unabhängige Booleans unbemerkt auseinanderlaufen zu können.

Zusätzlich abgesichert: eine Regression, bei der `ensure_transport()` in die offene
Schreibtransaktion des Schattenzyklus verschoben wird (genau der Fehler, der einmal
die SQLite-Datei 40 Sekunden gesperrt hatte) — bei der die gesamte Suite trotzdem grün
blieb. Ein neuer Test prüft jetzt die Eigenschaft selbst („keine Schreibtransaktion
offen während des Netzaufrufs"), nicht die Aufrufreihenfolge im Quelltext.

## Das Schaltprotokoll

Neu: `device_command` zeichnet jeden Befehl auf, der an ein Gerät hinausging oder im
Trockenlauf unterdrückt oder verworfen wurde — Zeitpunkt, Zone, Gerät, Nutzlast, Ergebnis,
Begründung, Auslöser. Der bestehende Sollwert-Weg an selbstregelnde Thermostatventile
(`services/publishing.py::_send_self_regulating_valves`) schreibt dorthin; Ansicht unter
„Einstellungen → Schaltprotokoll" (`/device-commands`, Recht `audit.read`). Anders als
`shadow_decision` überlebt ein Eintrag das Löschen oder Umbenennen seiner Zone oder seines
Geräts (`SET NULL` plus Namens-Momentaufnahme statt CASCADE), und unterliegt keiner
automatischen Aufbewahrung — anders als Messwerte ist ein Schaltbefehl selten (nur bei
Änderung gesendet) und jeder einzelne kann genau der Beleg sein, nach dem später jemand
sucht; eine automatische Löschung nach der üblichen kurzen Frist würde ausgerechnet den
Beweis entfernen, für den das Protokoll gebaut wurde. REST und MCP ziehen noch nicht nach;
das war eine bewusste, im Auftrag benannte Entscheidung für diese Runde, keine Lücke, die
übersehen wurde.

Der Anlass: Der Projektinhaber will den Schattenbetrieb überspringen und direkt scharf
schalten. Damit ist dieses Protokoll die einzige Stelle, an der später nachvollziehbar ist,
was an der Heizung passiert ist — es stand deshalb vor der Verdrahtung der Aktoren (Phase 4)
an, nicht danach.

## Zahlen

Selbst nachgeprüft, nicht aus Berichten übernommen (Stand 2026-09-04, Freigabe v0.6.1):

| | |
|---|---|
| Tests | 4281 unter SQLite, 4280 plus ein Skip unter MariaDB |
| Testabdeckung | 100 %, Mindestschwelle 100 % in der CI |
| Ruff, mypy strict | ohne Befund, 108 Quelldateien |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken geprüft; keine neue Migration in v0.6.1 |
| Container | baut, Paket im Abbild trägt Version 0.6.1; ein Start örtlich nicht geprüft (Docker-VM-Speicher hier voll) |

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`. Wer
die zweite setzt, läuft unbemerkt gegen SQLite und bekommt trotzdem einen grünen Lauf.

## v0.2.2 — freigegeben

**Sieben Freigabe-Reviews, sechs Ablehnungen, das siebte gibt frei.** Was die sechs
gefunden haben, steht im [CHANGELOG](../CHANGELOG.md). In Kürze: ein Regelungsfehler, eine
CSRF-Lücke in *jedem* ändernden Formular, ein Rückgängig, das einen veralteten
Schnappschuss annahm — und viermal dieselbe Klasse, Texte, die eine Ventilbewegung
versprechen, die es nicht gibt.

Das siebte Review hat sechs gezielte Mutationen über Regelung, Zeitplan, CSRF, Anzeige
und Rechte gesetzt; jedes Mal wurde **der inhaltlich richtige** Test rot. Dazu die
Angriffsliste gegen den Wirkungswächter (18 von 18) und die Migrationskette vorwärts,
rückwärts, vorwärts.

**Ein Befund blieb, bewusst offen gelassen:** Der Wirkungswächter erkennt deutsche
Komposita ohne Trennzeichen nicht (`Zirkulationspumpe`, `Ölbrenner`) und kennt reine
Temperaturaussagen („die Raumtemperatur steigt") gar nicht. Nachgeprüft: Keiner dieser
Begriffe kommt heute irgendwo vor — es ist also kein falscher Text, sondern eine Lücke im
Netz für künftige. Das gehört in die nächste Fassung, nicht in diese.

## Vier gemeldete Anzeigefehler behoben

- **Meross-Geräte galten dauerhaft als „hat sich noch nie gemeldet"**, obwohl der
  stündliche Abgleich sie fand und die Wolke sie als online meldete. Ursache:
  `web/device_views.py` sah für jedes Gerät nur `device_health.last_payload_at`
  an — das schreibt ausschliesslich die Zigbee2MQTT-Aufnahme
  (`services/ingest.py`) bei einer eingehenden MQTT-Nachricht, und ein
  Meross-Gerät schickt nie eine. Die Übersicht liest jetzt für ein Meross-Gerät
  `device.last_seen_at` (geschrieben vom stündlichen Abgleich,
  `services/meross_discovery.py::save_devices`) und wählt die dazu passende
  Formulierung: „abgeglichen" statt „gemeldet" — Stille bedeutet bei Meross, dass
  der eigene Abgleich nicht lief oder die Wolke das Gerät nicht mehr nennt, nicht,
  dass das Gerät selbst schweigt. Die Stille-Schwelle des selbstberichtenden
  Sensors (`default_sensor_timeout_seconds`) passt nicht zu einem stündlichen
  Abgleich; Meross bekam eine eigene, in `domain/device_survey.py`
  begründete Schwelle (`MEROSS_SILENT_AFTER_SECONDS`, zwei Abgleichzyklen — ein
  einzelner ausgefallener Durchlauf soll nicht sofort als defektes Gerät
  erscheinen). `MerossDevice.online` bleibt bewusst ohne eigene Spalte: es
  entscheidet weiterhin nur, ob `last_seen_at` bei diesem Durchlauf vorrückt, und
  ein dauerhaft offline gemeldetes Gerät wird dadurch — korrekt — irgendwann
  selbst als still erkannt.
- **Die Statistik schnitt Tage an UTC-Mitternacht** — ein lokaler Tag begann
  dadurch um 01:00 beziehungsweise 02:00 in `Europe/Berlin`. Neu:
  `domain/time.py::local_day_start_utc` liefert die UTC-Entsprechung einer
  lokalen Tagesgrenze; `domain/statistics.py::heating_periods` bucketiert danach
  statt nach UTC-Datum, `web/control_views.py` baut die Abfragegrenze ebenso.
  Gegen die Sommerzeit getestet: ein Tag mit 23 beziehungsweise 25 Stunden wird
  vollständig und richtig einem einzigen lokalen Kalendertag zugeschlagen.
- **Der Datumsfilter des Auditprotokolls deutete lokale Eingaben als
  UTC-Tagesgrenzen** — dieselbe Klasse Fehler, dieselbe Lösung
  (`local_day_start_utc`) in `web/audit_views.py`.
- **Eine laufende Übersteuerung erschien als „gerade eben"** — die Startseite
  beschrieb ihr **Ende** mit dem Vergangenheitsfilter `age`, der jeden
  Zukunftszeitpunkt als „gerade eben" las. `age_in_words`
  (`web/__init__.py`) unterscheidet jetzt Vergangenheit („vor 3 Minuten") von
  Zukunft („noch 42 Minuten"); geprüft, dass `age` sonst nirgends auf einen
  Zukunftszeitpunkt trifft — `override.ends_at` auf der Startseite war die
  einzige Stelle.

## Sensorstörungsmeldungen

Sensorstörungen und ihre Entwarnung erreichen jetzt neben Log und optionalem Webhook auch
Home Assistant: je Zone als binärer Problemsensor mit Meldungstext in den Attributen. Bei
einem veralteten Temperaturwert nennt die Meldung ausdrücklich, dass die Zone bis auf
Weiteres gegen ihren konkreten Frostschutz-Sollwert regelt. Der Meldeweg ist unabhängig
von den beiden Schalt-Riegeln und läuft nach Abschluss der Datenbanktransaktion.

## Ein Fehler in der Regelkette — der Ventilschutz-Marker hob den Taktschutz auf

Der erste echte Fehler in der Regellogik, den dieses Projekt gefunden hat. Alles zuvor
waren Testlücken.

Die Ausnahme von der Mindestschaltdauer hing an `valve_protection_active`. Dieses Feld
sagt aber nur, dass der **Marker** gesetzt ist, nicht dass der Ventilschutz auch noch
entscheidet. Verliert Regel 7 mitten im Lauf — durch Übersteuerung, Betriebsart „aus"
oder Sensorausfall —, bleibt der Marker bis zum Ablauf der Laufdauer gesetzt. In diesem
Fenster entschied die gewöhnliche Hysterese, **und die Mindestschaltdauern waren
ausgehebelt**: bei 60 Sekunden Zykluszeit und 10 Minuten Laufdauer bis zu zehn
ungebremste Schaltvorgänge an einem echten Relais. Genau der Taktschutz, den das
Altsystem nicht hatte.

`decide()` berechnet `protection_allowed` jetzt einmal oberhalb von Regel 5; beide Regeln
teilen sich dieselbe Bedingung für „der Schutzlauf gewinnt gerade". Der echte Schutzlauf
bleibt befreit, auch nach einem Neustart, wo der persistierte Marker die einzige
Erinnerung an ihn ist. Ein zweiter Fall hat sich mit erledigt: Kommt die Übersteuerung
während des Laufs, bleibt der Heizkörper jetzt bis zum Ablauf der Mindest-**Einschalt**dauer
an, statt sofort abzuschalten.

### Die Regelkette als vollständige Zustandstabelle

`tests/test_control_loop_state_table.py` prüft die Vorrangkette über acht Achsen
erschöpfend: 3.888 Rohkombinationen, **2.376 erreichbare geprüft**, 1.512 begründet
ausgeschlossen (ohne Quelle gibt es keinen Messwert relativ zur Hysterese; ein Sensor
`ok` oder `veraltet` hat einen). Die Erwartung ist von Hand aus der Spezifikation
übertragen, nicht aus `decide()` abgeleitet.

Ihre Prüfkraft ist gemessen, nicht behauptet — vier gezielte Sabotagen an
`control_loop.py`:

| Sabotage | gefangen | Zeilen |
|---|---|---:|
| Regel 5 wieder nur vom Marker ausgenommen | ja | 100 |
| Obere Hysteresekante `>` statt `>=` | ja | 24 |
| Übersteuerung aus `protection_allowed` entfernt | ja | 44 |
| Fenster-offen hinter die Mindestschaltdauer verschoben | ja | 340 |

Die zweite Zeile ist der Grund für die beiden Messwert-Ausprägungen genau **auf** den
Bandkanten: Vorher lagen alle Messwerte 1,0 K neben dem Sollwert bei 0,5 K Hysterese, und
eine vertauschte Vergleichsrichtung an der Kante blieb unbemerkt.

## Die Anlage läuft scharf — und was der Projektinhaber daraufhin entschieden hat

**Seit dem 2026-09-02 läuft v0.3.0 an der echten Heizung**, zum Zeitpunkt dieser Notiz
seit rund fünf Stunden, ohne Anlass zum Abschalten. Das Altsystem läuft im
Parallelbetrieb weiter und bleibt die Rückfallebene.

Auf die offenen Punkte hat der Projektinhaber entschieden:

| Punkt | Entscheidung | Stand |
|---|---|---|
| Ventilschutz wurde zu dauerhaftem Heizen | Mindest-Einschaltdauer gilt für einen Schutzlauf nicht | umgesetzt |
| Aufbewahrung `shadow_decision` | ein Jahr | umgesetzt, Vorgabe 365 Tage |
| PI-Regelung | optional je Zone, nur Schaltaktoren, zunächst Beta | umgesetzt (v0.5.0, in v0.6.0 nachgearbeitet); **Schalter steht je Zone weiterhin auf aus** |
| MQTT-Broker | EMQX mit Authentifizierung und Rechten ist vorhanden | dokumentiert, nicht erzwingbar |
| Repository öffentlich | erst mit einer zumutbaren, getesteten Fassung | offen |

### Sicherheitsbefunde: sechs behoben

Aus der Durchsicht vom 2026-09-02, zusätzlich zu den vier Rechtefehlern:

- **Anmeldeversuche konnten den Regelzyklus anhalten.** `time.sleep` und Argon2id liefen
  im async-Handler auf derselben Ereignisschleife wie die Regelung. Jetzt `await` und ein
  Thread. Der Fehlversuchszähler ist gedeckelt.
- **Ein Passwortwechsel beendet jetzt die anderen Sitzungen**, die eigene bleibt. Dazu
  „Andere Sitzungen beenden" unter `/users` — den Weg hatte der Docstring von
  `set_password` behauptet, ohne dass es ihn gab.
- **Das Einrichtungs-Token läuft nach einer Stunde ab.** Es steht weiterhin absichtlich im
  Log — das ist der einzige Kanal dafür —, aber eines aus einem alten Log legt keinen
  Administrator mehr an.
- **Meross bestätigt jetzt den Zustand, nicht nur den Befehl.** Namespace und Signatur der
  Antwort werden geprüft, und wo sie einen Schaltzustand mitliefert, muss er zum Befehl
  passen. Eine nicht verifizierbare Bestätigung gilt als Fehlschlag und wird wiederholt.
- **Der Webhook folgt keiner Weiterleitung mehr** und gibt den `Authorization`-Header
  damit nicht an ein fremdes Ziel weiter.
- **Sichere Cookies** waren bereits vollständig umgesetzt; alle sechs Cookies beachten den
  Schalter. Was fehlt, ist `THERMOCTL_SECURE_COOKIES=true` in der Installation. Neu ist
  ein Wächter, der jeden `set_cookie`-Aufruf im Quelltext über den AST prüft.

**Weiterhin offen und benannt:** der MQTT-Befehlsweg (Broker-Sache, siehe
[mqtt.md](mqtt.md)), die Vorgabe-Bindung an `0.0.0.0` ohne TLS (der Betrieb hier läuft
hinter einem Reverse Proxy), und dass ein `SET` ohne anschließendes `GET` nicht beweist,
dass ein Relais physisch geschaltet hat.

## Runde 6 — ein Refactoring, das seine Prüfkraft nachweist

`_process_zone` in der Regelschleife ist in drei benannte Funktionen zerlegt:
`_override_active`, `_advance_valve_protection`, `_apply_decision_to_state`. Woran die
Vermischung erkennbar war: Die lokale Variable `protection_started` wurde an zwei Stellen
mit unterschiedlicher Bedeutung benutzt — einmal, um zu entscheiden, ob eine Schutzfahrt
geschlossen wird, einmal roh als `valve_protection_active` für `decide()`. Dieser
Unterschied ist jetzt ein benannter Rückgabewert mit Begründung.

**Die Bedingung der Runde ist gemessen, nicht behauptet:** vorher 4 überlebende Mutanten,
nachher 4 — und es sind dieselben vier, die beiden aus `_process_zone` mitgewandert in die
beiden herausgezogenen Funktionen. Zusätzlich hat das Kreuzreview beide Fassungen gegen
dieselbe Datenbanklage laufen lassen (kein `ZoneState`, laufender Schutzlauf, gerade
abgelaufener Lauf, Übersteuerung, offenes Fenster, Sensorausfall) und `ShadowDecision` wie
`ZoneState` verglichen: identisch. Alle unberührten Funktionen sind AST-gleich.

Die drei übrigen Kandidaten (`app.py`, die fünf Frontend-Skripte, die Formularauswertung
in zwei Views) sind bewusst nicht angefasst.

### Ein zweiter Fehler in der Regelkette, behoben

**War: Ist die Mindest-Einschaltdauer einer Zone länger als ihr Ventilschutzlauf, wurde
der zeitlich begrenzte Lauf zu dauerhaftem Heizen.** Behoben am 2026-09-02: Die Ausnahme
von der Mindestschaltdauer in Regel 5 (`thermoctl/domain/control_loop.py`,
`protection_exempt`) gilt jetzt achsenabhängig — für einen gehaltenen Ein-Zustand reicht
`valve_protection_active` allein, für den Aus-Timer weiterhin
`valve_protection_active and protection_allowed`. Erwogen und verworfen wurden zwei
andere Wege: den Marker erst beim tatsächlichen Ende des Ein-Zustands zu löschen (trifft
die Ursache genauer, macht die Marker-Semantik aber zustandsbehafteter), und die
Konfiguration so einzuschränken, dass die Mindest-Einschaltdauer nie länger sein darf als
die Schutzlaufdauer (verschiebt das Problem nur auf den Bedienenden). Nebenwirkung, bewusst
in Kauf genommen: Verliert ein laufender Schutzlauf während seines Laufs Vorrang (Override,
„aus", Sensorausfall), hält `min_on_seconds` den Ein-Zustand nicht mehr künstlich — die
Regelung kann sofort umschalten, in einem auf die Schutzlaufdauer begrenzten Zeitfenster.

### Zwei Werkzeugfallen, beide bezahlt

- **`cosmic-ray exec` darf nie im Vordergrund unter einem Werkzeug-Timeout laufen.** Ein
  Abbruch mitten in einer Mutation überspringt die Wiederherstellung im `finally` und
  hinterlässt **mutierten Produktionscode ohne Fehlermeldung**. Ein Folgelauf maß dann
  gegen die kaputte Datei und lieferte ein vollständiges, aber ungültiges Ergebnis.
- **iCloud legt im Documents-Ordner Kopien der Form `test_deviation 2.py` an.** Sie sind
  alte Stände; `.gitignore` hält sie aus dem Repository, pytest sammelte sie aber ein und
  ließ die Suite an Zusicherungen scheitern, die vor Wochen einmal gestimmt haben. Beide
  Male sah es zuerst wie ein echter Fehlschlag aus. `--ignore-glob` in `pyproject.toml`
  fängt das jetzt ab, nachgewiesen mit einer absichtlich fehlschlagenden Dublette.

## Sicherheitsdurchsicht 2026-09-02 — vier Rechtefehler behoben

Die Durchsicht wurde nicht fortgeschrieben, sondern **noch einmal von vorn** geführt, mit
dem, was seit Teilprojekt 3 dazugekommen ist. Sie steht in
[sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md). Behoben und
mit Regressionstests belegt (`tests/test_security_review_2026_09_02.py`) sind vier
Befunde, alle drei ersten mit körperlicher Wirkung:

- **`device.manage` für eine Zone erreichte fremde Zonen.** Das Kanalformular prüfte
  das Bediengerät gegen die eigenen Zonen, die eingesandte Zielzone aber gar nicht. Ein
  bewusst auf eine Zone beschränkter Nutzer konnte einen fremden Raum abschalten — bei
  jeder Drehung am Regler erneut.
- **Eine Tastenbelegung wirkt in allen Zonen des Bediengeräts**, geprüft wurde nur eine.
  Der geteilte Flurregler reichte damit in jedes Zimmer, an dem er ebenfalls hängt.
  Jetzt braucht es das Recht für jede betroffene Zone.
- **Kiosk-Token galten als vollwertige REST- und MCP-Token.** Die Kioskoberfläche
  verstellt in festen Schritten; über REST setzte dasselbe Token jeden Sollwert und eine
  unbefristete Übersteuerung auf 35 Grad. Die enge Bedienfläche war die
  Sicherheitseigenschaft, und nur das Kiosk hat sie durchgesetzt. REST und MCP weisen
  Kiosk-Token jetzt ab.
- **Die Bediengeräteseite zeigte den gesamten Gerätebestand.** Gerätenamen tragen hier
  Raum- und Bewohnerbezüge. Die Seite verlangt jetzt `device.read` — das Recht, das die
  Navigation immer schon behauptet hat — und die Liste ist zonengefiltert.

**Nicht behoben, bewusst offen** — mit Begründung in der Durchsicht: der MQTT-Befehlsweg
(wer auf dem Broker veröffentlichen darf, steuert die Anlage ohne Konto — das ist eine
Frage der Broker-Konfiguration, nicht des Codes), die Meross-Bestätigung ohne
Zustandsprüfung, unbegrenzte Cloud-Antworten, das Einrichtungs-Token im Log,
Sitzungswiderruf beim Passwortwechsel und die Webhook-Weiterleitung mit `Authorization`.
Für die letzte hält ein ausdrücklich als offen benannter Test die Lücke fest.

## Offen

**Das Komplettreview ist durch.** Alle sieben Runden sind abgeschlossen.

| Runde | Zustand |
|---|---|
| 1 — Mutationstest | Stufe 1 und 2 abgeschlossen, im Kreuzreview |
| 2 — Zusicherungs-Audit | abgeschlossen |
| 3 — die Regelkette als Zustandstabelle | abgeschlossen, **ein Fehler gefunden** |
| 4 — Musterjagd | abgeschlossen |
| 5 — Messen vor Optimieren | abgeschlossen |
| 6 — Aufräumen | abgeschlossen, **ein Bestandsfehler gefunden** |
| 7 — Sicherheit von vorn | abgeschlossen, **vier Rechtefehler gefunden** |

**Runde 1, Stufe 2** hat vier weitere Domänendateien gemessen: `schedule.py` (138
überlebende Mutanten), `device_assignment.py` (14), `statistics.py` (12), `deviation.py`
(1). 115 wirksame Mutationen sind mit inhaltlichen Tests erschlagen, 50 als nachweislich
gleichwertig begründet abgelegt. **Echte Fehler in der Produktionslogik: wieder keine** —
über inzwischen sechs gemessene Dateien war jeder Befund eine Lücke in den Tests.

**Die drei Zeitzonen-Grenzfehler sind behoben** — Statistik und Auditfilter rechnen
Tagesgrenzen über `local_day_start_utc` in der konfigurierten Zeitzone, und der
`age`-Filter unterscheidet Zukunft von Vergangenheit, statt beides zu „gerade eben" zu
verschmelzen.

**Was nur der Projektinhaber entscheiden kann:**

- **Proportional-Integral-Regelung: ja, nein, oder erst messen?** Ein Zweipunktregler
  pendelt bei trägen Systemen (Fussbodenheizung) prinzipiell um den Sollwert; ein PI-Anteil
  würde das beheben, ist aber riskant (Integrator-Windup bei jeder der sieben
  Vorrangregeln — Fenster, Frostschutz, Sensorausfall — muss ihn zurücksetzen) und für die
  selbstregelnden Thermostatventile schlicht falsch (zwei Regler auf derselben
  Regelstrecke). Empfehlung: erst messen, wie weit die Anlage wirklich pendelt — die Daten
  dafür liegen seit 0.3.0 vor, und erst danach entscheidet sich, ob PI den Aufwand lohnt
  oder eine billigere Alternative (ein früheres Ausschalten, asymmetrische Hysterese)
  reicht.
- **Phase 2 wirklich abschliessen** — Schritt für Schritt in
  [inbetriebnahme-schattenbetrieb.md](inbetriebnahme-schattenbetrieb.md). Die Anlage muss
  über mehrere Tage laufen, bevor feststeht, dass plausible Ist-Temperaturen einlaufen
  und das Schattenprotokoll nachvollziehbare Entscheidungen zeigt.
- **Entscheiden, ob das Repository öffentlich werden soll.**
