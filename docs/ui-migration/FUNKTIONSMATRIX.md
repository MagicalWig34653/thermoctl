# Funktionsmatrix UI-Migration (Bestand v0.17.1 nach Svelte)

Nachverfolgbare Zuordnung `Bestandsfunktion -> Svelte-Ansicht -> Endpunkt/Adapter -> Berechtigung -> Test -> Status` für die gesamte heutige Weboberfläche. Sie ist die Abnahmegrundlage für „jede produktive Funktion ist einer neuen Ansicht zugeordnet“ (ENTSCHEIDUNGEN-UI-REDESIGN §8).

Quellen: `thermoctl/web/*_views.py` und `navigation.py` (v0.17.1, 126 Routen-Dekoratoren in 25 Modulen), Inventurberichte Web / API+Build / Demo, `svelte-demo/FUNKTIONSMATRIX.md` (v6), `ENTSCHEIDUNGEN-UI-REDESIGN.md` §2 (sieben Menübereiche). Berechtigungen und Tests stammen aus dem Quelltext bzw. der Inventur; die Testsuiten wurden dafür nicht neu ausgeführt.

## Legende

- **Svelte-Ansicht:** `Bereich > Seiten-ID` nach den sieben Menübereichen (Übersicht, Zeit & Abwesenheit, Auswertung, Geräte, Heizung & Regelung, System & Betrieb, Personen & Zugänge) plus „Konto“ und „Hilfe“ am unteren Rand sowie „Kiosk“ ohne Shell. Seiten-IDs sind die der Demo (`home`, `room/<id>`, `schedule/<id>`, `devices`, `mapping`, ...). Die URL-Form im Produkt (Hash- oder History-Router, Präfix) ist offen, siehe „Offene Fragen“.
- **Endpunkt/Adapter:** Heute liefern die Views nur Jinja-Kontext. „UI-Lesemodell“ und „Schreibweg“ bezeichnen eine **neu zu schaffende** JSON-Schnittstelle über der bestehenden Domänenfunktion (Grundsatz 6: Logik nicht im Adapter). „REST `...`“ = Endpunkt unter `/api/v1` existiert, ist aber Bearer-only; für die Oberfläche braucht er Sitzungsauth + CSRF. „vorhanden JSON“ = liefert heute schon JSON.
- **Berechtigung:** `vz(x)` = `visible_zones(..., x)`; nicht sichtbare Zone = 404. Profilwächter (`admin_ui_only`/`tenant_ui_only`) ist keine Berechtigung und entfällt mit der gemeinsamen Oberfläche; alle Rechte müssen serverseitig weiter geprüft werden.
- **Test:** vorhandene Python-Tests unter `tests/` (Präfix `browser` = `browser_tests/`). Für die Migration sind sie die Regressionsbasis; neue Tests je Endpunkt kommen hinzu (CLAUDE.md).
- **Status:** `offen` = Bestand hat ein Demo-Pendant, Umsetzung steht aus. **`Lücke`** = Bestandsfunktion ohne Demo-Pendant oder die Demo bildet die Bestandssemantik ausdrücklich nicht ab (Demo-Grenze lt. Demo-Matrix); hier fehlt zuerst eine Entwurfsentscheidung.


### A1 Anmeldung, Einrichtung, Konto, Hilfe

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R001 | `GET /` | Startseite: Zonenstatus, Notbetrieb-Banner, Sollwert, Hinweise (Admin- und Mieter-Hülle) | Übersicht > home | UI-Lesemodell `zone_status_context` (neu, Polling statt htmx-Seitenpoll) | zone.read (vz); override.create/report.create nur Bedienelemente | test_smoke_test, test_zone_views, test_tenant_views, test_emergency_display(_http) | offen |
| R002 | `GET /login` | Anmeldeformular, return_to, Passkey-Button | Konto > login (ohne Shell) | Svelte-`LoginView` (+ `GET /ui-api/login-options` für den Passkey-Knopf, `?return_to=` aus der Adresse); die HTML-Seite `/login` bleibt als Rücksprungziel von `/oauth/consent` bestehen | keine | test_login, test_login_weiterleitung, test_ui_api_auth | umgesetzt (ui/auth-flows), Legacy-Seite bleibt |
| R003 | `POST /login` | Anmeldung, Sitzung + CSRF-Cookie, Fehlversuchsverzögerung | Konto > login | `POST /ui-api/login` (`attempt_login`); optional `return_to`, Antwort enthält nur das von `safe_return_to` bestätigte Ziel | keine; CSRF nur mit altem Cookie | test_login, test_auth_sessions, test_csrf, test_ui_api_login, test_ui_api_auth | umgesetzt |
| R004 | `POST /logout` | Abmelden, Cookies löschen | Konto > Abmelden | Sitzungs-Logout (`revoke_session`) | keine | test_login, browser test_login_logout | offen |
| R005 | `GET /setup` | Ersteinrichtung (nur ohne Benutzer) | Konto > setup (ohne Shell) | `GET /ui-api/setup` (nur `{open: true}`, 404 nach Abschluss) + Svelte-`SetupApp` unter `/app/setup`; HTML-`/setup` bleibt (Ziel der Startseite) | Setup-Token | test_setup, test_ui_api_auth | umgesetzt (ui/auth-flows), Legacy-Seite bleibt |
| R006 | `POST /setup` | Einrichtung abschließen (`run_setup`, Setup-Token einmalig) | Konto > setup | `POST /ui-api/setup` (`run_setup`, Feldfehler mit `field`, 404 nach Abschluss, 403 bei ungültigem Token); Zeitzone wird jetzt auch im Bereich der Domäne geprüft (`InvalidTimezone`, beide Adapter) | Setup-Token | test_setup, test_ui_api_auth | umgesetzt |
| R007 | `GET /account` | Kontoseite: Profil, Passwort, Sitzungen | Konto > account | UI-Lesemodell Konto (neu) | nur Anmeldung | test_account_views | offen |
| R008 | `POST /account/password` | Eigenes Passwort ändern, andere Sitzungen beenden | Konto > account | Schreibweg `set_password` + `revoke_all_sessions` | nur Anmeldung | test_account_views, test_csrf | offen |
| R009 | `POST /account/sessions/revoke-others` | Andere eigene Sitzungen beenden | Konto > account (Sitzungsübersicht) | Schreibweg `revoke_all_sessions` | nur Anmeldung | test_account_views | offen |
| R010 | `GET /account/help` | Hilfeseite (je Profil) | Hilfe (unten) > help | statischer Inhalt | nur Anmeldung | test_smoke_test | offen |
| R011 | `GET /glossar` | Glossar mit Filter | Hilfe (unten) > glossary | UI-Lesemodell `default_glossary` (Quelle `data/glossar.json`) | nur Anmeldung | test_glossary, test_glossary_views, browser test_glossary | offen |
| R012 | `GET /passkeys` | Eigene Passkeys auflisten | Konto > account (Passkeys) | UI-Lesemodell `UserPasskey` (neu) | nur Anmeldung, nur eigene | test_passkey | **Lücke** |
| R013 | `POST /passkey/authentication/options` | WebAuthn Anmeldung: Challenge | Konto > login | vorhanden JSON, unverändert genutzt (Laufzeitpräfix, CSRF-Header aus Cookie) | keine (RP-Id nötig) | test_passkey, test_ui_api_auth | umgesetzt |
| R014 | `POST /passkey/authentication/verify` | WebAuthn Anmeldung: Prüfung, Sitzung setzen | Konto > login | vorhanden JSON; Anfrage darf `return_to` tragen, Antwort enthält es nur, wenn `safe_return_to` es bestätigt (`redirect` bleibt `{prefix}/` für die Alt-Seite) | keine | test_passkey, test_ui_api_auth | umgesetzt |
| R015 | `POST /passkey/registration/options` | Passkey anlegen: Challenge | Konto > account (Passkeys) | vorhanden JSON, übernehmen | nur Anmeldung | test_passkey | **Lücke** |
| R016 | `POST /passkey/registration/verify` | Passkey anlegen: Abschluss | Konto > account (Passkeys) | vorhanden JSON, übernehmen | nur Anmeldung | test_passkey | **Lücke** |
| R017 | `POST /passkeys/{passkey_id}/remove` | Eigenen Passkey entfernen | Konto > account (Passkeys) | Schreibweg `remove_passkey` | nur Anmeldung, nur eigene | test_passkey | **Lücke** |
| R018 | `GET /oauth/consent` | OAuth-Zustimmung für MCP-Client (Rechtebereiche, Voreinstellung) | Konto > oauth | **bewusste Ausnahme: bleibt serverseitig** (siehe „Entscheidung Auth-Flows“ unten) | token.self | test_web_oauth_consent, browser test_oauth_consent | Ausnahme, unverändert |
| R019 | `POST /oauth/consent` | Zustimmung erteilen oder ablehnen, Weiterleitung an Client | Konto > oauth | **bewusste Ausnahme: bleibt serverseitig** (`approve`/`deny`, absolute Weiterleitung auf den Client) | token.self; Teilmenge eigener Grants | test_web_oauth_consent, test_domain_oauth | Ausnahme, unverändert |
| R020 | `POST /zones/{zone_id}/report` | Problem melden (Raumbezug) | Konto > Probleme melden / Raumdetail | Schreibweg `build_report` | report.create (vz) | test_smoke_test, test_notification_settings | offen |

