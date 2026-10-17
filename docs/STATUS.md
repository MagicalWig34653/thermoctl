# Stand

Letzte Aktualisierung: 2026-10-17. Diese Datei sagt, was **jetzt** gilt. Verlauf steht in `CHANGELOG.md` und in Git.

- **Letzte Freigabe:** 0.18.0 (Svelte-Oberfläche, siehe unten): Tag `v0.18.0`, GitHub-Release, Abbild auf ghcr.io (amd64 und arm64, `latest`), CI grün; Add-on-Repository wird nachgezogen.
- **Anlage:** läuft seit 2026-09-02 scharf mit `thermoctl`; das Altsystem (Host, vier Skripte) bleibt parallel als
  Rückfallebene und ist nicht abgelöst. Repository öffentlich (AGPL-3.0), Betrieb als Docker-Container und als
  Home-Assistant-Add-on (eigenes Repository `MagicalWig34653/thermoctl-addon`).
- **Migrationskopf:** `a7c3e91d5b24`.
- **Prüfstand:** keine Zahlen aus alten Läufen übernommen. Maßgeblich ist ein frischer Lauf (Ruff, mypy, Tests gegen
  SQLite und MariaDB, Alembic vorwärts/rückwärts, Docker-Bau); die Suite liest `THERMOCTL_TEST_DATABASE_URL`.

## Offene Entscheidungen (Projektinhaber)

Bestehende:

- **UI-Profil als Sichtbarkeitssperre:** Das Zielbild der neuen Oberfläche war „Sichtbarkeit folgt allein den
  Rechten". Das gilt heute nur für den Wochenplan (`schedule.manage` je Raum, Entscheidung des Projektinhabers
  2026-10-16). Geräte, Betrieb und Einstellungen, Personen und Gruppen, Berichte, Urlaub und Modi prüft der Server
  weiter zuerst am Gruppenprofil (`_require_admin_ui`/`_need_profile` in `thermoctl/web/ui_api*.py`, Navigation in
  `thermoctl/web/navigation.py`); ein Konto mit Mieterprofil sieht diese Bereiche auch mit passendem Recht nicht. Das
  ist das Verhalten seit 0.9.0 und bricht nichts. Offen: soll das Profil entfallen und allein das Recht entscheiden?
- **Impressum der öffentlichen Seite** (`site/`, GitHub Pages): bewusst **offen gelassen** (Projektinhaber,
  2026-10-10). Für eine öffentliche Seite aus Deutschland kann ein Impressum nötig sein; es wurde nichts
  erfunden. Die Seite setzt keine Cookies, speichert nichts im Browser und stellt keine Anfragen an Dritte
  (per Test abgesichert, `tests/test_landingpage.py`). Wird eines gewünscht: `impressum.html` und ein
  Fußlink im Generator (`tools/landingpage_erzeugen.py`) ergänzen, `site/` neu erzeugen.
- Das Paket `ghcr.io/magicalwig34653/thermoctl` ist öffentlich (Projektinhaber, 2026-10-10).
- **Phase 2 wirklich abschließen.** Ein Auszug der Produktivdatenbank wurde am 2026-09-04 gegen die drei
  Abnahmekriterien geprüft ([phase-2-abnahme.md](phase-2-abnahme.md)) -- **nicht abnahmereif**: Fünf der sechs Zonen
  wurden erst nach dem Scharfschalten angelegt und liefen nie im Schattenbetrieb; Kriterium 3 (Altsystemvergleich)
  entfällt ersatzlos, weil der Vergleichsbetrieb übersprungen wurde. Eine Zone braucht mehrere Tage Schattenbetrieb
  ab ihrer Anlage, bevor das Kriterium für sie erfüllbar ist.
- **Die Ablösung des Altsystems** (Host, vier Skripte) ist noch nicht vollzogen.
- **Automatische Übernahme der Home-Assistant-eigenen MQTT-Broker-Zugangsdaten** ins Add-on ist nicht gebaut
  (`services: ["mqtt:want"]` gewährt nur Zugriff auf die Supervisor-API und füllt `options.json` nicht von selbst).
