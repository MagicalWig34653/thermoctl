# Stand

Letzte Aktualisierung: 2026-11-27. Diese Datei sagt, was **jetzt** gilt. Verlauf steht in `CHANGELOG.md` und in Git.

- **Regelmodule (L6, unveröffentlicht):** `domain/plant_settings.py` enthält Freigabe und globale Vorgaben, `domain/control_decision.py` die Regelentscheidung. `domain/wall_controller.py` und `domain/wall_controller_channels.py` behandeln Wandtaster und ihre Kanäle. Die [Modulübersicht](regelmodule.md) nennt Zuständigkeiten, Aufrufer und die früheren Namen.
- **Letzte Freigabe:** 0.25.0 (2026-11-20: PI-Beta zum Praxistest mit neuen Regeln standardmäßig aus, Codex-Befunde 1–3, eine Quelle für Parametergrenzen; Einzelheiten `CHANGELOG.md`). Davor 0.24.0 (Hysterese bis 0,0001 K; enthält die bis dahin unveröffentlichten Änderungen). Davor 0.23.0 (Schalter je Raum für Ausfallbenachrichtigungen): Tag `v0.23.0`, GitHub-Release, Abbild auf ghcr.io, Add-on-Repository nachgezogen. Davor 0.22.2 (Netzfehler stoppt die Regelung nicht mehr; PI behält den Integralanteil bei Zeitlücke und Kontextwechsel).
- **I-Anteil anzeigen (Karte 5481A15A, Teil 1, unveröffentlicht):** Nur lesend und nur für das Anlagenprofil (Wohnungskonten bekommen serverseitig keine Werte): Karte auf der Raumseite (`rooms[].pi`: Wert und letzter Reset), Reihe im Verlauf (`/ui-api/history`: `buckets[].pi_integral`, `pi_resets`), Kachel „Temperaturverlauf“ mit „I-Anteil zeigen“, Kachel „Regler“ (`controller`). Der Reset-Zeitpunkt wird aus `shadow_decision` gelesen (`pi_integrator_action = zurueckgesetzt`, Index aus Migration `c7e2a9f4d813`), reicht also nur so weit wie die Aufbewahrung. Gate-, Halte- und Anlaufzyklen protokollieren seither den gehaltenen Wert; ältere Zeilen haben dort Lücken. Alle fünf PI-Zahlen der Regelentscheidung (`pi_integral_before`/`after`, `pi_raw_duty`, `pi_frozen_duty`, `pi_feedforward_duty`) sind auch in der Betriebsübersicht und im MCP-Werkzeug `shadow_decisions` für Wohnungskonten `null` (`domain.pi_view.visible_to`; Ausnahme zur Regel „allein das Recht entscheidet“, siehe `domain/ui_profile.py`). Ebenso fehlt ihnen der Tastgrad im Begründungstext von Regelentscheidung und Schaltprotokoll (`pi_view.reason_for`: ui-api, REST, MCP). Die PI-Regelparameter (z. B. Verstärkung) bleiben mit `zone.read` über REST/MCP lesbar wie zuvor. Die Linie reißt, wo Zeilen ohne Wert liegen (`buckets[].pi_break`). **I zurücksetzen / I setzen (Karte A391AABC, Teil 2, unveröffentlicht):** `domain/pi_intervention` (eine Domäne für Weboberfläche, REST, MCP): Recht `zone.manage` des Raums **und** Anlagensicht (Wohnungskonten auch mit `zone.manage` abgewiesen); Wert 0–1 (`check_value`, sechs Stellen, NaN/Wahrheitswerte/Text abgewiesen); Raum ohne PI oder ohne `zone_state` → 409 `pi_not_active`. Der Eingriff **fügt nur eine Anforderung ein** (Tabelle `pi_manual_request`, Migration `e3b7c1d9a452`, CHECK 0–1) plus Audit (`pi.integral_reset`/`pi.integral_set`, Quelle `web`/`api`/`mcp`, Detail mit altem und neuem Wert); ein späterer Eingriff ersetzt einen vorgemerkten (die jüngste Zeile gilt, der Zyklus löscht alle gesehenen). **Eigene Tabelle statt Spalten in `zone_state`, weil sonst ein Klick den Zyklus abbricht:** Der Zyklus ist eine Transaktion über alle Räume; MariaDB mit Snapshot-Isolation (ab 11.6 Standard) bricht sie mit 1020 ab, wenn sie eine seit dem Snapshot von anderen geänderte Zeile schreibt (nachgestellt und abgesichert in `tests/test_pi_manual_race.py`, nur MariaDB; in der CI gegen `mariadb:11`). Web/REST/MCP dürfen `zone_state` deshalb nie schreiben, und der Zyklus löscht die gesehenen Anforderungen **einzeln über den Primärschlüssel** (ein Sammel-`DELETE … IN` wurde ab etwa 13 Zeilen zum vollen Scan mit denselben Folgen). Dieselbe Falle für andere Schreiber derselben Zeilen (z. B. MQTT-Empfang) ist hier nicht geprüft. **SQLite:** Der Eingriff schreibt als erste Anweisung einer frischen Transaktion (vorher wird die lesende festgeschrieben); „erst lesen, dann schreiben“ gab bei gleichzeitigem Schreiber sofort `database is locked` und damit 500 (im Browsertest gesehen, `tests/test_pi_manual_sqlite_lock.py`). Bleibt die Datenbank länger belegt, kommt 409 `busy`. **Andere schreibende Endpunkte** haben unter SQLite dasselbe Muster und wurden nicht geändert. **Offener, älterer Befund (nicht geändert):** `shadow_run.cycle()` fängt je Raum jede Ausnahme und macht weiter, auch wenn die Datenbank die **ganze** Transaktion zurückgerollt hat (1020, danach 1305 „SAVEPOINT does not exist“): Der Zyklus meldet dann scheinbar Erfolg, gespeichert ist nur, was nach dem Rollback kam (Nachprüfung zu Karte A391AABC, MariaDB 12.3). Vorschlag: bei verlorener Transaktion den ganzen Zyklus abbrechen und im nächsten Intervall neu rechnen. Übernommen wird sie **im nächsten Regelzyklus** (`shadow_run._pi_outcome`, Hülle um `_pi_outcome_core`): der Wert wird zum gehaltenen I-Anteil, **bevor** Tore, Hysterese-Haltephase und sicherer Anlauf ihn wie jeden gespeicherten Wert behandeln -- sie ändern sich nicht. Schaltprotokoll: `pi_reset_reason` = `manuell_reset`/`manuell_gesetzt`, `pi_integrator_action` = `zurueckgesetzt`/`gesetzt` (neu: `INTEGRATOR_SET`), `pi_integral_before` = der verworfene Wert. Ein Zurücksetzen zählt damit als „letzter Reset“ auf der Raumkarte und als Marke im Verlauf, ein Setzen nicht. **Bewusst:** Die 24-Stunden-Regel verwirft einen manuell gesetzten Wert in genau seinem Übernahmezyklus nicht (sonst wäre „Setzen“ in einer lange kalten Anlage wirkungslos), danach gilt sie wie gewohnt -- ein gesetzter Wert über 0 in einem Raum ohne Wärmeanforderung seit 24 h fällt also im Folgezyklus wieder auf 0. Ein Raum, dessen PI inzwischen aus oder ungeeignet ist, verwirft die Anforderung mit Audit-Eintrag `pi.integral_dropped` (Quelle `system`) und Warnung im Log. Oberfläche: Raumkarte „I-Anteil“ (`PiCard`, nur mit `actions.set_pi_integral`): „I zurücksetzen“ und „I setzen“ (Prozent) je mit Bestätigung, Hinweis auf den vorgemerkten Eingriff (`rooms[].pi.pending`). REST `GET /zones/{id}/pi-state`, `POST …/pi-state/reset`, `PUT …/pi-state/integral`; MCP `read_pi_state`, `reset_pi_integral`, `set_pi_integral`. CSV `/ui-api/history.csv`: letzte Spalte `pi_integral` nur in der Anlagensicht. Der Kiosk zeigt den I-Anteil nicht und erreicht die Eingriffe nicht (`tests/test_kiosk_api.py`). Nicht an der echten Anlage geprüft; der Browsertest läuft ohne Regelschleife (Eingriff bleibt dort vorgemerkt), die Übernahme belegen `tests/test_pi_manual_intervention.py` (beide Datenbanken laufen über die CI). Der Glossarbegriff `i-anteil` ist seit 2026-11-26 auf der Projektseite veröffentlicht (Freigabe des Projektinhabers).
- **Verlauf (Temperatur & Heizzeit), unveröffentlicht:** Standard ist ein Raum (der erste sichtbare), „Alle Räume“ wählbar; die Wahl steht in der Adresse (`?zone=<id>`/`?zone=alle`) und überlebt Neuladen. Nur Frontend, nichts am Backend.
- **Matter-Bridge (optional, `matter-bridge/`, lokal auf `main`, nicht gepusht, nicht veröffentlicht, kein CI-Job):** Eigener Node-Dienst (Matter.js 0.18.0) für Apple Home
  und optional Google Home, je Zone ein Heating-only-Thermostat (nur Aus/Heizen). Sollwertrad → `command/temporary_setpoint` (befristete Übersteuerung, nie
  `command/setpoint`), Aus → `operating_mode off`, Heizen → `auto`. Die thermoctl-Seite (`command/temporary_setpoint`) ist im Zweig des Sollwertrads gebaut und mit diesem Stand zusammengeführt.
  Tests: `npm test` im Verzeichnis (virtuelles Matter-Netz, Simulator statt thermoctl), `npm run test:network` (Loopback, nur macOS). Nicht an Apple Home, Google Home und
  mDNS im echten Netz geprüft; nicht zertifiziert (Test-VID). Einzelheiten und offene Geräteprüfungen: `matter-bridge/README.md`.