### A2 Räume (Zonen), Raumdetail, Sollwerte, Bedienung

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R021 | `GET /zones` | Raumliste mit Bearbeiten-Rechten | Übersicht > home (Raumliste/-tabelle) | REST `GET /api/v1/zones` (Sitzungsauth neu) | zone.read (vz); zone.manage je Zeile | test_zone_views | offen |
| R022 | `GET /zones/new` | Raum-Formular neu | Übersicht > home (Raum anlegen) | UI-Lesemodell (neu) | zone.manage | test_zone_views | offen |
| R023 | `POST /zones` | Raum anlegen | Übersicht > home | Schreibweg `create_zone` | zone.manage | test_zone_views | offen |
| R024 | `GET /zones/{zone_id}` | Raum bearbeiten inkl. Verlaufsgrafik | Übersicht > room/<id> | UI-Lesemodell + `chart_for_zone` | zone.manage (vz) | test_zone_views, test_zone_history_chart | offen |
| R025 | `POST /zones/{zone_id}` | Raum speichern | Übersicht > room/<id> | Schreibweg `update_zone` | zone.manage (vz) | test_zone_views | offen |
| R026 | `GET /zones/{zone_id}/delete` | Löschbestätigung mit Abhängigkeiten | Übersicht > room/<id> (Löschen-Dialog) | UI-Lesemodell `zone_dependencies` | zone.manage (vz) | test_zone_views | offen |
| R027 | `POST /zones/{zone_id}/delete` | Raum löschen | Übersicht > room/<id> | Schreibweg `delete_zone` | zone.manage (vz) | test_zone_views | offen |
| R028 | `GET /zones/{zone_id}/parameters` | Regelparameter, PI-Zulässigkeit, Sensorausfall-Parameter, Ersatz-Offset | Übersicht > room/<id> (Technik) | REST `GET /api/v1/zones/{id}/parameters` + Effektivwerte (neu) | zone.manage (vz); device.manage für Teilfelder | test_daily_views(+_backup_offset,_solar), test_api_configuration | offen |
| R029 | `POST /zones/{zone_id}/parameters` | Regelparameter speichern (Hysterese, PI, Ventilschutz, Fenster) | Übersicht > room/<id> (Technik) | REST `PUT .../parameters` + Feldfehler (neu) | zone.manage (vz); device.manage Teilfelder | test_daily_views, browser test_parameter_page_single_save, test_pi_switch | offen |
| R030 | `GET /zones/{zone_id}/setpoints` | Modus-Sollwerte je Raum | Übersicht > room/<id> (Sollwerte) | REST `GET /api/v1/zones/{id}/setpoints` | setpoint.write (vz) | test_mode_views | offen |
| R031 | `POST /zones/{zone_id}/setpoints` | Modus-Sollwerte speichern | Übersicht > room/<id> (Sollwerte) | REST `PUT .../setpoints` | setpoint.write (vz) | test_mode_views | offen |
| R032 | `POST /zones/{zone_id}/override` | Befristetes Ziel setzen (bis nächste Schaltung) | Übersicht > room/<id> / home-Kachel | REST `POST /api/v1/zones/{id}/override` | override.create (vz); kein Profilwächter | test_daily_views, test_tenant_views | offen |
| R033 | `POST /zones/{zone_id}/override/cancel` | Befristetes Ziel aufheben | Übersicht > room/<id> | REST `DELETE .../override` | override.cancel (vz) | test_daily_views | offen |
| R034 | `POST /zones/{zone_id}/thermostat` | Sollwert-Stepper (Heimsicht) | Übersicht > room/<id> (Stepper) | Schreibweg `step_setpoint` (neu) | setpoint.write (vz) | test_daily_views, test_tenant_views | offen |
| R035 | `POST /zones/{zone_id}/jump-next` | Nächste Phase vorziehen | Übersicht > room/<id> | Schreibweg `jump_to_next_switch` | override.create (vz) | test_daily_views | offen |

