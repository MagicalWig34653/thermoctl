# Stand

Letzte Aktualisierung: 2026-10-09.

## Unveröffentlicht (nach 0.11.1)

Laufende Arbeit der Sitzung vom 2026-10-09: Sonnenabsenkung sichtbar machen, Glossar,
Verlaufsdiagramm auf der Zonenseite, Landingpage (GitHub Pages). Gebaut und gemergt ist bisher:

**Sonnenabsenkung im Schaltprotokoll.** Auslöser war eine Meldung: Wohnzimmer-Sollwert
laut Zeitplan 20,5 °C, Ist 19,5 °C, trotzdem von 08:00 bis 13:00 keine Heizanforderung. Ursache war
keine Übertragungsstörung, sondern die Sonnenabsenkung (−2,0 K, wirksam 18,5 °C); sie stand
nur im Fließtext von `shadow_decision.setpoint_reason`. Die Regellogik der Absenkung ist
**unverändert** (Projektinhaber, 2026-10-09: nur sichtbar machen, nicht ändern).
Bekannt und beabsichtigt: Die Absenkung prüft nicht, ob der Raum gerade zu kalt ist.

- `shadow_decision.scheduled_setpoint_c` (Sollwert vor der Absenkung) und `solar_setback_k`
  (angewandte Absenkung, NULL = keine), Migration `f6c3a8d19b42` mit Index
  `ix_shadow_decision_solar_setback`. Alte Zeilen bleiben NULL und werden nicht aus dem Text
  geraten. Migrationskopf: `f6c3a8d19b42`.
- Schaltprotokoll: dritte Eintragsart `entry_kind = absenkung` (`beginn`, `aenderung`, `ende`
  je Zone), abgeleitet per LAG aus aufeinanderfolgenden Schattenzeilen
  (`domain/solar_setback_log.py`), identisch in HTMX, REST und MCP. Die Einträge sind
  **abgeleitet, nicht gespeichert**: Sie folgen der Aufbewahrungsfrist von `shadow_decision`
  und dem heutigen Zonennamen; am Anfang der verbleibenden Historie kann eine laufende
  Absenkung als neuer „Beginn“ erscheinen. Absenkungen vor der Migration fehlen.
- Zonenauswahl im Schaltprotokoll enthält auch Zonen, die nur Absenkungseinträge haben.
- Der Test `test_the_operating_page_shows_notbetrieb_detail_and_comparison` arbeitet mit
  relativem statt festem Datum (er war seit dem 2026-10-09 rot, weil das 7-Tage-Fenster des
  Ersatzquellen-Vergleichs den fest gesetzten 2.10. abschnitt).

Review: Codex (`gpt-6-sol`) hat Commit `8562711` unabhängig geprüft, beide Datenbanken
5564 grün, 1 bekannter roter Test (inzwischen behoben); die beiden „sollte“-Befunde
(Zonenfilter, Herkunft der Einträge) sind umgesetzt.

## 0.11.0 / 0.11.1: Notbetrieb bei Sensorausfall

Plan und Gerätevertrag liegen lokal (`lokal/plaene/0.11.0-notbetrieb.md`,
`lokal/plaene/0.11.0-geraetevertrag.md`), die Übergabe unter
`lokal/plaene/0.11.0-uebergabe.md`. Der Notbetrieb ist vollständig gebaut und für
**alle** Zonen **automatisch aktiv**: Migration `d4a81c6e5b29` schaltet jede Bestandszone
ein, neu angelegte Zonen starten mit `sensor_failure_enabled = true` (gesetzt vom
ORM-Standardwert in `Zone`, nicht vom `server_default`). Das ist eine bewusste
Verhaltensänderung beim Upgrade und steht deshalb in CHANGELOG und Add-on-Changelog an
erster Stelle. Ein Downgrade über `d4a81c6e5b29` setzt die Spalte für alle Zonen auf
`false` (der Vorzustand wird nicht gemerkt). Migrationskopf von 0.11.1: `e5b92d7f3a18`.

### Architektur

- **Reine Domänenbausteine** (kein Datenbank-/Netzwerkzugriff, `now` und Vorzustand kommen
  vom Aufrufer): `domain/temperature_source_health.py` (Quellenbewertung, Ersatzquelle,
  Echo-Regel), `domain/emergency_operation.py` (Zustandsautomat), `domain/emergency_cycle.py`
  (Festtakt und Außenkennlinie), `domain/emergency_actuator_plan.py` (Vorrangtabelle je
  Aktorzuordnung: Übergabe, Takt, Rückstellung), `domain/emergency_display.py` (Hinweistexte),
  `domain/sensor_failure_policy.py` (Konfiguration, Validierung, wirksame Werte).
- **Schattenlauf und Publisher teilen sich dieselben Bausteine** (Grundsatz 6).
  `services/shadow_run.py` bewertet je aktivierter Zone pro Zyklus Quellen und Stufe, schreibt
  Stufe und Episode (`zone_sensor_failure_state`, `sensor_failure_episode`) und protokolliert
  die Aktorentscheidung mit `simulated=True` (`actuator_decision`, `simulated_*`-Felder in
  `actuator_emergency_state`). Die Zonenentscheidung (`ShadowDecision`) bleibt in **jeder**
  Stufe das, was `decide()` und PI ohnehin liefern; die Aktor-/Taktentscheidung wird nie in sie
  zurückgespeist. Nur in der Stufe `ersatzquelle` ändert sich die Zonenentscheidung bewusst:
  der korrigierte Ersatzwert gilt als Istwert (Sensorstatus `ok`). PI ist in jeder Stufe außer
  `normal` neutralisiert (eigenes Signal `sensor_failure_emergency_active`).
- **Scharfer Versand nur im Publisher** (`services/publishing.py`, `_send_emergency_*`;
  Adapter in `integrations/actuators.py`), weil nur dort bekannt ist, ob ein Versand gelang.
  Die scharfen Felder von `actuator_emergency_state` (ohne `simulated_`-Präfix) werden nur
  nach erfolgreichem Versand fortgeschrieben; `armed_episode_id` hält fest, welcher Episode
  sie zugeordnet sind. Auf den gewöhnlichen Pfaden (Sollwert, zentraler Ein/Aus, Ventilschutz,
  Fehlerwiederholung) schweigen Thermostate und Schaltausgänge einer Zone in `notbetrieb` und
  `rueckkehrpruefung` vollständig; bei Rückkehr werden die scharfen Felder einmal geräumt und
  der Wiederholungs-Zwischenspeicher (`PublicationState.valve_commands`/`.switch_commands`)
  für das Gerät invalidiert, damit der gewöhnliche Sollwert sofort wieder gesendet wird.
- **Gerätevertrag:** Schaltausgänge sind Fußbodenkreise (Meross mss710), Thermostate
  ausschließlich Bosch BTH-RA, kein Mischgerät. Ein Thermostat gilt nur dann als
  übergabefähig, wenn `occupied_heating_setpoint` und `operating_mode` schreibbar sind
  (`domain.self_regulating.handover_capable`, mit `manual` unter den deklarierten Werten).
  Ohne diesen Vertrag gibt es nie einen Schreibversuch, stattdessen genau einen
  Schaltprotokoll-Eintrag je Episode mit dem Ergebnis `decided_no_command`.

### Stufen

`normal` → `ersatzquelle` → `notbetrieb` → `rueckkehrpruefung` → `normal`. Eine Episode
überdauert Eskalation und jeden Rückfall aus `rueckkehrpruefung` und endet erst bei `normal`.
Der Wandfühler gilt als brauchbar bei Status `ok` und vorhandenem Messwert. Ist er es nicht,
gilt unter den Thermostat-Zuordnungen der Zone die **kälteste** um den Ausgleichswert
korrigierte Messung (`korrigiert = roh − Ausgleich`, `temperature_backup_offset_k` je
Zuordnung, Vorgabe 0 K); nur Rolle `actuator` mit Fähigkeit `thermostat` und ohne `switch`
kommt in Frage. Gibt es keine brauchbare Quelle, beginnt der Notbetrieb. Bei
`enabled=false` gilt in jedem Zustand sofort wieder `normal`; eine offene Episode wird dabei
geschlossen, ohne Entwarnung.

**Rückkehr:** mindestens zwei verschiedene Messzeitpunkte und die konfigurierte Dauer
(Vorgabe 60 s) durchgehend brauchbar, gezählt gegen genau eine Quelle (der Wandfühler hat bei
Gleichzeitigkeit Vorrang; wechselt das Gerät hinter der Ersatzquelle, beginnt die Zählung neu).
Derselbe Messzeitpunkt zählt nie zweimal. In `rueckkehrpruefung` fällt ein unbrauchbarer
Zyklus sofort auf `notbetrieb` zurück (gleiche Episode).

### Echo-Regel (Bosch BTH-RA)

Solange `thermoctl` eine externe Temperatur an ein Thermostat schreibt, ist dessen
`local_temperature` ein Echo, keine unabhängige Messung. Ein Messwert gilt erst als
unabhängig, wenn er frühestens 30 Minuten (`ECHO_INDEPENDENCE_DELAY`) nach dem letzten
`remote_temperature`-Sendeversuch entstanden ist und danach empfangen wurde. Als Sendeversuch
zählt alles außer `suppressed` (Trockenlauf), auch ein als gescheitert protokollierter, weil
dessen Nachricht das Gerät trotzdem erreicht haben kann
(`services/temperature_source_health.py`, `last_external_temperature_write_at`). Ein älterer
Messwert bleibt ein Echo, auch wenn seither mehr als 30 Minuten vergangen sind.

### Takt der Fußbodenkreise

`domain/emergency_cycle.advance` (Decimal, randbegrenzt, kein Nachholen). **Festtakt**
(Vorgabe 10 min an / 20 min aus) gilt, wenn der Außenwert fehlt oder unbrauchbar ist; sonst
die **Kennlinie** nach Außentemperatur (Vorgabepunkte −10 °C: 20 min an / 10 min aus,
0 °C: 10/20, 15 °C: aus). Eine nichtleere Kennlinie braucht mindestens zwei Punkte mit genau
einem oberen Aus-Punkt und nicht steigendem Tastgrad; leer heißt Festtakt. Die
Wiederanlaufsperre am oberen Kennlinienpunkt hat eine Hysteresespanne (Vorgabe 1 K) und ist
mit Taktquelle in `cycle_source`/`warm_locked` persistiert, hält also über Zyklen und Neustart.
Die Taktquelle wechselt nur am Paarbeginn. Der Takt läuft auch im Betriebsmodus „Aus" und bei
offenem Fenster. Phasen zählen erst ab **erfolgreichem** Versand: ein Ein-Fehler hält die
Phase bis zum nächsten erfolgreichen Versuch, ein Aus-Fehler verhindert dadurch automatisch
eine neue Ein-Phase.

### Einmal-Übergabe und Rückstellung der Thermostate

- **Übergabe** genau einmal je Episode und Zuordnung: `operating_mode: manual` und Notsollwert
  (Vorgabe 20 °C; Bosch 5–30 °C in 0,5-K-Schritten), danach schweigt `thermoctl`.
  `handover_attempted_at` wird **vor** dem Versand committet, auch bei Absturz gibt es keinen
  zweiten Versuch. Der Trockenlauf verbraucht den Versuch nicht. Das Auslösesignal sitzt in
  `zone_sensor_failure_state.handover_due_signalled` und übersteht einen Neustart.
- **Vorwert:** unmittelbar vor der Übergabe wird der vom Gerät zuletzt **gemeldete**
  `operating_mode` festgehalten (`handover_previous_operating_mode`, aus
  `DeviceProperty.last_value_text`; nicht das zuletzt Geschriebene). `NULL` heißt unbekannt,
  nie ein geratener Wert.
- **Rückstellung** bei Rückkehr genau einmal: der gemeldete Vorwert wird zurückgeschrieben
  (`restore_attempted_at` vor dem Versand). Ist der Vorwert unbekannt, wird nichts geschrieben
  und das einmalig begründet (`sensorausfall_rueckstellung_unbekannt`). Im Trockenlauf bleibt
  die Rückstellung offen (`armed_episode_id` gesetzt), bis ein scharfer Zyklus sie ausführt.
  Gescheiterte Versuche bleiben sichtbar und werden nicht wiederholt. Die dauerhafte Spur ist
  `actuator_decision`, auch wenn die Zuordnung danach geräumt wird.

### Mindestdauer-Anrechnung

Für Mindest-Ein-/Aus-Dauer zählt der reale Relaiszustand, nicht der simulierte.

- **Rückkehr:** `shadow_run._seed_recovery_phase_marker` schreibt in dem Zyklus, in dem die
  Episode endet, eine `ShadowDecision` mit Outcome `notbetrieb_rueckkehr_start` und dem zuletzt
  real erfolgreich gesendeten Zustand (`last_successful_command_state`). Die Haltedauer beginnt
  damit konservativ ab der Markierung, nie mit mehr Anrechnung als real erreicht. Nur bei genau
  einem Schaltausgang je Zone; bei keinem oder mehreren wird nichts erfunden.
- **Ersteintritt:** der reale Relaiszustand samt Sendezeitpunkt kommt aus dem Befehlslog
  (`services/emergency_prior.py`, von Schattenlauf und Publisher gleich benutzt). Nur ein
  ausgeführter Schaltbefehl zählt als Wissen. Ein eingeschaltetes Relais erfüllt zuerst seine
  Mindest-Ein-Dauer, bei bekanntem Aus-Zustand wird dessen Aus-Zeit angerechnet, bei
  unbekanntem wird Aus gesendet und die volle Aus-Dauer abgewartet.
- **Bekannte Grenze:** beim Ersteintritt sieht der Befehlslog keine Handschaltung am Gerät; ein
  von Hand geänderter Relaiszustand fließt nicht ein.
- `domain/statistics.py::heating_periods` sortiert bei gleichem `decided_at` nach
  `ShadowDecision.id`.

### Meldung

Je Episode genau eine Störungsmeldung beim Eintritt und eine Entwarnung bei echter Rückkehr,
keine bei Deaktivierung (`sensor_failure_episode.ended_reason_code`); Schalter ist der
bestehende `notify_sensor_faults`. `app.py::_emergency_notices` treibt den dreistufigen
Meldezustand `sensor_failure_episode.notification_state` (Migration `e5b92d7f3a18`):
`offen` → `melden_laeuft` → `gemeldet` → `entwarnung_laeuft` → `entwarnung_gesendet`. Der
Zwischenzustand wird **vor** dem Versand committet, der Endzustand danach. Bricht der Prozess
dazwischen ab, findet der nächste Lauf die Zwischenstufe und sendet **einmal** erneut, denn
lieber eine doppelte als eine fehlende Meldung. Die Texte nennen die tatsächliche Strategie
(`domain/fault_notice.py::emergency_entered_notice`/`emergency_resolved_notice`).
`_sensor_notices` überspringt Zonen mit aktivem Notbetrieb (keine Doppelmeldung); die
Home-Assistant-MQTT-Entität kennt nur `sensor:<zone id>`-Schlüssel, Notbetriebsmeldungen
(`notbetrieb:<episode id>`, `EMERGENCY_NOTICE_KEY_PREFIX`) werden dort ausgenommen.

### Anzeige, REST und MCP

- **Hinweise** (`domain/emergency_display.py::zone_banner`) auf Start, Wohnungs-Start und Kiosk
  (Tafel, Panel-Übersicht, Panel-Detail): „Ersatzquelle aktiv", „Notbetrieb: Fußboden taktet
  x/y min", „Thermostat regelt selbst", „Rückkehrprüfung läuft (n/2)". Die Texte für Bewohner
  nennen weder Kennlinie noch Festtakt noch eine rohe Gradzahl; diese Begriffe bleiben der
  Aktorentabelle der Betriebsseite vorbehalten. Der Kopfbanner prüft Notbetrieb vor Sensor und
  Fenster.