- **Sicherheitsstand** ([sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md), am 2026-09-04 gegen
  den Code nachgeprüft): Von acht Hoch-/Mittel-Befunden sind sechs behoben oder teilweise behoben. **Weiterhin offen:**
  der MQTT-Befehlspfad (dokumentiert, im Code nicht erzwingbar -- Frage der Broker-Konfiguration), unbegrenzte/
  unmaskierte Meross-Cloud-Antworten, das unbegrenzt wachsende Schaltprotokoll, die HTTP-Netzwerkvorgabe (`0.0.0.0`
  ohne TLS-Erzwingung) und die CSRF-Ausnahme für Login/Logout. (Stand 2026-09-04; ob die SPA daran etwas ändert, ist
  nicht neu bewertet.)

Neu aus der Svelte-Umstellung:

- **Fehlendes Audit** bei der Ausstellung von API-Tokens und Kiosk-Tokens sowie bei `set_binding` und
  `configure_channel`. Dasselbe gilt für Setup, `jump_to_next_switch`, Boost und `send_test` (Testmeldung): sie
  schreiben bzw. lösen etwas aus, ohne dass ein Audit-Eintrag entsteht. Entscheiden: nachziehen oder bewusst lassen.
- **OAuth-Zustimmung** (`/oauth/consent`): serverseitig lassen (Clickjacking-Schutz, absolute Weiterleitung, Vorlage
  ohne Fremdcode) oder in die SPA holen.
- **Einrichtung (`/setup`)** als SPA-Seite mit `POST /ui-api/setup` -- derzeit als eigene Seite umgesetzt; Entscheidung
  über die endgültige Form steht aus.
- **Rechteeskalation über `user.manage` und `group.manage`:** wer Benutzer bzw. Gruppen verwaltet, kann sich Rechte
  verschaffen (Gruppenwechsel, Passwort für andere setzen). Gewollt oder zu begrenzen?
- **`group.manage` kann der letzte Inhaber sich selbst entziehen** -- danach gibt es niemanden mehr, der Gruppen
  verwalten darf. Sperre (letzter Inhaber) ja oder nein?
- **`audit.read` wirkt anlagenweit:** Audit und Schaltprotokoll zeigen auch Räume, die der Benutzer sonst nicht sieht.
- **Der Fehlversuchszähler der Anmeldung liegt im Prozessspeicher** und gilt je Instanz (mehrere Prozesse bzw. beide
  Instanzen im Verbund zählen getrennt; ein Neustart setzt ihn zurück).