### A3 Zeitplan, Urlaub, Abwesenheit, Modi

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R036 | `GET /zones/{zone_id}/schedule` | Wochenplan Raum (Segmente, Wärmefarbe) | Zeit & Abwesenheit > schedule/<id> | UI-Lesemodell `week_segments` (REST liefert nur Punkte) | zone.read (vz); schedule.manage für Bedienelemente | test_schedule_views, test_domain_schedule, browser test_schedule_editor | offen |
| R037 | `POST /zones/{zone_id}/schedule/points` | Schaltpunkt anlegen | Zeit & Abwesenheit > schedule/<id> (Inspektor) | Schreibweg `create_schedule_point` (Absicht) | schedule.manage (vz) | test_schedule_views | offen |
| R038 | `POST /zones/{zone_id}/schedule/points/move` | Schaltpunkt verschieben (Drag/Drop) | Zeit & Abwesenheit > schedule/<id> | Schreibweg `move_schedule_point` | schedule.manage (vz) | test_schedule_views | offen |
| R039 | `POST /zones/{zone_id}/schedule/paint` | Zeitbereich übermalen | Zeit & Abwesenheit > schedule/<id> (Aufziehen, Shift) | Schreibweg `paint_schedule_interval` (Absicht) | schedule.manage (vz) | test_schedule_views | offen |
| R040 | `POST /zones/{zone_id}/schedule/copy-day` | Tag kopieren | Zeit & Abwesenheit > schedule/<id> | Schreibweg `copy_schedule_day` | schedule.manage (vz) | test_schedule_views | offen |
| R041 | `POST /zones/{zone_id}/schedule/undo` | Letzte Geste zurücknehmen | Zeit & Abwesenheit > schedule/<id> (Undo) | Schreibweg `undo_schedule_gesture` (serverseitig) | schedule.manage (vz) | test_schedule_views | offen |
| R042 | `POST /zones/{zone_id}/schedule/points/mode` | Modus eines Schaltpunkts ändern | Zeit & Abwesenheit > schedule/<id> (Inspektor) | Schreibweg `change_schedule_point_mode` | schedule.manage (vz) | test_schedule_views | offen |
| R043 | `GET /zones/{zone_id}/schedule/points/{point_id}/delete` | Löschbestätigung Schaltpunkt | Zeit & Abwesenheit > schedule/<id> (Entfernen) | UI-Lesemodell | schedule.manage (vz) | test_schedule_views | offen |
| R044 | `POST /zones/{zone_id}/schedule/points/{point_id}/delete` | Schaltpunkt löschen | Zeit & Abwesenheit > schedule/<id> | REST `DELETE` Zeitplanpunkt | schedule.manage (vz) | test_schedule_views | offen |
| R045 | `GET /zones/{zone_id}/schedule/adopt` | Plan aus anderem Raum übernehmen: Auswahl | Zeit & Abwesenheit > schedule/<id> (Raumkopie) | UI-Lesemodell | Ziel schedule.manage, Quelle zone.read | test_schedule_views | offen |
| R046 | `POST /zones/{zone_id}/schedule/adopt` | Plan übernehmen | Zeit & Abwesenheit > schedule/<id> | Schreibweg `adopt_schedule` | Ziel schedule.manage, Quelle zone.read | test_schedule_views | offen |
| R047 | `GET /schedule` | Wochenplan Mieter (Prognosebalken) | Zeit & Abwesenheit > schedule/<id> | UI-Lesemodell `week_segments`, `_forecast_bars` | zone.read (vz); schedule.manage | test_tenant_views | offen |
| R048 | `POST /schedule/day` | Tag bearbeiten (Punktmenge) | Zeit & Abwesenheit > schedule/<id> | Schreibweg create/move/delete Punkt | schedule.manage (vz) | test_tenant_views | offen |
| R049 | `POST /schedule/copy-day` | Tag kopieren (Mieter) | Zeit & Abwesenheit > schedule/<id> | Schreibweg `copy_schedule_day` | schedule.manage (vz) | test_tenant_views | offen |
| R050 | `POST /schedule/adopt` | Plan übernehmen (Mieter) | Zeit & Abwesenheit > schedule/<id> | Schreibweg `adopt_schedule` | Ziel schedule.manage, Quelle zone.read | test_tenant_views | offen |
| R051 | `POST /schedule/undo` | Rücknahme (Mieter) | Zeit & Abwesenheit > schedule/<id> | Schreibweg `undo_schedule_gesture` | schedule.manage (vz) | test_tenant_views | offen |
| R052 | `POST /absence` | Abwesenheit starten (Raumbezug) | Zeit & Abwesenheit > absence | Schreibweg `start_absence` | override.create (vz) | test_absence, test_tenant_views | offen |
| R053 | `POST /absence/end` | Eigene Abwesenheit beenden | Zeit & Abwesenheit > absence | Schreibweg `end_absence` | nur eigene (kein require) | test_absence | offen |
| R054 | `GET /vacation` | Anlagenurlaub anzeigen | Zeit & Abwesenheit > vacation | REST `GET /api/v1/vacation` | zone.read | test_vacation_views, browser test_vacation | offen |
| R055 | `POST /vacation` | Urlaub anlegen | Zeit & Abwesenheit > vacation | REST `POST /api/v1/vacation` | vacation.manage | test_vacation_views | offen |
| R056 | `POST /vacation/cancel` | Urlaub beenden | Zeit & Abwesenheit > vacation | REST `DELETE /api/v1/vacation` | vacation.manage | test_vacation_views | offen |
| R057 | `GET /modes` | Temperaturmodi auflisten | Heizung & Regelung > modes | REST `GET /api/v1/modes` | mode.manage | test_mode_views | offen |
| R058 | `GET /modes/new` | Modus-Formular neu | Heizung & Regelung > modes | UI-Lesemodell | mode.manage | test_mode_views | offen |
| R059 | `POST /modes` | Modus anlegen | Heizung & Regelung > modes | REST `POST /api/v1/modes` | mode.manage | test_mode_views | offen |
| R060 | `GET /modes/{mode_id}` | Modus bearbeiten | Heizung & Regelung > modes | UI-Lesemodell | mode.manage | test_mode_views | offen |
| R061 | `POST /modes/{mode_id}` | Modus speichern (Umbenennen) | Heizung & Regelung > modes | Schreibweg `update_mode` (REST kennt kein Update) | mode.manage | test_mode_views | offen |
| R062 | `GET /modes/{mode_id}/delete` | Löschbestätigung Modus (`delete_guard`) | Heizung & Regelung > modes | UI-Lesemodell | mode.manage | test_mode_views | offen |
| R063 | `POST /modes/{mode_id}/delete` | Modus löschen | Heizung & Regelung > modes | Schreibweg `delete_mode` | mode.manage | test_mode_views | offen |

### A4 Auswertung

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R064 | `GET /zones/history` | Verlaufsübersicht aller Räume (Kacheln) | Auswertung > history | UI-Lesemodell `chart_for_zone` (Daten statt SVG, Entscheidung offen) | zone.manage (vz) | test_zone_history_chart(+_honesty,_hover,_series), browser test_zone_history_overview | offen |
| R065 | `GET /zones/{zone_id}/history` | Verlaufsgrafik eines Raums (Zeitraum) | Auswertung > history (Raum) | UI-Lesemodell Serien, Bänder, Hover | zone.manage (vz) | test_zone_history_chart, browser test_zone_history_marker | offen |
| R066 | `GET /temperature-history` | Verlauf Mieter | Auswertung > history | UI-Lesemodell `history_context` | zone.read (vz) | test_tenant_history, browser test_tenant_history | offen |
| R067 | `GET /temperature-history/{zone_id}/tile` | Verlaufskachel Mieter (Fragment) | Auswertung > history (Kachel) | UI-Lesemodell `chart_for_zone` | zone.read (vz) | test_tenant_history | offen |
| R068 | `GET /heating-time` | Heizzeit je Zeitraum (Mieter) | Auswertung > history (Heizzeit) | UI-Lesemodell `heating_periods` | zone.read (vz) | test_tenant_views, test_statistics | offen |
| R069 | `GET /statistics` | Heizstatistik (Anlage) | Auswertung > history (Heizzeit) | UI-Lesemodell `heating_periods` | zone.read | test_statistics | offen |
| R070 | `GET /relay-wear` | Relaisverschleiß, Hochrechnung | Auswertung > wear | UI-Lesemodell `relay_operations` | audit.read; Zonenfilter | test_relay_wear_views, browser test_relay_wear | offen |
| R071 | `GET /audit` | Protokoll mit Filtern (Partial-Swap) | Auswertung > events | UI-Lesemodell `AuditEvent` (neu, Filter/Seiten) | audit.read | test_audit_views | **Lücke** |
| R072 | `GET /device-commands` | Schaltprotokoll (Filter, Seiten) | Auswertung > events | REST `GET /api/v1/device-commands` | audit.read | test_device_commands_views | offen |
| R073 | `GET /changes` | Änderungsprotokoll | Auswertung > events (Rücknahme) | UI-Lesemodell `list_changes` | audit.read | test_changes_views, test_change_log | offen |
| R074 | `GET /changes/{change_id}/revert` | Rücknahme prüfen (Konflikt, `force_token`) | Auswertung > events (Rücknahme-Dialog) | UI-Lesemodell `get_change` | audit.read | test_changes_views (kein Browsertest) | offen |
| R075 | `POST /changes/{change_id}/revert` | Änderung zurücknehmen | Auswertung > events (Rücknahme) | Schreibweg `revert_change` (Rechte in Domäne) | Domäne prüft selbst | test_change_log, browser test_changes | offen |