- **Betriebsseite** (`/control`, `services/emergency_state.py` als gemeinsame Lesung): je Zone
  Stufe, aktive Quelle, Sensor-Timeout, Außenwertqualität, Rückkehrfortschritt, je Aktor
  Takt-Phase, Frist, Übergabe- und Rückstellungsstatus (mit eigenem Zustand „versucht, Ergebnis
  unbekannt"), Sendefreigabe (scharf/Trockenlauf) und der Vergleich Ersatzquelle ↔ Wandfühler
  aus `sensor_failure_source_comparison` (mittlere Abweichung der letzten 7 Tage je
  Thermostat, Echo- und unbrauchbare Zeilen ausgeschlossen) mit vorgeschlagenem Ausgleichswert
  `roh − Wandfühler`. Eine Vergleichszeile entsteht je neuem Kandidaten-Messzeitpunkt, wenn
  Wandfühler und Kandidat Werte haben oder die Zone in `ersatzquelle` steht.
- **Schaltprotokoll** (`domain/device_commands.py::list_commands`) führt neben echten
  `device_command`-Zeilen die `actuator_decision`-Zeilen mit `action != "normal"` zusammen
  (`entry_kind` `befehl`/`entscheidung`, `simulated`), identisch in HTMX, REST und MCP.
- **REST:** `GET /api/v1/zones/{id}/emergency-state`,
  `GET/PUT /api/v1/control/sensor-failure-defaults`, `GET/PUT /api/v1/zones/{id}/sensor-failure`.
  **MCP:** `read_emergency_state`, `read/set_sensor_failure_defaults`,
  `read/set_sensor_failure_policy`. Rechte: Lesen `zone.read`, Schreiben `setting.manage` bzw.
  `zone.manage`, Ausgleichswerte zusätzlich `device.manage`; Zonenisolation wie bei `/state`.

### Konfiguration

- **Datenmodell:** Profile samt Kennlinienpunkten als echte Zeilen, dazu Quellenzustand,
  Episoden, Aktorlaufzustand und Aktorentscheidungen als relationale Tabellen (kein ENUM,
  Wertemengen als CHECK-Constraints). Historien bleiben bei Zonenlöschung mit Namenssnapshot
  erhalten. Vorgabeprofil „Notbetrieb Vorgabe": Festtakt 600/1200 s, Rückkehr 60 s und 2
  Messwerte, Wiederanlaufspanne 1 K, drei Kennlinienpunkte. Bei leerem Profilverweis gilt das
  Profil mit diesem Namen und kleinster ID; fehlt es, meldet die Domäne einen
  Konfigurationsfehler, statt ein Ersatzprofil anzulegen.
- **Regelparameter-Sektion der Zone** (`web/templates/parameter.html`): im bestehenden einen
  Formular mit einem Speichern-Knopf, das atomar speichert. Aktivierung, Profil (erben oder
  vorhandenes), eigener Notsollwert, Ausgleichswerte je Thermostat (nur mit `device.manage`),
  wirksame Werte mit Herkunft daneben.
- **Regelvorgaben-Karte „Notbetrieb bei Sensorausfall"** (`/settings`, `settings.html`): ein
  Speichern-Knopf für Festtakt, Rückkehrprüfung, Wiederanlaufspanne, anlagenweiten Notsollwert
  und die Außenkennlinie als Zeilen (`sensor_failure_curve.js`, wiederholte
  `curve_outdoor_c`/`curve_on_seconds`/`curve_off_seconds`-Felder). Die Eingabe wird vor jedem
  Schreiben vollständig validiert; ein ungültiger Punkt oder eine unvollständige Zeile verwirft
  alles. Aktive Festtakte werden bei Änderung von Mindestzeiten und Regelintervall erneut
  geprüft.
- **Aufbewahrung** (`services/retention.py`): `sensor_failure_source_comparison` und
  `actuator_decision` werden nach `shadow_decision_retention_days` gelöscht;
  `sensor_failure_episode` bleibt (eine Zeile je Störung).

### Bewusste Entwurfsentscheidungen

- **Ein-Frist nach fehlgeschlagenem Versand:** Die Phase zählt ab erfolgreichem Versand. Nach
  einem gescheiterten Ein-Versuch darf die Ein-Frist neu beginnen; eine Ein-Phase ist dabei nie
  länger als konfiguriert.
- **Der Warm-Aus-Punkt beendet eine laufende Ein-Phase nicht vorzeitig** (Projektinhaber,
  2026-10-03: unkritisch).
- **Keine Mehrprofilverwaltung** (Projektinhaber, 2026-10-02): die Zone wählt „Anlage erben"
  oder ein vorhandenes Profil; bearbeitbar ist nur das eine anlagenweite Profil über die
  Regelvorgaben-Seite.

### Offen und als Nächstes

0.11.1 enthält die Korrekturen aus dem ersten Blick auf die laufende Anlage: Ausgleichswerte
werden im Formular angezeigt und beim Speichern nicht mehr gelöscht, der Notbetriebs-Hinweis
nennt bei Thermostat plus Fußbodenkreis beide, einheitliche Zahlenschreibweise in den
Entscheidungsgründen (`thermoctl/domain/number_text.py`), PI-Grund „Abweichung" und Tastgrad
in Prozent, Schaltprotokoll-Layout bei langen Texten.

1. **Mutationsläufe** der Regellogik sind gelaufen (cosmic-ray, gültig, kein `INCOMPETENT`;
   Konfigurationen `mutation/cosmic-ray-emergency-*.toml`, `-temperature-source-health.toml`):
   `emergency_cycle` 502 Mutanten, `emergency_operation` 339, `temperature_source_health` 116,
   `emergency_actuator_plan` 58, `emergency_prior` 135. Nach gezielten Zusatztests überleben
   14 Mutanten, alle als äquivalent begründet (Signaturmutanten `*,` → `/,`, Grenzvergleiche der
   Interpolation, `.limit(1)` → `.limit(2)`, `//60` bei 1800 s). Die Notbetriebsteile von
   `services/publishing.py` sind nicht mutiert, sondern durch Handmutanten und
   `tests/test_publishing_notbetrieb_versand.py` abgesichert.
2. Abnahme an der echten Anlage (Plan Abschnitt 4), zuerst im Schattenbetrieb: Wandfühler im Bad
   abklemmen, Ersatzquelle (frühestens 30 min nach dem letzten `remote_temperature`), Notbetrieb,
   Übergabe `pause` → `manual` am BTH-RA beobachten, Rückkehr, Rückstellung `manual` → `pause`;
   Fußbodenkreis über mindestens zwei Taktpaare; Webhook-Meldung und Entwarnung prüfen;
   Gerätevertrag vorher mit frischem Dump gegenprüfen.
3. Bekannte Kleinigkeiten: Die Spalte „Ergebnis" im Schaltprotokoll zeigt bei Notbetriebs-
   Entscheidungen noch den Rohcode (z. B. `sensorausfall_takt_laeuft`) statt einer deutschen
   Bezeichnung; ein nie gesetzter Ausgleichswert wird beim ersten Speichern als 0,00 abgelegt
   (wirkt wie „kein Wert"); beim Ersteintritt in den Notbetrieb sieht der Befehlslog keine
   Handschaltung des Relais.

## v0.10.1

Enthält: ein Formular und ein Speichern-Knopf auf Zonen → Regelparameter (der
Temperatursturz-Schalter ging verloren), Minus/Plus der Sollwertknöpfe als SVG
(schriftunabhängig zentriert, Anlagen-Stellknopf 44 px), und drei PI-Korrekturen:
richtiger Entscheidungsgrund statt der Hysterese-Mindestdauer, Sensorausfall wird
von der PI-Freigabe unabhängig vom Reason-Code erkannt (sicherheitsrelevant), und
eine unter Hysterese begonnene Phase hält deren Mindestdauer auch nach einer
PI-Übernahme. Keine Migration. SQLAlchemy bleibt auf <2.1 begrenzt (Umstieg offen).
Add-on-Repository wird mit derselben Freigabe nachgezogen.

Nächstes: Notbetrieb bei Sensorausfall (Konzept lokal unter
`lokal/konzepte/sensorausfall-notbetrieb.md`, Entscheidungen im lokalen
Implementierungsplan festgehalten) und das Konzept für übersichtlichere Einstellungsseiten
(`lokal/konzepte/regelparameter/`, fünf Fragen offen).

## Regelparameter: zwei Speichern-Knöpfe auf Regelparameter zusammengeführt

Meldung nach 0.10.0: „doppelte Speichern-Buttons und z. B. eine Checkbox, die
nicht gespeichert wird" unter Zonen → Regelparameter (`/zones/{id}/parameters`).
Bestätigt und behoben: `parameter.html` hatte zwei `<form>`s mit je einem
„Speichern" — das Hauptformular und ein zweites, direkt darunter, nur für den
Schalter „Fenster aus Temperatursturz erkennen". Wer den Schalter umlegte und den
*anderen* Knopf drückte, verlor die Änderung. Jetzt ein Formular, ein Knopf; der
frühere eigene Endpunkt `/zones/{id}/window-temp-drop-detection` ist entfernt
(nur von dieser Seite benutzt, REST/MCP exponieren dieses Feld nicht und sind
unverändert). Die Domänenregel (`domain.zone_settings.set_window_temp_drop_detection`)
bleibt unverändert; der Handler ruft sie nur zusätzlich zu `save_control_parameters`
auf. `valve_protection_enabled`, `pi_confirm`, `pi_enabled` (inkl. `disabled`-Fall)
geprüft — kein weiterer Fehler, `pi_confirm` wird bewusst nur beim Aktivieren des
Häkchens verlangt (unverändertes, bereits korrektes Verhalten), `pi_enabled` bleibt
beim bereits gesetzten, inzwischen ungeeigneten Zustand absichtlich *nicht*
`disabled`, sonst würde ein Reload es über ein fehlendes Formularfeld stillschweigend
zurücksetzen.

Projektweite Suche nach demselben Muster (mehrere Formulare, die für den Nutzer
wie ein zusammenhängender Bereich aussehen): `settings.html`, `device_assignment.html`,
`tenant_start.html`, `schedule.html`, `tenant_schedule.html`, `users.html`,
`groups.html`, `account.html`, `controllers.html`, `control.html`, `zone_form.html`
durchgesehen — überall trägt jeder Knopf eine eigene, die jeweilige Aktion
benennende Beschriftung (z. B. „Sonnenabsenkung speichern", „Gerät tauschen",
„Rechte speichern") statt eines wiederholten, unbeschrifteten „Speichern"; kein
weiterer bestätigter Fall.

Neuer Browsertest `browser_tests/test_form_hygiene.py::test_at_most_one_visible_
speichern_button_per_page` (seitenweit, nicht mehr nur je Karte — genau die Lücke,
durch die dieser Fehler bisher gerutscht ist) sowie
`browser_tests/test_parameter_page_single_save.py` (echter Rundlauf: Checkbox
setzen, den einen Knopf drücken, in neuem Kontext neu laden). HTTP-Regressionstests
in `tests/test_daily_views.py`.


## Icon-Zentrierung in den Sollwert-Steppern

Meldung: "Auf der Übersichtsseite sind manche Icons in den Buttons nicht
zentriert." Betroffen: `.tc-stage` (Übersicht `/`), `.tc-stepbtn`
(Wohnungssicht-Startseite) und `.kiosk-stage` (Kiosk-Tafel) -- alle drei sind
dieselbe Ursache mit unterschiedlicher Schriftgröße, keine drei getrennten
Fehler. Kein anderer Icon-Knopf im Programm ist betroffen (`.tc-iconbtn`,
`.tc-bottomnav-item` u. a. tragen nur Beschriftungstext, kein Einzelzeichen).

**Ursache:** Weder `line-height` noch `display: flex` mit
`align-items: center` zentrieren die tatsächlich gezeichnete Tinte eines
Zeichens wie "−"/"+" -- beide zentrieren nur die *Zeilenbox*, die eine
Schrift aus ihrer eigenen Ascent-/Descent-Metrik bildet. Die Tinte sitzt
innerhalb dieser Box nicht zwingend mittig; eine reine
`Range.getBoundingClientRect()`-Prüfung (Zeilenbox) hätte den Fehler nicht
gezeigt, weil die Zeilenbox nach dem `flex`-Zusatz bereits exakt zentriert
war, während die Tinte darin unverändert 1,3 px (`.tc-stage`), 1,1 px
(`.tc-stepbtn`) bzw. 2,1 px (`.kiosk-stage`) zu tief blieb.

**Erste Behebung (Commit 029de74) war selbst fehlerhaft, per Kreuzreview
gefunden.** Sie glich die Font-Metrik-Asymmetrie über ein an der
macOS-Systemschrift (`-apple-system`, da `--font-ui`s erster Eintrag "Inter"
keine im Programm eingebundene Webschrift ist) kalibriertes, asymmetrisches
`padding-block` aus. Auf jeder anderen Schrift überkorrigierte das in die
Gegenrichtung: gemessen mit Arial dy = -2,25 px, Times New Roman -1,86,
Courier New -2,04, Georgia -0,97 -- am Kiosk-Wandtablett mit unbekanntem
Betriebssystem also potenziell eine größere Abweichung als der ursprüngliche
Fund, nicht kleiner.

**Jetzige Behebung:** die Zeichen "−"/"+" sind kein Text mehr, sondern ein
gemeinsames Inline-SVG (`stepper_icon()`-Makro, `thermoctl/web/templates/
icons.html`, in `start.html`, `kiosk.html` und `tenant_start.html`
importiert) mit fester, zu seiner eigenen Bounding-Box symmetrischer
Pfadgeometrie -- `display: flex` mit `align-items: center` zentriert dessen
*tatsächliche* Fläche, nicht mehr eine schriftabhängige Zeilenbox, und ist
damit unabhängig von Schrift oder Plattform richtig. Das SVG trägt
`aria-hidden="true"` und `stroke="currentColor"`; der zugängliche Name kommt
weiterhin allein vom `aria-label` des jeweiligen Knopfes ("Sollwert senken"
usw., unverändert) -- alle Tests, die über `get_by_label`/`get_by_role(...,
name=...)` suchen, sind davon nicht betroffen. Die font-spezifischen
`padding-block`-Werte sind vollständig zurückgenommen.

**Beiläufig behoben:** `.tc-stage` auf der Anlagen-Übersicht war mit
32×34 px unter der sonst im Programm geltenden 44-px-Mindesttippzielgröße
(siehe `.btn`s eigene Begründung in `thermoctl.css`) -- jetzt 44×44 px, ohne
Layoutbruch bei 1280 und 390 px (Screenshots angesehen).

Neuer Regressionstest `browser_tests/test_icon_centering.py`: misst die
Icon-Fläche (SVG-Bounding-Box bzw., für den historischen Nachweis unten,
`CanvasRenderingContext2D.measureText()`s `actualBoundingBox*`-Metriken)
gegen 1 px Toleranz, auf `/`, der Wohnungssicht-Startseite und
`/kiosk/{token}`, **jeweils unter vier verschiedenen Schriften** (Arial,
Times New Roman, Courier New, Georgia -- über `--font-ui` injiziert, keine
davon `-apple-system`), zusätzlich mit einer laufenden Übersteuerung im DOM
(damit die Diagnose das Verschwinden des Thermostats bei aktiver
Übersteuerung nicht mit einem falschen Treffer verwechselt). Ein eigener
Test baut den historischen, textbasierten Stand aus 029de74 auf einer
eigenständigen Seite nach (ohne die echte Anwendung dafür zurückzudrehen)
und belegt, dass er unter Arial die 1-px-Grenze reißt -- der Kreuzreview-Fund
bleibt damit reproduzierbar nachvollziehbar, auch nachdem der fehlerhafte
Code selbst nicht mehr existiert.

## PI-Regelung: eine allgemeine Invariante ersetzt die einzelnen Übergangs-Sonderfälle

Zweiter Kreuzreview desselben Fixes fand zwei weitere, blockierende Befunde, beide
in genau der Behandlung des Übergangs Hysterese→PI, die der Abschnitt darunter
noch als "konservativ behoben" beschreibt -- das war zu eng gedacht.

**Befund (A):** Gate-Resets (Fenster/Frost/Sensor/Ventilschutz) erhalten
`pi_last_control_armed` -- ein einmal abgeschlossener Scharfschalt-Schutz
(`needs_safe_start`) feuert danach nie wieder, egal wie oft ein Gate zwischenzeitlich
zurücksetzt. Eine unter Hysterese begonnene Phase, die während eines vorübergehenden
Gates (z. B. Sensorausfall) läuft, verlor damit ihren Schutz, sobald das Gate endete:
PI übernahm mit seinen eigenen, kürzeren Mindestdauern, unabhängig davon, wie lange die
Phase tatsächlich schon lief. Reproduziert für Sensor, Fenster, Aus-Modus/Frost und
Wiederanlauf. Zusätzlich konnte ein Gate-Reset sogar eine noch laufende
Scharfschalt-Wartefrist löschen (`reset_pi_state()` ohne `await_next_boundary` schreibt
`awaiting_boundary_until=None` -- vorher unbedingt, auch wenn schon eine Frist lief).

**Befund (B):** Die vorherige, einmalige Berechnung der Wartefrist (siehe Abschnitt
unten) griff nicht, wenn PI aktiviert wurde, *bevor* überhaupt etwas gehalten wurde --
die Berechnung fand nichts zu verlängern. Eine Phase, die danach unter Hysterese
begann, war nie geschützt.

**Diagnose der Hauptsession:** Die Behandlung an einzelnen Übergangspunkten war der
falsche Ansatz. **Ersetzt durch eine allgemeine Invariante**, in jedem Zyklus geprüft,
in dem PI wirksam würde -- nicht nur an einem Übergang: *Eine Phase (Ein oder Aus), die
unter Hysterese-Steuerung begonnen hat, wird mindestens für die Hysterese-Mindestdauer
gehalten, egal wann und auf welchem Weg PI danach übernimmt. Eine Phase, die PI selbst
begonnen hat, unterliegt PI's eigenen, weichen Mindestdauern.*

Welcher Regler eine Phase begonnen hat, wird aus der bereits vorhandenen
`shadow_decision`-Historie abgeleitet -- derselben Zeile, aus der `held_for_s` schon
kommt (`_previous_state()`s neuer vierter Rückgabewert `phase_started_by`, deren
`effective_controller`). **Keine Migration nötig.** Die neue Prüfung
(`_hysteresis_phase_still_holding()`) sitzt in `_pi_outcome`, direkt nach den
bestehenden Gates (Fenster/Frost/Sensor/Ventilschutz) und vor der eigentlichen
PI-Berechnung; der bisherige Sonderfall im `needs_safe_start`-Zweig (Abschnitt unten)
ist ersatzlos entfernt, nicht zusätzlich stehen geblieben. Der `_write_reset_state()`-Fix
(Befund A, zweiter Teil) bleibt eigenständig bestehen -- er schützt weiterhin die
Scharfschalt-Wartefrist selbst, unabhängig von der neuen Invariante.

**Am Anlagenverhalten ändert sich dadurch:** Eine unter Hysterese begonnene Phase wird
jetzt unter allen Umständen für die volle Hysterese-Mindestdauer gehalten -- unabhängig
davon, wie oft zwischenzeitlich ein Gate (Sensor, Fenster, Frostschutz, Aus-Modus,
Wiederanlauf) zurückgesetzt hat, und unabhängig davon, ob PI schon vor oder erst nach
Beginn der Phase aktiviert wurde. Eine von PI selbst begonnene Phase ist davon
unberührt und folgt weiterhin PI's eigenen, kürzeren Mindestdauern.

**Geändert:** `thermoctl/services/shadow_run.py` (`_previous_state()` liefert
`phase_started_by`; `_hysteresis_minimum_still_running()` durch
`_hysteresis_phase_still_holding()` ersetzt und in `_pi_outcome` als allgemeines Gate
verdrahtet statt in `needs_safe_start`; `_write_reset_state()` löscht eine laufende
Wartefrist nicht mehr; neue Konstante `PI_FALLBACK_HYSTERESIS_MINIMUM`),
`tests/test_shadow_run_pi.py` (Regressionstests für beide Befunde, je ein Test für
Sensor/Fenster/Aus-Modus-und-Frost/Wiederanlauf, vorher rot belegt durch gezielte
Deaktivierung der neuen Prüfung). Keine Migration.

Ruff, mypy, Pytest gegen SQLite **und** MariaDB, Zahlen im Bericht. Einzelmutanten von
Hand für die neue Invariante und den `_write_reset_state()`-Fix geprüft (kein neuer
vollständiger Mutationslauf, wie beauftragt).

## PI-Regelung: Sensorausfall wurde von PI übersehen, Übergang Hysterese→PI umgeht jetzt nicht mehr die laufende Mindestdauer

*Der hier beschriebene Übergangs-Sonderfall (`_hysteresis_minimum_still_running`) ist durch die allgemeine Invariante im Abschnitt oben ersetzt; gültig bleibt aus diesem Abschnitt der Sensor-Teil.*

Kreuzreview des Fixes unten (Commit 45ecf1a) fand zwei weitere Fehler, einen davon
sicherheitsrelevant.

**Behoben (sicherheitsrelevant):** `_pi_gate_reason()` in `services/shadow_run.py`
erkannte einen veralteten Messwert nur an `decide()`s eigenem Antwortcode
(`REASON_CODE_FROST_SENSOR_FAILURE`) -- der aber erst in Regel 6 vergeben wird. Regel 5
(die gewöhnliche Mindestschaltdauer) liegt davor und kann mit
`REASON_CODE_BLOCKED_MINIMUM_DURATION` zurückkehren, bevor Regel 6 den
Sensorfehler-Code je vergibt. Auf einem solchen Zyklus lief PI unbeirrt gegen den
normalen Sollwert und den veralteten Messwert weiter -- genau das, was Regel 1 (Rückfall
auf den Frostschutz-Sollwert bei Sensorausfall) verhindern soll. Reproduziert mit Soll
21 °C, Messwert 20,9 °C, Hysterese-Mindestdauer 300s, PI-Mindestdauer 60s: `would_heat`
kippt 60s nach dem Sensorausfall auf `True`, mit weiterhin `effective_controller="pi"`.
**Am Verhalten der Anlage ändert sich dadurch:** Bei einem veralteten Messwert
verhält sich eine PI-Zone jetzt exakt wie eine reine Hysterese-Zone -- Regelung gegen
den Frostschutz-Sollwert, keine PI-Entscheidung mehr, unabhängig davon, ob eine
Mindestschaltdauer diesen Zyklus zufällig ebenfalls geblockt hätte. Behoben durch einen
eigenen, direkt aus `Situation.sensor_status`/`measured_c` berechneten `sensor_failed`-
Parameter, der nicht mehr über `decision.reason_code` laufen kann und deshalb von Regel
5 nicht mehr verdeckt wird. Fenster-, Frostschutz- und Aus-Modus-Gates waren bereits
unabhängig von `decision.reason_code` berechnet und damit nie betroffen (jetzt mit
eigenen End-zu-End-Tests belegt statt nur angenommen).

**Ersetzt (Regellogik):** Dieser Abschnitt beschrieb ursprünglich einen einmaligen
Sonderfall im `needs_safe_start`-Zweig, der den Übergang Hysterese→PI korrigieren
sollte. Ein zweiter Kreuzreview fand diesen Ansatz unzureichend (zwei weitere,
blockierende Befunde) -- siehe den Abschnitt ganz oben in dieser Datei, der ihn durch
eine allgemeine, in jedem Zyklus geprüfte Invariante ersetzt. `_hysteresis_minimum_
still_running()` existiert nicht mehr; die Nachfolgefunktion heißt
`_hysteresis_phase_still_holding()`.

**Zwei Testkorrekturen** aus demselben Review: `test_falling_back_to_hysteresis_
keeps_the_accumulated_hold` behauptete in seinen Kommentaren falsche Zeiten (30s
Heizbeginn, nicht wie beschrieben) und bewies die fortlaufende Frist nicht wirklich --
beide Hypothesen ("ursprüngliche Frist" vs. "neu gestartete Frist ab dem Rückfall")
hätten auf dem alten Test identisch ausgesehen. Jetzt unterscheidet der Test explizit
zwischen der wahren Frist (t=390s, 300s nach dem echten Heizbeginn) und einer
hypothetisch neu gestarteten (t=420s) und beobachtet das Abschalten exakt bei t=390s.
`test_a_hysteresis_only_zone_is_unaffected` assertierte bedingt (`if outcome_code ==
...`) -- die Bedingung war bei den gewählten Zeiten immer wahr, jetzt unbedingt
assertiert.

**Geändert:** `thermoctl/services/shadow_run.py` (`_pi_gate_reason()` um `sensor_failed`
ergänzt statt `decision.reason_code`-basiert; neue Funktion
`_hysteresis_minimum_still_running()`; deren Verdrahtung in `_pi_outcome`),
`tests/test_shadow_run_pi.py` (neue Testklassen `TestNonBlockedReasonCodesKeepDecide
OwnReasoning`-Nachbarklassen für die Sensor-/Fenster-Kombination, `TestHysteresisMinimum
StillRunning`, Korrekturen an den beiden oben genannten Tests). Keine Migration.

Ruff, mypy, Pytest gegen SQLite **und** MariaDB, Zahlen im Bericht. Einzelmutanten von
Hand für die neue Gate- und Übergangslogik geprüft (kein neuer vollständiger
Mutationslauf, wie beauftragt).

## PI-Regelung: Begründung nannte die 300s-Hysterese-Mindestdauer statt PI's eigener Werte

Meldung des Projektinhabers: „Bei der PI-Regelung greift aktuell noch die 300s
Mindest-Ein- und -Ausschaltdauer, nicht die PI-exklusiven Werte."

**Belegt und eingegrenzt:** `would_heat` selbst folgt bereits den PI-eigenen, weichen
Mindestdauern (`Zone.pi_min_on_seconds`/`pi_min_off_seconds`) -- ein Test mit
Hysterese-Mindestdauer 300s und PI-Mindestdauer 60s zeigt eine echte Umschaltung nach
90s (`tests/test_shadow_run_pi.py::TestHysteresisMinimumDurationDoesNotGovernPi`).
`domain.control_loop.decide()`s Regel 5 bleibt bewusst reglerunabhängig (Modul-eigener
Kommentar) und beantwortet immer nur die reine Hysterese-Frage; `_pi_gate_reason()` in
`services/shadow_run.py` behandelt `REASON_CODE_BLOCKED_MINIMUM_DURATION` schon seit der
PI-Anbindung ausdrücklich als einen der Codes, die PI **nicht** blockieren
(`TestPiGateReasonClassifiesEveryReasonCode`) -- das war korrekt und ist unverändert.

Der tatsächliche Fehler saß eine Ebene höher: `_process_zone()` übernahm für die
gespeicherte `shadow_decision.outcome_code`/`.reason` unverändert `decide()`s eigene,
dann unzutreffende Antwort -- auf genau den Zyklen, auf denen PI eine Umschaltung
gegen die Hysterese-Mindestdauer durchgesetzt hat, stand dort weiterhin
„gesperrt_mindestdauer" mit „Mindestdauer 300s ... die Heizanforderung bleibt
unverändert", obwohl sich `would_heat` in derselben Zeile gerade geändert hatte.
Betriebsseite, Schaltprotokoll, REST-API und MCP-Server lesen alle dieselbe Spalte
unverändert weiter und zeigten deshalb genau den gemeldeten Eindruck.

**Behoben** in `services/shadow_run.py::_process_zone`: Sobald PI der wirksame Regler
ist (`effective_controller == "pi"`) und `decide()`s eigene Regel 5 mit der
Hysterese-Mindestdauer geblockt hätte, wird `reason_code`/`reason` vollständig aus PI's
eigenem Ergebnis aufgebaut (`REASON_CODE_HEATING`/`REASON_CODE_OFF` je nach
tatsächlicher Heizanforderung, Text aus PI's eigener Begründung), statt die
Hysterese-Antwort zu übernehmen. `outcome_code` liest jetzt `effective_decision
.reason_code` statt `decision.reason_code` -- in jedem anderen Zweig identisch, nur in
diesem einen Fall verschieden. Nebenbefund gleich mitbehoben: `_apply_decision_to_state`
klammerte den Ventilschutz-Marker vorher nicht ab, wenn PI eine solche Blockade
überstimmt hat (`decision.reason_code != REASON_CODE_BLOCKED_MINIMUM_DURATION` blieb
`False`, weil `replace()` den Code nicht mit anfasste) -- derselbe Fehlerfall wie der
am 2026-09-02 für reine Hysterese-Zonen behobene, jetzt auch für PI geschlossen.

**Übergänge mitten in einer gehaltenen Phase:**
- **PI → Hysterese** (PI wird ineligibel oder abgeschaltet, während ein Zustand noch
  hält): unverändert korrekt -- die Hysterese-Mindestdauer zählt ab dem tatsächlichen
  Beginn des Zustands, nicht ab dem Rückfall (`Situation.held_for_s` liest die
  *wirksame* `would_heat`-Historie, die PI's eigene Haltezeit bereits einschließt).
- **Hysterese → PI**: **war hier als bereits korrekt beschrieben -- das war falsch**,
  siehe den Abschnitt oben. Der Scharfschalt-Schutz allein garantiert nicht, dass die
  Hysterese-Mindestdauer der gehaltenen Phase bis zur nächsten Fenstergrenze bereits
  abgelaufen ist; das ist jetzt oben nachgezogen und behoben.

Beide Übergänge sind jetzt in `tests/test_shadow_run_pi.py::TestControllerTransitionsMidHold`
explizit für dieses Szenario abgesichert.

**Geändert:** `thermoctl/services/shadow_run.py`, `tests/test_shadow_run_pi.py`,
`mutation/cosmic-ray-shadow-run.toml` (Testbefehl fehlte `tests/test_shadow_run_pi.py`
-- ohne die Ergänzung hätte der Mutationslauf die geänderten Zeilen nicht durch die
neuen Tests geprüft). Keine Migration, kein Eingriff in `domain/control_loop.py` oder
`domain/pi_control.py` -- beide bleiben unverändert reglerunabhängig bzw. eigenständig
korrekt; nur die Zusammenführung und die daraus abgeleitete Begründung in
`services/shadow_run.py` war falsch.

Ruff, mypy, Pytest gegen SQLite **und** MariaDB (100 % Abdeckung, keine Fehlschläge).
Mutationslauf-Ergebnis siehe Commit/Bericht.

## v0.10.0

Freigabe mit: der Kiosk-Panel-Ansicht für 480×480-Wandtabletts (Übersicht und
Zonen-Detail, umschaltbar über `?ansicht=panel`/`tafel`/`auto`, gemerkt im
Cookie `thermoctl_kiosk_ansicht`), der bebilderten Doku
(`docs/bedienung.md`, `docs/wohnung.md`, README), dem Schutz gegen einen
doppelten Tipp auf „Sollwert anheben" im Kiosk, den nachgezogenen
Bediengeräte-Kanalbindungen (Anzeige und Speichern), abgefangenen
500er-Fehlern bei ungültiger Token-Gültigkeitsdauer, der mobilen Navigation
der Anlagensicht als feste Schublade (statt eines zweiten, versenkbaren
Kopfzeilen-Knopfs) sowie mehreren behobenen Zeilenumbrüchen und
Spaltenbreiten in Tabellen und Formularen (1280 px und 390 px). Details und
Beim-Upgrade-Hinweise in `CHANGELOG.md`. Keine Migration.

**Das Add-on-Repository muss nachgezogen werden** (`MagicalWig34653/thermoctl-addon`,
`thermoctl/config.yaml` auf `0.10.0`, Abschnitt in dessen `CHANGELOG.md`),
erst nach `docker.yml` für den `v0.10.0`-Tag.

**SQLAlchemy ist auf `<2.1` gepinnt** (CI zog sonst 2.1.1 und brach an fünf
mypy-Stellen in unveränderten Dateien); Umstieg auf 2.1 ist ein eigener Auftrag —
Befund: mypy scheitert an den fünf genannten Stellen, die Testsuite selbst läuft
mit 2.1.1 gegen SQLite unverändert grün (100 % Abdeckung, keine Fehlschläge).

## Nachbesserung Kreuzreview: Formularfelder bei 390px, Escape/Backdrop-Test

Zwei Nachbesserungen aus dem Kreuzreview der Navigations-Änderung unten:

- **`/controllers`, `/zones/{id}/devices`, `/settings` (Außentemperaturquelle):**
  Bootstraps `.row > *` gibt jedem Zeilenkind ohne eigene Breitenklasse volle
  Breite; das unqualifizierte `.col`/`.col-auto` hebt das aber -- anders als
  `.col-sm`/`.col-sm-auto` -- bei *jeder* Bildschirmbreite auf, nicht erst ab
  einem Umbruchpunkt. Bei 390px saßen mehrere Auswahlfelder dadurch
  nebeneinander statt gestapelt und zeigten nur ihren abgeschnittenen Text
  ("Sollwe", "Tei", "Qu", "Zo"). Durchweg auf `col-sm`/`col-sm-auto`
  umgestellt -- nur Klassen, keine Feldnamen, Werte oder `selected`-Bindungen
  angerührt. Die „Rollen"-Tabelle in `device_assignment.html` trug denselben
  Fehler an einer Tabelle statt einer `.row` (fiel erst beim Ansehen der
  Screenshots auf) und ist jetzt ebenfalls `.tc-stack-table`.
  Neuer Browsertest `browser_tests/test_form_field_width.py`: 390px, alle
  eindeutigen Anlagensicht-Routen, jedes sichtbare `select`/Text-`input`
  braucht ≥8rem Breite (Kriterium bewusst die Breite, nicht
  `scrollWidth`/`clientWidth` -- bei `select` unzuverlässig). Begründete
  Ausnahme: `date`/`time`/`datetime-local`/`month`/`week`/`color`/`range` --
  native Steuerelemente mit festem, nie abschneidendem Format.
- **`test_the_drawer_closes_when_a_navigation_link_is_chosen`:** prüft jetzt
  zusätzlich, dass nach der hx-boost-Navigation `document.body.style.overflow`
  wieder leer ist und kein `.offcanvas-backdrop` übrig bleibt -- beide Spuren
  von Bootstraps Hintergrundsperre, die eine Seite sonst dauerhaft gegen
  Scrollen sperren könnten, ohne dass eine sichtbare Schublade das erklärt.

## Anlagensicht mobil: Navigation als Schublade, Zeilenumbrüche behoben

„Navigation" (Kopfzeile) und „Mehr" (untere Leiste) klappten dieselbe `#tc-sidebar`
bisher per Bootstrap-`collapse` im Seitenfluss auf; stand die Seite vor dem Öffnen weit
gescrollt, lag die Leiste danach komplett außerhalb des Sichtbereichs (gemessen:
`sidebar.y = -2788` bei 390×844 auf `/devices`). Der Kopfzeilen-Knopf „Navigation" ist
jetzt entfernt; „Mehr" ist der einzige mobile Zugang und öffnet `#tc-sidebar` als
Bootstrap-Offcanvas (`offcanvas-lg offcanvas-start`, Bootstrap 5.3.3) -- eine fixe
Schublade über dem Inhalt, unabhängig von der Scrollposition, mit Hintergrund, Escape,
Tippen daneben und Schließen-Knopf; ein Klick auf einen Navigationslink schließt sie
zusätzlich (`page_scripts.js`, da Bootstrap das von sich aus nicht tut). Am Desktop
steht die Seitenleiste unverändert fest.

Zwei Stolperfallen dabei, beide erst durch einen echten Browser sichtbar geworden, nicht
durch HTML-Kontrolle:
- **Nicht** zusätzlich zur responsiven Klasse `offcanvas-lg` auch die unqualifizierte
  Klasse `offcanvas` verwenden -- die ist selbst nicht responsiv (`position: fixed;
  visibility: hidden` bei jeder Breite, ohne `@media`) und machte die Seitenleiste am
  Desktop unsichtbar.
- **Nicht** Bootstraps `offcanvas-body`-Klasse auf denselben Knoten wie die eigene
  `.tc-sidebar-content` legen -- deren Desktop-Rücknahme setzt `display: flex` ohne
  `flex-direction` und streckte die Marke „thermoctl" auf die volle Seitenleistenhöhe,
  neben statt über der Navigation. Sichtbar erst im Screenshot, nicht im HTML.

Die Breite der Schublade ist bewusst auf `min(20rem, 85vw)` begrenzt (Bootstraps
400px-Vorgabe, nur durch `max-width: 100%` gedeckelt, deckte ein 390px-Telefon randlos
ab -- dann blieb kein Hintergrund für „Tippen daneben" übrig).

Mobile Zeilenumbrüche mitten im Wort (`overflow-wrap: anywhere` trifft auf zu enge
Layouts) behoben in: `/zones`, `/interfaces`, `/settings`, `/groups`, `/tokens`,
`/kiosk-tokens`, `/audit`, `/controllers`, `/zones/{id}/devices` (Rollen-Zuordnung) --
jeweils `.tc-stack-table` (mit einer neuen, generischen `data-label`-Beschriftung für
Tabellen ohne eigenes Spezial-Layout) oder gestapelte `dt`/`dd`. Der Wochenplan
(`/zones/{id}/schedule`) bleibt als Woche nebeneinander, jetzt aber horizontal
scrollbar mit 9rem Mindestbreite je Tag statt schrumpfend bis zur Unlesbarkeit;
`schedule.js` misst weiterhin per `getBoundingClientRect()` und ist unberührt. Der
winzige "ändern"-Verweis in jedem Zeitplanbalken (`.schedule-mode-link`, eine
1,15rem-Box) bekam `white-space: nowrap`, weil sein Text sonst grundsätzlich --
unabhängig von der Bildschirmbreite -- mitten im Wort brach.

Ein neuer, generischer Browsertest (`browser_tests/test_mobile_word_wrap.py`) fährt bei
390×844 alle Anlagensicht-Ansichten aus `tools/screenshot_views.py` mit den
Doku-Demodaten ab und schlägt bei jedem Wortumbruch mitten im Zeichen sowie bei
horizontalem Dokumentüberlauf fehl (Ausnahmen: `pre`/`code`-Blöcke für wörtlich
kopierten Inhalt, die Startseiten-Zeitspur `.tc-zone-track`). Er hat bereits drei
weitere, bis dahin unbenannte Fundstellen aufgedeckt (`/controllers`,
`/zones/{id}/devices`, `.schedule-mode-link`).
## Formulare: fünf vergessene Bindungen auf /controllers, zwei Abstürze bei falscher Eingabe

Der Projektinhaber meldete "immer mal wieder doppelte Speichern-Knöpfe und
Checkboxen" und "Einstellungen, die nach Neuladen weg sind", ohne eine Seite
nennen zu können. Empirisch geprüft (echter Server, Doku-Demodaten, Formular
im rohen HTML bzw. per Playwright, nicht nur statisch gelesen): `/controllers`,
`/users`, `/tokens`, `/kiosk-tokens`, `/passkeys`, `/device-commands`,
`/vacation`, `/interfaces`, `/zones/{id}/devices`, `/zones/{id}`,
`/zones/{id}/parameters`, `/zones/{id}/setpoints`, `/modes/{id}`, `/settings`,
`/account`, `/schedule`, `/heating-time` (Wohnung) sowie alle 39 eindeutigen
GET-Ansichten aus `tools/screenshot_views.py` über den neuen generischen
Browsertest `browser_tests/test_form_hygiene.py`.

**Bestätigt und behoben:**
- **`/controllers`, Lese-Kanal-Formular:** `<select name="kind">` hatte keine
  `selected`-Bindung -- zeigte nach jedem Neuladen die erste Option
  (`zone_setpoint`), unabhängig vom gespeicherten Wert. Der nächste Speichern
  eines anderen Feldes schrieb diesen falschen Wert zurück und verwarf z. B.
  einen auf `operating_mode` konfigurierten Kanal.
- **`/controllers`, Schreib-Kanal-Formular:** `kind`, `source_device_id`,
  `zone_id` ohne `selected`, `fixed_text`/`fixed_number` ohne `value` --
  alle fünf gespeicherten Felder fehlten nach dem nächsten Laden.
  `configure_channel()` leert jetzt zusätzlich die Felder, die zur gewählten
  `kind` nicht passen (vorher blieb z. B. eine alte `zone_id` in der
  Datenbank stehen, wenn nur auf `fixed` umgestellt wurde, ohne dass die
  Seite das je gezeigt hätte).
- **`device_assignment.html`:** toter, durch `{% if false and controllers %}`
  abgeschalteter zweiter "Tastenbelegung"-Editor entfernt (Zeilen 184-253);
  keine eigene, nur dafür vorhandene Kontextdaten im Handler gefunden.
- **`/tokens` und `/kiosk-tokens`:** Ein nicht-numerisches `valid_days` (das
  `<input type="number">` verhindert das im Browser, ein Werkzeug oder ein
  manueller POST nicht) ließ `int(valid_days)` ungefangen durchschlagen --
  Absturz mit 500 statt einer Fehlermeldung im Formular. Beide Stellen fangen
  jetzt `ValueError` und zeigen "Die Gültigkeit muss eine Zahl von Tagen
  sein." am Feld.

**Geprüft, kein Fund:** Gruppen-Rechte (bereits an anderer Stelle in Arbeit),
alle Formulare, die `form.html`s Makros (`text_field`, `number_field`,
`select_field`, `toggle`) benutzen -- diese binden `value`/`selected`
grundsätzlich korrekt; der Fehler oben saß genau in den beiden
Formularblöcken, die das nicht tun. Kein `hx-swap="afterend"`/`"beforeend"`
im ganzen Projekt (Risiko für sich duplizierende HTMX-Fragmente); die beiden
tatsächlich genutzten `hx-get`/`hx-swap="outerHTML"`-Polling-Stellen
(`start.html`, `tenant_start.html`) tauschen ein Element durch sich selbst
aus, verdoppeln nichts.

**Neuer Test:** `browser_tests/test_form_hygiene.py` -- für jede eindeutige
Anlagen- und Wohnungs-Ansicht aus `tools/screenshot_views.py`: höchstens ein
sichtbarer, gleichlautender Speichern-Knopf je Formular, kein sichtbares
Eingabefeld mit demselben Namen doppelt im selben Formular (Checkboxen/Radios
ausgenommen), keine Beschriftung "Speichern" zweimal im selben
`.tc-panel`/`.card`/`section`. `/controllers` ist dort ausdrücklich
ausgenommen (viele unabhängige Ein-Zeilen-Formulare je Karte sind dessen
Layout, kein Fehler).

## Verwaltungstabellen bei 1280 px und mobil

Schaltprotokoll, Benutzer und Geräte bleiben mit den Doku-Demodaten innerhalb ihrer
Zellen. Die frühere Schaltprotokoll-Korrektur (`9a2701e`) griff zwar: `table-layout:
fixed` und Prozentbreiten waren aktiv. Bei 1280 px blieben aber nur 955 px Tabellenbreite;
15 % für den Zeitpunkt enthielten nach Zellpolster nur 118 px für rund 152 px Text.
`nowrap` ließ ihn in die Quelle laufen; das globale `overflow-wrap: anywhere` trennte
Wörter in den ebenfalls zu knappen Nachbarspalten.

Jetzt haben Schaltprotokoll und Benutzer feste, am Inhalt samt Zellpolster bemessene
Spalten und am Desktop ein Mindestbudget von 59,5 rem im vorhandenen Scrollcontainer.
Datum und Uhrzeit dürfen am Leerzeichen umbrechen, Quellen und Benutzeraktionen bleiben
einzeilig. Mobil nutzen beide wie die Geräte `.tc-stack-table`. Lange Gerätestatuschips
brechen innerhalb der Zelle an Leerzeichen um; vorher ragte ihr `nowrap` über die feste
Statusspalte hinaus. Die globale Umbruchsicherung bleibt erhalten.

Der gemeldete Gruppenfehler ist im genannten Bild nicht sichtbar (Editoren geschlossen)
und bei 1280 px auch mit geöffneten Editoren nicht reproduzierbar. Keine Änderung dort.
Browsertests prüfen Wortfragmente, Zellgrenzen und Bedienelemente mit den echten
Doku-Demodaten bei 1280/390 px; die Gruppenrechteauswahl zusätzlich geöffnet bei 1280 px.

## Gewöhnliche Tests ohne Playwright

Die Screenshot-Ansichtenliste liegt in `tools/screenshot_views.py` und braucht nur
die Standardbibliothek. Doku-Konsistenztest und Screenshot-Werkzeug verwenden dieselben
Daten. Ein Regressionstest führt den echten Doku-Abgleich in einem Unterprozess mit
gesperrtem Playwright-Import aus und prüft auch die Server- und Seed-Importkette.
Playwright bleibt ausschließlich im Extra `browser-tests`.

## Kiosk: eine Panel-Ansicht für 480×480-Wandtabletts, neben der bisherigen Tafel

Gemessen an der laufenden 0.9.4-Anlage kam `/kiosk` mit sechs Zonen auf einem
480×480-Feld (3,95", z. B. Sonoff NSPanel Pro Gen2) ohne Scrollen auf genau eine
sichtbare Kachel -- das Raster brach erst ab 20rem je Kachel um, und für die
Kopfzeile fehlte jeder `@media`-Block. Jetzt (Teil von 0.10.0) gibt es dafür
eine zweite Ebene:

- **Übersicht:** festes 2×N-Raster, sechs Kacheln passen bei 480×480 ohne
  Scrollen. Je Kachel nur Raumname, Ist-Wert, Soll-Wert und ein kleiner
  Zustandspunkt (`.kiosk-status-dot`) -- keine Zeitplan-Zeile, keine Knöpfe.
- **Detail:** Tippen auf eine Kachel öffnet eine flächendeckende Ebene für genau
  diese Zone (großer Ist-Wert, Soll-Stellglied, "Nächste Schaltung vorziehen",
  ggf. "Übersteuerung aufheben", ein Zurück-Knopf). Rückkehr durch Zurück oder
  automatisch nach 45 s ohne Bedienung.
- **Umschaltung** über `?ansicht=panel`/`?ansicht=tafel`/`?ansicht=auto`
  (`auto` entscheidet die Bildschirmbreite bei 600 px, `kiosk_panel.js`,
  `matchMedia`), gemerkt in einem eigenen Cookie
  (`thermoctl_kiosk_ansicht`, `thermoctl/web/kiosk_views.py`) -- unabhängig vom
  Kiosk-Cookie selbst und ohne jeden Einfluss auf Token, Rechte oder Sitzung.
- **Ohne JavaScript** bleibt es bei der ursprünglichen, scrollenden Tafel-Form:
  `data-ansicht-aktiv` an `<body>` wird ausschließlich von `kiosk_panel.js`
  gesetzt, jede Panel-Regel in `thermoctl.css` hängt daran. Absichtlich, siehe
  `thermoctl/auth/kiosk.py::kiosk_csrf_protection` -- die drei Formulare (Soll,
  Boost, Übersteuerung aufheben) laufen ohne Skript weiter als gewöhnliche
  Formularübermittlung.
- **Ein allgemeiner Fehler wurde mitbehoben, nicht nur im Kiosk:** `.btn` hatte
  eine Mindesthöhe von 42 statt 44 px (`thermoctl/web/static/thermoctl.css`) --
  betraf "Nächste Schaltung vorziehen" und "Übersteuerung aufheben" überall im
  Programm, nicht nur am Wandtablett.
- Keine neue Route, keine Migration, kein Eingriff in `thermoctl/domain/kiosk.py`
  oder die Rechteprüfung der vier bestehenden Kiosk-Endpunkte.

Die Panel-Ansicht ist jetzt in der Bedienungs- und Self-Hosting-Dokumentation bebildert.
Tafel und Panel zeigen in der Kopfzeile keinen Produkt-Schriftzug, sondern nur den
weiterhin direkt sichtbaren Quelltext-Link (AGPL-3.0) und die Uhrzeit; auch der
Panel-Detaildialog bietet den Quelltext-Link direkt an.
Die Kiosk- und Mieter-Steppertests verwenden einen durchgehend gültigen Zeitplanmodus mit 21 °C, die Kiosk-Layouttests Übersteuerungen ohne Ablaufzeit und die Urlaubstests relative Datumsbereiche, damit ihre Erwartungen unabhängig von Uhrzeit, Wochentag und Datum gelten.

Kreuzreview erfolgt (2026-09-26, kein Blocker; Auth/CSRF/Domäne unverändert
bestätigt).

**Kreuzreview-Nachtrag: doppelter Tipp auf "Sollwert anheben" konnte
folgenlos verschwinden.** Alle drei Kiosk-Formulare (Sollwert, Boost,
Übersteuerung aufheben) tauschen `#kiosk-body` vollständig aus
(`hx-swap="outerHTML"`); nichts hinderte einen zweiten Tipp daran, den Knopf
noch vor dem Austausch erneut zu treffen. Nachgestellt mit zwei
`HTMLElement.click()`-Aufrufen auf denselben Knoten ohne jede Verzögerung
(nicht mit `Locator.click()` -- das wartet selbst auf Aktivierbarkeit und
verdeckt den Fehler dadurch): zuverlässig in allen Versuchen landete der
zweite Tipp folgenlos bei 34,5 → 35,0 statt der erwarteten Ablehnung bei
35,5 -- genau der gemeldete Befund.

Entscheidung: kein Tipp soll unbemerkt zu einem falschen Endwert führen,
aber ein Tipp während einer laufenden Anfrage darf sichtbar ins Leere
greifen. `hx-disabled-elt` sperrt jetzt den Knopf (beim Sollwert-Formular
über ein `<fieldset style="display: contents">`, da `find` nur den ersten
Treffer liefert und beide Tasten gemeinsam gesperrt werden müssen; Boost und
Übersteuerung-aufheben haben je nur einen Knopf) für die Dauer der eigenen
Anfrage -- Bootstraps `fieldset:disabled .btn`/`.btn:disabled` liefert die
sichtbare Sperre ohne eigene CSS-Regel. `hx-sync="this:queue first"` sichert
zusätzlich gegen doppelte Anfragen vom selben Formular ab. Ein gesperrter
Tipp ist nicht dauerhaft verloren: ein späterer, echter Tipp wirkt normal.
Ohne JavaScript unverändert eine gewöhnliche, sequenzielle
Formularübermittlung. Kein Eingriff in `thermoctl/auth/`, `thermoctl/domain/`
oder die Rechte- und CSRF-Prüfung der Kiosk-Endpunkte.
`browser_tests/test_kiosk.py::test_a_double_tap_on_raise_is_locked_out_instead_of_racing`
prüft das deterministisch (CPU-Drosselung via CDP zusätzlich gesetzt, ändert
das Ergebnis aber nicht), zehnfach hintereinander grün.

## Doku mit Bildern, Screenshot-Werkzeug, drei Fehler aus dem Hinsehen

`tools/screenshots.py` nimmt jede GET-Ansicht der Weboberfläche auf — Anlagensicht,
Wohnungssicht, Einrichtung, Anmeldung, Kiosk — gegen einen eigenen Server mit erfundenen
Demodaten (`tools/screenshot_seed.py`). Versioniert sind nur die 40 Bilder, die die Doku
einbindet (`--doku` → `docs/bilder/`, 3,9 MB); der volle Satz geht ins ignorierte
`var/bilder/`. `browser_tests/test_screenshots.py` meldet jede neue Route ohne Aufnahme,
`tests/test_docs_current.py` hält Kennzeichen und Einbindungen in beide Richtungen
zusammen. Chromium läuft mit `--lang=de-DE`, sonst zeigen die Zeitfelder AM/PM.
Schreibtisch-Aufnahmen sind auf 1280 × 900 Pixel begrenzt und zeigen bei Bedarf einen zweiten Sichtbereich; das Schaltprotokoll enthält sechs vielfältige Demoeinträge, während Messwerte und Regelentscheidungen vollständig bleiben.
Mobile Aufnahmen zeigen den Sichtbereich (390 × 844 Pixel, bei Bedarf mit einer zweiten Aufnahme weiter unten), weil feste Elemente in Ganzseitenaufnahmen verrutschen.

Neue Doku: `docs/bedienung.md` (beide Oberflächen, für den Betreiber) und
`docs/wohnung.md` (nur Wohnungssicht, zum Weitergeben an Bewohner). Dabei kamen zwei
bisher unbeschriebene Funktionen zum Vorschein: der anlagenweite Urlaub und der Regler auf
der Zuhause-Seite, der die normale Temperatur des laufenden Zeitplanabschnitts dauerhaft
ändert. Dieser Regler ist nur sichtbar, solange keine vorübergehende Änderung läuft — das
ist kein ausdrücklicher Schalter, sondern folgt daraus, dass jede Übersteuerung einen
Sollwert ohne `mode_id` liefert (`domain/schedule.py`, `tenant_start.html`). Wer daran
etwas ändert, ändert die Sichtbarkeit mit.

`tests/test_user_visible_effect_texts.py` hat zwei Sätze der neuen Doku abgewiesen, die
eine bestätigte Heizwirkung behaupteten, wo die Anwendung nur Entscheidungen kennt
(Heizstatistik, Startseitenchip). Beide umformuliert, nicht ins Verzeichnis eingetragen.

Beim Ansehen der Bilder gefunden und behoben:
- `/controllers`: Die Vorlage verglich Geräte-IDs mit Zonen-IDs (`manageable_ids`); jetzt
  eigene Menge `manageable_device_ids` mit demselben Join wie `_managed_device()`.
- `/controllers`: Ein Raumfühler hängt nur über `zone.temperature_source_device_id` an der
  Zone (bewusst keine Rolle `sensor`); `_devices_in()` las nur `ZoneDevice` und bot ihn
  deshalb nie an, `_require_readable_device()` wies ihn mit 404 ab. Beide lesen jetzt
  beide Quellen, gebunden an Zonen mit `device.read`.
- `.tc-roomtabs` brach lange Raumnamen mitten im Wort; jetzt einzeilig, seitlich scrollend.

Offen:
- `browser_tests/test_start_page_live.py::test_an_open_override_area_and_a_started_input_survive_a_refresh`
  schlug einmal unter Last fehl und ließ sich danach nicht reproduzieren.
- Die Gesamtabdeckung erreicht 100 % nur mit erreichbarer MariaDB:
  `tests/test_migration_lock.py` deckt `thermoctl/db/migration_lock.py:122-148` ab, und
  zwar gegen die lokale MariaDB, unabhängig von `THERMOCTL_TEST_DATABASE_URL`. Ein reiner
  SQLite-Lauf ohne MariaDB zeigt 99 % — das ist kein Befund.
## v0.9.5: eine Anwendungsversion ohne Anwendungsänderung, für das Add-on

Das Add-on hat `panel_admin: false` bekommen (Commit `8f8bd53` im Add-on-Repository):
der Seitenleisten-Eintrag in Home Assistant erscheint damit auch Nutzern ohne
Home-Assistant-Administratorrechte. Bisher galt die Supervisor-Vorgabe `true`, und
Nicht-Administratoren sahen thermoctl nicht -- obwohl die Rechteprüfung ohnehin bei
thermoctls eigener Anmeldung liegt. Home Assistant liefert eine geänderte
`config.yaml` aber erst mit einer neuen `version` aus, und die muss zu einem
vorhandenen ghcr.io-Abbild passen (siehe "Eine Freigabe ist erst fertig, wenn auch das
Add-on nachgezogen ist"). Deshalb 0.9.5 hier mit demselben Inhalt wie 0.9.4: nur
`pyproject.toml`, `thermoctl/__init__.py`, `CHANGELOG.md` und dieser Abschnitt.
Keine Migration, kein Quelltext berührt.

## Die Meross-Anmeldesperre war selbstverursacht -- Backoff, geteilte Sitzung, sichtbarer Grund

Meldung aus dem echten Betrieb: Schaltbefehle an zwei Meross-Steckdosen gingen nicht
mehr durch, „ungültige Meross-Sitzung". Das Protokoll zeigte 16 abgelehnte
Anmeldeversuche in 8 Minuten im konstanten Abstand von 32 Sekunden, alle mit
`apiStatus=1301, Beyond Login Limit`. Ursache war eine sich selbst erhaltende
Schleife: ein gescheiterter Schaltbefehl verwarf die zwischengespeicherte Sitzung
(`services/publishing.py`), der nächste Zyklus meldete sich deshalb neu an, die Cloud
lehnte wegen der Anmeldesperre ab, was wiederum jeden Meross-Befehl dieses Zyklus
scheitern ließ -- zurück zum Anfang, alle 32 Sekunden, ohne dass die Anlage von
selbst wieder herauskam.

Vier Behebungen, alle in `services/meross_session.py`, `services/meross_discovery.py`,
`integrations/meross.py`, `integrations/actuators.py` und `services/publishing.py`:

- **Backoff nach einer abgelehnten Anmeldung**, im `MerossSessionCache` selbst
  (keine neue Tabelle -- der Zwischenspeicher lebt ohnehin nur je Prozess, ein
  Neustart verwirft ihn wie vorher). Exponentiell ab einer Minute, gedeckelt bei
  30 Minuten -- lang genug, um eine echte Anmeldesperre nicht mit jedem Zyklus neu
  zu verlängern, kurz genug, um eine kurze Störung nicht unnötig lange nachwirken zu
  lassen. `apiStatus`-Werte, die eine dauerhaft falsche Zugangsdaten-Kombination
  anzeigen (`integrations/meross.py::is_permanent_login_failure`), springen sofort auf
  die Obergrenze statt sich dorthin hochzutasten -- ein falsches Passwort wird durch
  Warten nicht richtiger.
- **`invalidate()` wird nicht mehr bei jedem gescheiterten Befehl aufgerufen**, nur
  noch wenn `MerossSwitch.switching()` die Ursache selbst als Ablehnung durch den
  Broker erkennt (`SwitchResult.session_fault`). Ein `MerossError` aus
  `_transport.send()` (Gerät antwortet nicht, Verbindung endet vor der Antwort) kann
  nur auftreten, nachdem der Broker die Zugangsdaten bereits akzeptiert hat -- das ist
  ein Geräte- oder Funkproblem, keine kaputte Sitzung, und darf keine erneute
  Anmeldung mehr auslösen.
- **Geräteabgleich und Schaltsitzung melden sich nicht mehr unabhängig an.**
  `services/meross_discovery.py::fetch_devices` benutzt jetzt denselben
  `MerossSessionCache`: bei noch frischer Sitzung aus `ensure_transport()` entfällt
  die eigene Anmeldung vollständig (`valid_http_session()`), eine eigene Ablehnung
  respektiert denselben Backoff, und ein erfolgreicher Abgleich setzt ihn ebenso
  zurück wie eine erfolgreiche Schaltanmeldung. Im gesunden Betrieb sinkt die
  Anmelderate dadurch von rund 4 (Schalten, alle 6 Stunden `SESSION_TTL`) plus 24
  (Geräteabgleich, stündlich `MEROSS_RECONCILE_INTERVAL_SECONDS`) auf nahe 4 pro Tag.
- **Der Ablehnungsgrund der Cloud erreicht jetzt das Schaltprotokoll**
  (`MerossSessionCache.last_rejection`, durchgereicht über `MerossSwitch`s
  `session_unavailable_reason` bis in `integrations/actuators.py`s Fehlermeldung) --
  vorher stand dort nur „Keine gültige Meross-Sitzung vorhanden", und der Betreiber
  musste für „Beyond Login Limit" in die Containerprotokolle steigen.

**Entschieden und nicht umgesetzt, auf ausdrücklichen Wunsch des Projektinhabers:**
kein Sitzungstoken in der Datenbank, damit die Anlage einen Neustart übersteht. Ein
gespeichertes Token ist einem Passwort gleichwertig; bislang stehen keinerlei
Cloud-Zugangsdaten in der Datenbank, und nach den beiden ersten Behebungen oben
bleiben ohnehin nur rund vier Anmeldungen am Tag übrig -- das rechtfertigt die
größere Angriffsfläche nicht. **`SESSION_TTL` bleibt bei 6 Stunden** -- eine längere
Lebensdauer wäre geraten, nicht gewusst (Meross dokumentiert keine Token-Lebensdauer),
und bei vier Anmeldungen am Tag gibt es dafür ohnehin keinen Anlass mehr.

Die frühere Aussage weiter unten in diesem Dokument, ein gescheiterter Meross-Befehl
werde „unbegrenzt oft, bewusst ohne Backoff" erneut versucht, gilt nicht mehr -- siehe
dort.

## Schaltprotokoll: drei Ergebniszustände

**Kein viertes Ergebnis "verworfen"/"discarded" existiert.** `command_outcome` hat und
hatte immer nur drei Zeilen (`executed`/`suppressed`/`failed`, Migration
`3a3e44c560fb`); `record_command` (`services/device_commands.py`) schreibt nie einen
anderen Code, und ein unbekannter Code schlägt fehl, bevor eine Zeile entsteht --
"discarded" kann aus keinem echten Codepfad in die Tabelle gelangen. Der Wortlaut
"unterdrückt oder verworfen" in `device_commands.html` und weiter oben in dieser Datei
ist eine sprachliche Dopplung für denselben Zustand (`suppressed`), keine zweite Sorte.
Die ursprünglich gemeldete rohe Beschriftung "discarded" stammte vermutlich aus
Demodaten, die über den Testhelfer `command_outcome()` mit einem erfundenen Code erzeugt
wurden -- dessen Fallback (`label = code`) ist für Tests gedacht, nicht für eine
Vorschau. Keine Migration nötig.

## Übersicht: 8 s / 4 s auf < 200 ms -- die Entscheidungs-Historie, nicht Assets, nicht N+1 über Zonen

Meldung aus dem echten Betrieb: `/` lud ohne Cache 8 s, mit Cache 4 s (v0.9.1
hatte bereits die Asset-Auslieferung behoben -- diese Zeit ging vollständig auf den
Server). Vorgabe: höchstens 1 s mit Cache, höchstens 4 s ohne. Gemessen gegen **MariaDB**
mit realistischem Bestand (3/10/25 Zonen, 30 Tage Messwerte und Schattenentscheidungen
am Standardintervall), nicht gegen SQLite -- dort blieb das Problem unsichtbar.

**Ursprünglicher Verdacht (N+1 über `resolved_setpoint` je Zone) war real, aber nicht
die Hauptursache.** Eine gezielte Messreihe mit fester Zonenzahl (10, die tatsächliche
Anlagengröße) und unabhängig variierter Datenmenge trennte die drei möglichen Achsen:

| Achse (10 Zonen fest) | Datenmenge | Wandzeit |
|---|---|---|
| nur Zonenzahl (3 → 10, wenig Historie) | -- | 14 ms → 40 ms |
| `shadow_decision`-Menge (1.000 → 432.000 Zeilen) | 30 Tage @ 60 s Takt | **40 ms → 7.300 ms** |
| `measurement`-Menge (Außenfühler, 100 → 8.640 Zeilen) | 30 Tage @ 5 min Takt | 40 ms → 40 ms (unverändert) |

`shadow_decision_retention_days` steht vorgabemäßig auf **365** (nicht 30 wie
`measurement_retention_days`), bei 60 s Regeltakt macht das bis zu ~525.000 Zeilen
je Zone. `zone_status_context` (`web/start_views.py`) las bislang die **gesamte**
Historie aller sichtbaren Zonen (`select(ShadowDecision).where(zone_id.in_(...))
.order_by(decided_at.desc(), id.desc())`, ohne `LIMIT`) nur um in Python die jeweils
neueste Zeile je Zone zu behalten. `EXPLAIN` gegen MariaDB bestätigt es:
`type: ALL, rows: 432000, key: None, Extra: Using where; Using filesort` -- ein
voller Tabellenscan trotz vorhandenem Index, weil die Abfrageform (`ORDER BY` ohne
`LIMIT` über mehrere Zonen) dem Optimierer keine Wahl lässt. Die Messwerttabelle war
dagegen nie das Problem: ihr Index (`ix_measurement_device_capability_measured`)
bediente die einzige, anlagenweite Außentemperatur-Abfrage bereits mit `type: range`
und einem `LIMIT 1`-Seek, unabhängig vom Bestand.

Behoben, ohne neue Migration -- der vorhandene Index
`ix_shadow_decision_zone_decided_id (zone_id, decided_at, id)` genügt bereits:

- `_latest_decisions()` (neu, `web/start_views.py`) ersetzt den Vollscan durch zwei
  gruppierte `MAX()`-Abfragen (neuestes `decided_at` je Zone, dann per `id` aufgelöst
  -- derselbe Sekunden-genaue Tiebreak wie bei `ZoneOverride`) und eine finale Abfrage
  über genau die gefundenen Zeilen. `EXPLAIN` zeigt jetzt `Using index for group-by`
  (ein "loose index scan"): Kosten proportional zur Zonenzahl, nicht zum Bestand.
- **Der reale N+1 über `resolved_setpoint` je Zone war zusätzlich vorhanden**, nur
  nicht die Hauptursache (10 Zonen kosteten dadurch allein rund 25 ms, nicht Sekunden).
  `BulkSetpointContext` (neu, `domain/schedule.py`) lässt `resolved_setpoint` optional
  vorab geladene Daten (Einstellungen, Frostschutz-Code, Sollwerte je Zone/Modus,
  laufende Übersteuerungen, den Urlaub, Zeitplanpunkte) statt eigener Abfragen je Zone
  benutzen -- ohne `ctx` unverändert wie vorher (alle ~25 anderen Aufrufstellen,
  Regelschleife eingeschlossen, unberührt). `zone_status_context` baut den Kontext
  einmal je Seitenaufruf. Dieselbe Bündelung versorgt `tenant_views.render_home`
  mit, das dieselbe Funktion aufruft.
- Nebenbefund beim Bündeln behoben: die Anzeige-Abfrage für das
  Übersteuerungs-Banner sortierte nur nach `created_at`, `_running_override` (was
  tatsächlich gilt) zusätzlich nach `id` -- bei zwei Übersteuerungen in derselben
  MariaDB-Sekunde (Sekundenpräzision) konnten Banner und tatsächliche Entscheidung
  auseinanderlaufen. Beide lesen jetzt dieselbe, gleich sortierte Abfrage.

Ergebnis am realistischen Fall (10 Zonen, 30 Tage Historie, Außenfühler,
End-to-End über HTTP inklusive Auth und Template-Rendering):

| | vorher | nachher | Vorgabe |
|---|---|---|---|
| 10 Zonen, 30 Tage | ~7.400 ms | **~140 ms** | ≤ 1000 ms mit Cache |
| 25 Zonen, 30 Tage (Kontext allein) | ~18.000 ms | ~19 ms | -- |
| SQL-Anweisungen je Aufruf (25 Zonen) | 158 | 10 | -- |

Regellogik unberührt (`resolved_setpoint`, `control_loop.decide()` unverändert bei
gleicher Eingabe), Zonenisolation unberührt (jede Bündelabfrage bleibt auf die von
`visible_zones` gelieferten Zonen beschränkt). Ruff, mypy, Pytest gegen SQLite **und**
MariaDB mit 100 % Abdeckung, Browsertests einzeln -- alle grün.

**Noch offen, nicht Teil dieser Änderung:** `web/control_views.py` (die
Betriebsseite) hat dieselbe Vollscan-Abfrage über `shadow_decision` -- eigener
Auftrag. Die Wohnungssicht (`tenant_views.py`) profitiert vom Fix mit, hat aber
ihren eigenen, noch ungeprüften Aufruf von `next_switch()` je Zone.

## Die Betriebsseite las dieselbe Historie wie die Übersicht

Die Beschleunigung der Übersicht in v0.9.2 hat eine zweite Fassung derselben
Abfrage stehen lassen: `/control` holte weiterhin die **ganze**
`shadow_decision`-Historie aller sichtbaren Zonen ohne `LIMIT`, nur um je Zone
die neueste Zeile zu behalten. Bei 365 Tagen Aufbewahrung und einer Zeile je
Zone und Regelzyklus sind das nach Monaten Betrieb Hunderttausende.

Die Abfrage steht jetzt **einmal** als `domain/zones.py::latest_decisions_by_zone`
und wird von beiden Seiten benutzt. Sie lag zuvor in `web/start_views.py`; dass
`web/control_views.py` sie dort nicht mitbekam, ist genau der Grund, warum die
langsame Fassung überlebt hat -- Grundsatz 6, eine Regel wird einmal
implementiert. In der Domäne statt in einem der beiden Adapter, damit kein
Adapter den anderen importieren muss; die Domäne kennt weiterhin keinen Adapter
(`test_architecture.py`).

**Bewusst nicht geändert:** `next_switch` wird in der Wohnungssicht weiterhin je
Zone gerechnet. Die Achsenmessung aus v0.9.2 zeigt, dass die Zeit nicht mit der
Zonenzahl wächst (3 auf 10 Zonen: 14 auf 40 ms), sondern allein mit der
Entscheidungshistorie. Eine Bündelung wäre zusätzliche Komplexität in der
Regelungsdomäne ohne messbaren Gewinn -- und `next_switch` ist ausdrücklich die
einzige Stelle, an der diese Rechnung steht, damit Anzeige und tatsächlicher
Sprung nie auseinanderlaufen.

## Statische Auslieferung: versioniert, langfristig cachebar, ein Lader statt neun Skripte

Anlass waren drei Rückmeldungen aus dem echten Betrieb: zähes Laden seit v0.9,
Source-Map-Ladefehler ohne HA-Ingress, und veraltetes CSS ohne HA-Ingress, das erst
ein Cache-Leeren behob. Ursache für Letzteres: `StaticFiles` sendet weder
`Cache-Control` noch `Expires`, nur ein ETag -- Browser cachen dann heuristisch und
liefern ohne Rückfrage aus. v0.9.0 hat `thermoctl.css` stark umgeschrieben; wer die
alte Fassung im Cache hatte, bekam neues HTML mit alten Regeln.

Jetzt behoben, alle Pflichtläufe (Ruff, mypy, Pytest gegen SQLite **und** MariaDB
mit 100 % Abdeckung, Browsertests einzeln) grün geprüft:

- `thermoctl/web/assets.py`: `ASSET_VERSION` aus `__version__` plus SHA-256 über
  alle `.css`/`.js`/`.svg` im static-Verzeichnis. Jede Asset-URL trägt
  `?v={{ asset_version }}`; `/static` liefert `Cache-Control: public,
  max-age=31536000, immutable` nur für exakt diese Version, sonst `no-cache`.
  Ändert sich eine Datei, ändert sich zwangsläufig die URL -- geprüft mit einer
  echten Dateiänderung: Hash vorher `0.9.0-421a...`, nach einer Änderung an
  `thermoctl.css` `0.9.0-59f5...`, nach Rücknahme wieder der alte Wert.
- `thermoctl/web/static/page_scripts.js`: ein Lader im bleibenden `<head>`
  (`base_core.html`, `base_plain.html`), der die sechs Funktionsskripte
  (passkey, schedule, permissions, assignment, device_filter, homebridge_copy)
  nur nachlädt, wenn ihr CSS-Selektor auf der aktuellen Seite tatsächlich
  vorkommt -- vorher lud jede Admin-Seite alle sechs ungefragt mit (51 604
  zusätzliche Bytes, sechs Anfragen, auch auf Seiten wie `/audit`, die keines
  davon braucht). Ein `loaded`-Set verhindert Doppelregistrierung über
  `hx-boost`-Swaps, `htmx:beforeSwap` erzwingt bei einer geänderten
  `X-Thermoctl-Assets`-Kennung eine echte Navigation statt eines Teil-Swaps mit
  veraltetem Kopf -- ein offener Tab überlebt so ein Server-Update, ohne
  neues HTML in einen alten `<head>` zu mischen. Alle sechs Selektoren gegen
  die Vorlagen abgeglichen, jeder trifft.
- Kiosk (`base_plain.html`-Gegenstück `kiosk.html`) bewusst unverändert ohne
  Lader und ohne Ladebalken -- braucht keines der sechs Skripte.
  `browser_tests/test_page_scripts.py::test_features_load_once_across_boost_and_history`
  prüft genau die schwierige Stelle: zwei Runden Boost/Vor/Zurück, geladen wird
  jedes Skript nur einmal, auch mit Ingress-Prefix.
- `sourceMappingURL`-Verweise aus den drei minifizierten Vendor-Dateien entfernt
  (Bootstrap CSS/JS, Swagger-UI-CSS) -- die referenzierten `.map`-Dateien wurden
  nie mitgeliefert, DevTools fragte sie erfolglos an. `HERKUNFT.md` dokumentiert
  die SHA-384 der geänderten Fassungen, `tests/test_assets.py` prüft, dass kein
  verbleibender Verweis mehr auf eine fehlende Datei zeigt.
- Ein Codex-Agent hatte das begonnen und wurde durch ein Nutzungslimit mitten in
  der Arbeit abgebrochen; ein zweiter Agent hat gegengelesen, den einzigen echten
  Fund korrigiert (der neue Browsertest hing an einem Gerät, das nur zufällig aus
  einem *anderen*, früher laufenden Test in derselben, sitzungsweiten Datenbank
  übrig war -- beide betroffenen Tests seeden jetzt ihr eigenes Gerät) und alle
  Pflichtläufe unabhängig wiederholt.

## Geräteliste und Admin-Hülle

`/devices` zeigt kompakte Tabellen mit den Spalten Gerät, Status, Fähigkeiten und
Zone; auffällige Geräte bleiben zuerst. Am Desktop unverändert die volle Tabelle.
Mobil bricht sie in gestapelte Zellen um, aber ohne Beschriftung für jede Zelle:
Gerätename und Status sprechen für sich, und eine leere Zone oder eine fehlende
Fähigkeit erscheint gar nicht erst als eigene Zeile -- nur gesetzte Werte tragen
dort noch eine Beschriftung ("Zone: Wohnzimmer"). Das senkt die mobile Seitenhöhe
bei 14 Beispielgeräten von 2952 auf 2489 Pixel, unter den Stand vor dem
Tabellenumbau (2847 Pixel). Der Status „hat sich noch nie gemeldet“ erscheint nur
einmal. Freitextfilter, Anlagenbild und Zuordnung bleiben erhalten. Die
Admin-Kopfleiste zeigt mobil „Konto“ und den vollständigen Seitentitel in einer
eigenen Zeile. Der Seitenleistenhintergrund reicht bis zum Seitenende; ihr Inhalt
bleibt haftend und bei wenig Fensterhöhe separat scrollbar. Mit 14 Geräten bei
1440 und 390 Pixeln in hellem und dunklem Farbschema visuell geprüft, ein Teil
davon mit Zone und Fähigkeiten und ein Teil ohne.

## Eine Freigabe ist erst fertig, wenn auch das Add-on nachgezogen ist

`thermoctl` wird an zwei Orten ausgeliefert, und der zweite ist beim Release v0.9.0
zunächst liegen geblieben: das Home-Assistant-Add-on liegt in einem eigenen
Repository (`MagicalWig34653/thermoctl-addon`) und zeigt über
`thermoctl/config.yaml` auf eine **feste** Versionsnummer des ghcr.io-Abbilds.
Solange die dort nicht nachgezogen ist, installiert jeder Add-on-Betreiber weiter
die alte Fassung -- die Freigabe erreicht ihn schlicht nicht.

Die Reihenfolge ist nicht beliebig: erst Tag und `docker.yml`, dann das Add-on. Die
Versionsnummer dort muss zu einem Abbild passen, das es wirklich schon gibt, sonst
scheitert die Installation beim Betreiber statt bei uns. Als Bedingung festgehalten
in `CLAUDE.md`, Abschnitt „Arbeitsweise".

## Der Image-Bau wird jetzt schon vor dem Merge geprüft

**Zwei Workflows, nicht einer** -- das ist beim Aufräumen für v0.9.0 einmal
übersehen worden und hier festgehalten, damit es niemand erneut übersieht:
`.github/workflows/docker.yml` baut das Image für amd64 und arm64 und
veröffentlicht es nach `ghcr.io`, aber **nur** bei einem Push auf `main` und bei
einem `v*`-Tag. `latest` entsteht ausschließlich aus einem Tag.

Was fehlte, war deshalb nicht der Bau überhaupt, sondern der Bau **vor** dem
Merge: Auf einem Zweig und in einem Pull Request lief er nicht, ein kaputtes
Dockerfile fiel erst nach dem Merge auf. `ci.yml` trägt dafür jetzt einen eigenen
Job `docker-build` -- neben der Datenbank-Matrix, nicht in ihr, weil SQLite und
MariaDB dasselbe Image ergeben. Er baut nur: keine Anmeldung, keine Registry,
kein Upload, `permissions: contents: read` unverändert. Das Veröffentlichen
bleibt allein Sache von `docker.yml`.

## v0.9.0 -- Freigabevorbereitung: getrennte Admin- und Mieteroberfläche

Die Arbeitsanweisung zum Redesign liegt unter
`docs/ui-redesign/` (Zielbild als HTML-Demos, Bestandsaufnahme der heutigen WebUI,
`IMPLEMENTATION.md` mit der Definition of Done).

**Fertig: das UI-Profil.** `access_group.ui_profile` (`admin` / `tenant`, Migration
`c4d18b7e2a95`) entscheidet, **welche** Oberfläche jemand bekommt -- ausdrücklich
nicht, **was** er darf. Das bleibt allein Sache der Grants. Beide Prüfungen laufen
hintereinander: der Wächter `web/guards.py::require_web_ui_profile` hängt als
Router-Dependency vor den Anlagenseiten, jede vorhandene `require(...)`- und
`visible_zones(...)`-Prüfung in den Endpunkten bleibt unverändert bestehen.

Warum das Profil an der Gruppe hängt und nicht am Benutzer: dort hängen schon die
Rechte, und ein zweiter Zuordnungsweg wäre eine zweite Wahrheit darüber, wer wozu
gehört. Ein Benutzer in mehreren Gruppen ist nur dann Mieter, wenn **alle** seine
Gruppen Mietergruppen sind (`domain/ui_profile.py::combined_profile`).

**Beim Upgrade wird nichts umklassifiziert.** Bestehende Gruppen bekommen `admin` --
auch eine, die "Mieter" heißt. Ein Name ist kein Modell, und eine Anlage, die nach
`alembic upgrade head` ihre Verwaltung verliert, wäre der teuerste denkbare
Migrationsfehler (Test: `test_migrations.py::
test_existing_groups_keep_the_admin_interface_on_upgrade`). Die Einrichtung legt
zusätzlich die Vorlage "Wohnung" an -- Mieterprofil, aber **kein einziges Recht**;
welche Zonen dazugehören, trägt die Verwaltung danach ein.

Die Navigationstabelle (`web/navigation.py`) trägt jetzt Abschnitt und Profil je
Eintrag und ist nach den vier Blöcken der Admin-Demo geordnet (Hauptbereich,
Analyse, System, Zugänge). Die Startseite und `/statistics` sind dabei neu in die
Tabelle gekommen.

**Fertig: die Hüllen.** `base_core.html` trägt, was beide Oberflächen teilen --
Kopfbereich, Farbschema, Assets, HTMX, CSRF-Weitergabe, Ladeanzeige, der Hinweis auf
eine veraltete Seite. Darauf setzen `base_admin.html` (Seitenleiste am Desktop,
reduzierte Fußleiste mobil) und `base_tenant.html` (mobile first). Die bisherige
base.html ist entfernt; alle Vorlagen verwenden die jeweiligen Hüllen.
`base_plain.html` (Kiosk) bleibt bewusst daneben und erbt nichts davon --
ein Wandtablett hat weder Navigation noch Ladebalken, und seine CSRF-Behandlung ist
eine andere.

**Fertig: der persönliche Bereich** `/account` (`web/account_views.py`) -- eigenes
Passwort, andere Sitzungen beenden, Passkeys, Hilfe, Abmelden. Ohne Profil-Wächter,
weil er beiden Oberflächen gehört, und ohne jedes Recht: das eigene Passwort zu
ändern ist kein privilegierter Vorgang. Bis hierher lagen diese beiden Funktionen auf
der Benutzerverwaltungsseite und wären für ein Mieterprofil unerreichbar gewesen.
`/account/help` erklärt die Anzeigen in Alltagssprache und liest dafür ausdrücklich
nichts aus der Datenbank -- eine Seite ohne Rechteprüfung darf keine Zonennamen
zeigen.

**Fertig: die Wohnungssicht.** `/` verzweigt nach `principal.ui_profile` --
dieselbe Adresse, zwei Oberflächen. Dazu `/schedule` (Wochenplan je eigenem Raum,
24-Stunden-Vorschau aus `schedule_forecast`) und `/heating-time` (7/30/90 Tage, nur
sichtbare Räume, ausdrücklich „Heizzeit" und im Trockenlauf „hätte geheizt").
Alle drei mit dem Wächter `tenant_ui_only`.

Die Raumkarte zeigt Raumname, Entscheidung, Isttemperatur samt Alter, Modus,
Sollwert mit verständlicher Begründung, Tagesverlauf, „Als Nächstes", den
dauerhaften ±0,5-K-Schritt am laufenden Modus, „Für eine Weile wärmer" und „Zur
nächsten Schaltzeit springen". **Thermostat und Übersteuerung bleiben auch hier
getrennt**, samt der Beschriftung, welcher Modus dauerhaft geändert wird.

Die Bedienelemente rufen die vorhandenen Endpunkte aus `daily_views.shared_router`
-- derselbe Weg wie die Anlagensicht, über zonenbezogene Rechte abgesichert und ohne
Profil-Wächter, weil sie beiden Oberflächen gehören. Neu dort:
`POST /zones/{id}/jump-next` über `domain.schedule.jump_to_next_switch`.

Der Zeitplan der Wohnungssicht ruft **dieselben** Domänenfunktionen wie der
Admin-Editor (`copy_schedule_day`, `adopt_schedule`, `undo_schedule_gesture`,
`move_schedule_point` …) -- eine einfachere Oberfläche auf dieselben Daten, keine
zweite Zeitplanmechanik. Geschrieben wird mit `schedule.manage` **je Zone**; ein
Mieter bekommt dafür ausdrücklich kein `zone.manage`.

`domain.statistics.PERIODS` hält die drei Zeiträume jetzt an einer Stelle für beide
Auswertungen.

**Zonenisolation** ist die Zusicherung, die `tests/test_tenant_views.py` am
gründlichsten prüft: jede Zonen-Id aus Pfad oder Formular wird erneut gegen
`visible_zones` geprüft und ergibt sonst **404** -- nie 403, das den Unterschied
zwischen „gibt es nicht" und „gehört jemand anderem" verriete. Geprüft wird auf
Anzeigename **und** Id im gesamten HTML, auch als Quelle einer Übernahme.

**Fertig: Abwesenheit.** Ein Mieter setzt einen Zeitraum an, in dem seine Räume
sparsamer geregelt werden -- `POST /absence`, beendet über `POST /absence/end`.
Welche Räume betroffen sind, entscheidet **ausschließlich der Server**
(`visible_zones(..., "override.create")`); es gibt bewusst kein Formularfeld dafür.
Ausdrücklich nicht der anlagenweite Urlaubsbetrieb: der senkt auch die Räume anderer
Mieter ab. Umgesetzt über die vorhandene Übersteuerungs-Domäne, mit `absence` als
Klammer darum (`zone_override.absence_id`), damit sich die Gruppe als *eine*
Abwesenheit anzeigen und in *einem* Schritt beenden lässt. Alles oder nichts:
entweder entstehen Klammer und alle Übersteuerungen, oder gar nichts.

**Fertig: „Problem melden".** Keine Attrappe -- die Meldung geht über dieselbe
Meldekette wie jede Störungsmeldung (der vom Betreiber konfigurierte Webhook) und
steht im Audit. Eigenes zonenbezogenes Recht **`report.create`** (Migration
`d31f6a04c7e9`), ausdrücklich nicht `zone.read`: etwas nach außen auszulösen darf
nicht aus einem Leserecht folgen. Die Migration teilt das Recht **keiner** Gruppe
automatisch zu. Sechster Meldungsschalter `setting.notify_tenant_reports` auf
`/settings`. Ist kein Webhook eingerichtet oder der Schalter aus, wird die Meldung
trotzdem im Audit festgehalten und der Mieter erfährt ehrlich, dass es keine
automatische Weiterleitung gibt.

Was der Bericht enthält, steht abschließend im Docstring von
`domain/problem_report.py` -- Raum, Problemart, Hinweis, Zeitpunkt, letzter
Messwert, Sollwert samt Begründung, Modus, Melder. Und nichts sonst: keine
Zugangsdaten, keine Brokeradressen, keine Gerätebezeichner, nichts aus einer anderen
Zone. Ein Test legt eine zweite Zone mit auffälligem Namen an und prüft es.

`age_in_words` ist dabei von `web/__init__.py` nach `domain/time.py` gewandert: die
Meldung braucht dieselbe Formulierung, und die Domäne darf keinen Adapter
importieren (`test_architecture.py::test_the_domain_knows_no_adapter`).

**Verschoben, mit Begründung:** persönliche Störungsbenachrichtigungen je Mieter.
thermoctl hat keinen individuellen Zustellkanal -- der Webhook ist anlagenweit und
geht an den Betreiber, nicht an einzelne Bewohner. Ein Schalter „Wichtige Störungen"
in der Wohnungssicht wäre eine funktionslose Einstellung. Die Hinweise erscheinen
stattdessen in der Oberfläche selbst (Startseite, oberer Hinweisbereich). Ein echter
persönlicher Kanal wäre eine eigene Aufgabe.

Der Rückbau der Abwesenheitsmigration entfernt zuerst den Fremdschlüssel und dann
den zugehörigen Index, wie InnoDB es verlangt. Die aktuellen Prüfergebnisse stehen
unter „Zahlen“.

## Optik: die Palette und die Formen der Demos übernommen

Übernommen ist jetzt die Gestaltung beider Demos, über die **Tokens**: dieselben
Namen wie vorher, andere Werte, sodass der gesamte Bestand ihnen folgt. Die
Anlagensicht führt Blau als Primärfarbe (`#2463eb`), die Wohnungssicht gedämpftes
Grün (`#236248`) -- gesetzt am Rumpf über `.tc-tenant`, damit beide Hüllen dieselben
Bausteine benutzen und nur ihre Werte tauschen. Dazu: Ecken von 16 bzw. 20 px,
getragene Schatten, Zustandsmarken als Pillen in kleinen Versalien, große und eng
gesetzte Seitenüberschriften, Tabellenköpfe auf eigener Fläche, Zonen als
**Kartenraster** statt als Zeilenband.

Temperaturflächen -- Tagesspur, Wochenplan, Vorschau -- verwenden
`--warmth` (Orange) und `--cool` (Blau) als Messwertskala. Die Primärfarbe für
Bedienelemente ist davon getrennt; in der Anlagensicht ist auch sie blau. Alle drei
Ansichten desselben Zeitplans (Startseite, Admin-Wochenplan, Mieter-Wochenplan)
benutzen jetzt dieselbe zweiseitige Skala: unter der Mitte kühl, darüber warm, die
Sättigung sagt wie deutlich.

Der Kiosk verwendet dieselben gültigen Gestaltungsvariablen; die Uhr des
Wandtabletts nutzt die Instrumentschrift.

## Vollständige Rechte nach der Einrichtung

Die Seed-Revision `3685e30419a4_nachschlagetabellen` enthält eine feste Liste der
ursprünglichen Rechte mit ihren damaligen ASCII-Beschreibungen. `report.create`
steht am Ende von `PERMISSIONS` und wird über seine eigene Migration ergänzt.
`test_migrations.py::test_every_permission_exists_after_a_full_upgrade` prüft
nach einem vollständigen Upgrade, dass jedes Recht aus `PERMISSIONS` in der
Tabelle steht; damit ist auch `audit.read` bei einer frischen Einrichtung vorhanden.

## Browsertests

`browser_tests/` (Playwright) ist auf die neue Oberfläche gezogen: `.tc-head` gibt es
nicht mehr, der Anker ist `.tc-topbar` (nicht `.tc-sidebar` -- die klappt unter 992 px
weg), das Sammelmenü „Einstellungen" ist durch die Seitenleiste ersetzt. Neu:
`test_tenant_ui.py` mit sechs Tests der Wohnungssicht (richtige Hülle, vier Bereiche
erreichbar, Sollwert-Stepper rechnet serverseitig, `/settings` ergibt 403, Fußleiste
auf 390 px, Ladebalken vorhanden) und ein Test, dass die Seitenleiste auf schmalem
Bildschirm über den Knopf auf- und zugeht.

Die drei Tests, die prüfen, ob `thermoctl.css` überhaupt wirkt, hängen nicht mehr an
einem festen Farbwert -- der scheitert bei jeder gewollten Farbanpassung und damit
aus dem falschen Grund, und seit dem Redesign wäre er zusätzlich stumpf, weil die
Primärfarbe selbst ein Blau ist. Geprüft wird jetzt, dass die Gestaltungsvariablen
auf `:root` überhaupt ankommen; die kennt nur diese Datei.

Bisher dokumentierter Prüfstand: **59 Browsertests grün**, für v0.9.1 selbst erneut
einzeln ausgeführt.

## Mieter-Zeitplan: bearbeiten und leere Tage einrichten

Ohne ausdrückliche Raumwahl zeigt `/schedule` bevorzugt den ersten Raum mit
`schedule.manage`, sonst den ersten lesbaren Raum. Der Auf/Zu-Knopf einer Tageszeile ist als Bedienelement gestaltet.
Ein nur lesbarer Raum nennt die Einschränkung ausdrücklich und verweist auf die
bearbeitbaren Räume.

Der vereinfachte Editor bietet zwei Schaltzeiten je Tag („warm ab“, „kühler ab“).
Ein frisch angelegter Raum hat noch keine Schaltpunkte.

Hat ein Tag **keine** Schaltzeit, bietet die Seite jetzt dieselben zwei Felder an und
legt beide Punkte an. **Welche zwei Modi das sind, entscheidet der Server**
(`_modes_for_an_empty_day`): die beiden wärmsten Sollwerte dieser Zone, der wärmere
zuerst, der Frostschutz ausgenommen -- er ist die untere Schranke der Regelung und
kein Abschnitt eines Tagesablaufs. Eine Modus-Id aus dem Formular wird ignoriert; sie
wäre eine weitere Angabe, der man nicht glauben darf, und brächte nichts, was der
Server nicht ohnehin weiß.

Sind für den Raum noch keine zwei Temperaturen hinterlegt, erscheint kein Knopf,
sondern der Grund. Tage mit einer anderen Punktzahl als null oder zwei bleiben
weiterhin lesbar, aber ohne die vereinfachte Bearbeitung.

## Abgesicherte Randfälle der neuen Oberfläche

- Alle lokalen Formularziele berücksichtigen `url_prefix`, einschließlich des
  Zonenformulars. Ein Wächtertest prüft dies direkt an allen Vorlagen.
- `domain/modes.py::step_setpoint` ändert den Sollwert samt Grenzen mit einer
  atomaren Anweisung; gleichzeitige Thermostat-Klicks gehen nicht verloren.
- Die Auswahl der laufenden Übersteuerung berücksichtigt Beginn und Ende. Nach
  einer kurzen Übersteuerung gilt eine ältere, weiterhin laufende wieder.
- Die Zeitplan-Übernahme prüft `schedule.manage` an der Zielzone.
- „Abwesenheit beenden“ beendet alle laufenden Abwesenheiten und auch deren noch
  nicht begonnene Übersteuerungen. Gleichzeitige Anfragen können weiterhin mehrere
  Abwesenheitsklammern anlegen; die Beendigung macht sie gemeinsam unwirksam.
  `resolved_setpoint` liest dafür die Übersteuerungen, nicht `absence.cancelled_at`.
- Der Freitextfilter der Problemmeldung entfernt unsichtbare Steuerzeichen anhand
  ihrer Unicode-Kategorie und kürzt an Zeichengrenzen.

**Unabhängig sicherheitsdurchgesehen, kein Befund.** Ein Gegenleser, der nicht
umgesetzt hat, hat elf Punkte am Code geprüft: Mieter an Anlagenrouten, ob der
Profil-Wächter irgendwo eine Rechteprüfung ersetzt, Zonen-Leaks über Titel, Auswahlfelder,
versteckte Felder und Weiterleitungsziele, die Bulk-Aktion Abwesenheit samt Teilzuständen
und gelöschten Zonen, Schreibrechte, den Inhalt der Problemmeldung samt Manipulation über
den Freitext, CSRF, die drei Migrationen, den Kiosk, das Token-Profil in REST und MCP, und
Domänenlogik im Browser -- und die Suite selbst ausgeführt.

**Bewusst nicht geändert:** REST, MCP und Kiosk benutzen für „nächste Schaltung
vorziehen" weiterhin `domain/remote_control.py::boost`, das bei einer laufenden
Übersteuerung still eine zweite danebenlegt, während die neue Oberfläche über
`jump_to_next_switch` eine ausdrückliche Ersetzung verlangt. Das anzugleichen wäre
eine Verhaltensänderung am REST-Vertrag und gehört nicht in dieses Teilprojekt --
festgehalten als eigene Folgearbeit.

`end_absence` beendet nur Übersteuerungen mit passender `absence_id`. Eine während
der Abwesenheit gelöschte Zone fällt über `ondelete="CASCADE"` heraus, ohne die
Klammer für die übrigen Räume zu beschädigen.

## v0.8.2 -- die Anlage schaltete nichts: der Anlaufriegel ging nie mehr auf

Aus der echten Anlage gemeldet, nicht in einem Test gefunden: Schaltbefehle
scheiterten, die Oberflaeche zeigte dauerhaft "Scharf, Neustart fehlt", und nach einem
Neustart stand dort "Bereitschaft". Eine einzige Ursache erklaerte alle drei
Beobachtungen. `app.py`s Lifespan setzt den einmaligen, prozessweiten Riegel
`app.state.sending_allowed` aus `switching_allowed()` -- und die prueft seit dem
Aktiv-Bereitschafts-Verbund auch die Fuehrung. An dieser Stelle hat der Prozess den
Anspruch noch nie gestellt, die Schattenschleife startet erst danach; der Riegel war
also immer zu und wurde nur einmal je Prozess gelesen. Behoben mit
`control_armed_at_startup()` (`integrations/actuators.py`), die nur `control_armed`
liest. `switching_allowed()` selbst ist unveraendert: die Fuehrung wird weiterhin vor
jedem Sendevorgang geprueft, und die Schattenschleife ueberspringt ihren ganzen
Durchlauf, wenn sie nicht fuehrt -- eine Instanz in Bereitschaft schaltet nichts.
Zweitens stellt `_shadow_loop` den Anspruch jetzt vor dem ersten Schlafen statt danach,
in einem eigenen `try`, damit ein Datenbankfehler beim Start die Schleife nicht ohne
Wiederholungsversuch beendet.

**Der eigentliche Befund ist die Blindheit der Suite.** Sie baut ihr Schema ueber
`Base.metadata.create_all()`; die Migration `bb4a0ff63b2d` legt in jeder echten Anlage
eine `cluster_claim`-Zeile an, `create_all()` nicht, und `cluster.is_leader` faellt bei
fehlender Zeile bewusst offen aus. Dieselbe Funktion lieferte im Test `True` und in der
Anlage `False`. Die Fixtures wurden bewusst *nicht* pauschal umgestellt -- das haette
hunderte unbeteiligte Tests mit Verbund-Beiwerk zugestellt und dabei fail-open in
fail-closed verkehrt. Stattdessen ein gezielter Riegel gegen genau diese Kluft:
`tests/test_migrations.py::test_migrated_cluster_claim_does_not_freeze_the_startup_
bolt_closed` baut das Schema ueber `alembic upgrade head`, laeuft durch den echten
`_lifespan` und einen echten ersten Regelzyklus. **Wo eine Aussage von der Zeile
abhaengt, die nur die Migration legt, taugt ein `create_all()`-Test nicht als Beleg.**

Ausserdem: `CLAUDE.md` nannte das MariaDB-Testpasswort falsch (`prüfen` statt
`pruefen`, wie Container und CI es benutzen).

## v0.8.1 -- SQLite-Sperrfehler im Verbund-Anspruch behoben

CI schlug nach v0.8.0 zeitweise fehl: `tests/test_cluster.py::test_two_processes_
racing_for_a_stale_claim_only_one_wins` scheiterte auf einem langsamen Läufer mit
`OperationalError: database is locked` statt der erwarteten Niederlage
(`rowcount == 0`). `busy_timeout` allein behob es nicht (Python setzt ihn über den
Treiber ohnehin schon auf 5 s; `db/engine.py`s eigenes `PRAGMA busy_timeout=5000`
macht das nur ausdrücklich, ändert nichts). Ursache: `services/cluster.py::
try_become_leader` las vor dem Schreiben (Existenzprüfung, Datenbankzeit) --
unter SQLite hält eine solche Transaktion dabei nur eine SHARED-Sperre und muss
zum Schreiben auf RESERVED hochstufen. Zwei Prozesse, die beide erst lesen,
geraten beim gleichzeitigen Hochstufen in einen Fall, den SQLites Busy-Handler
grundsätzlich nicht auflöst (auch nicht mit `busy_timeout`) -- sofortiger Fehler
statt kurzer Wartezeit. Behoben, indem `try_become_leader` jetzt zuerst schreibt
und nur bei `rowcount == 0` nachträglich liest, um "verloren" von "Verbund nie
aktiviert" zu unterscheiden -- siehe die Funktion selbst und
`tests/test_cluster.py::test_try_become_leader_writes_before_it_reads_cluster_
claim`. Zusätzlich WAL-Journalmodus für dateibasierte SQLite-Datenbanken
(`db/engine.py`), mit Erkennung auch der URI-Speicherform
(`file::memory:?...&uri=true`).

**Bekannter, dokumentierter Vorbehalt, kein eigener Auftrag:** Dasselbe
Lese-vor-Schreiben-Muster steckt auch anderswo (z. B. `services/retention.py`,
siehe dortiger Kommentar) -- real, aber selten, mit einem einzelnen
fehlgeschlagenen Zyklus oder Zugriff als sichtbarer Folge, keinem stillen
Datenfehler. `try_become_leader` ist das Referenzmuster für eine Lösung, falls
das je zum echten Problem wird.

## Fenster-Erkennung aus einem Temperatursturz, ohne Kontakt

Eine Zone ohne zugeordneten Fensterkontakt kann jetzt trotzdem ein offenes Fenster
erkennen — an einem hinreichend steilen Abfall ihrer eigenen Raumtemperatur. Je Zone
einschaltbar (`Zone.window_temp_drop_detection_enabled`, Vorgabe **aus**), Regelparameter-
Seite der Zone. Ein zugeordneter Fensterkontakt hat immer Vorrang: `services/
ingest.py::_window_open` fragt zuerst, ob der Zone Fensterkontakt-Geräte zugeordnet sind
— ist das der Fall, entscheidet ausschließlich der Kontakt, auch während er gerade
unbekannt ist (stale/fehlend). Nur eine Zone ganz ohne Kontakt und mit eingeschaltetem
Schalter wird über die Temperatur beurteilt (`_window_open_from_temperature`).

**Kriterium** (`domain/window_temperature_drop.py::window_open_suspected`, im Aufbau
gespiegelt an `domain.fault.stuck_reading`): der Referenzwert im Verlauf der letzten
`setting.window_temp_drop_window_minutes` (Vorgabe 15) **vor** dem aktuellen Wert minus
dem aktuellen Wert erreicht `setting.window_temp_drop_threshold_k` (Vorgabe 1,5 K). Der
Schwellwert ist ein begründeter, aber ausdrücklich **nicht anlagenspezifischer**
Schätzwert — ohne Messdaten der echten Anlage lässt sich kein sicherer Wert herleiten;
deshalb bleibt die Erkennung je Zone standardmäßig aus, und der Betreiber schaltet sie
bewusst ein. Bewusst in Kauf genommene Fehlauslöser: eine geöffnete Tür, ein Luftzug, ein
ungünstig platzierter Sensor — keiner davon ist mit reiner Temperaturmessung von einem
echten Fensteröffnen zu unterscheiden. Ein langsames Auskühlen nach Heizende und das Ende
einer Heizphase selbst bleiben unterhalb der Schwelle und lösen nicht aus (mit Tests
belegt, nicht nur angenommen).

Jetzige Kennzahl: der **Referenzwert ist das Maximum der Vorwerte, es sei denn es gibt
mindestens drei — dann ist es deren zweithöchster Wert**. Mit weniger als drei Vorwerten
gibt es nichts, das sich gefahrlos verwerfen ließe, ohne genau die Werte zu verlieren,
die einen frühen Sturz überhaupt zeigen könnten — der Referenzwert bleibt dort ihr
Maximum. Bei `[20.00, 20.10, 18.60]` wird damit ein Sturz von 1,50 K erkannt.
Ab drei Vorwerten wird ein einzelner
verrauschter Ausreisser vom zweithöchsten Wert einfach überstimmt, während zwei oder
mehr echte Vorwerte auf dem tatsächlichen Sturzniveau den Vergleich weiterhin tragen —
ohne die Mehrheitsanforderung des Medians. **Bewusst bleibende Lücke, mit eigenem Test
festgehalten:** bei nur einem oder zwei Vorwerten (die kürzeste unterstützte
Fenstergröße, oder ein dünn besetztes Fenster nach einer Meldelücke) kann ein einzelner
Ausreisser weiterhin täuschen — das Schließen dieser Lücke würde genau die Daten
kosten, die ein kurzes Fenster nicht übrig hat. Mit eigenen Tests für das Reviewer-Beispiel, den
Ausreisser-Fall ab drei Vorwerten, die bewusst bleibende Lücke bei ein bis zwei
Vorwerten und eine Meldelücke mitten in der Messreihe belegt.

**Plausibilitätsgrenze:** Ein berechneter Sturz ab 6,0 K wird nicht als
Fensterereignis gewertet (`WINDOW_TEMP_DROP_MAX_PLAUSIBLE_DROP_K`). Auch der
zweithöchste Vorwert kann bei mehreren Ausreißern verfälscht sein, etwa bei
Vorwerten `[21.0, 30.0, 29.0]` und aktuellem Wert `21.0` (scheinbarer Sturz 8,0 K).
Die Grenze liegt über der höchsten einstellbaren Sturzschwelle von 5,0 K;
sie ist eine begründete Plausibilitätsannahme, keine Messung der echten Anlage.
Tests prüfen Mehrfachausreißer, den Grenzwert und den Abstand zur einstellbaren
Schwelle. Ausreißer unterhalb der Plausibilitätsgrenze bleiben möglich.

**Rücknahme.** Ein reines Sturzkriterium kennt kein Ende — ein bereits abgekühlter,
stabil kalter Raum zeigt keinen neuen Sturz mehr, obwohl das Fenster noch offen sein
könnte, und da die Erkennung selbst das Heizen abschaltet, gäbe es ohnehin keine aktive
Wärmequelle, die einen Temperaturanstieg als Entwarnungssignal liefern könnte — eine
Rücknahme über „die Temperatur steigt wieder" wäre also zirkulär. Gelöst über eine
gebundene Haltedauer (`temperature_detection_still_holding`,
`setting.window_temp_drop_hold_minutes`, Vorgabe 30): einmal ausgelöst, gilt die
Vermutung für diese Dauer weiter offen, auch ohne neuen Sturz, und fällt danach von
selbst wieder ab, sofern in der Zwischenzeit kein frischer Sturz sie erneuert. Dieselbe
Uhr wie beim echten Kontakt (`zone_state.window_open_since`) — kein zweiter Zeitstempel.

**Schutz gegen Rückkopplung:** Eine aufgrund der Vermutung abgeschaltete Heizung
kann weiteres Auskühlen und damit erneute Erkennung verursachen. Der Halt allein
begrenzt diese Folge nicht. Dagegen wirkt eine **kumulative** Zählung auf einer eigenen, von `window_open_since`
unabhängigen Uhr: `zone_state.window_temp_drop_streak_started_at` (seit wann die
Strähne läuft) und `zone_state.window_temp_drop_last_detected_at` (wann sie zuletzt
tatsächlich erkannt wurde). Ein frischer Sturz setzt die Strähne genau dann fort, statt
sie neu zu beginnen, wenn die Lücke seit der letzten Erkennung höchstens
`setting.window_temp_drop_gap_tolerance_minutes` (Vorgabe 10) beträgt
(`temperature_detection_gap_within_tolerance`) — kurz genug, um nur den einzelnen
verrauschten Ausreißer am Halt-Rand zu überbrücken, deutlich kürzer als der Halt selbst,
damit daraus kein zweiter Halt wird. Während der Halt selbst noch greift (`still_
holding`), braucht es diese Toleranzprüfung gar nicht — die Strähne läuft dort
ohnehin ununterbrochen weiter, bestätigt durch den Halt selbst, nicht nur vermutet aus
einer Lücke. Erreicht die Strähne `setting.window_temp_drop_max_suspected_minutes`
(Vorgabe 90, drei Halte), erzwingt die Erkennung eine **Zwangspause**: für
`setting.window_temp_drop_silence_minutes` (Vorgabe 60) bleibt die Zone geschlossen,
unabhängig von jedem weiteren Sturz (`temperature_detection_still_silenced`, eigene
Frist `zone_state.window_temp_drop_silence_until`). Danach darf die Erkennung wieder
auslösen, mit zurückgesetzter Strähne.

**Bewusst bleibende Lücke:** eine tolerierte kurze Unterbrechung ist nicht dasselbe wie
eine unbegrenzte — überschreitet die Lücke zwischen zwei erkannten Zyklen die Toleranz
tatsächlich (eine echte Erholung, oder eine längere Serie verrauschter Fehlschläge),
gilt die Strähne als beendet, und ein späteres erneutes Auslösen zählt wieder bei null.
Ein Raum, dessen Fehlauslöser zufällig weiter auseinanderliegen als die Toleranz, könnte
das im Prinzip unbegrenzt fortsetzen. Bewusst nicht geschlossen: eine deutlich längere
Toleranz würde beginnen, echte, voneinander unabhängige Episoden zu verschmelzen.

Mit eigenen Tests belegt: dem Reviewer-Szenario nachgestellt (ein einzelner Ausreißer
genau am Halt-Rand übersteht die Strähne unverändert), 20 simulierte Durchläufe über
420 Minuten mit demselben Ausreißer-Muster (die Zwangspause feuert; eine Gegenprobe mit
Toleranz null — dem alten, entfernten Verhalten entsprechend — feuert absichtlich nie),
sowie Obergrenze, Zwangspause während laufender Sturzverdachtsfälle und Ende der
Zwangspause wie zuvor.

Die bleibende Lücke ist in `tests/test_window_temperature_drop.py::
test_a_recovery_just_over_the_tolerance_lets_every_streak_restart` verankert:
20 Minuten Halt, danach 15 Minuten Erholung, 14 Wiederholungen über sieben Stunden.
Die Zwangspause greift dabei nie, während die Zone für zwei Drittel der Zeit als
offen gilt.

**Wirkt wie ein echter Kontakt.** `zone_state.window_open`/`window_open_since` werden für
beide Quellen identisch gesetzt; `domain/control_loop.py` (Fensterabschaltung,
Frostschutz-Ausnahme, EIN/AUS-Ausnahme, Wiederanlaufsperre) und
`domain/window_alarm.py` (Kälte-Alarm) bleiben deshalb **unverändert** — mit eigenem
Test belegt, dass Letzteres tatsächlich zutrifft, statt nur angenommen.

**Bleibt unterscheidbar, Grundsatz 5.** Neue Spalte `zone_state.
window_open_by_temperature`: `True` genau dann, wenn das aktuelle `window_open = true`
aus der Temperaturvermutung stammt, nie aus einem echten Kontakt. `domain/
control_loop.py::decide()` hängt jeder Begründung, bei der ein temperaturvermutetes
Fenster mitentscheidet, einen kurzen Zusatzsatz an („Fenster nicht gemessen, sondern aus
einem Temperatursturz vermutet."). Auf der Startseite ein eigener Status-Chip neben dem
bestehenden Fenster-Alarm-Chip.

**Nicht in REST, MCP oder Homebridge** — dieselbe, vom Projektinhaber vorgegebene Grenze
wie beim Fenster-Alarm. Die anlagenweiten Parameter liegen deshalb in einem eigenen
`WINDOW_TEMP_DROP_LIMITS` (`domain/control.py`), nicht im von REST und MCP mitbenutzten
`LIMITS`; der Zonen-Schalter ist keine `ControlParameters`-Spalte und hat eine eigene
kleine Speicherfunktion (`domain/zone_settings.py::set_window_temp_drop_detection`), damit
er die REST-Antwort `ControlParametersResponse` — die `ControlParameters` verbatim spiegelt
— nicht erreichen kann. **Stand v0.10.1:** der HTTP-Adapter dafür ist das eine Formular
`/zones/{id}/parameters` (nicht mehr eine eigene Route/eigenes `<form>` — das erzeugte zwei
gleichlautende „Speichern"-Knöpfe auf einer Seite und verlor die Änderung, wenn der jeweils
andere Knopf gedrückt wurde, s. Abschnitt oben); `save_parameter` ruft die Speicherfunktion
zusätzlich zu `save_control_parameters` auf, ohne dass das Feld dadurch Teil von
`ControlParameters` würde. In Home Assistant eine eigene, laufend gesendete
Diagnose-Entität je Zone (`state/window_open_by_temperature`), kein eigenes
Meldungssystem mit Zustellprotokoll wie beim Fenster-Alarm.

Migrationen: `e741133296d2`, `1b7bad26c13a` und `43aa18ba1c12`.

## Fenster: Frostschutz gewinnt, EIN/AUS-Aktoren schalten nicht ab

Zwei Entscheidungen des Projektinhabers an `domain/control_loop.py::decide()`.

**Frostschutz schlägt das offene Fenster.** Bisher schaltete Regel 3 bei offenem
Fenster bedingungslos ab — ein Raum konnte dabei tatsächlich unter den Frostschutz
fallen (Lüften vergessen, draußen kalt). Fällt die Zone trotz offenem Fenster unter
ihren Frostschutzwert, heizt sie jetzt wieder: Heizen gegen ein offenes Fenster ist
teuer, eingefrorene Leitungen sind teurer. Die Prüfung sitzt an Regel 3 selbst (vor
der bisherigen bedingungslosen Abschaltung), maßgeblich ist `frost_c`, nicht der
aufgelöste Sollwert, mit derselben Hysterese wie der Normalfall (kein Flattern am
Frostschutzwert) und weiterhin unter der Mindestschaltdauer aus Regel 5. Eigener
`reason_code`: `frostschutz_trotz_fenster_offen`.

**EIN/AUS-Aktoren (Fußbodenheizung an reinen Ein/Aus-Ventilen) schaltet das Fenster
nicht mehr ab** — zu träge, als dass ein Abschalten beim Lüften etwas brächte.
Erkennungsmerkmal: die Zone hat Aktoren, und keiner davon ist
`Device.self_regulating` (`Situation.on_off_actuators_only`, hergeleitet in
`services/shadow_run.py::_on_off_actuators_only`). Gemischte Zonen (mindestens ein
selbstregelndes Ventil) bleiben beim bisherigen, vorsichtigeren Verhalten. Nur das
Abschalten entfällt — Fenstererkennung, -protokollierung und der spätere
Kälte-Alarm sind unverändert. Rule 4 (Wiederanlaufsperre) ist für solche Zonen aus
demselben Grund mit ausgenommen: sie hat nie abgeschaltet, es gibt nichts, wovon sie
sich erholen müsste.

**PI-Zweig mitgezogen:** Jede PI-fähige Zone hat per Definition nur gewöhnliche
(nicht-selbstregelnde) Schaltaktoren — genau die Bedingung für
`on_off_actuators_only`. Ein offenes Fenster gated PI deshalb nur noch für
gemischte Zonen; für eine reine EIN/AUS-Zone läuft PI unverändert weiter, als gäbe
es kein Fenster (`services/shadow_run.py::_pi_gate_reason`, neuer Parameter
`window_governs`). Beim Bau fiel dabei ein zweiter, unabhängiger Fehler auf:
`_pi_outcome`s `resume_delay_active` berechnete rule 4s Bedingung nochmal selbst,
ohne von der EIN/AUS-Ausnahme zu wissen — behoben in derselben Änderung.

`shadow_decision.reason` und `.setpoint_reason` sind `Text` statt `String(255)`
(Migration `c1a4e9d872b3`), damit kombinierte Begründungen auch unter MariaDB Platz
haben. Der Hinweistext `on_off_zone_note` bleibt kurz.

Getestet in `tests/test_control_loop.py` (Schwellwert, Hysterese ohne Flattern,
Mindestschaltdauer, EIN/AUS- vs. gemischte Zone, Zusammenspiel beider Änderungen),
`tests/test_control_loop_state_table.py` (die vollständige Zustandstabelle, um die
Vorrangkette weiterhin erschöpfend zu beweisen) und `tests/test_shadow_run_pi.py`
(`_pi_gate_reason`-Klassifikation, End-zu-Ende gegen eine echte Zone, je eine
gemischte und eine reine EIN/AUS-Zone).

`already_engaged` verlangt neben `heating_now` auch
`not situation.valve_protection_active`: ein unterbrochener Ventilschutzlauf wird
nicht als bereits aktive Frostschutz-Ausnahme protokolliert. Geprüft durch
`test_frost_override_is_not_attributed_to_an_interrupted_protection_run` und die
Zustandstabelle. `tests/test_shadow_run.py` prüft `_on_off_actuators_only()` für
Zonen ohne Aktor, gemischte Zonen und mehrere reine EIN/AUS-Aktoren.

## Urlaubsbetrieb: Absenkung deckelt nicht mehr unter den Frostschutz einer Zone

Review-Befund, sicherheitsrelevant nach Grundsatz 7: `_vacation_setpoint()` gab den
eingegebenen Absenkwert bislang ungeprüft zurück. Zone mit Frostschutz 16,0 °C,
Urlaub mit 5,0 °C, Betriebsart `auto` → geregelt wurde auf 5,0 °C. Behoben in
`domain/schedule.py::_vacation_setpoint()`: der Absenkwert wird jetzt je Zone auf
`max(setback_temperature_c, Frostschutz dieser Zone)` gedeckelt — derselbe absolute
Frostschutz-Boden, den `domain/solar_setback.py::apply()` für seine eigene Korrektur
schon durchsetzt. Greift der Frostschutz, lautet die Begründung im `Setpoint`
„Urlaubsbetrieb — Absenkung durch Frostschutz angehoben" statt der bisherigen, dann
unehrlichen „Urlaubsbetrieb — Absenkung" (Grundsatz 5). `schedule_forecast()` heilt
dadurch mit, da es denselben Weg über `resolved_setpoint()` nimmt — mit eigenem Test
bestätigt statt nur angenommen. `docs/api.md` zieht die Grenze bei
`setback_temperature_c` entsprechend nach.

## Urlaubsbetrieb: anlagenweite Absenkung über ein festes Zeitfenster

Tabelle `vacation` (Migration `4bfefd4c10a4`): ein einziger
Absenkwert für die ganze Anlage über ein Zeitfenster mit fest eingegebenem Beginn
und Ende, danach läuft der Zeitplan von selbst weiter. Umgesetzt als eigener
Zustand, den `domain/schedule.py::resolved_setpoint()` direkt abfragt — nicht als
je-Zone angelegte `ZoneOverride`-Zeilen (die verworfene Alternative): Eine später
angelegte Zone wäre sonst nicht erfasst, und das Aufheben einer einzelnen
Override-Zeile hätte diese Zone stillschweigend aus dem Urlaub herausgenommen,
ohne dass irgendwo stünde, dass das passiert ist.

Vorrangkette in `resolved_setpoint()`: Betriebsart „Aus" > laufende, von Hand
gesetzte Zonen-Übersteuerung > Urlaub > Zeitplan > Frostschutz. Eine laufende
Übersteuerung geht beim Start eines Urlaubs also nicht verloren; eine Zone auf
„Aus" bleibt aus. `control_loop.decide()` selbst ist unverändert — Fenster,
Sensorausfall, Mindestschaltdauern und Ventilschutz laufen unwissend vom Urlaub
genau wie bei jeder Übersteuerung. `schedule_forecast()` zieht Beginn und Ende
des Urlaubs als zusätzliche Balkengrenzen neben dem nächsten Zeitplan-Schaltpunkt,
damit die 24-Stunden-Vorschau einen mitten in ihr beginnenden oder endenden
Urlaub nicht in einen falsch breiten Zeitplan-Balken auflöst.

Ein- und Ausgabe lokal, Speicherung über `domain/time.py::local_day_start_utc` in
UTC — DST-sicher, dieselbe Umrechnung wie Statistik und Audit-Log. Beginn und Ende
sind volle lokale Kalendertage, beide eingeschlossen. Eigenes Recht
`vacation.manage` statt `setting.manage` oder dem zonenbezogenen
`override.create` — Begründung in `db/models/lookup.py`. Oberfläche: eigene Seite
`/vacation` (Lesen `zone.read`, Ändern `vacation.manage`), zusätzlich ein Hinweis
auf der Startseite, sichtbar sowohl während ein Urlaub läuft als auch, solange
er erst geplant ist. REST (`GET`/`POST`/`DELETE /api/v1/vacation`) und MCP
(`read_vacation`, `vacation`, `cancel_vacation`) bekommen die Funktion
gleichermaßen — Grundsatz 6.

`services/shadow_run.py`: die PI-Sollwertkontext-Funktion
(`_pi_setpoint_context_key`) kannte bislang nur Zeitplan-Modus oder Zonen-
Übersteuerung als Ursprung eines Sollwerts; ein Urlaub liefert wie eine feste
Übersteuerung `mode_id=None`, ohne eine Übersteuerungs-Zeile zu sein. Ohne eigenen
Zweig hätte das den dortigen `assert` verletzt, sobald ein Urlaub auf einer
PI-aktivierten Zone ohne laufende Übersteuerung greift — gefunden beim Nachvoll-
ziehen der Vorrangkette, PI selbst hat noch keinen scharfen Betriebspfad.

## Außentemperatur und Fenster-Alarm

Zwei neue, zusammengehörige Stücke:

**Außentemperatur.** `setting.outdoor_temperature_source_device_id` -- genau eine
Quelle für die ganze Anlage, ausgewählt aus den bekannten Zigbee2MQTT-Geräten wie
`zone.temperature_source_device_id` für eine Zone (`domain/outdoor.py`, Auswahl
unter `/settings`). Alter und Ausfall werden wie bei einem Zonensensor beurteilt
(`domain.fault.sensor_state`, gegen `setting.default_sensor_timeout_seconds`) --
"keine Quelle gewählt" und "Wert veraltet" sind eigene Texte, keine Zahl, die man
mit einem tatsächlichen Messwert verwechseln könnte. Sichtbar auf der Startseite
und per MQTT als eigene, anlagenweite Home-Assistant-Entität (`sensor`,
`aussentemperatur`).

**Fenster-Alarm.** Eine fünfte, eigene Meldungsart (`notify_window_alarm`) neben
den bestehenden vier -- meldet, wenn ein Fenster einer Zone länger als
`setting.window_alarm_open_minutes` (Vorgabe 30) offen steht **und** die
Außentemperatur unter `setting.window_alarm_outdoor_threshold_c` (Vorgabe 5,0 °C)
liegt. Beide Bedingungen streng (`domain.window_alarm.window_alarm_state`):
"seit mehr als", nicht "seit mindestens"; "unter", nicht "höchstens". Fehlt die
Außentemperatur oder ist sie veraltet, ist das Ergebnis `None` -- ein
unbekannter Zustand, der nie als Alarm **oder** als Entwarnung gilt
(`zone_state.window_alarm`, tri-state). Gemeldet wird, wie beim festhängenden
Messwert, nur der Übergang, samt Entwarnung (`domain.fault_notice.
window_alarm_notice`) -- über eine eigene, von der Sensorstörung getrennte
Home-Assistant-Entität je Zone (`fenster:<id>`, nicht `sensor:<id>`: die beiden
Zustände können gleichzeitig gelten und dürfen sich nie überschreiben).

**Bewusst nicht in REST, MCP oder Homebridge.** Ausdrückliche Vorgabe des
Projektinhabers: Neues zu Fenster und Außenwert erscheint nur in der
Oberfläche und in Home Assistant. Die beiden Fenster-Alarm-Schwellen liegen
deshalb in einem eigenen `WINDOW_ALARM_LIMITS` (`domain/control.py`), nicht in
dem von REST und MCP mitbenutzten `LIMITS` -- eine eigene, kleine
Validierungsfunktion (`check_number`, jetzt parametrisiert) statt eines
gemeinsamen Wertebereichs, der die beiden Schwellen versehentlich mit
hinausgetragen hätte.

Migration `f18d4dcb3f5d`. Der Alarm ist eine Meldung, kein Eingriff in die Regelung.
Die Fensterabschaltung ist oben separat beschrieben.

## Migrationssperre: gleichzeitige `alembic upgrade head`-Läufe abgesichert

`migrations/env.py` nimmt jetzt vor jedem Migrationslauf eine Datenbank-Sperre
(neu: `thermoctl/db/migration_lock.py`) — wirkt für **jeden** Alembic-Aufruf,
auch von Hand, nicht nur beim Containerstart, weil sie dort und nicht im
Entrypoint sitzt. MariaDB und SQLite bekommen bewusst unterschiedliche, aber
je backend-eigene Mittel statt eines erzwungenen gemeinsamen: `GET_LOCK()`
(MariaDB, session-gebunden, mit eigenem Timeout) bzw. eine `flock`-Dateisperre
neben der Datenbankdatei (SQLite; eine In-Memory-Datenbank braucht keine, sie
gehört ohnehin nur einem Prozess). Beide lösen sich beim Absturz des Halters
von selbst — kein Risiko, dass eine tote Sperre die Anlage stilllegt
(Grundsatz 7). Wer die Sperre nicht bekommt, wartet bis zu
`THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS` (Vorgabe 60s) und bricht dann mit
einer klaren Fehlermeldung ab, statt endlos zu hängen; der Normalfall (unbesetzte
Sperre) kostet nichts Messbares. Die vorgeschalteten Einmal-Jobs in
`docs/docker-swarm.md` und `docs/kubernetes.md` sind dadurch für die
Absicherung selbst entbehrlich geworden (Anleitungen entsprechend angepasst),
bleiben aber als Option für alle, die den Migrationsschritt trotzdem sichtbar
getrennt vom Ausrollen des Dienstes sehen wollen.

Nachgewiesen in `tests/test_migrations.py`
(`test_two_concurrent_upgrade_head_runs_do_not_collide`, zwei echte
`alembic`-Unterprozesse gegen MariaDB, per Barriere gleichzeitig losgelassen,
und `test_upgrade_head_against_an_already_current_database_is_a_quick_no_op`)
und in `tests/test_migration_lock.py` (Erwerb, Freigabe, Warten mit
Zeitüberschreitung, Absturz des Halters — je gegen SQLite **und** MariaDB,
unabhängig vom Backend des jeweiligen Testlaufs).

## Betrieb unter Docker Swarm und Kubernetes dokumentiert

Zwei Anleitungen für den Aktiv-Bereitschafts-Verbund:
[`docs/docker-swarm.md`](docker-swarm.md) und [`docs/kubernetes.md`](kubernetes.md), je
mit lauffähigen Beispieldateien (`docker/swarm.compose.beispiel.yml`,
`docker/swarm.migrate.compose.beispiel.yml`, `k8s/*.beispiel.yaml`). Zwei Dateien statt
einer gemeinsamen, weil die Beispielmanifeste beider Systeme sonst dieselbe Anleitung mit
zwei unvereinbaren YAML-Dialekten überladen hätten.

## Erkennung eines festhängenden Messwerts

`domain/fault.py::sensor_state` prüfte bisher nur das Alter der letzten Meldung — ein
Sensor, der zuverlässig alle paar Minuten dieselbe Zahl schickt, galt dauerhaft als
`ok`. Neu: `stuck_reading()` vergleicht die Spanne aller Messwerte der letzten
`stuck_reading_hours` Stunden (Vorgabe 12, anlagenweit) gegen eine kleine Schwelle
(0,05 °C) — nicht bitgleiche Werte, sonst zählte ein Sensor, der zwischen zwei
Auflösungsschritten pendelt (22,7/22,8 °C), fälschlich als festhängend. Ist die
Historie kürzer als die Schwelle, gibt es keinen Verdacht, keinen Fehlalarm.

**Entscheidung des Projektinhabers: melden, aber weiterregeln.** `zone_state.sensor_stuck`
ist von `sensor_status_id` und damit von `decide()` in `domain/control_loop.py` komplett
unabhängig — die Regelentscheidung ändert sich nicht, eine Zone fällt deswegen nicht in
den Frostschutz. Nur berechnet, solange der Sensor sonst `ok` ist (eine ausgefallene
oder fehlende Quelle hat ihre eigene, unveränderte Erkennung). Eine vierte, eigene
Meldungsart (`notify_stuck_sensor`) statt ein Zusatz zur Sensorstörung — beide schließen
sich gegenseitig aus und ein gemeinsamer Schalter würde eine harmlose, oft tagelange
Beobachtung mit einem echten Sensorausfall verkoppeln. Sichtbar auf Startseite und
Kiosk, sowie über `sensor_stuck` in REST- und MCP-Zonenzustand. Migration `afb9832fba99`,
Kopf danach unverändert einzügig.

## Zeitplan-Vorschau: nächste 24 Stunden je Zone

Die Zeitplanseite (`/zones/{id}/schedule`) zeigt jetzt oberhalb des Wochenrasters eine
Leiste mit der Vorschau der nächsten 24 Stunden, sichtbar bereits mit `zone.read`
(keine Änderungsrechte nötig). Die Berechnung sitzt in der Domäne
(`thermoctl.domain.schedule.schedule_forecast`), nicht in der Ansicht: sie reicht
über `resolved_setpoint`s eigene Rangfolge (Betriebsart Aus schlägt alles, dann eine
laufende Übersteuerung bis zu ihrem Ende, dann Urlaub, Zeitplan und zuletzt Frostschutz) und
kann daher nie etwas zeigen, was zur Laufzeit nicht tatsächlich einträte. Sie rechnet
in echten UTC-Instanzen statt in Ortszeit-Arithmetik und bleibt deshalb auch über
Mitternacht, einen Wochentagswechsel und beide Sommerzeit-Umstellungen (23- bzw.
25-Stunden-Tag) exakt — mit Tests, die diese Grenzfälle einzeln mit von Hand
abgeleiteten Uhrzeiten belegen (`tests/test_domain_schedule.py`). REST und MCP bieten
die Vorschau noch nicht an; das ist eine bewusste Auslassung dieser Aufgabe, keine
technische Grenze — die Domänenfunktion ist adapterunabhängig nutzbar.

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

Am 2026-09-11 auf dem Freigabestand für v0.9.4 selbst nachgemessen:

| Prüfung | Ergebnis |
|---|---|
| Testsammlung | 5048 Tests in `tests/` (59 Browsertests separat, nicht in der CI) |
| SQLite, volle Suite | 5047 bestanden, 1 übersprungen (Exit 0) |
| MariaDB, volle Suite | 5047 bestanden, 1 übersprungen (Exit 0), gegen `THERMOCTL_TEST_DATABASE_URL` mit `mysql+pymysql` |
| Testabdeckung | beide Datenbanken: 100 %, 9078 erfasste Anweisungen, keine ungedeckt; CI-Mindestschwelle 100 % |
| Ruff | ohne Befund (Exit 0) |
| mypy strict | ohne Befund, 125 Quelldateien (Exit 0) |
| Migrationskette | ein Kopf `d31f6a04c7e9`; Upgrade-/Downgrade-Tests gegen beide Datenbanken bestanden |
| Container | `docker build -f docker/Dockerfile .` erfolgreich (Exit 0) |

Je ein Test ist backendbedingt übersprungen: unter SQLite der MariaDB-spezifische
Parallelmigrationsfall, unter MariaDB die SQLite-spezifische Fremdschlüsselprüfung.

v0.9.0 ergänzt drei aufeinanderfolgende Migrationen nach `43aa18ba1c12`:
UI-Profil `c4d18b7e2a95`, Abwesenheit `c724de89a13f` und
Problemmeldung/`report.create` `d31f6a04c7e9`.

**Unabhängig nachvollzogen.** Ruff, mypy, beide Datenbanken, Alembic vorwärts und
rückwärts und der Container-Bau sind von einem Gegenleser, der nicht umgesetzt hat,
noch einmal selbst ausgeführt worden -- mit demselben Ergebnis, und mit Nachweis, dass
der MariaDB-Lauf wirklich gegen MariaDB lief (`11.8.9-MariaDB`) und nicht unbemerkt
auf SQLite auswich.

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`.
Der MariaDB-Lauf verwendet `mysql+pymysql` gegen `127.0.0.1:3306/doku_090` und
`COVERAGE_FILE=.coverage.doku`. Der Server meldet `11.8.9-MariaDB-ubu2404`;
während des Laufs waren drei Verbindungen zu `doku_090` und 42 Tabellen dort
nachweisbar. Die Migrationstests verwenden die abgeleitete Datenbank
`doku_090_migrations`. Der SQLite-Lauf verwendet `sqlite:///./test.db`.

## Was geschaltet wird — genau

- **`setting.control_armed`** ist der erste Riegel. Steht er auf `false`, geht an keinen
  Aktor etwas hinaus — das ist der Zustand einer neuen Anlage. `/control/arm` öffnet ihn,
  mit eigenem Recht `control.arm`.
- **Der MQTT-Client trägt einen zweiten, unabhängigen Riegel**, der beim Prozessstart
  gebaut wird. Scharfschalten wirkt deshalb erst nach einem Neustart.
- **Als dritter Riegel muss die Instanz führen** (`cluster.is_leader`); im
  Einzelbetrieb ohne Verbund-Datensatz gilt sie als führend. Sind alle drei
  Riegel offen, veröffentlicht der Dienst: Sollwerte an selbstregelnde
  Thermostatventile, Ein/Aus an gewöhnliche Zigbee2MQTT-Aktoren
  (`services/publishing.py::_send_actuator_switches`), Sollwert und `system_mode`
  (wo vorhanden) an Zigbee2MQTT-Thermostatventile ohne eigene Regelung
  (`Zigbee2MqttThermostat`), und Schaltbefehle an Meross-Steckdosen über eine
  zwischengespeicherte Cloud-Sitzung (`services/meross_session.py`), außerhalb jeder
  Datenbanktransaktion.
- **Ein gescheiterter Befehl wird jeden scharfen Zyklus erneut versucht** — unbegrenzt
  oft und nur einmal pro Ausfallepisode geloggt; der Zwischenspeicher „nur bei
  Änderung senden" trägt das Ergebnis im Schlüssel, damit ein gescheiterter Befehl das
  Gerät nicht dauerhaft überspringt. Gilt unverändert für Zigbee2MQTT, wo ein
  gescheiterter Befehl keine erneute Anmeldung nach sich zieht. **Für Meross gilt es
  nicht mehr:** eine abgelehnte Cloud-Anmeldung wartet einen wachsenden, gedeckelten
  Backoff ab, statt im nächsten Zyklus sofort erneut anzumelden — siehe den Abschnitt
  oben zur Anmeldesperre.
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
- **Ingress-Präfix**: wird pro Anfrage anhand der gegen `THERMOCTL_ROOT_PATH`
  geprüften Kopfzeile `X-Ingress-Path` gesetzt (Details oben). Lokale Verweise,
  Cookie-`path` und statische Dateien folgen dem jeweiligen Zugangsweg.
  `/healthz` bleibt direkt erreichbar.
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

Sechs Arten lassen sich anlagenweit einzeln abschalten, unter „Einstellungen“:
**Sensorstörung**, **Brücke oder Broker weg**, **Schaltbefehl gescheitert**,
**festhängender Messwert**, **Fenster-Alarm** und **Mieter-Problemmeldung**.
Die Schalter sind ab Werk an. Automatische Störungsmeldungen folgen den
Zustandsübergängen samt Entwarnung; Mieter-Problemmeldungen werden ausdrücklich
vom Mieter ausgelöst. Home Assistant bleibt entkoppelt: Wer den Webhook stilllegt,
verliert den Problemsensor dort nicht.

Ein **Testknopf** unter „Einstellungen" schickt eine gekennzeichnete Testmeldung über
denselben Weg wie eine echte und zeigt Statuscode, Dauer und im Fehlerfall den Grund
unmittelbar auf der Seite; ohne hinterlegten Webhook wird er nicht angeboten, gegen
wiederholtes Auslösen liegt ein Zeitabstand von zehn Sekunden davor. Daneben steht der
**Zustellzustand** — wann zuletzt versucht, mit welchem Ergebnis; „noch nie versucht"
ist ein eigener Zustand. Das Audit-Protokoll unterscheidet `sent` (Versuch ging los,
unabhängig vom Netzerfolg) von `suppressed` (durch einen abgeschalteten Schalter nie
versucht).

Die Grundfelder für Meldungsschalter und Zustellzustand stammen aus Migration
`67e794059830`; weitere Meldungsarten sind oben bei ihren Funktionen beschrieben.

## Kiosk, Ladeanzeige, Sprache

- **`kiosk.html`** trägt den AGPL-§13-Quelltextverweis knapp in der Kopfzeile
  (`target="_blank"`) statt einer Fußzeile — abweichend von der Fußzeile in `base_core.html`, weil
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