- **Integrationstest Brücke ↔ echte Befehlsverarbeitung (`tests/test_matter_bridge_integration.py`, Treiber `matter-bridge/integration/driver.mjs`):** Ein Node-Unterprozess hält Aedes-Broker (127.0.0.1), die echte Brücke im virtuellen Matter-Netz und einen Matter-Controller; thermoctl hängt mit dem echten `MqttClient` (aiomqtt) am selben Broker und führt jeden Befehl durch `_process_mqtt_message` → Domäne → Audit → `_send_zone_state` (retained, zurück über den Broker), mit echter SQLite-Datenbank und Zonen mit Zeitplan (Tag 21,0 / Nacht 18,0). Beweist: Lesen konsistent mit `zone_state`; Rad = genau eine laufende Übersteuerung mit Ende an der nächsten Schaltung, Plan unverändert, Audit; Entprellung; Beenden/Zeitablauf → Planwert; Aus → `off` (Rad gesperrt, keine Übersteuerung), Heizen → `auto`; abgelehnter Befehl (auch 99 per Direktbefehl, auch Aus-Wettlauf) → unveränderter Zustand kommt zurück; Abwesenheit bleibt unberührt; Brücke sendet nie `command/setpoint`; zwei Zonen unabhängig. Beweist nicht: Broker mit Zugangsdaten/ACLs (Aedes erlaubt alles), echtes Apple/Google Home, mDNS, MariaDB (die Datenbank ist hier immer SQLite). Skipt ohne node oder `matter-bridge/node_modules` (`npm ci`). Laufzeit ca. 40 s.
- **In 0.25.0 enthalten:** Behebung der hohen Befunde 1–3 aus dem Codex-Gegenlesen (Rechteausweitung über selbst hergestellte Alleinverwalter-Ausnahme, Zukunftszeitstempel bei Messwerten, OAuth-Körperlimit) sowie Entflechten L2. 4 und 9 (siehe „Anmeldedrosselung und Verwaltungsrechte bei Gleichzeitigkeit“), 5 (MCP-Übersteuerung verlor den Zeitzonenoffset: `schedule.create_override` rechnet `ends_at` und `now` nach naive UTC um, naive Werte gelten als UTC; `start_absence` normalisiert beides am Eingang selbst, die REST-Übersteuerung nimmt kein `ends_at` an; ein aware Wert außerhalb des darstellbaren Bereichs wirft `ValueError`, der MCP-Client sieht dann nur die allgemeine SDK-Fehlermeldung), 6, 7 und 8 (Wiederholstunde der Zeitumstellung, siehe „Zeitplan über echte Zeit“) sind nach 0.25.0 behoben (unveröffentlicht). Ein Release (Vorschlag 0.24.1) braucht die Freigabe des Projektinhabers.
- **Anmeldedrosselung und Verwaltungsrechte bei Gleichzeitigkeit (unveröffentlicht, behebt Codex-Befunde 9 und 4):**
  *Anmeldung:* Ein Versuch wird **vor** der Wartezeit gezählt (`login.throttled_attempt`), nicht danach. Je Schlüssel laufen höchstens
  `MAX_PARALLEL_ATTEMPTS` (5) Prüfungen gleichzeitig, jede weitere wird sofort abgewiesen (Anmeldung: dieselbe Antwort wie ein
  Fehlversuch, ohne Passwortprüfung; Passwortwechsel: 429 `too_many_attempts`). Der Schlüssel hängt nur vom **getippten
  Namen** ab, nie davon, ob es das Konto gibt -- sonst verrieten die Wartezeiten die Existenz (`login.throttle_key`). **Unter
  MariaDB ist er ein Hash des Gewichts, das die Kollation der Spalte `user.username` dem Namen gibt** (`WEIGHT_STRING
  … AS CHAR(512)`; das Auffüllen mit Leerzeichen macht die Datenbank selbst, auch in mehrstufigen Gewichten): Zwei Namen
  haben denselben Zähler, wenn die Datenbank sie für denselben Namen hält, auch bei Zeichen, die `utf8mb4_uca1400_ai_ci`
  übergeht oder gleichsetzt (U+034F, Tatweel U+0640, `ø` = `o` und mehr). Unter NO-PAD-Kollationen ist das gröber als der
  Vergleich (Leerzeichen am Ende teilt den Zähler), nie feiner. Die Abfrage liest keine Zeile. Zeichensatz und Kollation
  werden einmal je Datenbank aus `information_schema` gelesen. Bewusste Ausnahme von „keine
  datenbankspezifischen Funktionen“: nur in diesem Dialektzweig; unter SQLite (binärer Vergleich) und wenn die Kollation nicht
  lesbar ist, gilt die Faltung in Python (`login.login_key`). Geprüft mit utf8mb4 (`tests/test_login_throttle_collation.py`);
  ein Latin-1-Schema ist nicht geprüft. Ein deaktiviertes Konto kostet dieselbe
  Passwortprüfung wie ein aktives (kein Zeit-Orakel). Der Nachschlag gibt seine
  Transaktion vor der Wartezeit frei. Bekannt: Wer für einen Namen 5 Anfragen offen hält, lässt die Anmeldung dieses Namens für die
  Dauer abweisen (höchstens 5 s je Runde, danach wieder möglich) -- der Preis gegen unbegrenztes Raten; eine Kontosperre gibt es
  nicht. **Datenbank-Pool:** Die Anmeldung (Passwort und Passkey) hält über keinen `await` hinweg und nach der Rückkehr keine
  Verbindung: Jeder Abschnitt committet selbst (`login.attempt_login`, `web/passkey_views.py`). Vorher blieb die Verbindung bis
  zum Aufräumen der Anfrage nach der Antwort draußen, die nächsten Anmeldungen warteten **blockierend auf der Ereignisschleife**
  (Regelschleife inklusive) auf den Pool -- bei mehr gleichzeitigen Anmeldungen als Pool-Verbindungen stand der Dienst bis zur
  Zeitgrenze und antwortete mit 500; zwei gleichzeitige Anmeldungen eines Kontos blockierten sich an der gesperrten Kontozeile.
  Geprüft: `tests/test_login_pool.py` (ganze Anwendung, Pool mit 2 Verbindungen, 20 Anfragen, beide Datenbanken). **Grenze:**
  Dasselbe Muster haben andere `async`-Endpunkte (die Datenbankarbeit läuft auf der Schleife, der Commit erst nach der Antwort),
  ohne Anmeldung erreichbar die Kiosk-Einlösung `GET /kiosk/{token}`, angemeldet unter anderem die Passwortänderung (hält die
  Verbindung während der Wartezeit); dort nicht geändert und nicht gemessen. Der Zähler bleibt im Prozessspeicher (siehe unten).
  *Verwaltungsrechte:* Zwei Verwaltende, die im selben Moment je den anderen deaktivieren, umgruppieren, ein Recht entziehen oder
  eine Gruppe löschen, würden unter MariaDB beide durchkommen, wenn jede Prüfung den anderen noch sähe, und alle aussperren. Jede solche
  Änderung nimmt jetzt zuerst `administration.lock_guarded_permissions` (die beiden geschützten Rechte-Zeilen in fester Reihenfolge,
  vor dem ersten Schreiben), und die Prüfungen sind sperrende Lesevorgänge (`_locked`): die zweite Änderung wartet und sieht, was die
  erste hinterlassen hat; **jede** Lesung, die in eine solche Entscheidung eingeht, ist eine sperrende (auch „trägt die Zielgruppe das
  Recht noch?" und die bisherigen Mitgliedschaften beim Umgruppieren -- eine einfache Lesung sähe den alten Snapshot). MariaDB mit
  Snapshot-Isolation lehnt statt zu warten mit 1020 ab, ebenso 1205/1213, die Schlüsselfehler 1062 (derselbe Name gleichzeitig
  angelegt) und 1451/1452 (Zielgruppe gleichzeitig gelöscht) und 1305 (die Transaktion ist schon zurückgerollt, der Savepoint der
  Endpunkte existiert nicht mehr; nur mit „SAVEPOINT" in der Meldung -- dieselbe Nummer für eine fehlende Funktion bleibt ein Fehler).
  Das alles gilt an jeder Stelle bis zum Commit: `administration.serialized_change` macht daraus `ConcurrentChange` (409
  `concurrent_change`, „nichts geändert, bitte erneut versuchen"; der Fehler der Datenbank steht im Log). Benutzt wird es von elf
  Endpunkten in `web/ui_api_access.py`: Konto anlegen, aktivieren/deaktivieren, Gruppe wechseln, Passwort setzen, Gruppe
  anlegen/löschen, Gruppenprofil, Gruppenrechte, eigenes Passwort, eigenen und Kiosk-Token widerrufen. Ist die Gruppe nach dem
  Commit weg, antwortet der Endpunkt 404. Gemessen über echte Anfragen (40 Runden je Fall, ON und OFF): dort keine 500 und nie ohne
  Verwalter. *Am Konto* gilt dasselbe für sieben weitere Endpunkte (`web/ui_api_access.py`, `web/ui_api.py`): alle anderen Sitzungen
  beenden, eine Sitzung beenden, Token-Rechte ändern, Token ausstellen, Passkey löschen -- je `serialized_change` (409
  `concurrent_change`), die betroffene Zeile zuerst als sperrende Lesung (`_locked_row`; die zweite Änderung wartet und sieht, was die
  erste hinterließ: beendete Sitzung/gelöschter Passkey 404, widerrufenes Token 409 `token_not_active`; die Rechte eines Tokens
  stehen danach nicht doppelt) -- und **Abmelden**: gegen eine gleichzeitige Änderung derselben Sitzung wird bis zu dreimal neu
  versucht (frischer Snapshot: schon beendet = Erfolg, sonst beendet es selbst), nur bei dauerhaftem Streit 409 „Sie sind noch
  angemeldet“; nie ein Erfolg bei lebender Sitzung. Auch das Auflösen des Cookies (`auth/dependencies._resolve_session`) schreibt
  seinen Zeitstempel (`last_seen_at`) dort, wo eine Kollision behandelt werden kann (neu lesen: beendet = 401), nicht erst im
  Autoflush der nächsten Abfrage. Geprüft: dieselben Szenarien in `tests/test_administration_race_http.py` (zehn Fälle am Konto).
  **Anmeldung gegen Passwort setzen:** `revoke_all_sessions` liest jetzt sperrend (sieht auch eine Sitzung, die nach dem Snapshot
  der Änderung entstand), und die Anmeldung liest die Kontozeile nach der Passwortprüfung gesperrt neu (`login._unchanged_since_the_check`)
  und öffnet die Sitzung nur, wenn Hash und Aktiv-Zustand noch dem entsprechen, wogegen geprüft wurde -- sonst gilt der Versuch als
  fehlgeschlagen (dieselbe Antwort wie ein falsches Passwort). Vorher lebte bei Passwort setzen parallel zur Anmeldung eine
  Sitzung mit dem alten Passwort (gemessen 6 von 6). Geprüft: `tests/test_login_password_state.py` (deterministisch, SQLite und
  MariaDB) und `test_a_login_with_the_old_password_leaves_no_session_...` (echtes Rennen, MariaDB). Die Kontosperre bei Streit
  (Anmeldung mit richtigem Passwort bei gleichzeitiger Passwortänderung) kostet einen erneuten Versuch, nichts weiter.
  Unter SQLite gibt es nur einen Schreiber, dort entfällt die Klausel (gleichzeitige Schreiber scheitern dort weiterhin
  mit „database is locked", ohne dass jemand ausgesperrt wird). Geprüft: `tests/test_administration_race.py` (echte gleichzeitige Transaktionen, nur MariaDB, je mit
  `innodb_snapshot_isolation` ON und OFF; gegenseitiges Aussperren auf vier Wegen, Umgruppieren gegen Entzug/Löschung/Leeren der
  Zielgruppe in beiden Reihenfolgen, zwei Umgruppierungen derselben Person, Fremdschlüssel; ohne die Sperre rot),
  `tests/test_administration_race_http.py` (dieselben und weitere Rennen über die echten Endpunkte: nie 5xx, nie ohne Verwalter,
  keine doppelten Rechte-Zeilen oder Namen; Standard 12 Runden, `THERMOCTL_RACE_ROUNDS` ändert sie), `tests/test_login_throttle.py`.
- **Parametergrenzen (L1, in 0.25.0):** Eine Quelle (`PARAMETERS`) für Raum und globale Standardwerte, gemeinsamer Prüfweg `zone_settings.check_parameter_value`, Test der PI-Constraints gegen die Tabelle, OpenAPI nennt die Bereiche. Das Speichern aller Raumfelder (REST, `/ui-api`) erzwingt dieselben Bereiche wie Einzelwert und MCP (`validate_inherited_parameters`); unveränderte Altwerte außerhalb des Bereichs blockieren es nicht, die Hysterese wird immer geprüft. Grenzfälle: `tests/test_parameter_limits.py`.
- **Anlage:** läuft seit 2026-09-02 scharf mit `thermoctl` und seit 2-3 Wochen im Produktivbetrieb ohne Auffälligkeiten
  (Projektinhaber, 2026-10-18). Phase 2 gilt als abgeschlossen, das Altsystem ist abgelöst. Repository öffentlich
  (AGPL-3.0), Betrieb als Docker-Container und als Home-Assistant-Add-on (eigenes Repository
  `MagicalWig34653/thermoctl-addon`).
- **PI-Erweiterungen (in 0.25.0, standardmäßig aus):** Kp bis 3,00; neue Zonenparameter
  `pi_surplus_decay_factor`, `pi_integral_separation_k`, `pi_feedforward_enabled/_gain/_smoothing_hours` (Vorgaben = bisheriges Verhalten);
  Vorsteuerung liest die Außentemperatur aus `measurement` (Quelle der Einstellung „Außenfühler“) mit dem Plan-Sollwert vor der Sonnenabsenkung;
  Entscheidungsdetails (UI-API, MCP) zeigen I vor/nach, Tastgrad roh/eingefroren und `pi_feedforward_duty`. REST hat keinen Entscheidungs-Endpunkt.
  Die Kp-Schrittprüfung der Datenbank ist neu gefasst (die alte `% 5` lehnte unter SQLite Werte wie 1,15 ab). Nicht an der Anlage gemessen.
- **Migrationskopf:** `b5d1f8a3c264` (Sollwertrad-Einstellungen; davor `a3e8c5d17b94`, PI-Erweiterungen).
- **Inhalt von 0.24.0 gegenüber 0.23.0:** Hysterese bis 0,0001 K (Migration `f7c2a91d4e08`; Raumwert strikt ab 0,0001 K geprüft); PI behält den Integralanteil bei Gates und Neustart; Verbindungsanzeige mit Schreibsperre; Fehler- und Leerzustände; Touch-Ziele am Handy; lesbare deaktivierte Primärknöpfe; Raumseite (Ausfallschalter, Erben erklärt, Raumtechnik eingeklappt). Einzelheiten: `CHANGELOG.md`, Abschnitt 0.24.0.
- **Schaltprotokoll am Handy:** Unter `lg` steht jede Zeile (Befehl, Entscheidung, Absenkung) als Karte statt Tabelle, die Erläuterung ist dort eingeklappt; ab `lg` bleibt alles wie zuvor. Zeilenschlüssel ist Art plus Nummer (die Arten zählen je Tabelle, gleiche Nummern brachen die Liste ab). Test: `browser_tests/test_events_mobile.py`.
- **Browsertests (`browser_tests/`):** 72 Tests (2026-11-22, lokal; im Gesamtlauf 71 grün, 1 unter Last ausgefallen und einzeln grün; laufen nicht in der CI); im Gesamtlauf unter Last fallen einzelne, wechselnde Tests aus, einzeln laufen sie grün. Die Ingress-Tests öffnen die Navigationsgruppe „Geräte“ vor dem Klick.
- **Antwortzeit (0.22.1):** Ursache der zähen Oberfläche war `shadow_run._previous_state`: Je Zone und Zyklus wurde die gesamte
  `shadow_decision`-Historie geladen (Betrieb: ~158 000 Zeilen je Zone, Zyklus ~14 s, die Ereignisschleife stand im Takt von ~45 s still).
  Jetzt vier Index-Einzelzugriffe (Migration `d4a8e1b73c52`), Ergebnis bitgleich zur alten Fassung (Referenztest). Gemessen auf MariaDB,
  10 Zonen mit je 100 000 Zeilen: 5-6 s auf 0,03-0,08 s. Die Raumübersicht (`GET /ui-api/rooms`) aggregiert die Ersatzquelle-Vergleiche in SQL
  und lädt Notbetriebs-Daten einmal je Anfrage (`services/emergency_state.emergency_context`, `shadow_run.actuator_assignments_by_zone`;
  119 auf 28 Statements bei 10 Zonen). Ein Index `(zone_id, measured_at)` auf den Vergleichen brächte bei Daten nur im 7-Tage-Fenster nichts.
  **An der Anlage noch zu bestätigen:** Nach dem Update darf `Ereignisschleife blockiert` im Log nicht mehr erscheinen.
- **Mehrere Fühler je Raum -- bekannte Eigenschaften** (Betrieb: `docs/self-hosting.md`, Abschnitt 8c): Ein Teilausfall meldet keinen
  Notbetrieb, der Mittelwert springt sofort auf die verbleibenden Fühler; festhängende Fühler gehen weiter in den Mittelwert ein; die
  Fenstererkennung über den Temperaturverlauf löst schon bei einem einzelnen Fühler aus (konservativ, auch durch Zugluft). REST
  `POST|PUT /api/v1/zones` lehnt das alte Feld `temperature_source_device_id` mit 422 ab.
- **Messbarkeit (seit 0.22.0):** Anfragedauer (`dauer_ms`, `pool_checked_out`) und eine Messung der Schleifenverzögerung
  gehen ins Log (`thermoctl/observability.py`); der DB-Pool ist über `THERMOCTL_DB_POOL_*` einstellbar, das Frontend bricht Anfragen nach
  30 s ab. Die Schleifenwarnung hat die Ursache sichtbar gemacht (siehe Antwortzeit oben).
- **Bekannte Folgearbeiten (Antwortzeit), nicht begonnen:** Regelschleife, MQTT-Ingest und Aufräumlauf laufen weiter als synchrone DB-Arbeit
  in `async def` (`app.py`) und blockieren die Ereignisschleife, solange sie arbeiten; per `asyncio.to_thread` auszulagern ist
  sicherheitsrelevant (Regelkette) und braucht eigene Tests samt Gegenlesen. Dazu: `PUT /ui-api/devices/map/layout` je Verschiebung
  entprellen, die rund 50-60 Asset-Dateien beim Kaltstart bündeln, Statistik-Abfragen in SQL aggregieren, Index auf der Aktion von
  `audit_event`, `/device-commands` (DISTINCT über Raumnamen).
- **Nicht gesehen (Projektinhaber):** Gerätekarte mit drei dichten Fühlern, 409/422-Meldungen der Fühlerzuordnung, Datenflussansicht und
  mobile Ansicht.
- **Prüfstand:** keine Zahlen aus alten Läufen übernommen. Maßgeblich ist ein frischer Lauf (Ruff, mypy, Tests gegen
  SQLite und MariaDB, Alembic vorwärts/rückwärts, Docker-Bau); die Suite liest `THERMOCTL_TEST_DATABASE_URL`.

## Offene Entscheidungen (Projektinhaber)

Entschieden am 2026-10-18 (Projektinhaber), umgesetzt in 0.21.0: Das Gruppenprofil sperrt nichts, allein das Recht
entscheidet (das Profil steuert nur noch Darstellung wie Wortwahl und Wohnungsstart); Phase 2 abgeschlossen und das
Altsystem abgelöst; Audit-Lücken nachgezogen; OAuth-Zustimmung bleibt serverseitig; Rechteeskalation begrenzt
(nur andere verwalten); letzter Inhaber von `group.manage` gesperrt; `audit.read` filtert nach Räumen; das
Schaltprotokoll ist auf 3 Jahre und etwa 2 GiB begrenzt.

Offen:

- **Impressum der öffentlichen Seite** (`site/`, GitHub Pages): bewusst **offen gelassen** (Projektinhaber,
  2026-10-10). Für eine öffentliche Seite aus Deutschland kann ein Impressum nötig sein; es wurde nichts
  erfunden. Die Seite setzt keine Cookies, speichert nichts im Browser und stellt keine Anfragen an Dritte
  (per Test abgesichert, `tests/test_landingpage.py`). Wird eines gewünscht: `impressum.html` und ein
  Fußlink im Generator (`tools/landingpage_erzeugen.py`) ergänzen, `site/` neu erzeugen.
- Das Paket `ghcr.io/magicalwig34653/thermoctl` ist öffentlich (Projektinhaber, 2026-10-10).
- **Automatische Übernahme der Home-Assistant-eigenen MQTT-Broker-Zugangsdaten** ins Add-on ist nicht gebaut
  (`services: ["mqtt:want"]` gewährt nur Zugriff auf die Supervisor-API und füllt `options.json` nicht von selbst).
- **Sicherheitsstand** ([sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md), Stand 2026-09-04,
  teils überholt): **Weiterhin offen:** der MQTT-Befehlspfad (dokumentiert, im Code nicht erzwingbar -- Frage der
  Broker-Konfiguration), unbegrenzte/unmaskierte Meross-Cloud-Antworten, die HTTP-Netzwerkvorgabe (`0.0.0.0` ohne
  TLS-Erzwingung) und die CSRF-Ausnahme für Login/Logout. Das unbegrenzt wachsende Schaltprotokoll ist mit 0.21.0
  erledigt.

Neu aus der Svelte-Umstellung:

- **Weiteres ohne Audit (Verdacht aus einer groben Suche, nicht Funktion für Funktion geprüft):** das anlagenweite
  Standard-Kartenlayout (`/devices/map/layout/default`), Geräteverbindungen (`connect`, `disconnect`, `regulation`),
  Raum anlegen/ändern/löschen (ob die Domäne protokolliert), REST und MCP (laufen über Domäne und Änderungsprotokoll),
  OAuth-Ausstellung (bewusst nicht je Refresh). Eigene Übersicht und Anordnung sind persönliche Einstellungen.
- **Einrichtung (`/setup`)** als SPA-Seite mit `POST /ui-api/setup` -- derzeit als eigene Seite umgesetzt; Entscheidung
  über die endgültige Form steht aus.
- **Der Fehlversuchszähler der Anmeldung liegt im Prozessspeicher** und gilt je Instanz (mehrere Prozesse bzw. beide
  Instanzen im Verbund zählen getrennt; ein Neustart setzt ihn zurück).
- **Mutationsmessungen neu fahren:** `mutation/cosmic-ray-administration`, `-authz`, `-auth-sessions`, `-auth-kiosk`,
  `-schedule` u. a. wurden gegen den Stand vor dem Entfernen der Alt-Testfunktionen gemessen und sollten danach neu
  gemessen werden (Regeln dafür: `CLAUDE.md`, Abschnitt „Praktisches zur Agentenarbeit").
- **Praxistest an Sonderfällen:** Passkey-Registrierung mit echtem Authenticator, Ingress-Betrieb unter Home Assistant
  und Touchgeräte (Zuordnungskarte, Wochenplan, Kiosk) sind nicht gesondert abgenommen; die Oberfläche im
  Alltag sieht laut Projektinhaber sehr gut aus.
- **Vorschau-Grenze:** Die Übersicht in der Vorschau eines Kontos rendert Raumkacheln mit den Raumdaten der
  ansehenden Person; Räume, die diese selbst nicht sieht, fehlen dort.
- **`AbsencePanel`** (Abwesenheit) erscheint nur im Mieterprofil, nicht nach Recht (`can_start`); offen, ob angleichen.

## Oberfläche auf Svelte umgestellt (2026-10-17, freigegeben mit 0.18.0)

Die Jinja/HTMX/Bootstrap-Oberfläche ist entfernt (Auftrag des Projektinhabers, „die alte UI rausschmeißen"; Bedingung
„alle Funktionen in der neuen UI" laut zweiter Paritätsprüfung erfüllt, Belege `docs/ui-migration/FUNKTIONSMATRIX.md`).
Seit 0.18.0 freigegeben.

- **Es gibt nur noch die Svelte-SPA** unter `/app` (Quelle `frontend/`, Ausgabe `thermoctl/web/static/app/`, nicht
  eingecheckt), Daten über `/ui-api` (Sitzung, CSRF-Header) und `/kiosk-api` (Kiosk-Cookie). Node nur zur Build-Zeit;
  CI-Job `frontend` und `docker/Dockerfile` unverändert. Ingress: Asset-Pfade relativ gebaut, der Server setzt den Präfix
  zur Laufzeit ein.
- **Entfernt:** alle Alt-HTML-Seiten und alle Alt-Formular-POSTs (21 `*_views.py`), 52 Templates, Alt-JS,
  `thermoctl.css`, Bootstrap, HTMX, Mermaid, die HTMX-Hilfen in `web/__init__.py` und `web/forms.py`, der Profil-Wächter
  web/guards (Datei entfernt), `/login`-Formular und `POST /logout`. Die Weiterleitungen in `web/legacy_redirects.py` übernehmen jeden
  alten GET-Pfad; ein alter POST antwortet 404/405.
- **Bewusst serverseitig geblieben:** OAuth-Zustimmung `/oauth/consent` (Clickjacking-Schutz, absolute Weiterleitung;
  Vorlage ohne Fremdcode), Passkey-JSON-Routen `/passkey/...`, Kiosk-Einlösung `GET /kiosk/{token}` (setzt das Cookie,
  leitet auf `/app/kiosk`) mit Hinweisseite für ein ungültiges Token, Hinweisseite „Seite veraltet" für server-
  gerenderte POSTs. Gemeinsame Hüllen: `base_plain.html` (eigener kleiner Stil, kein Fremdcode, keine Skripte).
- **Verschobene Funktionen:** `home_notice` (Wohnungshinweis) nach `web/shared/home_notice.py`; die übrigen Helfer standen
  schon in `web/shared/`.
- **Abweichungen gegenüber der Alt-Oberfläche** (Matrix, Zeilen „bewusst anders"): Sollwert −/+ nur im Raumdetail;
  Messquelle in der Zuordnung statt im Raumformular; Zeiträume 24 Std./7/30 Tage statt 24 h/3/7 Tage; Wochenplan per
  Pinsel und Inspektor statt Formular; Löschen mit „Rückgängig" statt Bestätigungsseite; Auswahlfelder statt Ziehen bei der
  Zuordnung; kein Autofill-Login; kein globaler Ladebalken; Sitzungsablauf führt zur Anmeldung. Schaltpunkte und Zeiträume ohne
  Zeiger legt das Formular „Neue Zeit anlegen“ an (`NewTimePanel.svelte`); die Notbetrieb-Aktortabelle zeigt Takt-Phase,
  Taktquelle und Frist, der Sensor-Timeout steht je Raum darüber (`RoomDecisions.svelte`).
- **Entfallen mit der Alt-Oberfläche:** die HTML-Passwortänderung ohne bisheriges Passwort (`/users/{id}/password`);
  eigenes Passwort ändert nur noch `PUT /ui-api/account/password` mit bisherigem Passwort.
- **Tests:** 328 übersprungene Alt-Tests (`legacy-ui`) und weitere reine Alt-Seiten-/Formulartests gelöscht; Wächter
  (`test_ui_profile`, `test_csrf`, `test_navigation`, `test_endpoint_coverage`, `test_smoke_test`) auf die verbleibenden
  Router umgestellt. `browser_tests/` auf Anmeldung/Abmeldung, Ingress-Präfix, Kiosk und OAuth-Zustimmung gekürzt.

## Funktionsumfang und Grenzen je Bereich

Gilt heute; Einzelheiten und Bedienung stehen in `docs/` (u. a. `bedienung.md`, `api.md`, `mcp.md`, `mqtt.md`,
`self-hosting.md`, `glossar.md`).

### Regelung (Hysterese, PI, Schaltschutz)

- **Entscheidung** trifft `decide()` in `thermoctl/domain/control_decision.py` als feste Vorrangkette; jede Entscheidung
  trägt einen Code und eine lesbare Begründung (Grundsatz 5). Vorrang des Sollwerts in `resolved_setpoint()`:
  Betriebsart „Aus" > laufende Übersteuerung > Urlaub > Zeitplan > Frostschutz.
- **Übersteuerung beenden** (`cancel_override`) trifft nur die gerade laufende Zeile (die Rücknahme im Änderungsprotokoll genau die protokollierte); das Sollwertrad übernimmt nur deren Ende, nie das einer abgelaufenen.
- **Befristete Übersteuerung (Sollwertrad, noch nicht freigegeben):** `remote_control.set_temporary_setpoint` legt eine Übersteuerung
  an, die von selbst endet, und **ersetzt** dabei laufende Übersteuerungen des Raums statt zu stapeln
  (`schedule.cancel_running_overrides`); die der **Abwesenheit** bleiben unberührt und gelten nach Ablauf wieder. Ende nach
  Einstellung (global, Regelvorgaben-Seite: `setting.temporary_setpoint_until_next_switch`, Standard 1 = nächste Schaltung, sonst
  feste Dauer; `temporary_setpoint_minutes`, 5–1440, Standard 120, zugleich **Ersatzfrist** ohne nächste Schaltung -- nie
  unbefristet). Betriebsart „Aus" wird abgelehnt. Audit wie bei `set_setpoint` (`zone_setpoint`, Quelle `system`). Einziger Weg
  derzeit: MQTT `command/temporary_setpoint` (für die Matter-Brücke); `command/setpoint` ändert unverändert die Modus-Temperatur.
  Einstellung nur in der Oberfläche (nicht in REST/MCP; `TEMPORARY_SETPOINT_LIMITS` bewusst außerhalb von `LIMITS`).
  **Bekannte Eigenschaft (Entscheidung des Projektinhabers, 2026-11-21):** Das Rad darf wie jede andere Übersteuerung unter den
  Frostschutz der Zone drehen (Grenzen nur −20 bis 35 °C); die Regelkette hebt das nicht an. Bewusst so gelassen, nicht ablehnen und nicht anheben.
  Gleichzeitige Radbefehle (beide Instanzen im Aktiv-Bereitschafts-Verbund wenden jeden Befehl an) sind abgesichert: Zonenzeile
  zuerst, dann die laufenden Übersteuerungen mit sperrendem Lesen (`FOR UPDATE`); trifft MariaDB mit Snapshot-Isolation dabei
  Fehler 1020, wiederholt `_process_mqtt_message` den Befehl einmal. Geprüft nur unter MariaDB (`tests/test_remote_control_race.py`,
  unter SQLite übersprungen -- dort gibt es keine Zeilensperren). Das Ende ist die nächste Schaltung als echter Zeitpunkt
  (`schedule.next_switch_utc`, siehe unten; in der Wiederholstunde der Zeitumstellung gilt der Plan über echte Zeit); die Ersatzfrist greift nur noch ohne
  Zeitplan, bei fester Dauer und als Absicherung, falls je ein Ende nicht nach „jetzt“ läge.
  Nicht an der Anlage gemessen.
- **Zeitplan über echte Zeit, nächste Schaltung und Zeitumstellung (unveröffentlicht, behebt Codex-Befund 8; Entscheidung des
  Projektinhabers „Plan über echte Zeit führen“):** Der Zeitplan wird nicht gegen die Wanduhr, sondern gegen eine monotone **Plan-Uhr**
  ausgewertet (`time.schedule_clock`; eine Stelle für `_schedule_setpoint` und damit `resolved_setpoint`, für den Modus am Ende von
  `boost` und, über `next_switch_utc`, für die nächste Schaltung). Plan-Uhr = Wanduhr, außer im zweiten Durchlauf der Wiederholstunde
  im Herbst (Europe/Berlin 02:00–03:00 kommt zweimal, `datetime.fold == 1`): dort steht sie auf dem letzten Wanduhrwert des ersten
  Durchlaufs (Berlin 02:59:59,999999; Lord Howe mit 30 Minuten Rücksprung 01:59:59,999999). Folgen: Der Plan-Sollwert springt nie
  zurück; Punkte in der Wiederholstunde feuern nur im ersten Durchlauf; ein Punkt um 03:00 feuert erst, wenn die Wanduhr 03:00
  (Winterzeit) erreicht. Den Umstellungsmoment findet `schedule_clock` per Bisektion in ganzen Sekunden (`zoneinfo` bietet keine
  Übergangstabelle), nur im zweiten Durchlauf (etwa eine Stunde im Jahr); sonst ist es ein `fold`-Test. Zonen ohne Sommerzeit und
  UTC: Plan-Uhr = Wanduhr. `current_point` und `next_point` bleiben reine Wanduhr-/Ringrechnung.
  Die nächste Schaltung ist der früheste echte Zeitpunkt **strikt nach „jetzt“**, zu dem die Plan-Uhr Wochentag und Minute eines
  Zeitplanpunkts erreicht (`schedule.next_switch_utc`, Baustein `time.wall_clock_reached_at`; eine Stelle für `end_of_next_switch`,
  `boost`, Sollwertrad, Sprung, „Als Nächstes“, MQTT-Thema `next_switch` und Vorschau). Liegt ein Punkt in der Wiederholstunde, zählt
  nur seine erste Lesart: Im zweiten Durchlauf bei 02:15 ist ein Punkt um 02:30 erst eine Woche später wieder dran, und im ersten
  Durchlauf feuert ein schon passierter Punkt (02:10) nicht noch einmal; bei mehreren Punkten ist das Ende einer Übersteuerung
  „bis zur nächsten Schaltung“ der nächste Punkt nach der Wiederholstunde. Sichtbare Folge: In der Wiederholstunde bleibt der
  Plan-Sollwert stehen, „Als Nächstes“, Boost, Sollwertrad und Sprung nennen nur Schaltungen, die noch kommen, und die Vorschau
  zeichnet einen Balken je echter Schaltung ohne Rücksprung.
  Ausgefallene Stunde (Frühjahr): ein Punkt in der Lücke gilt ab dem Umstellungsmoment, wie ihn `current_point`/`_schedule_setpoint`
  anwenden; das Ende der Übersteuerung fällt damit auf den Moment, ab dem der Zeitplan den neuen Wert liefert, und in der Vorschau
  beginnt eine Phase in der Lücke im Umstellungsmoment (01:00 UTC).
  Geprüft gegen ein unabhängiges Orakel, das Minute für Minute durch die echte Zeit läuft und die Plan-Uhr als laufendes Maximum der
  Wanduhr seit Wochenbeginn führt (`tests/test_schedule_plan_clock.py`: Europe/Berlin, America/New_York, Europe/London,
  Australia/Lord_Howe, Pacific/Chatham, UTC, Asia/Kolkata; alle Umstellungen 2026 und 2027 in beiden Richtungen; feste Seeds), dazu ein Differenztest gegen die alte Wanduhr-Rechnung (außerhalb des zweiten Durchlaufs unverändert), die Reproduktion
  an der Umstellung (`tests/test_schedule_real_time.py`) und `tests/test_dst_next_switch.py`. Nicht berührt: Regelschleife,
  Hysterese, Vorrangkette von `resolved_setpoint`. **Bekannt, bewusst nicht geändert:** Die Jetzt-Markierung im Tagesverlauf der
  Startseite (`zone_status`, `now_fraction`/Wochentag) zeigt die Wanduhr, nicht die Plan-Uhr, und springt im zweiten Durchlauf mit ihr
  zurück (reine Anzeige, wirkt nicht auf Sollwert oder Schaltung). Nicht an der Anlage gemessen.
- **Hysterese und Mindestschaltdauer** sind Pflicht (Altsystem hatte beides nicht). Ein Ventilschutzlauf, der seinen
  Vorrang mitten im Lauf verliert, hebt die Mindestschaltdauer nicht auf; `tests/test_control_decision_state_table.py` prüft
  die Vorrangkette erschöpfend.
- **PI-Regelung** je Zone optional (nur für Zonen mit gewöhnlichen Schaltaktoren). Invariante: Eine Phase, die unter
  Hysterese begonnen hat, hält deren Mindestdauer, egal wann und wie PI danach übernimmt; eine von PI begonnene Phase
  folgt PI's eigenen, weicheren Mindestdauern. Welcher Regler eine Phase begann, folgt aus der `shadow_decision`-
  Historie. Bei Sensorausfall, Frostschutz, Aus-Modus, Wiederanlauf und (bei gemischten Zonen) offenem Fenster ist PI
  ausgesetzt: der Modulator (Fensterrest, Tastgrad, Haltezeit) beginnt neu, der sichere Anlauf bleibt; die Begründung nennt dann PI's eigene Werte.
  **Der Integralanteil I bleibt dabei erhalten** (eingefroren, nicht integriert; auch bei Neustart und Update, Hysterese-Haltephase eingeschlossen) und
  wird nur auf 0 gesetzt, wenn die Zone mehr als 24 Stunden keine Heizanforderung hatte (`integral_expired_without_heating`, Grund
  `keine_heizaktivitaet_24h`, geprüft in jedem PI-Zyklus aus der `shadow_decision`-Historie; ohne Heizzeile zählt die älteste Zeile als Bezug, eine
  für eine neue Zone ohne Zeile gibt es nichts zu verwerfen). Nur wenn PI für die Zone deaktiviert oder ungeeignet wird, wird I weiterhin gelöscht.
  Ebenfalls erhalten bleibt I bei jedem
  Wechsel des Sollwertkontexts (Zeitplan-Moduswechsel, Beginn/Ende von Übersteuerung, Boost, Urlaub; auch bei anderer Temperatur;
  Modulator und Haltezeit der Phase laufen weiter) und bei einer Zeitlücke (mehr als zwei Zyklen Abstand: `angehalten`, nicht über
  die Lücke integriert, der Modulator beginnt neu; ohne vorherige Auswertung I = 0). Es bleibt die Begrenzung (Anti-Windup). Offen:
  ob der Modulator bei sehr großen Sollwertsprüngen neu starten soll (bewusst nicht, wegen der Haltezeit-Invariante). Nach langem
  Ausfall wirkt der erhaltene I beim Wiederanlauf sofort als Tastgrad (Entscheidung des Eigners, 2026-11-09). PI kann je nach Einstellung mehr Schaltspiele erzeugen.
- **Fenster:** Ein zugeordneter Kontakt entscheidet allein. Ohne Kontakt kann je Zone (Vorgabe aus) ein Temperatursturz
  als Fenster gelten: Referenzwert (Maximum der Vorwerte, ab drei Vorwerten der zweithöchste) minus aktueller Wert ≥
  Schwelle (Vorgabe 1,5 K in 15 min), Plausibilitätsgrenze 6 K, Haltedauer 30 min, kumulative Strähnenzählung mit
  Zwangspause (60 min nach 90 min). Der Schwellwert ist ein begründeter Schätzwert, keine Messung der Anlage;
  Fehlauslöser (offene Tür, Zugluft) sind unvermeidbar. Auswirkung wie ein echter Kontakt, aber unterscheidbar
  (`window_open_by_temperature`, Zusatzsatz in der Begründung). Nicht in REST, MCP, Homebridge (Vorgabe des Eigners);
  in Home Assistant als Diagnose-Entität.
- **Frostschutz schlägt das offene Fenster** (Regel 3, Code `frostschutz_trotz_fenster_offen`). **Reine
  EIN/AUS-Zonen** (Fußboden, kein selbstregelndes Ventil) schalten wegen eines offenen Fensters nicht ab; gemischte
  Zonen behalten das vorsichtigere Verhalten.
- **Fenster-Alarm** (Meldung, kein Regeleingriff): Fenster länger als 30 min offen und Außentemperatur unter 5 °C;
  fehlende oder veraltete Außentemperatur ergibt „unbekannt", nie Alarm oder Entwarnung. **Festhängender Messwert**
  (Spanne < 0,05 K über 12 h): melden, aber weiterregeln (Entscheidung des Eigners).
- **Urlaubsbetrieb:** eine anlagenweite Absenkung über ein festes Zeitfenster (volle lokale Tage, DST-sicher), je Zone
  mindestens auf deren Frostschutz angehoben. Recht `vacation.manage`; REST und MCP vorhanden.
- **Zeitplan-Vorschau** der nächsten 24 Stunden (`schedule_forecast`) rechnet in UTC-Instanzen über Mitternacht und
  beide Zeitumstellungen; REST und MCP bieten sie noch nicht an (bewusst).
- **Aus dem Wohnbereich:** Abwesenheit (Mieter, nur eigene Räume, über Übersteuerungen geklammert), „Problem melden"
  (Recht `report.create`, geht über die Meldekette, steht im Audit), „Zur nächsten Schaltzeit springen"
  (`jump_to_next_switch`).
- **Bekannte Grenze:** REST, MCP und Kiosk benutzen für „nächste Schaltung vorziehen" weiter `remote_control.boost`,
  das bei laufender Übersteuerung still eine zweite danebenlegt; die Oberfläche verlangt über `jump_to_next_switch`
  eine ausdrückliche Ersetzung. Angleichung wäre eine Vertragsänderung (offen, Folgearbeit).

### Sonnenabsenkung

- Regellogik unverändert (Entscheidung des Eigners: nur sichtbar machen). Die Absenkung prüft **nicht**, ob der Raum
  gerade zu kalt ist; sie bleibt nie unter dem Frostschutz der Zone.
- `shadow_decision.scheduled_setpoint_c` ist der Sollwert **vor** der Absenkung (folgt auch Übersteuerung, Urlaub,
  Abwesenheit, Aus), `solar_setback_k` die angewandte Absenkung (NULL = keine; alte Zeilen bleiben NULL).
- Das Schaltprotokoll führt dritte Eintragsart `absenkung` (`beginn`/`aenderung`/`ende`), **abgeleitet, nicht
  gespeichert**: Aufbewahrung wie `shadow_decision`; am Beginn der Restehistorie kann eine laufende Absenkung als neuer
  „Beginn" erscheinen.
- Der Temperaturverlauf je Zone zeichnet nur, was protokollierte Zeilen belegen (Lücken über 10 min reißen die Kurve,
  unbekannt ≠ „keine Absenkung").

### Notbetrieb bei Sensorausfall

- Für **alle** Zonen automatisch aktiv (`sensor_failure_enabled`, Vorgabe an; bewusste Verhaltensänderung seit 0.11.0).
  Stufen `normal` → `ersatzquelle` → `notbetrieb` → `rueckkehrpruefung` → `normal`; eine Episode endet erst bei
  `normal`. Reine Domänenbausteine (Quellenbewertung, Zustandsautomat, Takt, Aktorplan, Hinweistexte), die
  Schattenlauf **und** Publisher gemeinsam benutzen; scharf gesendet wird nur im Publisher, scharfe Felder werden erst
  nach erfolgreichem Versand fortgeschrieben.
- **Ersatzquelle:** brauchbarer Wandfühler, sonst die **kälteste** um den Ausgleich korrigierte Messung der
  Thermostate der Zone (`korrigiert = roh − Ausgleich`). **Echo-Regel (Bosch BTH-RA):** `local_temperature` gilt erst
  30 min nach dem letzten `remote_temperature`-Sendeversuch als unabhängig. Ohne brauchbare Quelle beginnt der
  Notbetrieb; PI ist in jeder Stufe außer `normal` aus.
- **Takt der Fußbodenkreise:** Festtakt (Vorgabe 10 min an / 20 min aus), wenn die Außentemperatur fehlt, sonst
  Kennlinie nach Außentemperatur (−10 °C 20/10, 0 °C 10/20, 15 °C aus; Wiederanlaufspanne 1 K). Phasen zählen ab
  **erfolgreichem** Versand. Der Takt läuft auch im Modus „Aus" und bei offenem Fenster.
- **Thermostat-Übergabe** genau einmal je Episode und Zuordnung (`manual` plus Notsollwert, Vorgabe 20 °C), nur für
  Geräte mit bestätigtem Gerätevertrag (`occupied_heating_setpoint` und `operating_mode` schreibbar); Rückstellung
  des zuletzt **gemeldeten** Vorwerts bei Rückkehr; ein Versuch wird vor dem Versand committet, nie wiederholt.
- **Rückkehr:** mindestens zwei verschiedene Messzeitpunkte und 60 s durchgehend brauchbar, gegen genau eine Quelle.
  Für Mindestdauern zählt der **reale** Relaiszustand (Befehlslog); Handschaltungen am Gerät sieht der Befehlslog nicht.
- **Meldung:** je Episode eine Störungsmeldung beim Eintritt und eine Entwarnung bei echter Rückkehr (Zustände mit
  Commit vor dem Versand; bei Absturz lieber doppelt als fehlend). Anzeige auf Start, Wohnungsstart, Kiosk und
  Betriebsseite; REST (`/zones/{id}/emergency-state`, Vorgaben und Zonenprofil) und MCP (`read_emergency_state`,
  `read/set_sensor_failure_*`).
- **Aufbewahrung:** Vergleichs- und Aktorentscheidungszeilen folgen `shadow_decision_retention_days`; Episoden bleiben.
- **Bewusste Entscheidungen:** der Warm-Aus-Punkt beendet eine laufende Ein-Phase nicht vorzeitig (2026-10-03);
  keine Mehrprofilverwaltung (nur das eine anlagenweite Profil ist bearbeitbar, Zonen erben oder wählen vorhandene).
- **Offen:** Abnahme an der echten Anlage (Wandfühler abklemmen, Ersatzquelle, Übergabe `pause` → `manual` am BTH-RA
  beobachten, Rückkehr, Rückstellung, Fußboden über mindestens zwei Taktpaare, Webhook und Entwarnung; Gerätevertrag
  vorher mit frischem Dump prüfen). Die Spalte „Ergebnis" im Schaltprotokoll zeigte bei Notbetrieb-Entscheidungen den
  Rohcode (z. B. `sensorausfall_takt_laeuft`); ob die SPA das übersetzt, ist hier **nicht** nachgeprüft.

### Schattenbetrieb, Freigabe und MQTT-Sendefreigabe (drei Riegel)

- **Schattenbetrieb/Trockenlauf:** Die Regelschleife entscheidet je Zone und Zyklus und protokolliert
  (`shadow_decision`), schaltet aber nur, wenn alle Riegel offen sind. Eine neue Anlage startet im Trockenlauf.
- **Riegel 1 -- `setting.control_armed`** („Freigabe"): geöffnet über `/control/arm` (Recht `control.arm`, bewusst in
  keinem Token-Preset); Rücknahme `control.disarm`.
- **Riegel 2 -- MQTT-Sendefreigabe:** der MQTT-Client trägt einen beim **Prozessstart** gebauten, unabhängigen Riegel
  (`control_armed_at_startup()`); Scharfschalten wirkt dort erst nach einem Neustart. Gilt auch für Meross.
- **Riegel 3 -- Primärrolle:** `cluster.is_leader` (Einzelbetrieb ohne Verbund-Datensatz gilt als primär, fail-open
  nur dort, weil die Suite ihr Schema nicht über Migrationen baut). Geprüft vor **jedem** Sendevorgang
  (`integrations/actuators.py::switching_allowed`).
- Sind alle offen, veröffentlicht der Dienst: Sollwerte an selbstregelnde Thermostatventile, Ein/Aus an gewöhnliche
  Zigbee2MQTT-Aktoren, Sollwert/`system_mode` an Zigbee2MQTT-Thermostate ohne eigene Regelung, Schaltbefehle an
  Meross über eine zwischengespeicherte Cloud-Sitzung (außerhalb jeder DB-Transaktion).
- **Ein gescheiterter Befehl wird jeden scharfen Zyklus erneut versucht** (einmal je Ausfallepisode geloggt;
  der Zwischenspeicher trägt das Ergebnis im Schlüssel). Für Meross gilt stattdessen der Anmelde-Backoff (unten).
- **Schaltprotokoll** (`device_command`, Recht `audit.read`): drei Ergebnisse `executed`/`suppressed`/`failed`
  (kein „verworfen"); Eintrag überlebt Löschen/Umbenennen von Zone oder Gerät (Namens-Momentaufnahme); Aufbewahrung höchstens 3 Jahre (`command_log_retention_days`) und etwa 2 GiB (`command_log_max_mb`, aus der Zeilengröße geschätzt), täglich im Aufbewahrungslauf. Das Recht `audit.read` ist raumbezogen: ohne anlagenweite Vergabe sieht man nur Einträge der eigenen Räume.
- **Anlaufriegel:** `app.state.sending_allowed` liest nur `control_armed`; die Primärrolle wird weiterhin je Sendevorgang
  geprüft.

### Primärrolle und Aktiv-Bereitschafts-Verbund

- Zwei Instanzen teilen eine Datenbank und einen Broker; eine regelt, die andere übernimmt nach
  `setting.cluster_takeover_cycles` (Vorgabe 5) Zyklen Ausfall. Anspruch in `cluster_claim` (nur
  `thermoctl/services/cluster.py`), Wechsel per **einer atomaren `UPDATE`-Anweisung**, Zeit ist die der Datenbank
  (`func.now()`). `try_become_leader` schreibt zuerst und liest danach (SQLite-Sperrverhalten); SQLite läuft im
  WAL-Modus.
- Die Bereitschaft lässt den ganzen Regelzyklus aus (keine Entscheidungen, keine Veröffentlichung), bleibt aber am
  MQTT verbunden und nimmt Messwerte auf; Befehle (z. B. Moduswechsel) werden auf beiden angewandt, nur die
  primäre Instanz bestätigt zurück. Geordnetes Herunterfahren gibt den Anspruch sofort frei. Die Oberfläche zeigt
  „Standby". Direkt nach einem Start steht dort „Primärrolle wird ermittelt", solange der Anspruch einer
  abgestürzten Vorgängerin noch gültig ist.
- **Bekannte Grenze:** eine gleichzeitig auf beiden Instanzen eingehende Brücken-/Störungsmeldung kann zwei
  Webhook-Sendungen auslösen (nicht gelöst). MQTT-Client-Kennungen müssen je Instanz verschieden sein
  (`docs/self-hosting.md`, Abschnitt 6d).
- **Migrationen sind abgesichert:** Datenbank-Sperre vor jedem Alembic-Lauf (`GET_LOCK` bei MariaDB, `flock`
  bei SQLite, Wartezeit `THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS`, Vorgabe 60 s); Anleitungen für Swarm und
  Kubernetes: `docs/docker-swarm.md`, `docs/kubernetes.md`.

### Zuordnungskarte und Ventilbetriebsarten

- **Anordnung der Karte** (Positionen, Ausschnitt, eingeklappte Knoten) liegt je Benutzer auf dem Server (Tabellen
  `map_layout*`, Version gegen Konflikte, nur sichtbare Knoten); ein Administrator mit anlagenweitem `device.manage`
  kann sie als Vorgabe für alle hinterlegen. Der Schalter „technische Werte“ bleibt im Browser; ohne Netz greift der
  `localStorage` als Rückfall. Reine Darstellung, nichts davon beeinflusst die Regelung.
- **Betriebsart je Thermostatventil** (`thermostat_mode`: `controlled` / `self_regulating` / `reading_only`,
  `domain/thermostat_mode.py`): `reading_only` schreibt nur den Istwert (kein Sollwert, kein Ein/Aus, kein Notbetrieb-
  Eintrag, keine Übergabe); `self_regulating` an einem Ventil ohne beschreibbaren Sollwert schreibt gar nichts.
  `swap_device` setzt die neue Zuordnung bewusst auf `controlled`. REST/MCP kennen das Merkmal nicht.
- **Die Betriebsart folgt aus den Linien der Zuordnungskarte** (Zone → Ventil): nur Heizbefehl = `controlled`;
  Sollwert + Raumtemperatur = `self_regulating`; nur Raumtemperatur = `reading_only`; nur Sollwert nur an Geräten ohne
  Istwert-Eingang. Abgelehnt: Sollwert ohne Raumtemperatur (wo es einen Istwert-Eingang gibt), Heizbefehl zusammen mit
  Sollwert/Raumtemperatur, Raumtemperatur in einer Zone ohne Messquelle. Die Regeln liegen in der Domäne
  (`domain/mapping_map.py`: `can_connect`, `lines_for_mode`/`mode_for_lines`), nicht in der Oberfläche. Linie
  entfernen: letzte Linie löst die Zuordnung; `detach_device` schickt dem Gerät **keinen** Ausschaltbefehl.
- **Ein Gerät, eine Zone als Aktor** (Entscheidung des Eigners): ein Gerät, das schon Aktor einer anderen Zone ist,
  nimmt keinen zweiten Heizbefehl; mehrere Aktoren je Zone gehen, eine ODER-Regel gibt es nicht. Gilt nur für **neue**
  Zuordnungen, Bestandsdoppelungen bleiben bearbeit- und lösbar. **Ein Fühler** darf Messquelle mehrerer Zonen, ein
  Kontakt Fensterkontakt mehrerer Zonen sein.
- **Mehrere Eingabefühler je Zone** (Tabelle `zone_temperature_source`, `ZoneTemperatureSource`; die Spalte
  `zone.temperature_source_device_id` gibt es nicht mehr): Der Raum-Istwert ist der **Mittelwert der brauchbaren
  Fühler** (`domain/room_temperature.py`, gebildet in `services/ingest.py::advance_zone_state`; Mindestzahl 1, `measured_at`
  = jüngster Zeitpunkt der brauchbaren, keine Glättung, bei genau einem Fühler wie zuvor). Nur **reine Fühler** sind
  zulässig; Thermostate (Fähigkeit `thermostat`, z. B. BTH-RA) werden abgelehnt, weil ihr Wert der eingespeiste Echo wäre.
  **Festhängend** wird je Fühler gemeldet (der Fühler zählt weiter mit). **Fenstererkennung per Temperatursturz** wertet
  jeden Fühler mit eigener Historie einzeln aus, einer genügt; es gibt keinen Mittelwertverlauf. **Rückkehrprüfung** des
  Notbetriebs: ein rückwärts springendes `measured_at` wird nicht ein zweites Mal gezählt (`shadow_run._apply_sensor_failure`).
  Episoden führen bei mehreren Fühlern die Namen kommagetrennt, `source_device_id` ist dann NULL. Der Messwert-Ausgleich
  gilt einmal je Zone auf den Mittelwert. Schnittstellen: UI-API `POST/DELETE /zones/{id}/devices/sources[/{device_id}]`,
  REST `temperature_source_device_ids` (Liste ersetzt, `null` = unverändert). Downgrade der Migration behält je Zone nur
  den ersten Fühler. **Nicht an der echten Anlage abgenommen.**
- **Restlücke:** zwei gleichzeitige Zuordnungen desselben Geräts zu zwei Zonen sind ohne DB-Constraint nicht ganz
  auszuschließen (Zeilensperre `SELECT ... FOR UPDATE`, unter MariaDB von Hand geprüft: keine zweite Zeile, aber DB-
  Fehler 1020 statt sauberer Meldung; SQLite sperrt keine Zeilen, dort ungeprüft).
- Zwischen zwei Geräten gibt es nur einen Entwurf (nicht gespeichert, Server antwortet 422); Luftfeuchte, Anwesenheit,
  Helligkeit haben keinen Zonen-Eingang. Die Datenfluss-Ansicht (`domain/data_flow.py`) entscheidet allein über
  `thermostat_mode`. **Nicht an der echten Anlage ausprobiert:** Karte auf Handy/Touch, Datenfluss bei vielen Aktoren,
  `reading_only` an einem echten Ventil.

### Meross

- **Netzfehler bei der Anmeldung** (DNS, Timeout, `OSError`) werden wie eine Ablehnung behandelt (kein Transport, der Zyklus läuft weiter,
  Backoff eine Minute, nicht eskalierend). Vorher stoppte ein solcher Fehler vor `cycle()` die Regelung aller Zonen (Vorfall 2026-11-09).
  Offen: `meross_discovery` ohne Backoff bei Netzfehlern; der MQTT-Zustandsversand in `publishing.py` hat kein eigenes `try` (ein Fehler
  erreicht nur die äußere Schleife).
- Schaltbefehle an Meross-Steckdosen (Fußbodenkreise, mss710) über die Cloud; Zugangsdaten nur aus Konfiguration.
- **Anmeldung mit Backoff** im `MerossSessionCache` (exponentiell ab 1 min, gedeckelt bei 30 min; dauerhaft falsche
  Zugangsdaten springen sofort auf den Deckel). Ein gescheiterter Befehl verwirft die Sitzung nur noch, wenn der
  Broker sie ablehnt; Geräteabgleich und Schalten teilen eine Sitzung (etwa vier Anmeldungen am Tag). Der
  Ablehnungsgrund der Cloud erscheint im Schaltprotokoll.
- **Entschieden:** kein Sitzungstoken in der Datenbank (einem Passwort gleichwertig); `SESSION_TTL` bleibt 6 Stunden
  (Meross dokumentiert keine Lebensdauer). Meross-Antworten sind nicht maskiert/begrenzt (offener Sicherheitspunkt, s. o.).

### Zigbee2MQTT und Homebridge

- **Zigbee2MQTT:** Geräte und Messwerte per MQTT; Thermostate (Bosch BTH-RA) mit Echo-Regel und Übergabevertrag, EIN/AUS-
  Aktoren, Fensterkontakte, Außentemperatur-Quelle (genau eine für die Anlage, wie ein Zonensensor auf Alter/Ausfall
  geprüft).
- **Homebridge** (`docs/homebridge.md`): keine eigene Integration, `mqtt-thing`-Blöcke gegen dieselben Topics wie Home
  Assistant; `/interfaces` erzeugt den Block je sichtbarer Zone (Topics aus `states_topics`/`command_topics`, **nie**
  Zugangsdaten), Wächtertests halten Doku und Topic-Vertrag zusammen. `heatingCoolingStateValues` trägt thermoctls
  Vokabular (`off`/`manual`/`auto`); die Ist-Zustands-`apply`-Funktion nutzt `message.toString()`; die Temperatur
  bildet eine leere Nutzlast auf `undefined` ab (Zone ohne Messwert).

### MQTT und Home Assistant

- Optionale Integration per MQTT (Discovery, Zustände, Befehle), **keine** Voraussetzung. Zustände werden nur von der
  primären Instanz veröffentlicht. Entitäten u. a.: Außentemperatur, `state/window_open_by_temperature`,
  `state/override_active`, Befehl `command/cancel_override`, Befehl `command/temporary_setpoint` (nur für Brücken, nicht in der
  Discovery); nach einem von der Domäne **abgelehnten** Befehl geht der unveränderte Zustand zurück (nur die primäre Instanz),
  der Zustand nach einem Befehl wird mit frischem Zeitpunkt gemeldet; Störungen getrennt pro Art (`sensor:`, `fenster:`,
  `notbetrieb:`-Schlüssel). Wer den Webhook stilllegt, verliert den Problemsensor in Home Assistant nicht.
- **Schalter je Raum** (`zone.fault_notifications_enabled`, Vorgabe an, Recht `zone.manage`; UI, REST, MCP `set_fault_notifications`): unterdrückt nur die
  Webhook-Zustellung der Störungsmeldungen des Raums (Sensor, Notbetrieb samt Entwarnung, festhängender Messwert, Fenster-Alarm, Schaltbefehl);
  Anzeige und Audit (`suppressed`) bleiben, Home Assistant wird nicht berührt (Notbetrieb und Schaltbefehl gehen ohnehin nicht dorthin). Auflösung Meldung → Räume in `services/notice_routing.py`, fail-open (nicht auffindbar =
  zustellen); Gerät in mehreren Räumen: nur unterdrückt, wenn alle abgeschaltet sind. Mitten in einer Episode umgelegt: jede Meldung wird einzeln
  zum Versandzeitpunkt entschieden (Entwarnung ohne Störungsmeldung oder umgekehrt möglich, ohne Merker). Noch nicht an der Anlage abgenommen.
- Sechs Meldungsarten sind anlagenweit einzeln abschaltbar (Sensorstörung, Brücke/Broker weg, Schaltbefehl
  gescheitert, festhängender Messwert, Fenster-Alarm, Mieter-Problemmeldung), ab Werk an. Der Testknopf („send_test")
  schickt eine gekennzeichnete Meldung über denselben Weg (Abstand 10 s) und zeigt den Zustellzustand; das Audit
  unterscheidet `sent` und `suppressed`.

### REST, MCP und OAuth

- **REST** `/api/v1/...` mit Bearer-Token (Presets Read-only/Read-write, `control.arm` in keinem, `control.disarm` für
  `force_dry_run`); jeder Endpunkt steht in `docs/api.md` (Wächtertest). **MCP** (Werkzeuge in `docs/mcp.md`, Zahl per
  Test geprüft) per stdio oder HTTP (`THERMOCTL_MCP_MODE` = `off` Vorgabe / `path` / `port`, `MCP_PATH`, `MCP_PORT`,
  `PUBLIC_URL`, `MCP_PUBLIC_URL`; Start bricht bei Fehlkonfiguration ab).
- **OAuth** am MCP (Registrierung, `/authorize`, Zustimmung `/oauth/consent`, `/token`, `/revoke`): Zugriffs-Token 1 h,
  Refresh 30 Tage rotierend, Wiederverwendung sperrt die Familie; OAuth-Token gelten nur am MCP (REST: 401).
- Neues zu Fenster/Außenwert/Zeitplan-Vorschau erscheint bewusst nicht in REST/MCP (s. o.); Schaltprotokoll und Fenster-
  Parameter liegen in eigenen Grenzen (`WINDOW_ALARM_LIMITS`, `WINDOW_TEMP_DROP_LIMITS`), nicht im gemeinsamen `LIMITS`.
- Login: Authentifizierung ist Pflicht; geschützte Seiten leiten Browser auf `/login?return_to=…` (nur sicherer eigener
  Pfad, `safe_return_to`); `/api/` und `/mcp` antworten 401. Passkeys: hinter Ingress nur mit Hostnamen
  (`passkey_rp_id`/`passkey_origin`), reiner IP-Zugriff funktioniert grundsätzlich nicht (WebAuthn).

### Kiosk

- Ein Wandtablett nutzt eine Token-Einlösung `GET /kiosk/{token}` (Cookie, Weiterleitung auf `/app/kiosk`) und die
  Daten über `/kiosk-api`. Ansichten **Tafel** (scrollend, ab 600 px) und **Panel** (480×480, 2×N-Kacheln mit
  Zustandspunkt, Detailebene mit Sollwert, „Nächste Schaltung vorziehen", „Übersteuerung aufheben", Rückkehr nach 45 s).
  Die Wahl wird im Cookie `thermoctl_kiosk_ansicht` gemerkt (kein Einfluss auf Rechte).
- Kiosk-Token tragen `KIOSK_CONTROL_PERMISSIONS` (inkl. `override.cancel`); vor der Ergänzung ausgestellte Token
  brauchen eine erneute Ausstellung. Doppeltipp auf „Sollwert anheben" darf nicht zu einem falschen Endwert führen
  (Sperre während der Anfrage). Quelltextverweis (AGPL § 13) bleibt sichtbar.
- Ein **Praxistest** auf Touchgeräten und echten Wandtabletts steht aus.

### Betrieb: Docker, Add-on, Ingress

- Ein Container (Python, FastAPI; Node nur in einer Build-Stufe, Endabbild ohne Node). Das Abbild startet als root und
  gibt Rechte über `setpriv` an den Benutzer `thermoctl` ab (der Supervisor legt `/data/options.json` als root:root
  an); mit `user:` in compose bleibt es unprivilegiert. Mehrarchitektur `linux/amd64` und `linux/arm64`
  (`armv7` nicht). `/healthz` ist direkt erreichbar.
- **Add-on:** flaches Optionsschema (`secret_key`, `log_*`, `database_*`, `mqtt_*`, `meross_*`, `notify_webhook*`,
  `mcp_*`/`public_url`, `passkey_*`, dazu ein freies Feld `env` für jede weitere `THERMOCTL_*`-Variable;
  Reihenfolge: eigene Felder, `env`, gesetzte Umgebungsvariable gewinnt). Übersetzung in `docker/thermoctl_optionen.py`
  (`ABGEBILDETE_FELDER`/`BEWUSST_AUSGELASSEN`, gegen `Settings.model_fields` gewächtert); Rückrichtung
  `tools/env_nach_addon.py`. `panel_admin: false`; Port 8001 (MCP) optional.
- **Ingress:** der Präfix gilt **pro Anfrage** (Middleware `resolve_root_path`): nur wenn `X-Ingress-Path` exakt dem beim
  Start vom Supervisor erfragten Wert entspricht. Sitzungscookies folgen dem Präfix (`web/urls.py::cookie_path`). Ingress
  und direkter Reverse-Proxy-Zugang laufen gleichzeitig aus demselben Prozess.
- **Releases:** erst nach beiden Repositories fertig (Hauptrepo: Merge, Tag, Release, CI grün, `docker.yml`; Add-on:
  `version`, Changelog, `DOCS.md`, `pruefe-konfiguration.py`) -- Regel in `CLAUDE.md`. CI-Job `docker-build` baut das
  Abbild schon vor dem Merge; veröffentlicht wird nur durch `docker.yml` (Push auf `main` und `v*`-Tag, `latest` nur aus Tag).

### Datenbanken (SQLite, MariaDB) und Retention

- Datenbankagnostisch: kein ENUM/SET, keine JSON-Spalten; Wertemengen als CHECK-Constraints, Schema nur per
  Alembic. Die Anlage läuft auf **MariaDB**; SQLite ist Standard/Entwicklung. SQLAlchemy ist auf `<2.1` gepinnt
  (Umstieg offen).
- Die Testsuite baut ihr Schema über `create_all()`; was von Zeilen aus Migrationen abhängt (z. B. `cluster_claim`),
  braucht einen Test über `alembic upgrade head` (`tests/test_migrations.py`).
- **Retention** (`services/retention.py`): `shadow_decision_retention_days` Vorgabe **365**, `measurement_retention_days`
  Vorgabe 30; Vergleichszeilen und Aktorentscheidungen folgen der Entscheidungsfrist; das Schaltprotokoll hat Zeit- und Größengrenze (siehe oben). Das Lese-vor-Schreiben-Muster in `services/retention.py` kann unter SQLite selten einen
  Sperrfehler eines einzelnen Zyklus verursachen (bekannt, kein stiller Datenfehler).
- Die Übersicht liest je Zone die jüngste Entscheidung über `domain/zones.py::latest_decisions_by_zone` (gruppierte
  `MAX()`-Abfragen, Kosten proportional zur Zonenzahl) und `BulkSetpointContext` statt N+1; `next_switch` wird je Zone
  einzeln gerechnet (bewusst, einzige Stelle dieser Rechnung).

## Als Nächstes

1. **Mutationsläufe neu fahren** (siehe oben) und Wächter/Smoke-Tests nach dem Entfernen der Alt-Oberfläche
   gegenlesen.
2. **Notbetrieb abnehmen** (Plan Abschnitt 4; Wandfühler abklemmen, Ersatzquelle, Übergabe, Rückkehr).
3. Offene Entscheidungen oben (Fehlversuchszähler, Einrichtungsseite, Audit-Verdachtsliste, `AbsencePanel`) entscheiden.