### A5 Geräte, Zuordnung, Karte, Bediengeräte

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R076 | `GET /devices` | Geräteüberblick, Befunde (`DeviceSurvey`, `findings`) | Geräte > devices | REST `GET /api/v1/devices` + Befunde (neu) | device.read | test_device_views | **Lücke** |
| R077 | `GET /plant` | Anlagendiagramm | Geräte > mapping (Anlagenbild) | UI-Lesemodell `plant_diagram` | device.read; Zonen nach zone.read | test_plant_diagram | offen |
| R078 | `GET /devices/map` | Zuordnungskarte (Knoten, Ports, Ventilbetriebsarten, reading_only) | Geräte > mapping (Gerätekarte) | UI-Lesemodell `build_mapping_map` (Payload heute im HTML-Attribut) | device.read (vz); device.manage je Zone | test_mapping_map(+_multi,_valve), browser test_mapping_map | **Lücke** |
| R079 | `POST /devices/map/connect` | Verbindung ziehen (Quelle/Betriebsart folgt aus Linien) | Geräte > mapping (Gerätekarte) | vorhanden JSON `can_connect`, `mode_for_lines` | device.manage (vz) | test_mapping_map(+_valve) | **Lücke** |
| R080 | `POST /devices/map/disconnect` | Verbindung lösen | Geräte > mapping (Gerätekarte) | vorhanden JSON | device.manage (vz) | test_mapping_map(+_multi) | **Lücke** |
| R081 | `GET /zones/{zone_id}/devices` | Zuordnung je Raum (Rollen, Quellen, Flussbild) | Geräte > devices (Zuordnung/Quellen) | UI-Lesemodell (neu) | device.read (vz); device.manage | test_device_assignment, test_actuator_one_zone | offen |
| R082 | `POST /zones/{zone_id}/devices/assign` | Gerät zuordnen (mehrere Sensoren, exklusiver Aktor) | Geräte > devices (Zuordnung) | Schreibweg `assign_device` | device.manage (vz) | test_device_assignment | offen |
| R083 | `POST /zones/{zone_id}/devices/detach` | Gerät trennen | Geräte > devices (Zuordnung) | Schreibweg `detach_device` | device.manage (vz) | test_device_assignment | offen |
| R084 | `POST /zones/{zone_id}/devices/regulation` | Thermostat-Betriebsart (Ventil) | Geräte > devices (Zuordnung) / mapping | Schreibweg `set_thermostat_mode` | device.manage (vz) | test_device_assignment, test_mapping_map_valve | **Lücke** |
| R085 | `POST /zones/{zone_id}/devices/source` | Temperaturquelle setzen (primär/Ersatz/Offset) | Geräte > devices (Quellen) | Schreibweg `set_temperature_source` | device.manage (vz) | test_device_assignment | offen |
| R086 | `POST /zones/{zone_id}/devices/swap` | Gerät austauschen | Geräte > devices (Austausch) | Schreibweg `swap_device` | device.manage (vz) | test_device_assignment | offen |
| R087 | `POST /zones/{zone_id}/devices/button` | Bedientaste zuordnen (Raumseite) | Geräte > controllers | Schreibweg `set_binding` | device.manage (vz) | test_controller | offen |
| R088 | `GET /controllers` | Bediengeräte, Kanäle, gesehene Aktionen | Geräte > controllers | UI-Lesemodell `controller_zones`, `seen_actions` | device.read; Zonenfilter | test_controller, test_controller_channels | offen |
| R089 | `POST /controllers/channel` | Kanal konfigurieren | Geräte > controllers | Schreibweg `configure_channel` | device.manage (zone) | test_controller_channels | offen |
| R090 | `POST /controllers/button` | Taste zuordnen (Aktion, Raum, Richtung, Wert) | Geräte > controllers | Schreibweg `set_binding` | device.manage (zone) | test_controller | offen |
| R091 | `GET /zones/data-flow` | Datenfluss (Mermaid) Anlage | Geräte > mapping (Datenfluss) | UI-Lesemodell `build_data_flow` (Mermaid-Quelle) | zone.manage (vz) | test_data_flow, browser test_data_flow | **Lücke** |
| R092 | `GET /zones/{zone_id}/data-flow` | Datenfluss eines Raums | Geräte > mapping (Datenfluss) | UI-Lesemodell `build_data_flow` | zone.manage (vz) | test_data_flow | **Lücke** |

### A6 Heizung & Regelung, System & Betrieb

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R093 | `GET /control` | Betriebsübersicht: Entscheidungen, Notbetrieb, Freigabe, Begründung | System & Betrieb > operation | REST `GET /api/v1/control` + Entscheidungsbegründung (neu) | zone.read | test_control_views, test_emergency_display | **Lücke** |
| R094 | `POST /control/arm` | Regelung scharf/unscharf schalten (Trockenlauf) | System & Betrieb > operation | REST `PUT /api/v1/control/armed` | control.arm | test_control_views | offen |
| R095 | `GET /settings` | Regelvorgaben, Standardprofil, Sensorausfall-Defaults, Außentemperatur | Heizung & Regelung > regulation | REST `GET /api/v1/control` | zone.read | test_control_views | offen |
| R096 | `POST /settings` | Regelvorgaben und Standort speichern | Heizung & Regelung > regulation / solar | REST `PUT /control/defaults`, `/control/solar-location` | setting.manage | test_control_views, test_daily_views_solar | offen |
| R097 | `POST /settings/window-alarm` | Fenster-Alarm-Einstellungen | Heizung & Regelung > window | Schreibweg `save_window_alarm_settings` | setting.manage | test_control_views, browser test_window_alarm | offen |
| R112 | `POST /settings/window-temp-drop` | Fenstererkennung per Temperatursturz | Heizung & Regelung > window | Schreibweg `save_window_temp_drop_settings` | setting.manage | test_control_views | offen |
| R099 | `POST /settings/sensor-failure` | Notbetrieb: Profile, Kurve, Rückkehr (Anlagenvorgaben) | Heizung & Regelung > emergency | REST `PUT /control/sensor-failure-defaults` | setting.manage | test_api_sensor_failure, browser test_sensor_failure_curve_rows | offen |
| R100 | `POST /settings/notifications` | Benachrichtigungskanäle speichern | System & Betrieb > notifications | Schreibweg (Setting; View schreibt direkt, in Domäne ziehen) | setting.manage | test_notification_settings | offen |
| R101 | `POST /settings/outdoor-source` | Außentemperatur-Quelle | Heizung & Regelung > outdoor | Schreibweg `set_outdoor_temperature_source` | setting.manage | test_control_views, browser test_outdoor_temperature | offen |
| R102 | `POST /settings/notifications/test` | Test-Benachrichtigung senden | System & Betrieb > notifications | Schreibweg `integrations.notification` | setting.manage | test_notification_settings | offen |
| R103 | `GET /interfaces` | Schnittstellen (MQTT, Zigbee2MQTT, Homebridge, MCP, API) | System & Betrieb > interfaces | UI-Lesemodell `overview`, `homebridge_zone_configs` | setting.manage | test_interfaces, test_homebridge_interface | offen |