- **Mutationsmessungen neu fahren:** `mutation/cosmic-ray-administration`, `-authz`, `-auth-sessions`, `-auth-kiosk`,
  `-schedule` u. a. wurden gegen den Stand vor dem Entfernen der Alt-Testfunktionen gemessen und sollten danach neu
  gemessen werden (Regeln dafür: `CLAUDE.md`, Abschnitt „Praktisches zur Agentenarbeit").
- **Praxistest der neuen Oberfläche an der echten Anlage steht aus:** Passkey-Registrierung mit echtem Authenticator,
  Ingress-Betrieb unter Home Assistant, Touchgeräte (Zuordnungskarte, Wochenplan, Kiosk).

## Oberfläche auf Svelte umgestellt (2026-10-17, freigegeben mit 0.18.0)

Die Jinja/HTMX/Bootstrap-Oberfläche ist entfernt (Auftrag des Projektinhabers, „die alte UI rausschmeißen"; Bedingung
„alle Funktionen in der neuen UI" laut zweiter Paritätsprüfung erfüllt, Belege `docs/ui-migration/FUNKTIONSMATRIX.md`).
Stand Worktree `ui/delete-legacy`, **nicht gemergt, nicht released, Version unverändert, Add-on-Repository unberührt.**

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

- **Entscheidung** trifft `decide()` in `thermoctl/domain/control_loop.py` als feste Vorrangkette; jede Entscheidung
  trägt einen Code und eine lesbare Begründung (Grundsatz 5). Vorrang des Sollwerts in `resolved_setpoint()`:
  Betriebsart „Aus" > laufende Übersteuerung > Urlaub > Zeitplan > Frostschutz.
- **Hysterese und Mindestschaltdauer** sind Pflicht (Altsystem hatte beides nicht). Ein Ventilschutzlauf, der seinen
  Vorrang mitten im Lauf verliert, hebt die Mindestschaltdauer nicht auf; `tests/test_control_loop_state_table.py` prüft
  die Vorrangkette erschöpfend.
- **PI-Regelung** je Zone optional (nur für Zonen mit gewöhnlichen Schaltaktoren). Invariante: Eine Phase, die unter
  Hysterese begonnen hat, hält deren Mindestdauer, egal wann und wie PI danach übernimmt; eine von PI begonnene Phase
  folgt PI's eigenen, weicheren Mindestdauern. Welcher Regler eine Phase begann, folgt aus der `shadow_decision`-
  Historie. Bei Sensorausfall, Frostschutz, Aus-Modus, Wiederanlauf und (bei gemischten Zonen) offenem Fenster ist PI
  ausgesetzt; die Begründung nennt dann PI's eigene Werte. PI kann je nach Einstellung mehr Schaltspiele erzeugen.
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
- **Riegel 3 -- Führung:** `cluster.is_leader` (Einzelbetrieb ohne Verbund-Datensatz gilt als führend, fail-open
  nur dort, weil die Suite ihr Schema nicht über Migrationen baut). Geprüft vor **jedem** Sendevorgang
  (`integrations/actuators.py::switching_allowed`).
- Sind alle offen, veröffentlicht der Dienst: Sollwerte an selbstregelnde Thermostatventile, Ein/Aus an gewöhnliche
  Zigbee2MQTT-Aktoren, Sollwert/`system_mode` an Zigbee2MQTT-Thermostate ohne eigene Regelung, Schaltbefehle an
  Meross über eine zwischengespeicherte Cloud-Sitzung (außerhalb jeder DB-Transaktion).
- **Ein gescheiterter Befehl wird jeden scharfen Zyklus erneut versucht** (einmal je Ausfallepisode geloggt;
  der Zwischenspeicher trägt das Ergebnis im Schlüssel). Für Meross gilt stattdessen der Anmelde-Backoff (unten).
- **Schaltprotokoll** (`device_command`, Recht `audit.read`): drei Ergebnisse `executed`/`suppressed`/`failed`
  (kein „verworfen"); Eintrag überlebt Löschen/Umbenennen von Zone oder Gerät (Namens-Momentaufnahme); keine
  automatische Aufbewahrung (bekannte offene Sicherheitsfrage, s. o.). REST/MCP lesen es noch nicht (bewusst).
- **Anlaufriegel:** `app.state.sending_allowed` liest nur `control_armed`; die Führung wird weiterhin je Sendevorgang
  geprüft.

### Führungsrolle und Aktiv-Bereitschafts-Verbund

- Zwei Instanzen teilen eine Datenbank und einen Broker; eine regelt, die andere übernimmt nach
  `setting.cluster_takeover_cycles` (Vorgabe 5) Zyklen Ausfall. Anspruch in `cluster_claim` (nur
  `thermoctl/services/cluster.py`), Wechsel per **einer atomaren `UPDATE`-Anweisung**, Zeit ist die der Datenbank
  (`func.now()`). `try_become_leader` schreibt zuerst und liest danach (SQLite-Sperrverhalten); SQLite läuft im
  WAL-Modus.
- Die Bereitschaft lässt den ganzen Regelzyklus aus (keine Entscheidungen, keine Veröffentlichung), bleibt aber am
  MQTT verbunden und nimmt Messwerte auf; Befehle (z. B. Moduswechsel) werden auf beiden angewandt, nur die
  Führende bestätigt zurück. Geordnetes Herunterfahren gibt den Anspruch sofort frei. Die Oberfläche zeigt
  „Bereitschaft".
- **Bekannte Grenze:** eine gleichzeitig auf beiden Instanzen eingehende Brücken-/Störungsmeldung kann zwei
  Webhook-Sendungen auslösen (nicht gelöst). MQTT-Client-Kennungen müssen je Instanz verschieden sein
  (`docs/self-hosting.md`, Abschnitt 6d).
- **Migrationen sind abgesichert:** Datenbank-Sperre vor jedem Alembic-Lauf (`GET_LOCK` bei MariaDB, `flock`
  bei SQLite, Wartezeit `THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS`, Vorgabe 60 s); Anleitungen für Swarm und
  Kubernetes: `docs/docker-swarm.md`, `docs/kubernetes.md`.

### Zuordnungskarte und Ventilbetriebsarten

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
  Kontakt Fensterkontakt mehrerer Zonen sein; mehrere Fühler → eine Messquelle bleibt abgelehnt.
- **Restlücke:** zwei gleichzeitige Zuordnungen desselben Geräts zu zwei Zonen sind ohne DB-Constraint nicht ganz
  auszuschließen (Zeilensperre `SELECT ... FOR UPDATE`, unter MariaDB von Hand geprüft: keine zweite Zeile, aber DB-
  Fehler 1020 statt sauberer Meldung; SQLite sperrt keine Zeilen, dort ungeprüft).
- Zwischen zwei Geräten gibt es nur einen Entwurf (nicht gespeichert, Server antwortet 422); Luftfeuchte, Anwesenheit,
  Helligkeit haben keinen Zonen-Eingang. Die Datenfluss-Ansicht (`domain/data_flow.py`) entscheidet allein über
  `thermostat_mode`. **Nicht an der echten Anlage ausprobiert:** Karte auf Handy/Touch, Datenfluss bei vielen Aktoren,
  `reading_only` an einem echten Ventil.

### Meross

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
  führenden Instanz veröffentlicht. Entitäten u. a.: Außentemperatur, `state/window_open_by_temperature`,
  `state/override_active`, Befehl `command/cancel_override`; Störungen getrennt pro Art (`sensor:`, `fenster:`,
  `notbetrieb:`-Schlüssel). Wer den Webhook stilllegt, verliert den Problemsensor in Home Assistant nicht.
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
  Vorgabe 30; Vergleichszeilen und Aktorentscheidungen folgen der Entscheidungsfrist; das Schaltprotokoll hat keine
  Aufbewahrungsgrenze. Das Lese-vor-Schreiben-Muster in `services/retention.py` kann unter SQLite selten einen
  Sperrfehler eines einzelnen Zyklus verursachen (bekannt, kein stiller Datenfehler).
- Die Übersicht liest je Zone die jüngste Entscheidung über `domain/zones.py::latest_decisions_by_zone` (gruppierte
  `MAX()`-Abfragen, Kosten proportional zur Zonenzahl) und `BulkSetpointContext` statt N+1; `next_switch` wird je Zone
  einzeln gerechnet (bewusst, einzige Stelle dieser Rechnung).

## Als Nächstes

1. **Svelte-Umstellung freigeben:** `ui/delete-legacy` prüfen (volle Suite gegen SQLite **und** MariaDB, Ruff, mypy,
   Frontend-Prüfung, Docker-Bau), die offenen Entscheidungen oben beurteilen, mergen, Version anheben, `CHANGELOG.md`,
   Tag und Release, danach das Add-on-Repository nachziehen.
2. **Praxistest der neuen Oberfläche** an der echten Anlage (Passkey, Ingress, Touch).
3. **Mutationsläufe neu fahren** (siehe oben) und Wächter/Smoke-Tests nach dem Entfernen der Alt-Oberfläche
   gegenlesen.
4. **Notbetrieb abnehmen** (Plan Abschnitt 4, zuerst im Schattenbetrieb) und **Phase 2** mit echten Betriebsdaten
   abschließen, danach die Ablösung des Altsystems.
5. Offene Sicherheitspunkte (Audit-Lücken, Rechteeskalation, Fehlversuchszähler) entscheiden und umsetzen.