### A7 Personen & Zugänge

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R104 | `GET /users` | Benutzerliste | Personen & Zugänge > access | UI-Lesemodell (neu) | user.manage | test_admin_views, test_administration | offen |
| R105 | `POST /users` | Benutzer anlegen | Personen & Zugänge > access | Schreibweg `create_user` | user.manage | test_admin_views | offen |
| R106 | `POST /users/{user_id}/active` | Sperren/Aktivieren | Personen & Zugänge > access | Schreibweg `set_user_active` | user.manage | test_admin_views | offen |
| R107 | `POST /users/{user_id}/group` | Gruppenzuordnung ändern | Personen & Zugänge > access | Schreibweg `set_user_group` (Schutz letzter Gruppenadmin) | user.manage | test_admin_views, test_administration | offen |
| R108 | `POST /users/sessions/revoke-others` | Eigene andere Sitzungen beenden (Adminseite) | Konto > account (Sitzungsübersicht) | Schreibweg `revoke_all_sessions` | eigene Sitzungen | test_admin_views | offen |
| R109 | `POST /users/{user_id}/password` | Passwort setzen, Sitzungen beenden | Personen & Zugänge > access | Schreibweg `set_password` | user.manage | test_admin_views | offen |
| R110 | `GET /groups` | Gruppen, Rechtebereiche | Personen & Zugänge > access | UI-Lesemodell `PERMISSION_AREAS` | group.manage | test_admin_views | offen |
| R111 | `POST /groups` | Gruppe anlegen | Personen & Zugänge > access | Schreibweg `create_group` | group.manage | test_admin_views | offen |
| R112 | `POST /groups/{group_id}/ui-profile` | Oberflächenprofil (admin/tenant) je Gruppe | Personen & Zugänge > access (entfällt oder wird Rechtemodell) | Schreibweg `set_group_ui_profile` | group.manage | test_ui_profile, test_admin_views | **Lücke** |
| R113 | `POST /groups/{group_id}/delete` | Gruppe löschen | Personen & Zugänge > access | Schreibweg `delete_group` | group.manage | test_admin_views | offen |
| R114 | `POST /groups/{group_id}/permissions` | Rechte je Anlage/Raum | Personen & Zugänge > access (Rechte) | Schreibweg `set_group_permissions` | group.manage | test_admin_views, test_authz | offen |
| R115 | `GET /tokens` | Eigene API-Tokens | Personen & Zugänge > tokens | UI-Lesemodell `principal_for_token`, `PRESETS` | token.self | test_web_tokens_presets, test_tokens | offen |
| R116 | `POST /tokens` | API-Token ausstellen (Klartext einmalig) | Personen & Zugänge > tokens | Schreibweg `issue_token` | token.self; Teilmenge eigener Rechte | test_web_tokens_presets | offen |
| R117 | `POST /tokens/{token_id}/permissions` | Token-Rechte ändern | Personen & Zugänge > tokens | Schreibweg (View) | token.self | test_web_tokens_presets | offen |
| R118 | `POST /tokens/{token_id}/revoke` | Token widerrufen | Personen & Zugänge > tokens | Schreibweg `revoke_token` | token.self | test_tokens | offen |
| R119 | `GET /kiosk-tokens` | Kiosk-Zugänge | Personen & Zugänge > kiosktokens | UI-Lesemodell `kiosk_scope` | token.manage | test_kiosk | offen |
| R120 | `POST /kiosk-tokens` | Kiosk-Token ausstellen (Klartext einmalig) | Personen & Zugänge > kiosktokens | Schreibweg `issue_kiosk_token` | token.manage | test_kiosk | offen |
| R121 | `POST /kiosk-tokens/{token_id}/revoke` | Kiosk-Token widerrufen | Personen & Zugänge > kiosktokens | Schreibweg `revoke_token` | token.manage | test_kiosk | offen |

### A8 Kiosk (eigene Hülle, eigenes Cookie)

| Nr | Methode + Pfad (Bestand) | Bestandsfunktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| R122 | `GET /kiosk/{plaintext}` | Kiosk-Token einlösen, Cookie setzen, weiterleiten | Kiosk (ohne Shell) | Seite/Handler bleibt serverseitig (Cookie `thermoctl_kiosk`) | Kiosk-Token (is_kiosk) | test_kiosk, browser test_kiosk | **Lücke** |
| R123 | `GET /kiosk` | Kiosk-Ansicht (Räume, Ansicht, 20-s-Poll) | Kiosk (ohne Shell) | UI-Lesemodell Kiosk-Dashboard (neu; Betrieb ohne JS entfällt) | zone.read (Kiosk-Principal) | test_kiosk, browser test_kiosk | offen |
| R124 | `POST /kiosk/zones/{zone_id}/setpoint` | Sollwert +/- 0,5 Grad | Kiosk (ohne Shell) | Schreibweg `set_setpoint(source="kiosk")` | setpoint.write (Kiosk-Principal) | test_kiosk | offen |
| R125 | `POST /kiosk/zones/{zone_id}/boost` | Boost | Kiosk (ohne Shell) | Schreibweg `remote_control.boost` | override.create | test_kiosk | offen |
| R126 | `POST /kiosk/zones/{zone_id}/override/cancel` | Befristetes Ziel aufheben | Kiosk (ohne Shell) | Schreibweg `cancel_override` | override.cancel | test_kiosk | offen |

### A9 Außerhalb `web/`

| Nr | Pfad (Bestand) | Funktion | Svelte-Ansicht | Endpunkt/Adapter | Berechtigung | Test | Status |
|---|---|---|---|---|---|---|---|
| X01 | `GET /static/*` | CSS, JS, Vendor, künftig Svelte-Bundle | Träger aller Ansichten | `app._serve_static`; Bundle-Pfad `static/app/...` muss in Package-Data und `RESERVED_MCP_PATHS` | keine | test_assets, test_ingress_prefix | offen |
| X02 | `GET /docs`, `GET /openapi.json` | Swagger-UI lokal | unverändert (kein Svelte) | FastAPI | keine | test_app | offen |
| X03 | `GET /healthz` | Liveness `{status, version}` | unverändert | `app.py` | keine | test_app | offen |
| X04 | `* /api/v1/*` | REST für Skripte/Integrationen | unverändert; Grundlage der UI-Lesemodelle | `api/routes.py` | Bearer-Token | test_api, test_api_configuration, test_api_sensor_failure | offen |
| X05 | `* /mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/*` | MCP und OAuth-Server | unverändert; `/authorize` leitet auf `/oauth/consent` (R018) | `mcp/*` | Bearer/OAuth | test_mcp, test_mcp_oauth_routes, test_domain_oauth | offen |

## B. Zeilen der Demo-Funktionsmatrix (v6)

Jede Zeile der Demo-Matrix (35 Bereiche) mit den zugehörigen Bestandsrouten aus A (R-Nummern).

| Nr | Demo-Bereich | Svelte-Ansicht | Bestandsrouten | Endpunkt/Adapter-Bedarf | Test | Status |
|---|---|---|---|---|---|---|
| D01 | Übersicht/Räume (Dashboard-Layouts, Galerie, Raumliste, Raum-CRUD) | Übersicht > home | R001, R021, R023, R022, R021, R023; R024, R025, R026, R027 | Lesemodell Zonenstatus + Raum-CRUD; **Dashboard-Layouts haben keinen Bestand** (Persistenz offen) | test_zone_views, test_smoke_test | offen |
| D02 | Raumsteuerung (Modus-Sollwerte, Stepper, befristetes Ziel, Phase vorziehen, Auto/Manuell/Frostschutz) | Übersicht > room/<id> | R030, R031, R032, R033, R034, R035 | REST setpoints/override, Schreibweg Stepper | test_mode_views, test_daily_views | offen |
| D03 | Raumtechnik (Parameter, Hysterese, PI, Mindestzeiten, Ventilschutz) | Übersicht > room/<id> (Technik) | R028, R029 | REST parameters; Effektivwerte/Vererbung vom Server (Demo: offen) | test_daily_views, test_api_configuration | offen |
| D04 | Zeitplan (Wocheneditor, Gesten, Kopieren, Übernahme) | Zeit & Abwesenheit > schedule/<id> | R036, R037, R038, R039, R040, R041, R042, R043, R044, R045, R046, R047, R048, R049, R050, R051 | Absichts-API (Bereich setzen/verschieben/duplizieren), serverseitiges Undo, Versionskonflikt | test_schedule_views, test_domain_schedule, test_tenant_views | offen |
| D05 | Vorschau (24-h-Zielverlauf) | Zeit & Abwesenheit > schedule/<id> | R036, R047 | Server-Forecast mit Prioritäten und Zeitzone (Bestand: `_forecast_bars`) | test_tenant_views | offen |
| D06 | Abwesenheit/Urlaub | Zeit & Abwesenheit > absence, vacation | R052, R053, R054, R055, R056 | Schreibwege; Prioritätsauflösung serverseitig | test_absence, test_vacation_views | offen |
| D07 | Modi (CRUD, Umbenennen) | Heizung & Regelung > modes | R057, R059, R058, R060, R061, R062, R063 | REST + `update_mode`; `delete_guard` | test_mode_views | offen |
| D08 | Geräte (CRUD, Suche, Fähigkeit/Integration/Adresse) | Geräte > devices | R076, R077 | REST devices + Befunde; **Bestand kennt kein Geräte-CRUD in der Oberfläche** (Demo-Neu, Discovery serverseitig) | test_device_views | **Lücke** |
| D09 | Zuordnungen (Rollen, Mehrfachsensoren, exklusive Aktoren, Quelle/Ersatz/Offset) | Geräte > devices (Zuordnung/Quellen) | R081, R082, R083, R085, R084 | Schreibwege; echtes Fähigkeitsmodell, `reading_only`, Ventilbetriebsarten fehlen in der Demo | test_device_assignment, test_actuator_one_zone | **Lücke** |
| D10 | Anlagenbild/Datenfluss (Karte, Ports, Verbindungen, Pan/Zoom) | Geräte > mapping | R078, R079, R080, R077, R091, R092 | Karten-JSON vorhanden; Ventilbetriebsarten, `reading_only`, Mehrfachsensoren, Mermaid-Datenfluss nicht in Demo | test_mapping_map(+_multi,_valve), test_data_flow | **Lücke** |
| D11 | Geräteaustausch | Geräte > devices (Austausch) | R086 | Schreibweg `swap_device` | test_device_assignment | offen |
| D12 | Bediengeräte (Kanal, Taste, Aktion, Raum, Richtung, Wert) | Geräte > controllers | R088, R089, R090, R087 | Schreibwege; kein Audit in `set_binding`/`configure_channel` (siehe Offene Fragen) | test_controller, test_controller_channels | offen |
| D13 | Notbetrieb (Profile, Festtakt, Kurve, Rückkehr, Raumprofil) | Heizung & Regelung > emergency | R099, R028, R029 | REST sensor-failure-defaults; Takt-Zustandsmaschine serverseitig | test_api_sensor_failure, test_emergency_display | offen |
| D14 | Fenster/Alarm | Heizung & Regelung > window | R097, R112, R028, R029 | Schreibwege | test_control_views, browser test_window_alarm | offen |
| D15 | Solar/Außenfühler | Heizung & Regelung > solar, outdoor | R095, R096, R101 | REST solar-location; Beispielrechnung ist keine Prognose | test_daily_views_solar, browser test_outdoor_temperature | offen |
| D16 | Verbindungen (Zigbee2MQTT, Meross, HA, Homebridge, MCP) | System & Betrieb > interfaces | R103 | UI-Lesemodell `overview`, `homebridge_zone_configs` | test_interfaces, test_homebridge_interface | offen |
| D17 | Meldungen (Kanäle, Webhook, Test) | System & Betrieb > notifications | R100, R102 | Schreibweg; Testversand wirklich (Demo: Simulation) | test_notification_settings | offen |
| D18 | Verbund/Betrieb (Freigabe mit Grund, MQTT-Startriegel, Führungsrolle, Trockenlauf) | System & Betrieb > operation, cluster | R093, R094 | REST control/armed; **Trockenlauf/Schattenbetrieb-Status, MQTT-Riegel, Führungsrolle, Entscheidungsbegründung ohne Demo-Pendant im Bestandsvertrag** | test_control_views, test_emergency_display | **Lücke** |
| D19 | Verläufe (Raum/Zeitraum, Bänder, Hover, Zoom, Tabelle, CSV) | Auswertung > history | R064, R065, R066, R067 | Historie/Serien vom Server statt SVG-Fragment; CSV, Datentabelle neu | test_zone_history_chart(+_honesty,_hover,_series), test_tenant_history | offen |
| D20 | Heizzeit/Verschleiß | Auswertung > history, wear | R068, R069, R070 | UI-Lesemodelle `heating_periods`, `relay_operations` | test_statistics, test_relay_wear_views | offen |
| D21 | Ereignisse (Art/Raum/Datum/Text, Details, CSV) | Auswertung > events | R071, R072 | Filter/Seiten; Bestand hat Audit **und** Schaltprotokoll getrennt; Demo-Daten sind Beispiele | test_audit_views, test_device_commands_views | **Lücke** |
| D22 | Änderungsrücknahme (Snapshot, Konflikt, Schreibrecht) | Auswertung > events (Rücknahme) | R073, R074, R075 | `revert_change`, HMAC `force_token`; Bestand ist sitzungsübergreifend, Demo nur 20 Aktionen/Sitzung | test_change_log, test_changes_views | offen |
| D23 | Gruppen (Rechte je Anlage/Raum) | Personen & Zugänge > access | R110, R111, R112, R113, R114 | Schreibwege; `ui_profile` fällt konzeptionell weg (R112) | test_admin_views, test_authz, test_ui_profile | offen |
| D24 | Personen (Gruppen, Sperren, Passwort, Sitzungen, letzter Admin) | Personen & Zugänge > access | R104, R105, R106, R107, R108, R109 | Schreibwege | test_admin_views, test_administration | offen |
| D25 | API-Tokens (Rechte, Ablauf, Klartext einmalig, Widerruf) | Personen & Zugänge > tokens | R115, R116, R117, R118 | Schreibwege; Teilmenge eigener Rechte serverseitig | test_web_tokens_presets, test_tokens | offen |
| D26 | Kioskzugänge (Rechte je Raum/Aktion, Ablauf, Widerruf) | Personen & Zugänge > kiosktokens | R119, R120, R121 | Schreibwege `issue_kiosk_token` | test_kiosk | offen |
| D27 | Kiosk (eigene Oberfläche, Raumwahl, Änderung, Rückkehr) | Kiosk (ohne Shell) | R119, R120, R121, R122, R123, R124, R125, R126 | Eigener Kiosk-Principal, Kiosk-CSRF; Betrieb ohne JS entfällt | test_kiosk, browser test_kiosk | **Lücke** |
| D28 | Konto/Login (Profil, Passwort, Anmeldung, Abmeldung, Sitzungen) | Konto > account, login | R002, R003, R004, R007, R008, R009 | Echte Sitzung, CSRF, Cookie-Pfad je Ingress | test_login, test_account_views, test_csrf | offen |
| D29 | Passkeys (Datensätze anlegen/entfernen) | Konto > account (Passkeys) | R012, R013, R014, R015, R016, R017 | WebAuthn-Aufruf (Demo: nur Datensätze) | test_passkey | **Lücke** |
| D30 | Einrichtung (mehrstufig, Trockenlauf) | Konto > setup | R005, R006 | `run_setup` mit Setup-Token (Demo: Trockenlauf) | test_setup | offen |
| D31 | OAuth (lokaler Zustimmungsablauf) | Konto > oauth | R018, R019 | `approve`/`deny`, Code-Austausch (Demo: keiner) | test_web_oauth_consent, test_domain_oauth | offen |
| D32 | Probleme melden (Formular, Liste) | Konto > Probleme melden | R020 | Schreibweg `build_report`; Liste hat keinen Bestand | test_smoke_test | offen |
| D33 | Glossar (Katalog, Suche, Kategorien) | Hilfe > glossary | R011, R010 | `data/glossar.json` | test_glossary, test_glossary_views | offen |
| D34 | Oberfläche (sieben Bereiche, Einstellungssuche, Akzent, Hell/Dunkel, mobile Navigation) | App-Shell | ; `navigation.py` (24 Einträge, Abschnitt C) | `visible_navigation` als Server-Navigationsbaum je Principal; Akzent serverseitig je Konto (Demo: Local Storage) | test_navigation, test_ui_profile, browser test_navigation | offen |
| D35 | Offline/Speicher (Schreibschutz-Simulation, Local Storage) | App-Shell | – | **Kein Bestand**; echte Offline-/Sessionablauf-Behandlung ohne stille Heizbefehle ist Akzeptanzkriterium (§8) | – | **Lücke** |

## C. Navigationseinträge (`navigation.py`, 24 Einträge plus Konto)

Der Navigationsvertrag (`NAVIGATION_ITEMS`, Test `test_navigation_permissions_match_destination_guards`) wandert als Server-Navigationsbaum in die neue Shell; die neue Shell zeigt sieben Bereiche, nicht 24 Punkte.

| Nr | Alter Menüpfad | Label | Neue Ansicht | Berechtigung | Status |
|---|---|---|---|---|---|
| N01 | `/` | Übersicht / Zuhause | Übersicht > home | – | offen |
| N02 | `/zones` | Zonen | Übersicht > home (Räume sind Teil der Übersicht, Menüpunkt entfällt) | zone.read | offen |
| N03 | `/devices` | Geräte | Geräte > devices | device.read | offen |
| N04 | `/control` | Betrieb | System & Betrieb > operation | zone.read | offen |
| N05 | `/statistics` | Heizstatistik | Auswertung > history | zone.read | offen |
| N06 | `/zones/history` | Zonenverläufe | Auswertung > history | zone.manage | offen |
| N07 | `/zones/data-flow` | Datenfluss | Geräte > mapping | zone.manage | offen |
| N08 | `/relay-wear` | Relaisverschleiß | Auswertung > wear | audit.read | offen |
| N09 | `/audit` | Protokoll | Auswertung > events | audit.read | offen |
| N10 | `/device-commands` | Schaltprotokoll | Auswertung > events | audit.read | offen |
| N11 | `/changes` | Änderungsprotokoll | Auswertung > events | audit.read | offen |
| N12 | `/settings` | Regelvorgaben | Heizung & Regelung > regulation | zone.read (lesen), setting.manage (schreiben) | offen |
| N13 | `/modes` | Sollwert-Modi | Heizung & Regelung > modes | mode.manage | offen |
| N14 | `/vacation` | Urlaub | Zeit & Abwesenheit > vacation | zone.read / vacation.manage | offen |
| N15 | `/interfaces` | Schnittstellen | System & Betrieb > interfaces | setting.manage | offen |
| N16 | `/controllers` | Bediengeräte | Geräte > controllers | device.read | offen |
| N17 | `/devices/map` | Zuordnungskarte | Geräte > mapping | device.read | offen |
| N18 | `/users` | Benutzer | Personen & Zugänge > access | user.manage | offen |
| N19 | `/groups` | Gruppen | Personen & Zugänge > access | group.manage | offen |
| N20 | `/tokens` | API-Tokens | Personen & Zugänge > tokens | token.self | offen |
| N21 | `/kiosk-tokens` | Kiosk-Tokens | Personen & Zugänge > kiosktokens | token.manage | offen |
| N22 | `/schedule` | Zeitplan (Mieter) | Zeit & Abwesenheit > schedule/<id> | zone.read | offen |
| N23 | `/heating-time` | Heizzeit (Mieter) | Auswertung > history | zone.read | offen |
| N24 | `/temperature-history` | Verlauf (Mieter) | Auswertung > history | zone.read | offen |
| N25 | `/account` | Mehr / Konto | Konto > account | nur Anmeldung | offen |
| N25 | `/glossar`, `/passkeys` (nur Kontomenü/Fußzeile) | Hilfe, Passkeys | Hilfe > glossary; Konto > account | nur Anmeldung | offen |

## D. Alte Direktlinks -> neue Ziele

Ziele sind Ansichts-IDs der Demo; die konkrete URL-Form (Router, Präfix) ist offen. Alle Links müssen direkt **und** hinter wechselndem Ingress-Präfix funktionieren (§8). GET-Pfade mit Formular-POST-Gegenstück (Löschbestätigungen) leiten auf die Ansicht, die den Dialog öffnet. Bookmarks mit `?zone=`, `?period=`, `?return_to=` behalten ihre Parameter.

| Alter Pfad | Neues Ziel | Anmerkung |
|---|---|---|
| `/` | `home` | Profilweiche (admin/tenant) entfällt |
| `/zones`, `/zones/new` | `home` (Raumliste), Dialog „Raum anlegen“ | Menüpunkt „Räume“ entfällt (§2) |
| `/zones/{id}`, `/zones/{id}/delete` | `room/<id>`, Löschen-Dialog | |
| `/zones/{id}/parameters` | `room/<id>` Reiter Technik | |
| `/zones/{id}/setpoints` | `room/<id>` Sollwerte | |
| `/zones/{id}/schedule` und Unterpfade `/points/{pid}/delete`, `/adopt` | `schedule/<id>` | Löschen/Übernehmen als Dialog |
| `/schedule?zone=<id>` (Mieter) | `schedule/<id>` | `?zone` bleibt gültig |
| `/zones/{id}/devices` | `devices` (Zuordnung, Raum vorgewählt) | |
| `/zones/history`, `/zones/{id}/history`, `/temperature-history`, `/temperature-history/{id}/tile` | `history` (Raum/Zeitraum) | `?period=` übernehmen |
| `/statistics`, `/heating-time` | `history` Heizzeit | |
| `/relay-wear` | `wear` | |
| `/audit`, `/device-commands`, `/changes`, `/changes/{id}/revert` | `events` (Filter Art), Rücknahme-Dialog | Rücknahme-Link muss `force_token`-Ablauf behalten |
| `/devices`, `/plant` | `devices`, `mapping` | |
| `/devices/map`, `/zones/data-flow`, `/zones/{id}/data-flow` | `mapping` | |
| `/controllers` | `controllers` | |
| `/control` | `operation` | |
| `/settings` | `regulation` | Unterpfade `/settings/*` sind nur POST |
| `/vacation`, `/absence` | `vacation`, `absence` | |
| `/modes`, `/modes/new`, `/modes/{id}`, `/modes/{id}/delete` | `modes` (Dialog) | |
| `/interfaces` | `interfaces` | |
| `/users`, `/groups` | `access` | |
| `/tokens`, `/kiosk-tokens` | `tokens`, `kiosktokens` | |
| `/account`, `/account/help`, `/passkeys`, `/glossar` | `account`, `help`, `account` (Passkeys), `glossary` | |
| `/login?return_to=...`, `/setup`, `/oauth/consent?request=...` | unverändert erreichbar | `/oauth/consent` wird von `/authorize` mit absolutem `public_url` aufgerufen; Pfad darf sich nicht ändern |
| `/kiosk`, `/kiosk/{token}` | unverändert | Cookie-Einlösung bleibt serverseitig |
| `/api/v1/*`, `/mcp`, `/healthz`, `/docs`, `/static/*` | unverändert | |

## Entscheidung Auth-Flows (ui/auth-flows)

- **Setup** wandert nach Svelte (`/app/setup`, ohne Shell, in `main.ts` vor Bootstrap gewählt) über `/ui-api/setup`. Dieselbe Domänenfunktion `run_setup` und dieselbe Sperre wie das HTML-Formular; der Status gibt nur „offen“ oder 404 her, das Token wird nie geloggt oder zurückgegeben, ungültig/verbraucht/abgelaufen sind nicht unterscheidbar. Die Svelte-Anmeldeseite leitet bei offenem Setup dorthin (wie `/` im Altsystem). Das HTML-`/setup` bleibt bestehen, solange `/` darauf zeigt.
- **Passkey-Anmeldung** wandert nach Svelte (Knopf in `LoginView`, WebAuthn im Browser). Genutzt werden die bestehenden Routen `/passkey/authentication/*`. Das Rückkehrziel wird nie ungeprüft übernommen: der Client schickt den Wunsch (`return_to`), der Server antwortet nur mit dem durch `safe_return_to` bestätigten Ziel, der Client prüft die Form nochmals und navigiert per `window.location.assign` mit dem Laufzeitpräfix. Bedingte Anmeldung (Autofill im Benutzernamenfeld) der Alt-Seite ist nicht übernommen.
- **OAuth-Zustimmung bleibt serverseitig (begründete Ausnahme).** Schutzmaßnahmen der Seite (`_unframed`: CSP `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Cache-Control: no-store` auf allen Antworten der Route; `_leave` mit `HX-Redirect`; serverseitige Teilmengenprüfung der Rechte) ließen sich für eine SPA-Hülle nur nachbauen, indem `spa_views` für diese eine Route dieselben Header setzt und die Hülle den „Zulassen“-Knopf selbst zeichnet. Nachweisbar wäre das, ein Gewinn gegen das Risiko (Clickjacking auf eine Freigabe, die einem Fremdclient Zugriff auf die Heizung gibt) ist aber nicht erkennbar. `/oauth/consent` bleibt daher unverändert; der Rücksprung nach der Anmeldung läuft über die HTML-Seite `/login?return_to=...` (getestet in `test_login_weiterleitung`, `browser_tests/test_oauth_consent`).

## E. Offene Fragen

Gemeldet, nicht entschieden oder behoben.

1. **Audit-Lücken im Bestand.** Im Web-Bericht fehlt `audit.record` bei: `logout`, `run_setup`, `issue_token`, `issue_kiosk_token`, OAuth `approve`/`deny`, `set_binding`, `configure_channel`, Override anlegen/beenden, `jump_to_next_switch`, `boost`, Test-Benachrichtigung. Bei `run_setup`, `issue_token`, `issue_kiosk_token`, `approve`, `set_binding`, `configure_channel`, `jump_to_next_switch` und `remote_control.boost` per AST-Stichprobe im Worktree bestätigt (kein `audit` im Funktionskörper). Offen: `domain/change_log.py` führt `create_override`, `cancel_override`, `boost` und `create_vacation`; ob Überschreibungen dort protokolliert werden (anderer Weg) ist nicht geprüft. Absicht oder Lücke ist aus dem Code allein nicht ableitbar; Grundsatz 7 und Auth-Bezug (Token-Ausgabe, Zustimmung) sprechen für eine Entscheidung vor der Migration, weil die neue Oberfläche dieselben Schreibwege nutzt.
2. **Sitzungsauth für JSON.** `/api/v1` akzeptiert nur Bearer (kein Cookie, kein CSRF). Eine SPA braucht einen Router mit `current_principal` + `csrf_protection` oder eine andere Auth-Entscheidung. Hauptsession (Grundsatz 4/7).
3. **Router und Präfix.** Demo nutzt Hash-Routing mit `base './'`. Direktlinks, wechselnder Ingress-Präfix (pro Anfrage, `data-url-prefix`), Cookie-Pfad und neue Wurzel in `config.RESERVED_MCP_PATHS` sind ungeklärt; Durchstich ist Pflicht (§1).
4. **Packaging/Build.** Package-Data deckt nur `web/static/*` und `web/static/vendor/*/*`; ein verschachteltes Bundle fehlt im Wheel/Abbild. CI hat weder Node noch Browsertests. CLAUDE.md „kein Build-Schritt, kein npm“ ist durch §1 ersetzt, Dokumentation nachzuziehen.
5. **Gruppenprofil `ui_profile`.** Gemeinsame Oberfläche löst die Admin-/Mieter-Hüllen ab (R112); Migration der Bestandsgruppen darf keinen Zugriff verlieren, `combined_profile`/Wächter-403 müssen durch Rechte ersetzt werden (§1).
6. **Dashboard-Layouts.** Demo speichert pro Person im Browser; Bestand kennt nichts Vergleichbares. Speicherort, Versionierung und serverseitige Rechteprüfung beim Laden sind zu entscheiden (§4).
7. **Darstellung serverseitig oder Daten.** SVG-Verlauf, Farbskala `warmth_fraction`, Mermaid-Quelle, deutsche Zahlen/Zeitformate werden heute im Server berechnet; ob Daten + Client-Rendering, bleibt zu entscheiden (Verlauf, Datenfluss).
8. **Kiosk ohne JS.** Bestand ist bewusst ohne JavaScript bedienbar; Svelte gibt das auf. Entscheidung nötig.
9. **Einmalklartext.** Neue Tokens/Kiosk-Tokens werden nur in der Antwortseite gezeigt (Setup-Token im Log); JSON-Schreibweg braucht ein Einmal-Anzeige-Muster.
10. **Fehlersemantik.** `Forbidden`, `StalePage`, `NotLoggedIn` mit Rücksprung, `DomainError.notice`/`FormError` brauchen einheitliche JSON-Codes und Feldfehler.
11. **Prozessübergreifender Zustand.** Fehlversuchszähler, `app.state.sending_allowed`, `bridge_reachable` liegen im Prozess; relevant bei mehreren Workern.
12. **Bestand ohne Demo-Pendant (Lücken).** Zuordnungskarte mit Ventilbetriebsarten, `reading_only`, Mehrfachsensoren; Passkeys/WebAuthn; Audit-Protokoll mit Filtern; Trockenlauf/Schattenbetrieb und Entscheidungsbegründung; Geräteüberblick-Befunde; Datenfluss (Mermaid); Kiosk-Token-Einlösung; Gruppen-`ui_profile`. Tests: kein Browsertest für `/changes/{id}/revert`; `/oauth/consent`, `/changes`, `/zones/data-flow` ohne Screenshot.
13. **Nicht verifiziert.** Rerender-Pfade (`paint`, `copy-day`, `undo`) und Leserecht von `/interfaces` wurden aus dem Code abgeleitet, nicht gegen eine laufende Instanz; Testsuiten wurden nicht ausgeführt.
