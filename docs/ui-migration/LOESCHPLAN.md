# Löschplan Alt-Oberfläche (Jinja/HTMX)

**Status 2026-10-17: umgesetzt / entfernt** (Worktree `ui/delete-legacy`, nicht gemergt). Abweichungen vom Plan: `tenant_views._home_notice` wurde als `home_notice` nach `web/shared/home_notice.py` verschoben (die SPA nutzt es); `base_plain.html` ist ohne Bootstrap/HTMX neu geschrieben, `base_core/base_admin/base_tenant`, `empty.html` und `passkeys.html` entfielen mit den Alt-Seiten; `GET /passkeys` und `POST /passkeys/{id}/remove` entfielen (Weiterleitung auf `/app/konto`); `forms.py` behält nur `FormError`/`password_form_error`; `POST /logout` entfiel ersatzlos (die SPA nutzt `/ui-api/logout`). Der Rest des Dokuments beschreibt den Plan vor dem Löschen.

Stand: Worktree `ui/cutover-redirects`, nach dem Verschieben der gemeinsam genutzten Helfer nach `thermoctl/web/shared/`. Dieses Dokument verändert nichts; es sagt, was der Löschschritt entfernen darf, was bleibt und was vorher noch fehlt.

## 1. Zusammenfassung

- Löschbar sind alle `*_views.py` außer `spa_views.py`, `oauth_views.py`, `passkey_views.py`, dazu 52 von 61 Templates, die Alt-JS, `vendor/mermaid`, die Alt-Anteile der Tests (Abschnitt 6) und `browser_tests/` weitgehend.
- Vor dem Löschen hängen die bleibenden Module nicht mehr an Alt-Views: `ui_api*.py`, `kiosk_api.py`, `legacy_redirects.py`, `app.py` importieren nur noch Router (`app.py`) oder `thermoctl.web.shared.*`.
- 328 Alt-Tests sind übersprungen (`tests/legacy_ui_skips.py`, nicht 330). Zuordnung unten (Abschnitt 7): ERSETZT 159, ENTFÄLLT 52, GEÄNDERT 27, **FEHLT 90**. Die FEHLT-Liste (Abschnitt 8) ist vor dem Löschen abzuarbeiten, vor allem die Rechte-/Sichtbarkeitsfälle.
- Nicht geprüft wurde, ob die **nicht** übersprungenen Alt-POST-Tests (z. B. in `test_schedule_views.py`, `test_admin_views.py`) ihr Gegenstück in `/ui-api` haben; sie laufen heute noch gegen die Alt-Routen und fallen mit ihnen weg. `docs/ui-migration/FUNKTIONSMATRIX.md` ist die Orientierung dafür.

## 2. Verschobene Helfer (Verhalten unverändert)

Die Alt-Views importieren sie von dort; Docstrings und Kommentare sind mitgewandert. Private Namen wurden öffentlich benannt (alt -> neu).

| Neues Modul | Inhalt (alt -> neu) | Verbraucher |
|---|---|---|
| `web/shared/login.py` | aus `auth_views`: `FEHLVERSUCHE`, `_MAX_FEHLVERSUCHE_EINTRAEGE` -> `MAX_FEHLVERSUCHE_EINTRAEGE`, `_merke_fehlversuch` -> `merke_fehlversuch`, `_ERROR_MESSAGE` -> `ERROR_MESSAGE`, `_VERGLEICHS_HASH` -> `VERGLEICHS_HASH`, `sleep`, `MAX_USERNAME_LENGTH`, `MAX_PASSWORD_LENGTH`, `throttle`, `record_failure`, `reset_failures`, `attempt_login`, `set_session_cookies` | `ui_api`, `ui_api_access`, `auth_views`, Tests |
| `web/shared/tokens.py` | aus `admin_views`: `_token_grants` -> `token_grants`, `_grant_label` -> `grant_label`, `replace_token_grants` | `ui_api_access` |
| `web/shared/kiosk.py` | aus `kiosk_views`: `THERMOSTAT_STEP` | `kiosk_api` |
| `web/shared/controllers.py` | aus `controller_views`: `_zones` -> `zones_with_permission`, `_controllers` -> `controller_devices`, `_devices_in` -> `devices_in_zones`, `_require_manageable_zone` -> `require_manageable_zone`, `_require_readable_device` -> `require_readable_device`, `_context` -> `controllers_context`, `_managed_device` -> `managed_device` | `ui_api_devices` |
| `web/shared/device_assignment.py` | aus `device_assignment_views`: `_context` -> `assignment_context` | `ui_api_devices` |
| `web/shared/device_commands.py` | aus `device_commands_views`: `ENTRIES_PER_PAGE`, `SETBACK_OUTCOME_LABELS` | `ui_api_devices` |
| `web/shared/device_survey.py` | aus `device_views`: `SILENT_WITHOUT_DEFAULTS_SECONDS`, `build_device_survey` | `ui_api_devices` |
| `web/shared/mapping_map.py` | aus `mapping_map_views`: `MapOutcome`, `connect_ports`, `disconnect_connection`, `_title` -> `connection_title`, `_no` -> `no_outcome`, `_device_lines` -> `device_lines`, `_fresh_lines` -> `fresh_lines`, `_editable_graph` -> `editable_graph`, `ACTUATOR_DETACHED_NOTE`, `DOWNGRADE_NOTE`, `STALE_NOTICE` | `ui_api_devices` |
| `web/shared/zone_parameters.py` | aus `daily_views`: `FELDER`, `GANZZAHLEN`, `PI_FELDER`, `SOLAR_GAIN_FACTOR_LIMITS`, `_check_parameters` -> `check_parameters`, `_check_solar_gain_factor` -> `check_solar_gain_factor` | `ui_api_operations` |
| `web/shared/control.py` | aus `control_views`: `PI_FALLBACK_LABELS`, `_TEST_COOLDOWN` -> `TEST_COOLDOWN` | `ui_api_operations` |
| `web/shared/schedule.py` | aus `schedule_views`: `_undo_token` -> `undo_token`, `_undo_payload` -> `undo_payload` | `ui_api_schedule`, `tenant_views` |
| `web/shared/changes.py` | aus `changes_views`: `_confirmation_token` -> `confirmation_token` | `ui_api_reports` |
| `web/shared/report.py` | aus `report_views`: `submit_report` | `ui_api_zones` |
| `web/shared/zone_status.py` | aus `start_views`: `MINUTES_PER_DAY`, `_day_track` -> `day_track`, `zone_status_context` | `ui_api_rooms`, `tenant_views`, `start_views` |

Nicht verschoben, weil nur Router/Hilfen der Alt-Views selbst: alles übrige in den `*_views.py`. `thermoctl/web/__init__.py` (`templates`, `warmth_fraction`, `is_partial_swap`, `STATIC_DIR`), `forms.py`, `urls.py`, `navigation.py`, `assets.py` und `guards.py` bleiben (siehe Abschnitt 3). Offen: `forms.py` (`FormError`, `form_again`, `password_form_error`) wird von `ui_api_operations` und `shared/zone_parameters` genutzt (`FormError`); der Rest ist View-Hilfe und kann nach dem Löschen gekürzt werden.

Tests wurden an die neuen Orte angepasst: Monkeypatch-Ziele `thermoctl.web.shared.login.sleep|verify_password`, `shared.mapping_map.editable_graph|can_connect`, `shared.schedule.hmac`, `thermoctl.integrations.notification.deliver` (statt `report_views.notification`). Zusätzlich: `tests/approved_physical_vocabulary.json` führt die verschobenen Fundstellen jetzt unter den neuen Dateien und `legacy_redirects.py`; der Test `test_physical_vocabulary_occurrences_are_explicitly_reviewed` prüft nur git-getrackte Dateien und ist deshalb erst nach dem Commit der neuen `shared/`-Dateien grün.

## 3. Python-Module unter `thermoctl/web/`

| Datei | Urteil | Grund |
|---|---|---|
| `spa_views.py` | bleibt | liefert die Svelte-Hülle `/app` |
| `oauth_views.py` | bleibt | `/oauth/consent` bleibt serverseitig (Clickjacking-Schutz, absolute Weiterleitung); nutzt `oauth_consent.html` |
| `passkey_views.py` | bleibt | JSON-Routen WebAuthn (`/passkey/...`) nutzt die Svelte-Oberfläche; `GET /passkeys` (`passkey_list`, `passkeys.html`) wird durch Weiterleitung verdeckt und kann nach dem Löschen aus der Datei raus |
| `legacy_redirects.py` | bleibt (bis Altlinks egal) | Weiterleitungen alter Lesezeichen; kein Altview-Import |
| `ui_api*.py`, `kiosk_api.py` | bleibt | neue Schnittstelle |
| `__init__.py`, `urls.py`, `navigation.py`, `assets.py`, `guards.py`, `forms.py` | bleibt | von bleibenden Modulen genutzt; `guards.py` (`admin_ui_only`, `tenant_ui_only`) wird nur von Alt-Views gebraucht und fällt weg, wenn niemand sonst es importiert (prüfen) |
| `auth_views.py` | löschbar nach Verschiebung, mit zwei Vorbehalten | `GET /login` ist Weiterleitung; `POST /login` ersetzt `POST /ui-api/login`. Vorbehalt: `POST /logout` (Alt-Abmeldung, `test_csrf`/`test_login*` prüfen sie) vor dem Löschen klären; `/ui-api/logout` existiert |
| `kiosk_views.py` | **bleibt teilweise** | `GET /kiosk/{plaintext}` (`kiosk_entry`: Token einlösen, Cookies setzen, auf `/app/kiosk`) und `_invalid_page` (`kiosk_invalid.html`) bleiben; `kiosk_dashboard` und die drei POST-Kioskrouten sind löschbar (ersetzt durch `kiosk_api`). `ANSICHT_COOKIE_NAME` entfällt: die Ansichtswahl liegt jetzt in localStorage |
| `setup_views.py` | löschbar | `GET /setup` leitet um, `POST /setup` ersetzt `POST /ui-api/setup` |
| `start_views.py`, `account_views.py`, `admin_views.py`, `audit_views.py`, `changes_views.py`, `control_views.py`, `controller_views.py`, `daily_views.py`, `device_assignment_views.py`, `device_commands_views.py`, `device_views.py`, `glossary_views.py`, `kiosk_admin_views.py`, `mapping_map_views.py`, `mode_views.py`, `report_views.py`, `schedule_views.py`, `tenant_views.py`, `vacation_views.py`, `zone_views.py` | löschbar nach Verschiebung | Funktion in `/ui-api` vorhanden (Matrix: `docs/ui-migration/FUNKTIONSMATRIX.md`); gemeinsam genutzte Helfer stehen jetzt in `shared/`. Beim Löschen aus `app.py` die `include_router`-Zeilen und Importe entfernen |

Verbleibende Funktionen in den löschbaren Views, die von anderen **bleibenden** Modulen genutzt würden: keine (per AST geprüft; `app.py` importiert nur `router`/`shared_router`).

## 4. Templates (`thermoctl/web/templates`, 61 Dateien)

Bleiben (9): `base_core.html`, `base_plain.html`, `base_admin.html`, `base_tenant.html` (Basis der bleibenden Seiten), `empty.html`, `oauth_consent.html`, `passkeys.html` (nur bis `passkey_list` entfernt wird), `stale_page.html` (von `app.py` für `StalePage`), `kiosk_invalid.html` (Kiosk-Einlösung mit ungültigem Token).

Löschbar nach Verschiebung (52): `account`, `audit`, `change_force_invalid`, `change_not_found`, `change_revert`, `changes`, `control`, `controllers`, `data_flow`, `device_assignment`, `device_commands`, `devices`, `flow_diagram`, `form`, `glossar`, `groups`, `help`, `icons`, `interfaces`, `kiosk`, `kiosk_tokens`, `login`, `mapping_map`, `mode_delete`, `mode_form`, `modes`, `parameter`, `plant`, `relay_wear`, `schedule`, `schedule_adopt`, `schedule_point_delete`, `setpoints`, `settings`, `setup`, `start`, `statistics`, `tenant_heating_time`, `tenant_history`, `tenant_schedule`, `tenant_schedule_empty`, `tenant_start`, `tokens`, `users`, `vacation`, `zone_delete`, `zone_form`, `zone_header`, `zone_history_chart`, `zones`, `zones_history`, `zones_history_body` (jeweils `.html`).

Folge: `base_admin.html`/`base_tenant.html` laden Seiten-Skripte; nach dem Löschen prüfen, ob sie noch gebraucht werden (nur `oauth_consent` und `passkeys` erben). Die Einträge dieser Templates in `tests/approved_physical_vocabulary.json` werden mit gelöscht; `tests/test_user_visible_effect_texts.py`, `test_architecture.py`, `test_docs_current.py`, `test_packaging.py` verweisen auf Templates/Static und sind danach durchzusehen.

## 5. Static (`thermoctl/web/static`)

| Datei | Urteil | Grund |
|---|---|---|
| `app/` (Svelte-Build) | bleibt | die neue Oberfläche |
| `thermoctl.css`, `favicon.svg`, `vendor/bootstrap/*`, `vendor/htmx/htmx.min.js`, `loading_indicator.js`, `page_scripts.js` | bleibt (vorerst) | von `base_core/base_plain` geladen, die `oauth_consent`, `passkeys`, `stale_page`, `kiosk_invalid` erben. Entfallen erst, wenn diese vier Seiten gelöst sind; `favicon.svg` wird auch von `app.py`/`test_ui_api` genutzt |
| `vendor/swagger-ui/*` | bleibt | `/docs` in `app.py` |
| `HERKUNFT.md` | bleibt | Herkunftsnachweis der gebündelten Dateien (auch die in `web/__init__.py` und `app.py` referenziert); nach dem Löschen der Vendor-Teile kürzen |
| `assignment.js`, `data_flow.js`, `device_filter.js`, `glossary_filter.js`, `homebridge_copy.js`, `kiosk_panel.js`, `mapping_map.js`, `passkey.js`, `permissions.js`, `schedule.js`, `sensor_failure_curve.js`, `zone_history.js` | löschbar nach Verschiebung | nur von löschbaren Templates geladen (`passkey.js` nur von `login.html`; die Svelte-Seite hat eigenen Code in `frontend/src/lib/access/passkey.ts`) |
| `vendor/mermaid/mermaid.min.js` | löschbar nach Verschiebung | nur `data_flow.html`; `/ui-api` liefert den Mermaid-Text, Rendern im Svelte-Bundle prüfen (`frontend/src/lib/devices/types.ts: mermaid`) |

## 6. Tests

**Python-Tests mit übersprungenen Alt-Tests** (Spalten: Tests der Datei, davon übersprungen). Datei fällt ganz weg, wenn der Rest nur Alt-POST-Routen prüft; sonst nur die Alt-Teile löschen.

| Testdatei | Tests | übersprungen | Hinweis |
|---|---|---|---|
| `tests/test_account_views.py` | 10 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_admin_views.py` | 43 | 3 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_app.py` | 22 | 1 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_assets.py` | 4 | 1 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_audit_views.py` | 12 | 12 | alle Tests übersprungen -> Datei löschbar |
| `tests/test_changes_views.py` | 12 | 11 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_control.py` | 48 | 13 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_control_solar.py` | 6 | 1 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_control_views.py` | 6 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_controller_channels.py` | 38 | 9 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_daily_views.py` | 47 | 21 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_daily_views_backup_offset.py` | 5 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_daily_views_solar.py` | 9 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_data_flow.py` | 29 | 6 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_device_assignment.py` | 48 | 7 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_device_commands_views.py` | 18 | 17 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_device_views.py` | 10 | 8 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_emergency_display_http.py` | 5 | 4 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_glossary_views.py` | 21 | 18 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_homebridge_interface.py` | 18 | 7 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_ingress_prefix.py` | 22 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_kiosk.py` | 50 | 19 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_mapping_map.py` | 33 | 3 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_mapping_map_multi.py` | 18 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_mapping_map_valve.py` | 28 | 1 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_mode_views.py` | 25 | 10 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_navigation.py` | 6 | 4 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_notification_settings.py` | 17 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_passkey.py` | 28 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_relay_wear_views.py` | 4 | 4 | alle Tests übersprungen -> Datei löschbar |
| `tests/test_schedule_views.py` | 65 | 15 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_security_review_2026_09_02.py` | 9 | 2 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_smoke_test.py` | 25 | 4 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_tenant_history.py` | 42 | 28 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_tenant_views.py` | 72 | 26 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_ui_profile.py` | 26 | 4 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_vacation_views.py` | 13 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_web_oauth_consent.py` | 26 | 1 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_web_tokens_presets.py` | 15 | 3 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_zone_history_chart.py` | 26 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_zone_history_honesty.py` | 42 | 13 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_zone_history_hover.py` | 24 | 12 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |
| `tests/test_zone_views.py` | 13 | 5 | Rest läuft gegen Alt-Routen/Helfer (POST, Domäne): beim Löschen der Routen mitprüfen |

**Tests, die Alt-Views noch importieren** und beim Löschen angepasst werden müssen (nicht übersprungene Tests darin): `test_daily_views.py` (Module `daily_views`, `start_views`), `test_kiosk.py` (`ANSICHT_COOKIE_NAME`), `test_mapping_map_multi.py` (`mapping_map_views._payload`), `test_notification_settings.py` (`control_views.get_settings` per Monkeypatch), `test_web_tokens_presets.py` (`admin_views.issue_token` per Monkeypatch), `test_zone_history_hover.py` (`zone_views._attribute_json`), `conftest.py`/`test_login*.py`/`test_setup.py`/`test_web_oauth_consent.py` (Monkeypatch-Ziel `thermoctl.web.shared.login.sleep`, bereits umgestellt). Bleiben unverändert: `test_spa_views.py`, `test_legacy_redirects.py`, `test_web_oauth_consent.py` (außer dem übersprungenen Test), `test_passkey.py` (JSON-Routen), `test_ui_api*.py`, `test_kiosk_api.py`.

**Weitere Tests, die auf Templates/Static/`*_views.py`-Quelltext zeigen** und nach dem Löschen durchzusehen sind: `test_assets`, `test_api_sensor_failure`, `test_daily_views_solar`, `test_glossary`, `test_architecture`, `test_control_views`, `test_csrf`, `test_docs_current`, `test_packaging`, `test_shadow_run`, `test_smoke_test`, `test_mode_views`, `test_tenant_history`, `test_user_visible_effect_texts`, `test_schedule_views`, `test_ui_profile`, `test_ingress_prefix`, dazu `tests/approved_physical_vocabulary.json` (Einträge der gelöschten Dateien entfernen).

**`tests/legacy_ui_skips.py`** und die Einbindung in `tests/conftest.py` (`pytest_collection_modifyitems`) fallen mit den Tests weg.

**`browser_tests/` (Playwright, 38 Testdateien gegen die Alt-Oberfläche):** löschbar nach Verschiebung, mit diesen Ausnahmen: `test_oauth_consent.py`, `test_oauth_consent_cross_origin.py` (bleibende Zustimmungsseite), `live_server.py`, `seed.py`, `_ingress_proxy.py`, `conftest.py`, `history_tiles.py` (Infrastruktur, solange irgendein Browsertest bleibt). `test_screenshots.py` (Routeninventar der Dokumentation) und `test_ingress_prefix.py`/`test_stylesheet.py` prüfen Alt-Seiten bzw. die gemeinsame Hülle: neu ausrichten auf `/app` oder löschen. Die Svelte-Oberfläche hat hier bislang **keine** Browsertests; sie wird nur durch Vitest (`frontend/tests`) und die `/ui-api`-Tests abgedeckt.

## 7. Zuordnung der 328 übersprungenen Alt-Tests

Urteile: ERSETZT = konkreter Ersatztest in `/ui-api`/Domäne; ENTFÄLLT = prüfte nur Markup/Hülle; GEÄNDERT = bewusst anders entschieden; FEHLT = Verhalten gilt, kein (voller) Ersatztest (Typ Test = Funktion da, Test fehlt; Typ Funktion = Verhalten fehlt in der neuen Oberfläche). Gesamt: ERSETZT 159, ENTFÄLLT 52, GEÄNDERT 27, FEHLT 90. Die Tabellen entstanden durch Lesen der Alt- und Ersatztests (ohne Testlauf).


## 8. FEHLT-Liste (vor dem Löschen abarbeiten)

Jede Zeile: Alttest -> was keine neue Prüfung hat. Rechte-, Sichtbarkeits- und Profilfälle zuerst bearbeiten (Grundsatz 7); Typ Funktion heißt: die neue Oberfläche kann es nicht, das ist Entscheidung oder Nachbau, nicht nur ein Test.

| Altdatei | Alttest | Lücke und Typ |
|---|---|---|
| `test_admin_views` | the_group_page_shows_permissions_by_area_in_plain_words | test_ui_api_access.py::test_group_list_gives_areas_zones_and_permissions prueft nur 2 Bereichstitel, code und zone_scoped; Klartext-description je Recht (ui_api_access.py Z.530) und alle PERMISSION_AREAS-Namen nicht geprueft (Vollstaendigkeit Domaene: test_authz). Typ: Test |
| `test_audit_views` | the_date_filter_uses_the_configured_local_day_not_utc_midnight | Kein Test, dass from_date/to_date am konfigurierten lokalen Tag (Europe/Berlin) schneiden statt an UTC-Mitternacht (Eintrag 23:30 UTC am 1.8. gehoert zum lokalen 2.8.). Test nimmt "heute" und 2999/2000 (nicht deterministisch); local_day_start_utc in keinem Test. Funktion vorhanden (audit_query.py Z.47/51). Typ: Test |
| `test_audit_views` | to_filters_inclusive_of_the_whole_day | to_date wird nur mit "heute" (Eintraege von jetzt) und 2000 geprueft; die obere Kante (23:59:59 des Tages enthalten, 00:00 des Folgetags ausgeschlossen) ist nicht geprueft. Funktion vorhanden (audit_query.py Z.50-52). Typ: Test |
| `test_audit_views` | user_actually_filters | Nur Zweig Token-Name getestet (user="mein-token"); Zweig Benutzername (User.username == user, audit_query.py Z.54) ohne Test. Typ: Test |
| `test_changes_views` | date_and_reverted_filters | Tagesgrenze im Adapter nicht geprueft (nur from 2999/to 2000/weit), hide_reverted nur ohne zurueckgenommenen Eintrag (ids(hide_reverted)==ids() trivial); Domaene deckt include_reverted (test_change_log.py::test_list_changes_can_hide_reverted_entries) und from_at/to_at. Typ: Test |
| `test_control_views` | the_arming_notes_name_both_command_paths_behind_the_second_latch | Neue Oberflaeche nennt nur "MQTT-Sendefreigabe"/"MQTT-Riegel" (ArmPanel.svelte Z.62/82, glossary.ts "sending", rooms/logic.ts Z.155); dass der Riegel auch Meross-Befehle sperrt, steht nirgends, kein Test. Typ: Funktion |
| `test_control_views` | the_operating_page_shows_notbetrieb_detail_and_comparison | Stufe/Aktoren/Vergleich: test_operation_shows_emergency_actuators_and_comparisons (Ansicht gemockt) + test_emergency_state.py (Domaene, "nicht versucht", Offset-Vorschlag). Nicht geprueft: outdoor_status_text ("keine Außenquelle eingerichtet") im JSON (Uebersetzung nur test_emergency_display.py) und Durchlauf mit echten DB-Zeilen. Typ: Test |
| `test_controller_channels` | a_fixed_number_with_more_than_one_decimal_survives_an_unchanged_resubmit | Kein Test, dass fixed_number mit bis zu 4 Nachkommastellen (z.B. 3,125) unverrundet ausgeliefert (_num, ui_api_devices.py Z.843) und beim erneuten Senden unveraendert gespeichert wird; test_channel_writes_and_validates nutzt nur "21,5". Typ: Test |
| `test_controller_channels` | a_saved_write_channel_shows_every_field_after_reload | Nach Speichern wird nur direction/zone_id bzw. DB fixed_number geprueft; Ausgabe von kind, source_device_id, fixed_text, fixed_number im GET /controllers (ui_api_devices.py Z.838-843) je Art (fixed/sensor/zone) nicht geprueft. Typ: Test |
| `test_controller_channels` | resubmitting_the_rendered_form_unchanged_keeps_the_configuration | Kein Round-Trip GET -> unveraendert POST, der die Konfiguration (z.B. Leseart operating_mode + Zone) erhaelt; Kind operating_mode in ui-api ungetestet. Typ: Test |
| `test_controller_channels` | the_controllers_page_with_no_visible_zone_shows_nothing | Fall "anlagenweites device.read, aber keine Zone vorhanden" -> leere Antwort statt aller Geraete nicht geprueft (Guards shared/controllers.py Z.26/51, Funktion vorhanden). Fremde Namen bei zonenweitem Recht: test_a_room_a_reader_finds_no_device_of_room_b_anywhere. Typ: Test |
| `test_daily_views` | the_start_page_uses_local_day_and_time_for_its_schedule_track | Lokaler Wochentag/Zeitzone nahe Mitternacht (UTC So 23:30 = Berlin Mo 00:30) für `day_track` und `now_fraction` ungeprüft; test_ui_api_rooms prüft nur Summe 1440 und Modusnamen. Typ: Test (zone_status.py:210 existiert) |
| `test_daily_views` | the_parameter_page_shows_the_pi_warning_with_the_switching_table_and_relay_wear_link | Modellrechnungstabelle (262.800/52.560 je Jahr), Hinweis Kessel/Verdichter, Link Relaisverschleiß, Verhältnis 0,53 fehlen in RoomTechnik.svelte (nur Bestätigungs-Checkbox). Typ: Funktion |
| `test_daily_views` | the_pi_warning_ratio_follows_a_changed_assumption | Verhältnis ungünstigster Fall / angenommene Lebensdauer wird nirgends berechnet; /ui-api liefert nur assumed_relay_lifetime_operations. Typ: Funktion |
| `test_daily_views` | valve_protection_checkbox_round_trip | Nur true wird gespeichert; explizites `valve_protection_enabled=false` (Ausschalten) wird über /ui-api nie getestet. Semantik "fehlend = unverändert" ist durch null_returns_to_inheritance... gedeckt. Typ: Test |
| `test_daily_views` | an_enabled_but_now_ineligible_pi_switch_is_never_disabled_in_the_form | Weder Clientlogik `piSwitchLocked` (RoomTechnik.svelte:102) noch Serververhalten beim Speichern bei aktivem, inzwischen unzulässigem PI sind getestet. Typ: Test |
| `test_daily_views` | showing_and_cancelling_an_override | Anlegen/Aufheben und Rechte gedeckt (UA rooms override_permanent..., cancel_override, writes_without_permission_are_403), aber Begründung `setpoint.reason` "Übersteuerung (feste Temperatur)" wird nirgends geprüft (domain/schedule.py:1300). Typ: Test |
| `test_daily_views` | the_overview_shows_the_latest_of_several_decisions | Tiebreak von `latest_decisions_by_zone` (gleiches decided_at, höhere id gewinnt) hat nach Löschen keinen Test; test_ui_api_operations importiert die Funktion nur ungenutzt (noqa). Typ: Test (Funktion existiert, von /ui-api/rooms genutzt) |
| `test_daily_views` | without_setpoint_write_no_thermostat_is_on_the_page | Nur "alle Rechte" vs. "nur zone.read" (list_shows_state_setpoint_and_actions, list_zone_without_state_or_decision); `actions.edit_setpoint_step` ist nie isoliert an setpoint.write (zonengebunden) gebunden getestet. Typ: Test |
| `test_daily_views` | the_start_page_shows_the_outdoor_temperature | Dashboard/Startseite zeigt keine Außentemperatur (tiles.ts: Kachel "outdoor" nicht verfügbar); /ui-api/settings liefert reading nur admin-seitig, Wert 3,2 nie getestet. Typ: Funktion |
| `test_daily_views` | the_start_page_shows_a_forgotten_window_chip | `sensor.window_alarm` (ZoneState.window_alarm) wird in /ui-api/rooms nicht geprüft; Clientzweig window_alarm in roomStatus nur über window_open getestet, Beschriftung "Fenster offen" statt "vergessen". Typ: Test |
| `test_daily_views_solar` | the_page_shows_the_documented_default_of_zero | `solar_gain_factor` Vorgabe 0 in GET /rooms/{id}/parameters nicht geprüft (nur Speichern 0.5 und Fehler). Typ: Test |
| `test_daily_views_solar` | the_setback_maximum_inherits_the_global_default | Vererbung `solar_setback_max_k` von default_solar_setback_max_k (inherited/effective 3.5) in /ui-api nicht geprüft; auch kein Dom-Test. Typ: Test |
| `test_data_flow` | single_zone_page_has_one_heading_and_the_dry_run_note | Hinweis "Im Trockenlauf wird nichts gesendet ... Notbetrieb-Übergabe nicht dargestellt" fehlt in DataFlowPanel.svelte (Überschrift ist Markup). Typ: Funktion |
| `test_device_commands_views` | to_filters_inclusive_of_the_whole_day | Tagesende 23:59:59 der Adapterumrechnung (to_at = Folgetag minus 1 us, ui_api_devices.py:989) ungeprüft; UA-Test hat nur einen Eintrag um 09:00. Typ: Test |
| `test_device_commands_views` | error_and_reason_are_shown | Felder `error` und `reason` der Einträge werden in /ui-api/device-commands nicht geprüft (create_device_command setzt error None, reason wird nicht asserted). Typ: Test |
| `test_device_commands_views` | absenkung_entries_are_shown_with_kind_text_and_labels | Dom test_solar_setback_log.py::list_commands_marks_the_entries_as_absenkung_and_merges_them_by_time prüft Texte; im Adapter nur startswith("absenkung_") bei outcome_labels, nicht Absenkung-Eintrag, kind absenkung, Quelle "Regelung (Sonnenabsenkung)". Typ: Test |
| `test_device_commands_views` | the_zone_filter_offers_a_zone_that_only_has_absenkung_entries | Vereinigung `zone_names` mit `setback_zone_names` im Adapter (ui_api_devices.py:1013) nie mit reiner Absenkungszone geprüft; Dom test_solar_setback_log.py::setback_zone_names_lists_only_zones_with_a_setback deckt nur die Funktion. Typ: Test |
| `test_device_commands_views` | the_zone_filter_lists_a_zone_with_commands_and_absenkung_only_once | Deduplizierung/Sortierung von `zone_names` (Befehle und Absenkung gleicher Zone) im Adapter ungeprüft. Typ: Test |
| `test_device_views` | the_device_page_shows_signs_of_life_capability_and_zone | model, is_group, capabilities-Labels, battery_percent, radio_quality im /ui-api/devices nicht asserted (nur Name, Zonen, Integration, Befund disabled); Batterie-Rundung nur im Client (SurveyList.svelte:72, ohne Test). Typ: Test |
| `test_device_views` | a_meross_socket_recently_confirmed_online_is_not_flagged_as_never_reporting | DB-Logik in build_device_survey (Meross: last_seen_at statt device_health) ungeprüft; Dom test_device_survey.py prüft nur `findings()` mit last_heard_kind. Typ: Test |
| `test_device_views` | a_meross_socket_never_reconciled_says_so_without_claiming_a_report | Wie oben: Befundtext "wurde noch nie als online abgeglichen" nur auf Domänenebene (test_device_survey.py::a_never_reconciled_meross_device_is_not_told_it_never_reported), Integration Meross -> last_heard_kind in build_device_survey ungeprüft. Typ: Test |
| `test_device_views` | battery_and_radio_appear_as_a_number_not_as_a_chip | WITHOUT_CHIP/`quiet_capabilities` (Batterie-Chip unterdrückt, Gerät nicht "ohne Fähigkeiten") nirgends getestet; SurveyList zeigt "keine Fähigkeiten gemeldet" nicht. Typ: Test |
| `test_emergency_display_http` | start_page_shows_the_ersatzquelle_banner | Banner-Text Dom getestet (test_emergency_display.py::ersatzquelle_banner_names_the_device_without_jargon), aber `emergency` in /ui-api/rooms nur als None geprüft; Verdrahtung mit echtem ZoneSensorFailureState fehlt. Typ: Test |
| `test_emergency_display_http` | tenant_page_shows_the_ersatzquelle_banner | Wie oben, zusätzlich Mieterprofil (tenant_client) mit gefülltem `emergency` ungeprüft. Typ: Test |
| `test_emergency_display_http` | kiosk_dashboard_shows_the_ersatzquelle_banner | test_kiosk_api.py::a_failing_sensor_and_an_emergency_banner_are_reported mockt zone_emergency_view (nur Durchreichung "Notbetrieb aktiv"); Ersatzquelle-Text mit echtem Zustand ungeprüft. Typ: Test |
| `test_emergency_display_http` | tenant_top_banner_shows_notbetrieb_instead_of_the_calm_message | Beruhigungstext "Heizung läuft normal" existiert nicht mehr; Entsprechung ist die Kachel "issues" (tiles.ts zählt `emergency` mit, Hinweis "Keine Auffälligkeiten"), dafür kein Test (dashboard-misc.test.ts setzt emergency nur null). Typ: Test |
| `test_glossary_views` | the_operation_page_offers_help_for_the_dry_run | Hilfe zu Trockenlauf existiert als Popover (frontend/src/lib/operations/glossary.ts, OpsHelp), kein Test prüft, dass die Betriebsseite sie trägt. Typ: Test |
| `test_glossary_views` | the_settings_page_offers_help_for_the_emergency_profile_and_the_curve | Popover "Notsollwert", "Außentemperaturkurve" existieren (operations/glossary.ts), aber ohne Test, dass die Regelvorgaben-Seite sie anbietet. Typ: Test |
| `test_glossary_views` | the_zone_parameter_page_offers_help_for_its_hard_terms | Hilfe zu Hysterese, Mindestzeiten, Sonnenabsenkung, PI-Regler, Notbetrieb existiert als eigene Popover-Texte (operations/glossary.ts), kein Test; zudem entfällt die Prüfung, dass Hilfeverweise auf vorhandene Glossareinträge zeigen (Popover sind nicht verlinkt). Typ: Test |
| `test_homebridge_interface` | global_zone_read_shows_every_zone | API-Tests prüfen homebridge_zones nur mit einer Zone (never_return_a_secret) oder zonenbezogenem Recht; anlagenweites zone.read mit zwei Zonen fehlt. Typ: Test |
| `test_homebridge_interface` | no_zone_visible_without_zone_read | setting.manage ohne jedes zone.read: homebridge_zones muss leer sein und keinen Zonennamen preisgeben. test_interfaces_name_only_visible_zones_in_the_homebridge_part setzt zone.read(zone) voraus. Typ: Test |
| `test_homebridge_interface` | the_rendered_topics_match_publication_py | Der Wächter "alle sechs angezeigten Topics = publication.py" fehlt: Domäne prüft nur getCurrentTemperature (zone_configs_use_the_configured_mqtt_prefix), test_docs_current prüft nur docs/homebridge.md. Typ: Test |
| `test_ingress_prefix` | rendered_page_direct_uses_bare_links_even_with_ingress_configured | Direktzugriff auf einen Prozess mit konfiguriertem Ingress muss die SPA-Hülle (/app/) ohne Präfix liefern; client_direct_with_ingress_configured wird nur hier benutzt, test_spa_views kennt nur "ohne Konfiguration". Typ: Test |
| `test_kiosk` | a_kiosk_cookie_reaches_no_administration_page | Kiosk-Cookie gegen Verwaltung: ui-api/access (test_kiosk_cookie_never_reaches_the_ui_api) und rooms/REST (KA) sind geprüft, nicht aber /ui-api/devices, audit, settings, interfaces, control, schedule. Typ: Test |
| `test_kiosk` | the_dashboard_carries_an_agpl_source_link_that_stays_off_the_tablet | AGPL-§13-Quelltextlink (github-Adresse, target=_blank rel=noopener) fehlt in der Svelte-Kioskansicht, in der gesamten SPA gibt es keinen Lizenz-/Quelltexthinweis. Typ: Funktion |
| `test_kiosk` | the_kiosk_keeps_its_own_fixed_refresh | Fester 20-s-Takt (POLL_INTERVAL_MS in kiosk/logic.ts, Standard im Controller) wird von keinem Test festgehalten. Typ: Test |
| `test_kiosk` | the_rendered_kiosk_clock_uses_the_configured_timezone | Uhr in Anlagen-Zeitzone: KA prüft timezone nur mit Standardwert Europe/Berlin, FE clockText nur Berlin im Oktober; abweichende konfigurierte Zone und Sommer-/Winterzeit-Fälle fehlen. Typ: Test |
| `test_kiosk` | the_rendered_kiosk_token_expiry_uses_the_configured_timezone | Ablauf in KioskPage.svelte nutzt formatDateTime (Browser-Zeitzone); ui_api_access liefert keine Anlagen-Zeitzone, kein Test. Typ: Funktion |
| `test_kiosk` | the_ui_profile_of_the_issuer_does_not_reach_the_kiosk | Kiosk-Token eines Ausstellers mit Mieterprofil muss /kiosk-api/state weiter bedienen (kiosk_api prüft kein Profil); kein Test in test_kiosk_api.py. Typ: Test |
| `test_mapping_map_multi` | a_one_sensor_is_the_temperature_source_of_two_zones_and_both_zones_read_it | Ein Fühler als Messquelle zweier Zonen über /ui-api/devices/map/connect, zwei Linien vom selben Ausgang und Messwert für beide Zonen in advance_zone_state (ZoneState); kein Test deckt das, nur das Lösen einer Linie (a_the_sensor_stays_...). Typ: Test |
| `test_mapping_map_multi` | f_device_to_device_fan_out_is_judged_fit_but_never_stored | Ein Ausgang auf mehrere Geräteeingänge ist laut can_connect zulässig und steht nicht in rejections, "single"-Flag des Geräteeingangs und Meldung "nimmt nur eine Verbindung an"; nur der 422 "nicht gespeichert" ist ersetzt (test_device_to_device_is_judged_...). Typ: Test |
| `test_mode_views` | a_builtin_mode_cannot_be_deleted_and_says_why | Sperre für eingebaute Modi ("weil die Anwendung sie benötigt", is_builtin) über DELETE /ui-api/modes oder delete_lock; is_builtin kommt in UA nur als False vor. Typ: Test |
| `test_navigation` | user_with_all_permissions_sees_every_navigation_item | Kein Test, dass ein Administrator mit allen Rechten in /ui-api/bootstrap (visible_navigation) jeden ADMIN-Eintrag aus NAVIGATION_ITEMS erhält und keine Mietereinträge; geprüft ist nur /zones und /users. Typ: Test |
| `test_navigation` | zone_scoped_permission_shows_only_zone_filtering_destinations | Zonenbezogenes zone.read zeigt /zones aber nicht /control, device.read(zone) zeigt /controllers aber nicht /devices (scope any_zone gegen anlagenweit) in Bootstrap-Navigation; nur Mieter-Verlauf ist domänenseitig geprüft. Typ: Test |
| `test_relay_wear_views` | relay_wear_highlights_actionable_levels_and_the_visible_assumption | API-Test prüft nur ein Gerät in Stufe normal; Reihenfolge nach Jahreshochrechnung, Stufen warning/danger über /ui-api/relay-wear und Durchreichen von Zeitzone sowie Lebensdauer an relay_operations fehlen (Stufen selbst: test_statistics.py). Typ: Test |
| `test_relay_wear_views` | relay_wear_reflects_a_changed_assumption | Einstellung assumed_relay_lifetime_operations (hier 100000) wird in /ui-api/relay-wear nicht geprüft, nur Standard 500000 und Fallback ohne Setting. Typ: Test |
| `test_relay_wear_views` | relay_wear_requires_audit_read_and_a_visible_zone | Recht audit.read anlagenweit ist ersetzt (test_relay_wear_access: nur zone.read 403, zonenbezogenes audit.read 403). Es fehlt der Fall audit.read ohne sichtbare Zone (200, leere Liste). Typ: Test |
| `test_schedule_views` | the_forecast_is_visible_with_only_zone_read | Kein Test, dass GET /ui-api/schedule/{z}/preview mit nur zone.read (ohne schedule.manage) 200 liefert; alle preview-Tests nutzen PLAN. Funktion vorhanden (show_preview nur _visible). Typ: Test |
| `test_security_review_2026_09_02` | bediengeraeteseite_verlangt_geraeteleserecht | /ui-api/controllers verlangt device.read (_need_zones), aber kein Test mit einem Konto, das z. B. nur zone.read (global) hat: UA_devices::endpoints_without_rights_are_403 nutzt client_als([]), würde auch bei Prüfung auf zone.read bestehen. Typ: Test |
| `test_tenant_history` | a_global_and_a_room_grant_together_show_each_room_once | Kombination globales + zonengebundenes zone.read (jeder Raum einmal, kein Duplikat) in visible_zones/Raumliste ungetestet. Typ: Test |
| `test_tenant_history` | a_page_of_a_plant_that_is_not_armed_says_that_it_shows_decisions | Daten: UA_reports::heating_time_counts_seconds_and_marks_dry_run (control_armed). Hinweistext in HeatingTime.svelte (Trockenlauf) nicht per Vitest geprüft; HistoryChart zeigt keinen Hinweis "hätte, keine Aufzeichnung dessen". Typ: Test (Chart-Hinweis ggf. Funktion) |
| `test_tenant_history` | a_tenant_without_any_room_gets_an_empty_page_not_an_error | Kein Test für Konto ohne sichtbare Räume bei /ui-api/bootstrap (zones leer), /ui-api/rooms; Svelte zeigt Text bei zones.length==0 (HistoryPage). /ui-api/heating-time antwortet ohne Recht 403 (UA_reports::heating_time_access). Typ: Test |
| `test_tenant_history` | the_hover_data_of_a_tile_belongs_to_its_own_room_only | Kein Test mit Messdaten zweier Räume, dass /ui-api/history nur Werte der angefragten Zone liefert (alle History-Tests nutzen eine Zone). Typ: Test |
| `test_tenant_history` | the_page_explains_why_it_can_stay_cooler_than_the_plan | Erklärtexte der Mieter-Verlaufsseite (Sonnenabsenkung, Abwesenheit/Urlaub/Aus, "keine Daten") und Glossar-Verweise gibt es in HistoryPage/HistoryChart nicht; nur Reihenbeschreibungen. Typ: Funktion |
| `test_tenant_history` | the_plant_navigation_does_not_list_the_tenant_history | Kein ungeskippter Test, dass Mieter-Eintrag /temperature-history in der Anlagen-Navigation fehlt (test_ui_profile::a_tenant_sees_no_plant_navigation prüft nur die Mieter-Richtung). Typ: Test |
| `test_tenant_history` | the_tile_drawing_and_summary_carry_only_the_own_measurements | Wie hover-data: Messwerte anderer Räume dürfen nicht in Buckets/Summen eingehen; ungetestet. Typ: Test |
| `test_tenant_history` | the_tile_of_a_foreign_room_is_not_found_even_when_asked_for_directly | Fremde Zone 404 getestet (UA_reports::history_foreign_and_unknown_zone_are_the_same_404), aber nicht: fremde Zone mit ungültigem from/to/resolution muss 404 bleiben (nie 422, sonst Existenzhinweis). Code prüft Sichtbarkeit zuerst. Typ: Test |
| `test_tenant_history` | the_tile_speaks_the_language_of_the_dwelling | Mieter-Wortlaut (Gemessen / Zielwert ohne Sonnenabsenkung / Tatsächlicher Zielwert, Erklärung zur Heizanforderung) ist in der Svelte-Seite nicht vorhanden; technische Reihenbezeichnungen für beide Profile. Typ: Funktion |
| `test_tenant_views` | a_read_only_room_says_so_instead_of_showing_nothing | Flag can_edit=False getestet (UA_schedule::read_only_and_tenant_views_flags); Text "ansehen, aber nicht ändern" (Inspector.svelte) und Hinweis auf bearbeitbaren Raum nicht geprüft. Typ: Test |
| `test_tenant_views` | a_room_taken_away_afterwards_no_longer_appears_in_the_absence_banner | _absence_state listet absence_zones(entry) ungefiltert; ein inzwischen entzogenes zone.read blendet den Raumnamen im laufenden Abwesenheits-Banner nicht aus. Namens-Leck. Typ: Funktion (und Test) |
| `test_tenant_views` | a_room_without_any_switch_time_can_be_set_up_by_the_tenant | Vereinfachtes "Einrichten" (zwei Zeiten, wärmerer Sollwert zuerst) gibt es nicht; Mieter auf leerem Plan nur über Paint/Punkte, dort ungetestet (UA_schedule::empty_plan_has_no_segments nur GET). Typ: Funktion |
| `test_tenant_views` | a_stale_measurement_is_explained_as_an_effect_not_a_cause | Hinweis "Messwert möglicherweise nicht aktuell / Heizung regelt weiter" nur als Hilfetext (help/explanations.ts); RoomCard zeigt Sensorstatus-Label des Geräts statt neutralem Effekt-Hinweis; Altersschwelle ungetestet. Typ: Funktion |
| `test_tenant_views` | a_tenant_without_any_room_gets_a_readable_page_not_an_error | Konto ohne Räume: bootstrap/rooms leer, kein Fehler, nicht getestet (siehe tenant_history). Typ: Test |
| `test_tenant_views` | the_adoption_appears_even_when_seen_from_a_read_only_room | copy-week erlaubt Quelle mit nur zone.read (Ziel schedule.manage), aber show_schedule liefert copy_targets nur wenn can_edit; vom nur lesbaren Raum aus ist die Übernahme nicht anbietbar, und ein Test mit Quelle ohne schedule.manage fehlt. Typ: Funktion und Test |
| `test_tenant_views` | the_dry_run_is_spelled_out_as_text | Daten control_armed getestet (UA_reports::heating_time_counts_seconds_and_marks_dry_run); Text "hätte" und Abwesenheit von kWh/Euro/Kosten-Beschriftung in HeatingTime.svelte ungeprüft. Typ: Test |
| `test_tenant_views` | the_live_poll_is_quiet_and_uses_the_server_interval | poll_interval_seconds in /ui-api/rooms aus Setting.shadow_interval_seconds nicht getestet; Front rooms-poller prüft nur Timer/Tab-Verhalten. Typ: Test |
| `test_tenant_views` | the_next_switch_line_comes_from_the_domain | UA_rooms::detail_has_modes_next_switch_vacation prüft next_switch nur gegen die Menge {21.0,17.0}, nicht Gleichheit mit domain next_switch(). Typ: Test |
| `test_tenant_views` | the_schedule_lands_on_a_room_the_tenant_may_actually_edit | ScheduleView wählt zones[0] (erster lesbarer Raum), nicht den ersten bearbeitbaren; bootstrap.zones trägt kein can_edit. Im Betrieb gemeldeter Fehler kann wiederkehren. Typ: Funktion |
| `test_ui_profile` | the_profile_cannot_be_switched_from_the_client | Kein Test, dass Abfrageparameter ui_profile, Kopfzeile X-UI-Profile oder Cookie ui_profile an /ui-api das Profil nicht ändern (Profil kommt aus Gruppe, UA_access::account_profile prüft nur das Ergebnis). Typ: Test |
| `test_vacation_views` | cancelling_ends_the_running_vacation | Lücke: Audit-Ereignis (object_type vacation, action update) beim Beenden wird in /ui-api nicht geprüft; UAS prüft nur ChangeLog vacation_cancel und cancelled_at. Funktion vorhanden (domain.schedule.cancel_vacation ruft audit.record). Typ: Test |
| `test_web_tokens_presets` | zone_scoped_permission_can_be_selected_without_widening | Lücke: (a) available[] mit zone_id-Eintrag für ein zonengebundenes Recht wird nicht geprüft (UAA vergleicht nur Codes eines anlagenweiten Besitzers); (b) der gespeicherte ApiTokenPermission.zone_id bei Ausgabe aus gehaltenem Zonenrecht wird nicht geprüft, test_a_token_can_never_carry_more_than_its_issuer prüft nur 201. Die Nicht-Ausweitung selbst ist abgedeckt (UAA::test_a_token_can_never_carry_more_than_its_issuer). Typ: Test |
| `test_zone_history_chart` | partial_route_and_zone_isolation | Anonym 401, fremde und unbekannte Zone gleiches 404, ohne Leserecht 404: UAR::test_history_and_csv_without_session_are_401, test_history_foreign_and_unknown_zone_are_the_same_404, test_history_without_any_read_right_is_404_not_an_existence_hint. Mieter 403 -> GEÄNDERT (UAR::test_history_tenant_profile_may_read_own_zone, Verlauf ist zone.read je Zone, ui_api_reports.py-Docstring). Lücke: ungültige Eingabe auf fremder Zone muss 404 statt 422 liefern (erst Recht, dann Eingabe); Code tut das (_visible_zone vor _moment), kein Test. Typ: Test |
| `test_zone_history_honesty` | a_changing_setback_is_labelled_with_its_range_not_its_maximum | Lücke: Bucket liefert nur solar_setback_k = max(); kein Minimum, die Oberfläche (formatSetback) zeigt bei wechselnder Absenkung im Fach den Höchstwert statt Bereich (history_report._bucket). Typ: Funktion |
| `test_zone_history_honesty` | a_setback_shorter_than_a_pixel_gets_a_marker_but_no_stretched_band | Lücke: HistoryChart.svelte zeichnet Solar-/Heiz-/Lückenrechtecke mit Math.max(1, Breite) (also Mindestbreite) plus 4-px-Balken; Regel "kein verbreitertes Band, dafür Marker" ist nicht umgesetzt und nicht getestet (kein Komponententest). Typ: Funktion |
| `test_zone_history_honesty` | a_ten_minute_setback_in_seven_days_has_a_thin_band_and_a_marker | Lücke: wie zuvor, dünnes Band samt Dreiecksmarker in der neuen Grafik weder vorhanden noch getestet; nur Datenwert solar_setback_k pro Fach vorhanden. Typ: Funktion |
| `test_zone_history_honesty` | drawn_widths_carry_no_minimum_that_would_claim_time | Lücke: Zeichenbreiten ohne Mindestbreite; HistoryChart.svelte setzt Math.max(1, w) bei Lücken, Solar, Heizung und Math.max(1.5, ..) bei Höhe. Weder Regel noch Test. Typ: Funktion |
| `test_zone_history_hover` | overview_tile_of_a_foreign_zone_is_refused_even_when_asked_for_directly | Fremde Zone gleiches 404 wie unbekannte: UAR::test_history_foreign_and_unknown_zone_are_the_same_404 (beide Endpunkte). Lücke: ungültige Eingabe (period/from) auf fremder Zone liefert 404 statt 422 und nennt Zonennamen nicht; kein Test (Code: _visible_zone vor _moment). Typ: Test |
| `test_zone_views` | the_delete_confirmation_names_every_dependency | Lücke: UAZ::test_delete_impact_counts_what_goes_with_the_room prüft nur setpoints=1 (übrige Zähler 0); Zähler schedule_points, devices, overrides, shadow_decisions werden nie > 0 geprüft; auch nicht mit zonengebundenem manage (nur anlagenweit). Funktion vorhanden (delete-impact liefert alle fünf). Typ: Test |
| `test_zone_views` | the_empty_zone_form_needs_an_installation_wide_permission | Lücke: GET /ui-api/room-form ohne zone_id mit nur zonengebundenem zone.manage -> 403 ist nicht getestet (nur POST /ui-api/rooms in test_manage_on_another_zone_does_not_reach_this_zone und GET mit nur zone.read); anlagenweit 200 gedeckt (UAZ::test_form_options_for_a_new_room). Code: _need in room_form ist anlagenweit. Typ: Test |

## Blocker vor dem Löschen (Funktionsparität)

**Stand zweiter Durchgang (siehe „Freigabe zum Löschen“ am Ende): Blocker 1–11 sind erledigt; die zwei neu gefundenen Lücken (Punkte 12 und 13) sind ebenfalls erledigt (Berichtigung 0.18.1).** Ursprünglicher Befund: Strenger Nachweis, Prüfung 2026-10-16, Einzelheiten und Belege je Funktion in `FUNKTIONSMATRIX.md`, Abschnitt „Funktionsparität je Alt-Seite“ (118 Funktionen: 80 vorhanden, 13 bewusst anders, 17 teilweise, 8 fehlt). Freigabe des Projektinhabers gilt nur, „wenn wirklich alle Funktionen in der neuen UI verfügbar sind“: Die folgenden Punkte sind die echten Lücken (keine Tests), nach Aufwand sortiert, kleinster zuerst. Punkt 8 ist sicherheitsrelevant (Grundsatz 7).

| Nr | Aufwand | Lücke | Alt-Funktion (Datei) | Stand 2026-10-16 (zweiter Durchgang) |
|---|---|---|---|---|
| 1 | klein | Homebridge „Konfiguration kopieren“ mit Rückmeldung und Auswahl-Fallback; Dauerwarnung „Platzhalter-Zugangsdaten“ auch bei gesetztem Broker | `static/homebridge_copy.js`, `templates/interfaces.html` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 2 | klein | Schaltprotokoll: Kennzeichnung Befehl / Entscheidung / Absenkung und Erklärtext (`kind` kommt schon vom Server) | `templates/device_commands.html`, `device_commands_views.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 3 | klein | Relaisverschleiß: Tagestabelle, „% der Lebensdauer je Jahr“, Definition Schaltspiel, Link zum Schaltprotokoll (Daten kommen schon) | `templates/relay_wear.html`, `report_views.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 4 | klein | Gerätekarte: „Alle einklappen / Alle ausklappen“ | `templates/mapping_map.html`, `static/mapping_map.js` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 5 | klein | Hinweis- und Leerzustandstexte: Modus löschen („Sollwerte werden mitgelöscht“), Frostschutz bei leerem Plan, „Noch kein Gerät bekannt“ mit MQTT-Hinweis, Urlaub-Erklärtext, „Mindestens 12 Zeichen“ / „Benutzername unveränderlich“ / „Ohne Gruppe keine Rechte“, Räume-Liste mit Sortierwert und Links | `templates/mode_delete.html`, `schedule.html`, `devices.html`, `vacation.html`, `users.html`, `zones.html` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 6 | klein | Rechte-Matrix: Raumfelder sperren, wenn das Recht anlagenweit gilt | `static/permissions.js`, `templates/groups.html` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 7 | klein bis mittel | Bediengeräte: „Letzter Wert“ mit Alter je lesbarem Merkmal (Server liefert ihn nicht: `ui_api_devices.py` plus Seite) | `templates/controllers.html`, `controller_views.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 8 | mittel | Startseite: dauerhafte Anlagenhinweise wie im Alt-System: Aktorfreigabe (Scharf / Neustart fehlt / Trockenlauf), MQTT-Ausgabe, Verbundrolle, Brücke nicht erreichbar, Sammelchips Kein Messwert / Messwert unverändert / Fenster vergessen, Urlaub läuft; Mieter: „Heizung läuft normal“; Leerzustand „Erste Zone anlegen“. Heute ist `comp/rooms/ActuationPanel.svelte` unbenutzt und die Kachel `system` nicht im Standard-Layout | `templates/start.html`, `tenant_start.html`, `start_views.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 9 | mittel | Raumkontext: Sprunglinks aus dem Raumdetail zu Zeitplan, Geräten, Datenfluss und Raumvorwahl über die Adresse (`?zone=`); Direktlinks `?period=`, `?zone=`, `?ansicht=`, Glossar-Fragment werden von den Umleitungen und der Oberfläche heute verworfen | `templates/zone_header.html`, `schedule_views.py`, `templates/tenant_schedule.html`, `zones_history_body.html`, `kiosk_views.py`, `legacy_redirects.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 10 | mittel | Mieter „Einrichten“: zwei Zeiten für leeren Tag mit vorgeschlagenen Zeiten und vereinfachtes Zwei-Zeiten-Bearbeiten (bekannt offen) | `templates/tenant_schedule.html`, `tenant_views.py` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 11 | klein (Aufräumen) | Verweise der Oberfläche auf Alt-Pfade auf `/app/...` umstellen (`more` in `domain/interfaces.py`: /devices, /tokens, /passkeys, /control; `legacyHref` in `AreaView.svelte`, `Placeholder.svelte`, `ConnectionsPage.svelte`); sie funktionieren heute nur über die Umleitung | `domain/interfaces.py`, `frontend/src/lib/views/AreaView.svelte` | **erledigt**, im Code belegt (Matrix, Abschnitt „Funktionsparität je Alt-Seite“) |
| 12 | klein | Wochenplan (Verwaltung): Schaltpunkt bzw. Zeitraum ohne Zeiger/Touch anlegen -- ein Formular mit Tag, Uhrzeit (bzw. Von/Bis) und Modus fehlt; Anlegen geht nur durch Ziehen mit dem Pinsel, per Formular nur auf Tagen mit 0 oder 2 Zeiten | `templates/schedule.html` (Formulare „Schaltpunkt anlegen“, „Zeit malen“), `schedule_views.py` | **erledigt** -- `components/schedule/NewTimePanel.svelte` |
| 13 | klein | Betrieb/Notbetrieb: Aktortabelle zeigt Frist der Takt-Phase (`phase_deadline_at`), Taktquelle (`cycle_source`: Kennlinie/Festtakt) und wirksamen Sensor-Timeout (`sensor_timeout_seconds`) nicht; die Werte kommen schon von `ui_api_operations.py` | `templates/control.html`, `control_views.py` | **erledigt** -- `components/operations/RoomDecisions.svelte` (Takt-Phase, Taktquelle, Frist, Sensor-Timeout) |

Nicht als Blocker gewertet (begründet anders): Ziehen von Gerätekarten (`assignment.js`) ersetzt durch Auswahlfelder, Zeit-malen-Formular durch Ziehen und Inspektor, Mermaid durch SVG, globale Ladeanzeige durch lokale Ladezustände, Passkey-Autofill (Entscheidung Auth-Flows), OAuth-Zustimmung bleibt serverseitig.

## 9. Alle Zuordnungen im Einzelnen

### tests/test_account_views.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_tenant_reaches_their_own_account_page | ERSETZT | test_ui_api_access.py::test_account_profile (tenant_client([]) -> 200, ui_profile tenant) und ::test_tenant_profile_is_refused_before_the_right_is_checked (Konto bleibt 200 fuer Mieter) |

| the_tenant_page_uses_the_tenant_shell | ERSETZT | Regel (Profil kommt aus Gruppe des Nutzers): test_account_profile (ui_profile "tenant") und test_ui_api.py::test_bootstrap_reports_tenant_profile; CSS-Klassen der Huelle entfallen |



### tests/test_admin_views.py (3)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_group_page_shows_permissions_by_area_in_plain_words | FEHLT | test_ui_api_access.py::test_group_list_gives_areas_zones_and_permissions prueft nur 2 Bereichstitel, code und zone_scoped; Klartext-description je Recht (ui_api_access.py Z.530) und alle PERMISSION_AREAS-Namen nicht geprueft (Vollstaendigkeit Domaene: test_authz). Typ: Test |

| the_rendered_group_form_changes_the_group_and_the_rights | ERSETZT | test_ui_api_access.py::test_a_second_administrator_lifts_the_guard (PUT users/{id}/group, Mitgliedschaft, Audit) + test_administration.py::test_changing_a_users_group_changes_their_rights (Rechte wirken) |

| the_user_list_needs_user_manage | ERSETZT | test_ui_api_access.py::test_each_endpoint_demands_exactly_its_own_permission (GET access/users ohne user.manage 403 forbidden) + test_admin_endpoints_without_rights_are_403_and_account_stays_open + test_user_list_has_groups_and_marks_self (200 mit Recht) |



### tests/test_app.py (1)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_login_page_includes_the_stylesheet | ERSETZT | test_spa_views.py::test_shell_without_prefix (Huelle verweist auf /static/app/assets/index-ab.css); Bootstrap-Stylesheet der Altseite entfaellt |



### tests/test_assets.py (1)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| html_announces_asset_set_for_tabs_open_during_an_update | ENTFÄLLT | Einziger Verbraucher des Headers X-Thermoctl-Assets war static/page_scripts.js (HTMX-Altoberflaeche); Svelte-Frontend wertet ihn nicht aus (grep frontend/src leer). Aktualisierung ueber gehashte Assets + test_spa_views.py::test_shell_must_be_revalidated. Header in app.py (Z.1563) bleibt ungetestet, wenn die Datei geloescht wird |



### tests/test_audit_views.py (12)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| action_actually_filters | ERSETZT | test_ui_api_reports.py::test_audit_lists_filters_and_names_the_actor (action_code=zone.updated) |

| audit_requires_audit_read | ERSETZT | test_ui_api_reports.py::test_audit_access (401, wrong_profile 403, zone.read allein 403 forbidden); mit audit.read 200 in test_audit_lists_filters_and_names_the_actor |

| detail_is_only_visible_on_request | ERSETZT | Detail wird im JSON geliefert: test_audit_lists_filters_and_names_the_actor (detail == "Detail 3"); Einklappen (details-Markup) ist Darstellung |

| from_actually_filters | ERSETZT | test_audit_lists_filters_and_names_the_actor (from_date=heute alle, from_date=2999 leer). Hinweis: exakte Tagesgrenze nicht geprueft, siehe to_filters/local_day |

| object_filters_type_and_optional_id | ERSETZT | test_audit_lists_filters_and_names_the_actor (object=zone:1, object=zone) |

| pagination_returns_the_second_page | ERSETZT | test_ui_api_reports.py::test_audit_pagination_and_validation (51 Eintraege, Seite 2, has_more) |

| source_actually_filters | ERSETZT | test_audit_lists_filters_and_names_the_actor (source=web) |

| the_date_filter_uses_the_configured_local_day_not_utc_midnight | FEHLT | Kein Test, dass from_date/to_date am konfigurierten lokalen Tag (Europe/Berlin) schneiden statt an UTC-Mitternacht (Eintrag 23:30 UTC am 1.8. gehoert zum lokalen 2.8.). Test nimmt "heute" und 2999/2000 (nicht deterministisch); local_day_start_utc in keinem Test. Funktion vorhanden (audit_query.py Z.47/51). Typ: Test |

| to_before_from_shows_a_message_and_keeps_the_values | ERSETZT | test_audit_pagination_and_validation (to < from -> 422, field to_date). Werterhalt im Formular ist Frontend |

| to_filters_inclusive_of_the_whole_day | FEHLT | to_date wird nur mit "heute" (Eintraege von jetzt) und 2000 geprueft; die obere Kante (23:59:59 des Tages enthalten, 00:00 des Folgetags ausgeschlossen) ist nicht geprueft. Funktion vorhanden (audit_query.py Z.50-52). Typ: Test |

| unreadable_filter_values_show_messages_instead_of_422 | GEÄNDERT | Bewusst 422 mit Feld statt Seite mit Meldung (ui_api_reports.py Z.19 Fehlerform {"detail","code","field"}; _page/_day -> _invalid). Test: test_audit_pagination_and_validation (page=x, from_date=kaputt) |

| user_actually_filters | FEHLT | Nur Zweig Token-Name getestet (user="mein-token"); Zweig Benutzername (User.username == user, audit_query.py Z.54) ohne Test. Typ: Test |



### tests/test_changes_views.py (11)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| changes_page_requires_audit_read | ERSETZT | test_ui_api_reports.py::test_changes_access (401; zone.read -> 403 forbidden; Liste und Detail). Navigationslink: test_ui_api.py::test_bootstrap_navigation_follows_grants |

| changes_page_shows_before_and_after | ERSETZT | test_changes_list_and_detail (fields before/after, revertible, zone_id, summary) |

| confirmation_and_success_restore_the_value | ERSETZT | test_revert_restores_the_value_and_cannot_repeat (200, outcome reverted, hysteresis_k None). Bestaetigungsseite per GET entfaellt (SPA-Dialog) |

| conflict_needs_second_confirmation | ERSETZT | test_revert_conflict_needs_the_confirmation_token (409 conflict mit current/expected, force ohne/mit falschem Token 403, mit Token forced) |

| date_and_reverted_filters | FEHLT | Tagesgrenze im Adapter nicht geprueft (nur from 2999/to 2000/weit), hide_reverted nur ohne zurueckgenommenen Eintrag (ids(hide_reverted)==ids() trivial); Domaene deckt include_reverted (test_change_log.py::test_list_changes_can_hide_reverted_entries) und from_at/to_at. Typ: Test |

| filters_and_escapes_free_text | ERSETZT | Filter zone/Typ/Quelle: test_changes_list_and_detail. HTML-Escaping entfaellt: JSON liefert Rohtext, Svelte escaped ({@html} in frontend/src nicht vorhanden) |

| invalid_filter_choices_keep_the_form_usable | ERSETZT | test_changes_pagination_and_validation (422 je Feld to_date, change_type, source). Ungueltige zone_id (kein int) nicht eigens geprueft (Pydantic-422) |

| page_two_and_bad_filters | ERSETZT | test_changes_pagination_and_validation (Seite 2, page=x -> 422, page=0 -> 1) |

| revert_needs_change_right_but_not_audit_read | GEÄNDERT | Bewusst strenger: Ruecknahme verlangt audit.read + Anlagensicht (ui_api_reports.py Docstring Z.14-16 "strenger als die HTML-Route"). Tests: test_changes_access (Revert ohne audit.read 403) und test_revert_needs_the_right_of_the_original_change (Recht der Ursprungsaenderung 403) |

| tenant_profile_cannot_open_change_log | ERSETZT | test_changes_access (tenant_client mit audit.read -> 403 wrong_profile fuer Liste, Detail, Revert) |

| unknown_and_repeated_revert_are_friendly | ERSETZT | test_revert_restores_the_value_and_cannot_repeat (999999 -> 404 not_found, zweite Ruecknahme 409 already_reverted, Ruecknahme-Eintrag 409 not_revertible) |



### tests/test_control.py (13)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| an_invalid_curve_point_rejects_the_whole_write_without_data_loss | ERSETZT | Atomaritaet/Kennlinienform in der Domaene: test_sensor_failure_policy.py::test_invalid_curve_shape_preserves_profile_and_points und ::test_invalid_profile_is_atomic_even_when_caller_commits; Adapter: test_ui_api_operations.py::test_sensor_failure_defaults_reject_bad_input_without_writing (422, nichts geschrieben; Kennlinienform dort nur ueber ungueltige Zahl) |

| backup_offsets_are_only_shown_and_saved_with_device_manage | ERSETZT | test_ui_api_operations.py::test_backup_offsets_need_device_manage (ohne device.manage backup_offsets None), ::test_backup_offsets_are_saved_with_device_manage, ::test_backup_offsets_without_device_manage_are_refused_not_ignored (strenger: 403 statt Ignorieren, gleiches Recht) |

| operating_pages_describe_armed_after_restart_truthfully | ERSETZT | Zustand live: test_ui_api_operations.py::test_the_three_latches_are_reported_separately (live, sending_allowed) + frontend/tests/operations-logic.test.ts (Entscheidungen nur bei live+fuehrend). Wortlaut der Befehlswege je Aktortyp entfaellt (Text, keine Regel) |

| operating_pages_describe_armed_before_restart_truthfully | ERSETZT | test_the_three_latches_are_reported_separately (state armed_restart_missing, restart_required) + operations-logic.test.ts "Ausstehender Neustart wird nie als schaltend dargestellt" |

| operating_pages_describe_dry_run_truthfully | ERSETZT | test_the_three_latches_are_reported_separately (base: dry_run True, live False, state dry_run) + operations-logic.test.ts |

| saving_the_sensor_failure_defaults_round_trips | ERSETZT | test_ui_api_operations.py::test_sensor_failure_defaults_are_saved_with_curve (Werte, Kennlinie, profile_id, Audit) |

| the_operations_page_names_the_second_bolt | ERSETZT | test_the_three_latches_are_reported_separately (armed True, sending_allowed False, restart_required) + operations-logic.test.ts (Karte Gesperrt, "Neustart") |

| the_page_shows_the_dry_run | ERSETZT | test_the_three_latches_are_reported_separately (dry_run True) |

| the_settings_page_shows_the_effective_sensor_failure_profile | ERSETZT | test_settings_show_every_group_with_limits (sensor_failure profile_name, fixed_on/off 600/1200) |

| the_settings_page_shows_the_outdoor_source_selection | ERSETZT | test_settings_show_every_group_with_limits (outdoor.devices, source_device_id) + test_outdoor_source_is_set_and_cleared_through_the_domain |

| the_solar_setback_can_be_switched_on_through_its_own_form | ERSETZT | test_ui_api_operations.py::test_solar_location_is_saved_and_cleared (enabled True, Koordinaten persistiert). Name/Wert-Fehler des HTML-Schalters entfaellt (JSON-Boolean) |

| the_start_page_keeps_the_three_latches_apart | ERSETZT | test_the_three_latches_are_reported_separately und ::test_the_cluster_role_is_a_third_separate_latch + operations-logic.test.ts "vier getrennte Karten" |

| the_zone_parameter_page_shows_the_sensor_failure_section | ERSETZT | test_parameters_show_own_and_effective_values_separately (sensor_failure: profile_source, effective_profile_name, profiles) |



### tests/test_control_solar.py (1)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_page_shows_the_off_state_by_default | ERSETZT | test_ui_api_operations.py::test_settings_show_every_group_with_limits (solar == {enabled: False, ...}) |



### tests/test_control_views.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| an_unknown_period_falls_back_to_the_default | GEÄNDERT | Bewusst 422 statt stillem Rueckfall: ui_api_reports.py::_period_start ("Ungueltiger Zeitraum.", field period). Test: test_ui_api_reports.py::test_relay_wear_access (period=x -> 422 validation) |

| the_arming_notes_name_both_command_paths_behind_the_second_latch | FEHLT | Neue Oberflaeche nennt nur "MQTT-Sendefreigabe"/"MQTT-Riegel" (ArmPanel.svelte Z.62/82, glossary.ts "sending", rooms/logic.ts Z.155); dass der Riegel auch Meross-Befehle sperrt, steht nirgends, kein Test. Typ: Funktion |

| the_operating_page_shows_notbetrieb_detail_and_comparison | FEHLT | Stufe/Aktoren/Vergleich: test_operation_shows_emergency_actuators_and_comparisons (Ansicht gemockt) + test_emergency_state.py (Domaene, "nicht versucht", Offset-Vorschlag). Nicht geprueft: outdoor_status_text ("keine Außenquelle eingerichtet") im JSON (Uebersetzung nur test_emergency_display.py) und Durchlauf mit echten DB-Zeilen. Typ: Test |

| the_operating_page_shows_pi_and_its_fallback_reason | ERSETZT | test_operation_shows_decisions_with_reasons_and_emergency_per_zone (requested/effective_controller, controller_fallback_label fuer fenster_offen und unbekannt). Label fuer pi_ungeeignet nur als Konstante in shared/control.py |

| the_operating_page_shows_the_most_recent_decision_per_zone | ERSETZT | test_operation_shows_decisions_with_reasons_and_emergency_per_zone (je Zone Entscheidung, Zone ohne Entscheidung None; nutzt latest_decisions_by_zone). Alttest prueft nur Zonenname im Text |



### tests/test_controller_channels.py (9)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_fixed_number_with_more_than_one_decimal_survives_an_unchanged_resubmit | FEHLT | Kein Test, dass fixed_number mit bis zu 4 Nachkommastellen (z.B. 3,125) unverrundet ausgeliefert (_num, ui_api_devices.py Z.843) und beim erneuten Senden unveraendert gespeichert wird; test_channel_writes_and_validates nutzt nur "21,5". Typ: Test |

| a_room_sensor_used_only_as_temperature_source_appears_as_a_source | ERSETZT | test_ui_api_devices.py::test_controllers_read_model_with_properties_channels_and_seen_actions ("fuehler-a" nur ueber zone.temperature_source in source_devices) |

| a_saved_read_channel_keeps_its_kind_selected_after_reload | ERSETZT | Serverseite: test_controllers_read_model_with_properties_channels_and_seen_actions (gespeicherte Leseart kind zone_temperature + zone_id). Kind operating_mode nicht eigens; Select-Bindung ist Svelte-Zustand (ControllersPage.svelte), ohne Test |

| a_saved_write_channel_shows_every_field_after_reload | FEHLT | Nach Speichern wird nur direction/zone_id bzw. DB fixed_number geprueft; Ausgabe von kind, source_device_id, fixed_text, fixed_number im GET /controllers (ui_api_devices.py Z.838-843) je Art (fixed/sensor/zone) nicht geprueft. Typ: Test |

| resubmitting_the_rendered_form_unchanged_keeps_the_configuration | FEHLT | Kein Round-Trip GET -> unveraendert POST, der die Konfiguration (z.B. Leseart operating_mode + Zone) erhaelt; Kind operating_mode in ui-api ungetestet. Typ: Test |

| the_controller_page_and_both_form_endpoints | ERSETZT | test_channel_writes_and_validates (POST channel, fixed 21,5), test_controller_button_checks_every_zone_the_device_hangs_in (POST button), test_controllers_read_model_... (GET), Recht device.manage je Zone: test_channel_cannot_target_a_zone_or_device_beyond_the_principals_rights |

| the_controllers_page_shows_a_configured_channel | ERSETZT | test_controllers_read_model_with_properties_channels_and_seen_actions (channels[0] kind/zone_id/Richtung) |

| the_controllers_page_with_no_visible_zone_shows_nothing | FEHLT | Fall "anlagenweites device.read, aber keine Zone vorhanden" -> leere Antwort statt aller Geraete nicht geprueft (Guards shared/controllers.py Z.26/51, Funktion vorhanden). Fremde Namen bei zonenweitem Recht: test_a_room_a_reader_finds_no_device_of_room_b_anywhere. Typ: Test |

| the_edit_form_is_shown_only_to_a_principal_who_may_manage_the_zone | ERSETZT | manageable-Flag: test_controllers_read_model_... (manageable True mit zonenbezogenem device.manage) und test_controllers_hide_zones_the_viewer_cannot_read (nur device.read -> manageable False). Anzeige des Formulars selbst ist Frontend |

# Teil B: Alt-Tests daily_views bis emergency_display_http



Kurzformen: UA = tests/test_ui_api_*.py, Dom = Domänentest. Testnamen ohne Präfix `test_`.



### tests/test_daily_views.py (21)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_start_page_uses_local_day_and_time_for_its_schedule_track | FEHLT | Lokaler Wochentag/Zeitzone nahe Mitternacht (UTC So 23:30 = Berlin Mo 00:30) für `day_track` und `now_fraction` ungeprüft; test_ui_api_rooms prüft nur Summe 1440 und Modusnamen. Typ: Test (zone_status.py:210 existiert) |

| the_parameter_page_shows_inherited_values_and_empty_restores_inheritance | ERSETZT | test_ui_api_operations.py::parameters_show_own_and_effective_values_separately (own/effective/inherited/source) und ::null_returns_to_inheritance_and_absent_fields_stay_untouched |

| the_rendered_parameter_form_carries_the_valve_protection_field_names | ERSETZT | test_ui_api_operations.py::save_parameters_writes_the_zone_and_the_audit_log (Ventilschutz 14/5) plus rejected_input_names_the_field_and_writes_nothing; Formularnamen/Texte sind Markup |

| the_parameter_page_shows_the_pi_warning_with_the_switching_table_and_relay_wear_link | FEHLT | Modellrechnungstabelle (262.800/52.560 je Jahr), Hinweis Kessel/Verdichter, Link Relaisverschleiß, Verhältnis 0,53 fehlen in RoomTechnik.svelte (nur Bestätigungs-Checkbox). Typ: Funktion |

| the_pi_warning_ratio_follows_a_changed_assumption | FEHLT | Verhältnis ungünstigster Fall / angenommene Lebensdauer wird nirgends berechnet; /ui-api liefert nur assumed_relay_lifetime_operations. Typ: Funktion |

| an_ineligible_zone_shows_the_reason_and_refuses_to_switch_pi_on | ERSETZT | test_ui_api_operations.py::parameters_show_own_and_effective_values_separately (eligible False, Grund "Schaltaktor") und ::rejected_input_names_the_field_and_writes_nothing[pi_enabled] (422, nichts geschrieben) |

| valve_protection_checkbox_round_trip | FEHLT | Nur true wird gespeichert; explizites `valve_protection_enabled=false` (Ausschalten) wird über /ui-api nie getestet. Semantik "fehlend = unverändert" ist durch null_returns_to_inheritance... gedeckt. Typ: Test |

| pi_enabled_stays_on_across_further_saves_without_reconfirming | ERSETZT | test_ui_api_operations.py::pi_is_switched_on_with_confirmation_on_an_eligible_zone (späteres Speichern ohne pi_confirm 200, Abschalten braucht keine) |

| an_enabled_but_now_ineligible_pi_switch_is_never_disabled_in_the_form | FEHLT | Weder Clientlogik `piSwitchLocked` (RoomTechnik.svelte:102) noch Serververhalten beim Speichern bei aktivem, inzwischen unzulässigem PI sind getestet. Typ: Test |

| parameters_of_a_foreign_zone_yield_404 | ERSETZT | test_ui_api_operations.py::parameters_hidden_zone_is_the_same_404_as_unknown (GET und PUT, identischer Body, code not_found, Recht nur auf fremde Zone) |

| the_parameter_page_shows_the_window_temp_drop_switch_off_by_default | ERSETZT | test_ui_api_operations.py::parameters_show_own_and_effective_values_separately (window_temp_drop_detection_enabled False); "ein Speichern-Knopf" ist Markup |

| showing_and_cancelling_an_override | FEHLT | Anlegen/Aufheben und Rechte gedeckt (UA rooms override_permanent..., cancel_override, writes_without_permission_are_403), aber Begründung `setpoint.reason` "Übersteuerung (feste Temperatur)" wird nirgends geprüft (domain/schedule.py:1300). Typ: Test |

| a_running_timed_override_describes_its_remaining_time_not_its_end_as_past | GEÄNDERT | Server liefert nur `ends_at` (UA rooms override_duration_and_next_switch); Anzeige als "bis HH:MM" statt Restdauer (frontend dashboard/tiles.ts agendaItems, kiosk-logic.test.ts overrideText) |

| the_overview_explains_a_missing_reading_and_shows_the_decision | ERSETZT | test_ui_api_rooms.py::list_zone_without_state_or_decision (kein Wert, unknown), rooms-logic.test.ts roomStatus (Sensorzustand), test_ui_api_operations.py::operation_shows_decisions_with_reasons_and_emergency_per_zone (Begründung) |

| the_overview_shows_the_latest_of_several_decisions | FEHLT | Tiebreak von `latest_decisions_by_zone` (gleiches decided_at, höhere id gewinnt) hat nach Löschen keinen Test; test_ui_api_operations importiert die Funktion nur ungenutzt (noqa). Typ: Test (Funktion existiert, von /ui-api/rooms genutzt) |

| the_overview_labels_heating_as_a_decision | GEÄNDERT | Formulierung "Heizanforderung" statt "Heizentscheidung/Heizt" (frontend/src/lib/rooms/logic.ts roomStatus-Kommentar: Entscheidung, kein Schaltzustand); rooms-logic.test.ts prüft kind heating |

| the_operating_mode_is_shown_only_when_it_deviates | ERSETZT | test_ui_api_rooms.py::change_operating_mode (operating_mode off im Raum) und rooms-logic.test.ts roomStatus (Betriebsart off -> kind off) |

| without_setpoint_write_no_thermostat_is_on_the_page | FEHLT | Nur "alle Rechte" vs. "nur zone.read" (list_shows_state_setpoint_and_actions, list_zone_without_state_or_decision); `actions.edit_setpoint_step` ist nie isoliert an setpoint.write (zonengebunden) gebunden getestet. Typ: Test |

| the_start_page_shows_the_outdoor_temperature | FEHLT | Dashboard/Startseite zeigt keine Außentemperatur (tiles.ts: Kachel "outdoor" nicht verfügbar); /ui-api/settings liefert reading nur admin-seitig, Wert 3,2 nie getestet. Typ: Funktion |

| the_start_page_names_no_source_and_stale_distinctly_from_a_value | ERSETZT | Dom test_outdoor.py::no_source_configured_reads_as_no_source und ::a_stale_reading_is_veraltet_not_confused_with_a_warm_value; Anzeige in OutdoorPage.svelte (STATUS-Map); Startseite zeigt es nicht mehr (siehe vorige Zeile) |

| the_start_page_shows_a_forgotten_window_chip | FEHLT | `sensor.window_alarm` (ZoneState.window_alarm) wird in /ui-api/rooms nicht geprüft; Clientzweig window_alarm in roomStatus nur über window_open getestet, Beschriftung "Fenster offen" statt "vergessen". Typ: Test |



### tests/test_daily_views_backup_offset.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_stored_backup_offset_is_shown_in_its_input | ERSETZT | test_ui_api_operations.py::backup_offsets_need_device_manage (offset_k "-1.50" mit device.manage, None ohne) |

| saving_without_touching_the_backup_offset_keeps_it | ERSETZT | test_ui_api_operations.py::backup_offsets_are_saved_with_device_manage (fehlender Schlüssel lässt Wert, null löscht) und ::backup_offsets_without_device_manage_are_refused_not_ignored (403) |



### tests/test_daily_views_solar.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_page_shows_the_documented_default_of_zero | FEHLT | `solar_gain_factor` Vorgabe 0 in GET /rooms/{id}/parameters nicht geprüft (nur Speichern 0.5 und Fehler). Typ: Test |

| the_setback_maximum_inherits_the_global_default | FEHLT | Vererbung `solar_setback_max_k` von default_solar_setback_max_k (inherited/effective 3.5) in /ui-api nicht geprüft; auch kein Dom-Test. Typ: Test |



### tests/test_data_flow.py (6)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| page_shows_the_zone_with_diagram_source_and_edge_list | ERSETZT | test_ui_api_devices.py::data_flow_is_structured_and_filtered_by_zone_manage (Knoten, Kanten, mermaid) und Dom test_data_flow.py::reading_only_valve_gets_only_the_actual_value_even_with_a_window_contact; Teil "kein externes Skript" ist HTML-Hülle (Hinweis: für die SPA-Hülle gibt es keinen Test auf Fremdquellen) |

| page_lists_only_visible_zones | ERSETZT | test_ui_api_devices.py::data_flow_is_structured_and_filtered_by_zone_manage (zone.manage nur Wohnen -> Liste ["Wohnen"], Bad vorhanden) |

| single_zone_page_refuses_a_foreign_zone | ERSETZT | test_ui_api_devices.py::zone_of_another_principal_is_404_and_read_only_is_403 (data-flow fremder Zone 404 not_found) und ::zone_endpoints_without_any_right_are_the_same_404_as_an_unknown_zone |

| page_without_the_right_is_empty_like_its_neighbours | GEÄNDERT | Ohne Recht 403 statt leerer Seite (ui_api_devices.py `_need_zones`-Docstring); Einzelzone 404 gedeckt durch data_flow_is_structured_... (device.read+zone.read -> 404) und endpoints_without_rights_are_403 |

| page_is_closed_to_tenants_and_anonymous_visitors | ERSETZT | test_ui_api_devices.py::tenant_profile_is_refused_everywhere (403 wrong_profile, Übersicht und Einzelzone, mit zone.manage) und ::every_endpoint_without_session_is_401 |

| single_zone_page_has_one_heading_and_the_dry_run_note | FEHLT | Hinweis "Im Trockenlauf wird nichts gesendet ... Notbetrieb-Übergabe nicht dargestellt" fehlt in DataFlowPanel.svelte (Überschrift ist Markup). Typ: Funktion |



### tests/test_device_assignment.py (7)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_page_shows_assignments_and_the_temperature_source | ERSETZT | test_ui_api_devices.py::zone_devices_read_model (assignments, temperature_source, roles); Lesezugriff nur device.read: zone_of_another_principal_is_404_and_read_only_is_403 |

| the_writing_routes_and_their_permissions | ERSETZT | test_ui_api_devices.py: assign_several_sensors_and_windows_but_one_actuator_zone, source_can_be_set_replaced_and_cleared, swap_moves_all_roles_and_resets_the_valve_mode, detach_removes_the_assignment_only_of_this_zone; fremde Zone 404 per zone_of_another_principal_is_404_and_read_only_is_403 |

| drop_targets_only_with_device_manage | ERSETZT | test_ui_api_devices.py::zone_of_another_principal_is_404_and_read_only_is_403 (Leser: may_edit False, Schreibwege 403 forbidden) und zone_devices_read_model (may_edit True mit device.manage) |

| zugeordnete_karten_tragen_ihre_kennung | ENTFÄLLT | Nur data-Attribute der Drag-Karten; JSON trägt assignment `id` (ui_api_devices._zone_devices), von detach_removes_the_assignment_only_of_this_zone genutzt |

| the_page_shows_the_buttons_that_actually_arrived | ERSETZT | Dom test_controller.py::seen_actions_come_from_what_actually_arrived plus test_ui_api_devices.py::zone_devices_read_model (controllers, commands); Durchreichung `last_seen` im Zonenmodell nur mit None geprüft |

| a_thermostat_can_be_dragged_onto_the_actuator_slot | ERSETZT | test_ui_api_devices.py::zone_devices_read_model (required_capabilities actuator = switch, thermostat) und Dom test_device_assignment.py::a_thermostat_can_be_assigned_as_an_actuator |

| the_regulation_switch_is_rendered_with_its_own_id_and_a_fallback | ENTFÄLLT | Reine Markup-Eigenschaften (Element-IDs, noscript, data-submit-on-change); Regel selbst: regulation_switches_the_valve_mode_and_is_audited |



### tests/test_device_commands_views.py (17)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| device_commands_requires_audit_read | ERSETZT | test_ui_api_devices.py::device_commands_validate_the_filters_and_need_audit_read (device.read+zone.read -> 403 forbidden), endpoints_without_rights_are_403; Positivfall mit audit.read allein nicht isoliert (nur im Rechtepaket) |

| from_actually_filters | ERSETZT | test_ui_api_devices.py::device_commands_list_filter_and_page (from_date/to_date Tag) und Dom test_domain_device_commands.py::date_range_filter_is_inclusive_on_both_ends |

| to_filters_inclusive_of_the_whole_day | FEHLT | Tagesende 23:59:59 der Adapterumrechnung (to_at = Folgetag minus 1 us, ui_api_devices.py:989) ungeprüft; UA-Test hat nur einen Eintrag um 09:00. Typ: Test |

| zone_filter_matches_the_name_snapshot_of_a_deleted_zone | ERSETZT | Dom test_domain_device_commands.py::zone_filter_matches_the_name_snapshot_of_a_deleted_zone; Adapter: device_commands_list_filter_and_page (zone=Bad) |

| outcome_filter_actually_filters | ERSETZT | Dom test_domain_device_commands.py::outcome_filter; Adapter: device_commands_list_filter_and_page (outcome unbekannt -> leer) |

| error_and_reason_are_shown | FEHLT | Felder `error` und `reason` der Einträge werden in /ui-api/device-commands nicht geprüft (create_device_command setzt error None, reason wird nicht asserted). Typ: Test |

| pagination_returns_the_second_page | ERSETZT | test_ui_api_devices.py::device_commands_list_filter_and_page (52+1 Einträge: Seite 1 = 50 und has_more, Seite 2 = 3) |

| to_before_from_shows_a_message_and_keeps_the_values | GEÄNDERT | Jetzt 422 validation mit field to_date statt 200-Seite (ui_api_devices.py:985 `_invalid`), gepinnt in device_commands_validate_the_filters_and_need_audit_read; Werte behält der Client |

| unreadable_filter_values_show_messages_instead_of_422 | GEÄNDERT | Jetzt 422 mit field (from_date, page) statt Meldungen im 200-Body; gepinnt in test_ui_api_devices.py::device_commands_validate_the_filters_and_need_audit_read |

| the_time_shown_is_local_not_utc | GEÄNDERT | Server liefert UTC-ISO (+00:00) plus `timezone`, Umrechnung im Client: rooms-logic.test.ts formatInstant (Europe/Berlin); Feld `timezone` im Befehlsendpunkt nicht asserted |

| the_rendered_filter_form_actually_filters | ENTFÄLLT | Prüft nur Formular-/name=-Attribute des HTML-Templates; Query-Parameter gedeckt durch device_commands_list_filter_and_page |

| absenkung_entries_are_shown_with_kind_text_and_labels | FEHLT | Dom test_solar_setback_log.py::list_commands_marks_the_entries_as_absenkung_and_merges_them_by_time prüft Texte; im Adapter nur startswith("absenkung_") bei outcome_labels, nicht Absenkung-Eintrag, kind absenkung, Quelle "Regelung (Sonnenabsenkung)". Typ: Test |

| the_page_does_not_call_the_value_before_the_setback_a_schedule | ERSETZT | Dom test_solar_setback_log.py::no_setback_text_calls_the_value_before_the_setback_a_schedule (Hinweistext der Seite ist Markup) |

| absenkung_entries_need_audit_read_like_every_other_entry | ERSETZT | Gleicher Endpunkt: test_ui_api_devices.py::device_commands_validate_the_filters_and_need_audit_read (403 ohne audit.read); REST test_api.py::device_commands_absenkung_entries_need_audit_read |

| the_outcome_filter_hides_absenkung_entries | ERSETZT | Dom test_solar_setback_log.py::an_outcome_filter_excludes_the_absenkung_entries |

| the_zone_filter_offers_a_zone_that_only_has_absenkung_entries | FEHLT | Vereinigung `zone_names` mit `setback_zone_names` im Adapter (ui_api_devices.py:1013) nie mit reiner Absenkungszone geprüft; Dom test_solar_setback_log.py::setback_zone_names_lists_only_zones_with_a_setback deckt nur die Funktion. Typ: Test |

| the_zone_filter_lists_a_zone_with_commands_and_absenkung_only_once | FEHLT | Deduplizierung/Sortierung von `zone_names` (Befehle und Absenkung gleicher Zone) im Adapter ungeprüft. Typ: Test |



### tests/test_device_views.py (8)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_device_page_needs_device_read | ERSETZT | test_ui_api_devices.py::endpoints_without_rights_are_403 (GET /ui-api/devices ohne Recht 403 forbidden) |

| an_empty_device_page_explains_the_normal_case | ENTFÄLLT | Erklärtext des Leerzustands (Markup/Text); die Regel steckt in keiner Aussage, SurveyList zeigt den Text nicht mehr |

| the_device_page_shows_signs_of_life_capability_and_zone | FEHLT | model, is_group, capabilities-Labels, battery_percent, radio_quality im /ui-api/devices nicht asserted (nur Name, Zonen, Integration, Befund disabled); Batterie-Rundung nur im Client (SurveyList.svelte:72, ohne Test). Typ: Test |

| a_meross_socket_recently_confirmed_online_is_not_flagged_as_never_reporting | FEHLT | DB-Logik in build_device_survey (Meross: last_seen_at statt device_health) ungeprüft; Dom test_device_survey.py prüft nur `findings()` mit last_heard_kind. Typ: Test |

| a_meross_socket_never_reconciled_says_so_without_claiming_a_report | FEHLT | Wie oben: Befundtext "wurde noch nie als online abgeglichen" nur auf Domänenebene (test_device_survey.py::a_never_reconciled_meross_device_is_not_told_it_never_reported), Integration Meross -> last_heard_kind in build_device_survey ungeprüft. Typ: Test |

| the_start_page_shows_zone_state_without_a_zero_temperature | GEÄNDERT | Kein Runden auf eine Stelle mehr (frontend rooms/decimal.ts formatTemperature, rooms-decimal.test.ts); fehlender Wert gedeckt durch test_ui_api_rooms.py::list_zone_without_state_or_decision |

| the_device_page_puts_conspicuous_devices_on_top | ERSETZT | test_ui_api_devices.py::device_overview_lists_findings_and_zones (Reihenfolge nach severity, Befund) plus Dom test_device_survey.py::disabled_offline_silent_battery_and_radio_are_each_detected_individually und ::severity_ranks_failure_before_early_warning |

| battery_and_radio_appear_as_a_number_not_as_a_chip | FEHLT | WITHOUT_CHIP/`quiet_capabilities` (Batterie-Chip unterdrückt, Gerät nicht "ohne Fähigkeiten") nirgends getestet; SurveyList zeigt "keine Fähigkeiten gemeldet" nicht. Typ: Test |



### tests/test_emergency_display_http.py (4)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| start_page_shows_the_ersatzquelle_banner | FEHLT | Banner-Text Dom getestet (test_emergency_display.py::ersatzquelle_banner_names_the_device_without_jargon), aber `emergency` in /ui-api/rooms nur als None geprüft; Verdrahtung mit echtem ZoneSensorFailureState fehlt. Typ: Test |

| tenant_page_shows_the_ersatzquelle_banner | FEHLT | Wie oben, zusätzlich Mieterprofil (tenant_client) mit gefülltem `emergency` ungeprüft. Typ: Test |

| kiosk_dashboard_shows_the_ersatzquelle_banner | FEHLT | test_kiosk_api.py::a_failing_sensor_and_an_emergency_banner_are_reported mockt zone_emergency_view (nur Durchreichung "Notbetrieb aktiv"); Ersatzquelle-Text mit echtem Zustand ungeprüft. Typ: Test |

| tenant_top_banner_shows_notbetrieb_instead_of_the_calm_message | FEHLT | Beruhigungstext "Heizung läuft normal" existiert nicht mehr; Entsprechung ist die Kachel "issues" (tiles.ts zählt `emergency` mit, Hinweis "Keine Auffälligkeiten"), dafür kein Test (dashboard-misc.test.ts setzt emergency nur null). Typ: Test |

# Teil C: Ersatzprüfung der Alt-UI-Tests



Kurzformen: UA = tests/test_ui_api_*.py, KA = tests/test_kiosk_api.py, FE = frontend/tests.



### tests/test_glossary_views.py (18)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_boosted_navigation_returns_the_whole_page | ENTFÄLLT | HTMX-Boost (HX-Boosted) der Alt-Seiten; die SPA hat keine Teilseiten-Antworten. |

| a_help_icon_is_an_accessible_link_that_keeps_unsaved_form_input | ENTFÄLLT | Markup des Alt-Hilfesymbols (a.tc-help, target=_blank, aria-label). Die SPA nutzt Popover (HelpTip), die die Seite nicht verlassen. |

| a_signed_in_user_without_any_permission_can_read_the_glossary | ERSETZT | test_ui_api_zones.py::test_glossary_is_the_domain_glossary_for_every_login (client_als([]) und Mieterprofil, 200); 401 ohne Sitzung dort in der 401-Liste. |

| every_entry_carries_the_text_the_search_looks_in | ERSETZT | test_glossary.py::test_search_text_contains_term_synonyms_and_texts_in_lower_case; FE zones-logic.test.ts filterGlossary (Begriff, Synonym, Kurzfassung, Erklärung). |

| help_icons_carry_the_ingress_prefix | ENTFÄLLT | Präfix in Alt-HTML-Links; die SPA bildet Pfade über appHref(prefix). |

| the_current_page_is_marked_in_the_navigation | ENTFÄLLT | aria-current in der Alt-Seitenleiste. |

| the_glossary_is_alphabetical_in_the_page | ERSETZT | test_glossary.py::test_entries_are_sorted_the_german_way_and_umlauts_count_as_their_base_letter; API-Test vergleicht entries[0] und Anzahl mit der Domäne. |

| the_glossary_link_in_the_navigation_is_there_even_without_any_permission | ENTFÄLLT | Link-Markup der Alt-Navigation; die Regel "kein Recht nötig" gilt für /ui-api/glossary (siehe Zeile 3). |

| the_glossary_lists_every_entry_with_its_texts | ERSETZT | test_ui_api_zones.py::test_glossary_is_the_domain_glossary_for_every_login (alle Felder des ersten Eintrags, Anzahl, Buchstaben, Bereiche). Nur Eintrag 0 wird feldweise verglichen. |

| the_jump_marks_cover_a_to_z_and_link_only_to_letters_that_exist | ERSETZT | test_ui_api_zones.py::test_glossary_is_the_domain_glossary_for_every_login (letters == Domäne). Darstellung A-Z/Anker ist FE-Markup. |

| every_see_also_link_on_the_page_hits_an_entry_on_the_page | ERSETZT | test_glossary.py::test_see_also_must_point_at_an_existing_entry und test_the_shipped_glossary_loads_and_is_cached (Validierung beim Laden). |

| the_search_field_exists_but_stays_hidden_until_the_script_runs | ENTFÄLLT | No-JS-Rückfall des Alt-Suchfelds; die SPA setzt JavaScript voraus. |

| the_operation_page_offers_help_for_the_dry_run | FEHLT | Hilfe zu Trockenlauf existiert als Popover (frontend/src/lib/operations/glossary.ts, OpsHelp), kein Test prüft, dass die Betriebsseite sie trägt. Typ: Test |

| the_plant_view_links_to_the_glossary_in_navigation_and_footer | ENTFÄLLT | Link-Markup in Seitenleiste und Fußzeile; die SPA hat feste Hilfe-Verknüpfung in Shell.svelte. |

| the_settings_page_offers_help_for_the_emergency_profile_and_the_curve | FEHLT | Popover "Notsollwert", "Außentemperaturkurve" existieren (operations/glossary.ts), aber ohne Test, dass die Regelvorgaben-Seite sie anbietet. Typ: Test |

| the_tenant_help_page_points_to_the_glossary | ENTFÄLLT | Link-Markup der Mieter-Hilfeseite (AccountView/HelpView verlinken auf /app/hilfe). |

| the_tenant_view_gets_the_glossary_in_its_own_shell | ENTFÄLLT | Seitenhülle (tc-tbottomnav/tc-sidenav); Mieter erhalten /ui-api/glossary (Zeile 3, ERSETZT). |

| the_zone_parameter_page_offers_help_for_its_hard_terms | FEHLT | Hilfe zu Hysterese, Mindestzeiten, Sonnenabsenkung, PI-Regler, Notbetrieb existiert als eigene Popover-Texte (operations/glossary.ts), kein Test; zudem entfällt die Prüfung, dass Hilfeverweise auf vorhandene Glossareinträge zeigen (Popover sind nicht verlinkt). Typ: Test |



### tests/test_homebridge_interface.py (7)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| broker_placeholder_hint_when_mqtt_host_is_not_configured | ERSETZT | test_homebridge_interface.py::test_zone_configs_placeholder_broker_when_none_is_configured (Domäne) und UA test_ui_api_operations.py::test_interfaces_without_a_webhook_show_the_placeholder_unchanged (homebridge_broker_configured False). |

| global_zone_read_shows_every_zone | FEHLT | API-Tests prüfen homebridge_zones nur mit einer Zone (never_return_a_secret) oder zonenbezogenem Recht; anlagenweites zone.read mit zwei Zonen fehlt. Typ: Test |

| no_zone_visible_without_zone_read | FEHLT | setting.manage ohne jedes zone.read: homebridge_zones muss leer sein und keinen Zonennamen preisgeben. test_interfaces_name_only_visible_zones_in_the_homebridge_part setzt zone.read(zone) voraus. Typ: Test |

| only_the_zone_read_scoped_zone_is_shown | ERSETZT | test_ui_api_operations.py::test_interfaces_name_only_visible_zones_in_the_homebridge_part (setting.manage + zone.read(zone), nur diese Zone, "Fremd" nicht im Text). |

| password_field_is_a_placeholder_not_thermoctls_own | ERSETZT | test_homebridge_interface.py::test_zone_configs_never_carry_a_real_broker_credential (Benutzer homebridge, Secret nicht enthalten) und UA test_interfaces_never_return_a_secret (config_json ohne "geheim"). |

| the_page_names_the_zone_and_carries_placeholder_credentials | ERSETZT | test_homebridge_interface.py::test_zone_configs_use_the_zones_display_name_and_id_in_the_topic (Domäne, zone_name) plus UA zone_id. Verweise auf docs/*.md sind Markup (entfällt). |

| the_rendered_topics_match_publication_py | FEHLT | Der Wächter "alle sechs angezeigten Topics = publication.py" fehlt: Domäne prüft nur getCurrentTemperature (zone_configs_use_the_configured_mqtt_prefix), test_docs_current prüft nur docs/homebridge.md. Typ: Test |



### tests/test_ingress_prefix.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| htmx_and_form_attributes_carry_the_prefix | ENTFÄLLT | hx-/action-Attribute der Alt-Formulare (/modes/new); in der SPA gibt es sie nicht. |

| rendered_page_carries_the_prefix_on_every_local_link | ERSETZT | test_spa_views.py::test_shell_with_ingress_prefix (Meta thermoctl-prefix und Skript-Pfad mit Präfix). Das Stylesheet-href mit Präfix wird nur ohne Präfix geprüft. |

| rendered_page_direct_uses_bare_links_even_with_ingress_configured | FEHLT | Direktzugriff auf einen Prozess mit konfiguriertem Ingress muss die SPA-Hülle (/app/) ohne Präfix liefern; client_direct_with_ingress_configured wird nur hier benutzt, test_spa_views kennt nur "ohne Konfiguration". Typ: Test |

| rendered_page_with_forged_header_uses_bare_links | ERSETZT | test_spa_views.py::test_foreign_ingress_header_is_not_trusted (content="" und kein hassio_ingress). |

| rendered_page_without_prefix_uses_bare_local_links | ERSETZT | test_spa_views.py::test_shell_without_prefix. |



### tests/test_kiosk.py (19)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_kiosk_cookie_reaches_no_administration_page | FEHLT | Kiosk-Cookie gegen Verwaltung: ui-api/access (test_kiosk_cookie_never_reaches_the_ui_api) und rooms/REST (KA) sind geprüft, nicht aber /ui-api/devices, audit, settings, interfaces, control, schedule. Typ: Test |

| a_previously_chosen_ansicht_survives_a_visit_without_the_query_string | GEÄNDERT | Wahl liegt jetzt im Browser (localStorage), nicht im Server-Cookie (frontend/src/lib/kiosk/view.ts Kopfkommentar); Test: FE kiosk-view.test.ts (initialView('', 'tafel') bleibt tafel). |

| a_setpoint_beyond_the_domain_limit_shows_up_as_an_error | ERSETZT | KA test_the_domain_rejection_is_422_and_changes_nothing (422 rejected, "zwischen", keine Änderung); FE kiosk-logic.test.ts "Ablehnung der Regelung zeigt deren Grund". |

| an_explicit_ansicht_is_reflected_and_written_to_its_own_cookie | GEÄNDERT | Wie oben: Speicherung im localStorage statt eigenem Cookie; FE kiosk-view.test.ts "Adresse schlägt gemerkte Wahl und wird gemerkt" (remember: true). |

| an_ordinary_api_token_does_not_work_as_a_kiosk_cookie | ERSETZT | KA test_an_ordinary_api_token_in_the_kiosk_cookie_is_refused (401 kiosk_token_invalid). |

| an_unknown_ansicht_value_falls_back_to_auto_instead_of_failing | ERSETZT | FE kiosk-view.test.ts "unbekannte Werte werden auto, nie ein Fehler" und initialView('?ansicht=unsinn'). |

| ansicht_defaults_to_auto_with_neither_query_nor_cookie | ERSETZT | FE kiosk-view.test.ts initialView('?x=1', null) liefert auto ohne Merken. |

| boosting_a_zone_without_a_schedule_shows_up_as_an_error | ERSETZT | KA test_boost_without_a_schedule_is_422 (rejected, "Zeitplan", kein Override). |

| the_buttons_on_the_dashboard_work_the_way_a_browser_sends_them | ERSETZT | FE kiosk-client.test.ts "Schreiben: CSRF aus dem Kiosk-Cookie im Header" (Client sendet Token selbst) und KA test_a_write_without_the_csrf_header_is_403_json. |

| the_cancel_button_appears_once_a_boost_is_running | ERSETZT | KA test_boost_creates_a_kiosk_override_and_cancel_ends_it (actions.cancel_override nach Boost True) und test_a_running_override_is_reported_and_cancellable. |

| the_dashboard_carries_an_agpl_source_link_that_stays_off_the_tablet | FEHLT | AGPL-§13-Quelltextlink (github-Adresse, target=_blank rel=noopener) fehlt in der Svelte-Kioskansicht, in der gesamten SPA gibt es keinen Lizenz-/Quelltexthinweis. Typ: Funktion |

| the_dashboard_shows_a_shadow_decision_once_one_exists | ERSETZT | KA test_state_lists_only_the_rooms_in_scope_with_their_actions (ShadowDecision ergibt would_heat True). Beschriftung "Heizanforderung" ist FE-Darstellung. |

| the_dashboard_shows_only_the_assigned_zone | ERSETZT | KA test_state_lists_only_the_rooms_in_scope_with_their_actions (nur zugewiesener Raum, Name der anderen nicht im Text). |

| the_dashboard_without_any_cookie_shows_the_invalid_page | ERSETZT | KA test_no_cookie_is_401_everywhere (401, code kiosk_token_invalid, no-store). |

| the_entry_link_sets_a_cookie_and_redirects_without_the_token_in_the_url | ERSETZT | test_legacy_redirects.py::test_kiosk_redemption_sets_the_cookie_and_goes_to_the_spa_kiosk (303 nach /app/kiosk, Cookie gesetzt, Token nicht in Location). |

| the_kiosk_keeps_its_own_fixed_refresh | FEHLT | Fester 20-s-Takt (POLL_INTERVAL_MS in kiosk/logic.ts, Standard im Controller) wird von keinem Test festgehalten. Typ: Test |

| the_rendered_kiosk_clock_uses_the_configured_timezone | FEHLT | Uhr in Anlagen-Zeitzone: KA prüft timezone nur mit Standardwert Europe/Berlin, FE clockText nur Berlin im Oktober; abweichende konfigurierte Zone und Sommer-/Winterzeit-Fälle fehlen. Typ: Test |

| the_rendered_kiosk_token_expiry_uses_the_configured_timezone | FEHLT | Ablauf in KioskPage.svelte nutzt formatDateTime (Browser-Zeitzone); ui_api_access liefert keine Anlagen-Zeitzone, kein Test. Typ: Funktion |

| the_ui_profile_of_the_issuer_does_not_reach_the_kiosk | FEHLT | Kiosk-Token eines Ausstellers mit Mieterprofil muss /kiosk-api/state weiter bedienen (kiosk_api prüft kein Profil); kein Test in test_kiosk_api.py. Typ: Test |



### tests/test_mapping_map.py (3)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| page_answers_lists_connections_and_is_visible_zone_filtered | ERSETZT | test_ui_api_devices.py::test_map_is_a_real_model_with_ports_directions_and_reasons (Richtung, Linie temperature_source), ::test_map_marks_zones_without_manage_as_read_only und ::test_map_hides_the_names_of_invisible_zones_for_exclusive_actuators (fremde Zone/Name fehlt). Tooltip-Texte: test_mapping_map.py::test_graph_gives_every_node_its_outputs_and_inputs. |

| page_marks_technical_ports_and_stored_zone_ports | ERSETZT | test_mapping_map.py::test_graph_keeps_technical_values_apart_and_unconnectable (technical, nicht verbindbar). data-stored ist reines Markup (node_kind zone) und entfällt. |

| page_needs_a_right_and_is_closed_to_tenants_and_anonymous | ERSETZT | UA devices: test_every_endpoint_without_session_is_401, test_endpoints_without_rights_are_403 (/devices/map, forbidden), test_tenant_profile_is_refused_everywhere (wrong_profile). |



### tests/test_mapping_map_multi.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_one_sensor_is_the_temperature_source_of_two_zones_and_both_zones_read_it | FEHLT | Ein Fühler als Messquelle zweier Zonen über /ui-api/devices/map/connect, zwei Linien vom selben Ausgang und Messwert für beide Zonen in advance_zone_state (ZoneState); kein Test deckt das, nur das Lösen einer Linie (a_the_sensor_stays_...). Typ: Test |

| f_device_to_device_fan_out_is_judged_fit_but_never_stored | FEHLT | Ein Ausgang auf mehrere Geräteeingänge ist laut can_connect zulässig und steht nicht in rejections, "single"-Flag des Geräteeingangs und Meldung "nimmt nur eine Verbindung an"; nur der 422 "nicht gespeichert" ist ersetzt (test_device_to_device_is_judged_...). Typ: Test |



### tests/test_mapping_map_valve.py (1)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| page_carries_the_legend_and_the_tooltip | ERSETZT | test_mapping_map_valve.py::test_the_room_temperature_tooltip_explains_where_the_value_comes_from (Tooltip-Text) und UA test_map_is_a_real_model_... (valve_modes, valve_channel). Legende (data-map-valve-note) ist Markup; Durchreichung von tooltip/label/description in der API nicht gesondert geprüft. |



### tests/test_mode_views.py (10)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_builtin_mode_cannot_be_deleted_and_says_why | FEHLT | Sperre für eingebaute Modi ("weil die Anwendung sie benötigt", is_builtin) über DELETE /ui-api/modes oder delete_lock; is_builtin kommt in UA nur als False vor. Typ: Test |

| a_mode_in_use_cannot_be_deleted | ERSETZT | test_ui_api_schedule.py::test_modes_crud_audit_and_locks (delete_lock enthält "Zeitpläne", DELETE 409 mode_locked, Modus bleibt). |

| fremde_zone_ergibt_404 | ERSETZT | test_ui_api_rooms.py::test_writes_on_foreign_zone_are_404 (setpoint.write nur für andere Zone, PUT .../modes/{id}/setpoint 404 not_found) und ::test_unknown_and_invisible_zone_are_identical_404 (gleiches 404 wie unbekannt). |

| modusliste_braucht_mode_manage | ERSETZT | test_ui_api_schedule.py::test_missing_permission_is_403_and_changes_nothing (GET /ui-api/modes ohne mode.manage 403 forbidden), test_wrong_profile_is_403, test_modes_crud_audit_and_locks (Liste mit Recht). |

| the_delete_form_for_an_unused_mode | ENTFÄLLT | Bestätigungsseite "wirklich gelöscht"; die Löschbarkeit prüft UA über delete_lock None und erfolgreiches DELETE (test_modes_crud_audit_and_locks). |

| the_edit_mode_form_shows_the_values | ENTFÄLLT | Formularvorbelegung (value="nacht"); Werte liefert GET /ui-api/modes (test_modes_crud_audit_and_locks). |

| the_frost_protection_mode_cannot_be_deleted_and_says_why | ERSETZT | test_ui_api_schedule.py::test_modes_crud_audit_and_locks (delete_lock von frost-wohnzimmer enthält "Frostschutzmodus"). Vollständiger Satz wird nur auf Stichwort geprüft. |

| the_new_mode_form_is_shown | ENTFÄLLT | Anzeige des leeren Formulars (Label "Technischer Code"). |

| the_setpoint_form_shows_every_mode_and_the_help_text | ENTFÄLLT | Formularmarkup und Hilfetext; Leeren löscht den Sollwert: test_ui_api_rooms.py::test_set_mode_setpoint_and_delete (modes je Code im Raumdetail). |

| unbekannter_modus_ergibt_404 | ERSETZT | test_ui_api_schedule.py::test_modes_crud_audit_and_locks (PUT/DELETE auf /ui-api/modes/999999 404 mode_not_found). |



### tests/test_navigation.py (4)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| no_section_heading_remains_when_none_of_its_entries_is_available | ERSETZT | FE navigation.test.ts "Sichtbarkeit kommt nur aus der Server-Navigation" (buildVisibleAreas([]) nur Übersicht, keine leeren Bereiche). |

| user_with_all_permissions_sees_every_navigation_item | FEHLT | Kein Test, dass ein Administrator mit allen Rechten in /ui-api/bootstrap (visible_navigation) jeden ADMIN-Eintrag aus NAVIGATION_ITEMS erhält und keine Mietereinträge; geprüft ist nur /zones und /users. Typ: Test |

| user_without_user_management_neither_sees_link_nor_gets_access | ERSETZT | test_ui_api.py::test_bootstrap_navigation_follows_grants (/users fehlt ohne user.manage) und test_ui_api_access.py::test_admin_endpoints_without_rights_are_403_and_account_stays_open (user.manage-Endpunkte 403). |

| zone_scoped_permission_shows_only_zone_filtering_destinations | FEHLT | Zonenbezogenes zone.read zeigt /zones aber nicht /control, device.read(zone) zeigt /controllers aber nicht /devices (scope any_zone gegen anlagenweit) in Bootstrap-Navigation; nur Mieter-Verlauf ist domänenseitig geprüft. Typ: Test |



### tests/test_notification_settings.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_row_without_the_columns_yet_still_renders_with_the_documented_default | ENTFÄLLT | Übergangsabsicherung für fehlende Spalten vor der Migration; die Spalten gibt es. "Noch nie versucht": test_ui_api_operations.py::test_settings_show_every_group_with_limits (last_attempt_at None). |

| never_shown_when_notify_last_attempt_at_is_absent | ERSETZT | test_ui_api_operations.py::test_settings_show_every_group_with_limits (notifications.last_attempt_at is None). |

| the_settings_page_shows_the_three_notification_toggles | ERSETZT | test_ui_api_operations.py::test_notification_channels_are_saved_with_audit (sensor_faults, bridge_faults, command_failures in der Antwort). |

| the_settings_page_shows_the_window_alarm_toggle | ERSETZT | test_ui_api_operations.py::test_notification_channels_are_saved_with_audit (window_alarm in der Antwort). |

| without_a_webhook_the_button_is_not_offered | ERSETZT | test_ui_api_operations.py::test_settings_show_every_group_with_limits (webhook_configured False) und ::test_test_notification_without_webhook_sends_nothing (409 no_webhook). Button-Darstellung ist FE. |



### tests/test_passkey.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_login_page_offers_passkeys_only_when_set_up | ERSETZT | test_ui_api_auth.py::test_login_options_without_passkey_configuration und ::test_login_options_with_passkey_configuration. |

| the_passkey_page_explains_a_missing_setup | ERSETZT | test_ui_api_access.py::test_passkeys_list_and_remove_own_only (available False) und ::test_account_reports_passkey_availability (True). Der Text nennt THERMOCTL_PASSKEY_RP_ID nicht mehr (PasskeysCard.svelte), Darstellung. |



### tests/test_relay_wear_views.py (4)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| relay_wear_empty_period_is_explicit | GEÄNDERT | Unbekannter Zeitraum liefert jetzt 422 validation statt stillem Rückfall (ui_api_reports._period_start "Ungültiger Zeitraum.", Test test_relay_wear_access); leere Liste devices == [] dort ebenfalls geprüft. Texte und Zeitraum-Links sind Markup. |

| relay_wear_highlights_actionable_levels_and_the_visible_assumption | FEHLT | API-Test prüft nur ein Gerät in Stufe normal; Reihenfolge nach Jahreshochrechnung, Stufen warning/danger über /ui-api/relay-wear und Durchreichen von Zeitzone sowie Lebensdauer an relay_operations fehlen (Stufen selbst: test_statistics.py). Typ: Test |

| relay_wear_reflects_a_changed_assumption | FEHLT | Einstellung assumed_relay_lifetime_operations (hier 100000) wird in /ui-api/relay-wear nicht geprüft, nur Standard 500000 und Fallback ohne Setting. Typ: Test |

| relay_wear_requires_audit_read_and_a_visible_zone | FEHLT | Recht audit.read anlagenweit ist ersetzt (test_relay_wear_access: nur zone.read 403, zonenbezogenes audit.read 403). Es fehlt der Fall audit.read ohne sichtbare Zone (200, leere Liste). Typ: Test |

## Teil D: Alt-Test-Abdeckung (Schluessel schedule_views, security_review, smoke_test, tenant_history, tenant_views, ui_profile)



Kurzzeichen: UA = tests/test_ui_api_<x>.py, Front = frontend/tests/<x>.test.ts. Testnamen ohne Präfix `test_`.



### tests/test_schedule_views.py (15)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| an_unknown_schedule_point_yields_404 | ERSETZT | UA_schedule::delete_point (zweites Löschen 404 point_not_found) und ::point_of_another_zone_is_not_addressable (999999 -> 404) |

| carried_segments_are_visibly_marked_as_not_draggable | ERSETZT | UA_schedule::show_schedule_delivers_points_segments_modes_version (Rest vom Vortag hat point_id None); Front schedule-geometry::"Blöcke sind die Läufe zwischen Schaltpunkten..." |

| copy_controls_are_natively_operable_without_javascript | ENTFÄLLT | Nur details/summary-Markup ohne JS; Funktion copy-day (alle Tage, Undo) in UA_schedule::copy_day_scopes_noop_and_tenant |

| paint_copy_and_undo_routes_use_normal_csrf_protected_forms | ERSETZT | UA_schedule::paint_changes_plan_audits_and_undo_restores (Malen+Undo) und ::writes_without_csrf_are_403_and_change_nothing (paint, undo, copy-day parametrisiert) |

| paint_mode_keeps_draggable_bars_movable_and_paints_everywhere_else | ENTFÄLLT | Prüft schedule.js und CSS-Cursor-Regeln; keine Fachregel |

| painting_until_midnight_is_selectable_without_javascript | ERSETZT | Regel "Malen bis 24:00" (end 1440, Rest geht auf Folgetag): Domäne schedule_views::painting_to_end_of_sunday_restores_monday_mode; Front schedule-inspector (parseClock 00:00 -> 1440). Kein /ui-api-Test mit end_minute=1440, Domäne deckt es |

| permissions_and_foreign_zones_yield_404 | GEÄNDERT | Fremde Zone 404: UA_schedule::hidden_and_unknown_zone_are_identical_404. Leser (nur zone.read) beim Schreiben jetzt 403 statt 404: Modul-Doku ui_api_schedule ("erst danach kommt die Aktion (403)"), UA_schedule::tenant_without_permission_is_403_forbidden_not_wrong_profile |

| rendered_palette_defaults_to_painting_and_explains_move_only_gestures | ENTFÄLLT | Vorbelegung eines HTML-Radios und Hilfetext; Bedienung des Svelte-Editors |

| the_forecast_is_visible_with_only_zone_read | FEHLT | Kein Test, dass GET /ui-api/schedule/{z}/preview mit nur zone.read (ohne schedule.manage) 200 liefert; alle preview-Tests nutzen PLAN. Funktion vorhanden (show_preview nur _visible). Typ: Test |

| the_forecast_shows_a_fixed_temperature_override_with_no_mode_name | ERSETZT | UA_schedule::preview_shows_override_without_mode |

| the_forecast_shows_the_running_bar_and_its_temperature | ERSETZT | UA_schedule::preview_over_midnight_and_week_ring (laufender Abschnitt, temperature_c "21.0", warmth) |

| the_rendered_mode_form_changes_the_point_without_javascript | ENTFÄLLT | Formular/Feldnamen ohne JS; Funktion in UA_schedule::change_point_mode |

| the_rendered_paint_form_works_without_javascript | ENTFÄLLT | Formular/Feldnamen ohne JS; Malen in UA_schedule::paint_changes_plan_audits_and_undo_restores |

| the_week_view_carries_the_point_identifier_for_dragging | ERSETZT | UA_schedule::show_schedule_delivers_points_segments_modes_version (points[].id, segments[].point_id) |

| the_week_view_shows_the_wraparound_from_sunday_to_monday | ERSETZT | UA_schedule::show_schedule_delivers_points_segments_modes_version (Sonntag 22:00 gilt bis Montag 07:00) und ::preview_over_midnight_and_week_ring |



### tests/test_security_review_2026_09_02.py (2)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| bediengeraeteseite_verlangt_geraeteleserecht | FEHLT | /ui-api/controllers verlangt device.read (_need_zones), aber kein Test mit einem Konto, das z. B. nur zone.read (global) hat: UA_devices::endpoints_without_rights_are_403 nutzt client_als([]), würde auch bei Prüfung auf zone.read bestehen. Typ: Test |

| bediengeraeteseite_verraet_keine_fremden_geraetenamen | ERSETZT | UA_devices::a_room_a_reader_finds_no_device_of_room_b_anywhere (GET /ui-api/controllers, device.read nur Raum A, Namen aus Raum B fehlen) und ::controllers_hide_zones_the_viewer_cannot_read |



### tests/test_smoke_test.py (4)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| boosted_navigation_returns_the_full_page | ENTFÄLLT | HTMX-hx-boost und Seitenhülle (tc-topbar); SPA-Shell: test_spa_views |

| the_new_zone_form_posts_to_a_real_endpoint | ENTFÄLLT | Form-action im HTML-Template; Anlegen über JSON in UA_zones::create_writes_the_zone_and_the_audit_entry, Client-Pfade in Front zones-client |

| the_rendered_form_carries_the_field_names_the_view_reads | ENTFÄLLT | Drift Template-Feldnamen/View; JSON-Body wird vom Server typisiert geprüft, Client-Bodys in Front schedule-client festgenagelt |

| the_thermostat_buttons_send_values_their_view_accepts | ENTFÄLLT | HTML-Knopfwerte; Server Literal["up","down"], Client typisiert (RoomView step), UA_rooms::step_setpoint_moves_half_a_degree |



### tests/test_tenant_history.py (28)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_foreign_room_answers_exactly_like_one_that_does_not_exist | ERSETZT | UA_reports::history_foreign_and_unknown_zone_are_the_same_404 (/history und /history.csv, gleicher Body). Ungültige Parameter siehe tile_of_a_foreign_room |

| a_global_and_a_room_grant_together_show_each_room_once | FEHLT | Kombination globales + zonengebundenes zone.read (jeder Raum einmal, kein Duplikat) in visible_zones/Raumliste ungetestet. Typ: Test |

| a_global_read_right_shows_every_room_and_that_is_intended | ERSETZT | test_authz::visible_zones_returns_all_zones_with_an_installation_wide_permission; UA_reports::history_tenant_profile_may_read_own_zone |

| a_global_right_for_something_else_adds_no_room | ERSETZT | test_authz::visible_zones_ignores_grants_for_other_permission_codes (zonengebunden anderes Recht; globales setpoint.write nicht eigens, Prüfung nur auf denselben Code) |

| a_page_of_a_plant_that_is_not_armed_says_that_it_shows_decisions | FEHLT | Daten: UA_reports::heating_time_counts_seconds_and_marks_dry_run (control_armed). Hinweistext in HeatingTime.svelte (Trockenlauf) nicht per Vitest geprüft; HistoryChart zeigt keinen Hinweis "hätte, keine Aufzeichnung dessen". Typ: Test (Chart-Hinweis ggf. Funktion) |

| a_period_window_changes_the_tile | GEÄNDERT | Zeitraum kommt als from/to vom Client (kein period-Parameter); Fenster/Auflösung: UA_reports::history_31_days_is_allowed_with_bucket_cap, ::history_input_limits |

| a_right_for_a_different_permission_does_not_open_the_tile | ERSETZT | UA_reports::history_without_any_read_right_is_404_not_an_existence_hint plus test_authz::visible_zones_ignores_grants_for_other_permission_codes |

| a_tenant_without_any_room_gets_an_empty_page_not_an_error | FEHLT | Kein Test für Konto ohne sichtbare Räume bei /ui-api/bootstrap (zones leer), /ui-api/rooms; Svelte zeigt Text bei zones.length==0 (HistoryPage). /ui-api/heating-time antwortet ohne Recht 403 (UA_reports::heating_time_access). Typ: Test |

| an_administrator_does_not_reach_the_tenant_history | GEÄNDERT | /ui-api/history ohne Profilwächter, beide Profile (Modul-Doku ui_api_reports: "zone.read je Zone"); UA_reports::history_tenant_profile_may_read_own_zone |

| an_unknown_period_is_refused_like_in_the_plant_view | ERSETZT | UA_reports::history_input_limits (ungültiges from/to/resolution -> 422 validation) und ::heating_time_access (period=5 -> 422) |

| an_unparseable_room_id_is_not_found_like_everywhere_else_in_the_tenant_view | GEÄNDERT | zone_id ist typisierter Parameter: 422 validation statt 404 (UA_reports::history_input_limits, zone_id=x); kein Existenzhinweis |

| each_tenant_sees_tiles_of_exactly_their_own_rooms | ERSETZT | UA_rooms::list_shows_only_visible_zones und test_ui_api::bootstrap_lists_only_visible_zones (fremder Name nicht im Body); UA_reports::heating_time_access |

| every_address_carries_the_ingress_prefix | ENTFÄLLT | HTML-Adressen mit Ingress-Präfix; SPA-Shell: test_spa_views::shell_with_ingress_prefix |

| rooms_of_two_access_groups_add_up_and_nothing_more | ERSETZT | test_authz::permissions_from_multiple_groups_are_combined (has_permission; Raumliste selbst nicht eigens) |

| the_chosen_period_is_marked_and_handed_to_every_tile | ENTFÄLLT | Markup (tc-segment, hx-get-URLs) |

| the_heating_time_page_points_to_the_history | ENTFÄLLT | Verweistext im Template |

| the_history_page_is_a_page_of_the_tenant_view | ENTFÄLLT | Seitenhülle (Navi, h1, title, script) |

| the_hover_data_of_a_tile_belongs_to_its_own_room_only | FEHLT | Kein Test mit Messdaten zweier Räume, dass /ui-api/history nur Werte der angefragten Zone liefert (alle History-Tests nutzen eine Zone). Typ: Test |

| the_notice_follows_the_same_source_as_the_heating_time_page | ENTFÄLLT | Eine Quelle (control_armed in HeatingTime auf derselben Seite); Wert: UA_reports::heating_time_counts_seconds_and_marks_dry_run |

| the_page_explains_why_it_can_stay_cooler_than_the_plan | FEHLT | Erklärtexte der Mieter-Verlaufsseite (Sonnenabsenkung, Abwesenheit/Urlaub/Aus, "keine Daten") und Glossar-Verweise gibt es in HistoryPage/HistoryChart nicht; nur Reihenbeschreibungen. Typ: Funktion |

| the_plant_history_routes_stay_closed_to_tenants | GEÄNDERT | Verlauf gilt für beide Profile, Recht zone.read statt zone.manage (Modul-Doku ui_api_reports); UA_reports::history_tenant_profile_may_read_own_zone. Relay-Wear/Audit bleiben zu: UA_reports::relay_wear_access |

| the_plant_navigation_does_not_list_the_tenant_history | FEHLT | Kein ungeskippter Test, dass Mieter-Eintrag /temperature-history in der Anlagen-Navigation fehlt (test_ui_profile::a_tenant_sees_no_plant_navigation prüft nur die Mieter-Richtung). Typ: Test |

| the_plant_tile_keeps_its_technical_wording | ENTFÄLLT | Wortlaut der alten HTML-Kachel (Ist/Wirksamer Soll); eine gemeinsame Svelte-Seite |

| the_room_id_must_be_plain_ascii_digits_and_nothing_else | ENTFÄLLT | Adressform /temperature-history/{id}/tile entfällt; Zonenfilter (visible_zones) bleibt dicht |

| the_tenant_navigation_offers_the_history_next_to_the_heating_time | ENTFÄLLT | HTML-Navigation (aria-current, Reihenfolge); Mieter-Pfadmenge: test_ui_profile::a_tenant_sees_no_plant_navigation, Front navigation |

| the_tile_drawing_and_summary_carry_only_the_own_measurements | FEHLT | Wie hover-data: Messwerte anderer Räume dürfen nicht in Buckets/Summen eingehen; ungetestet. Typ: Test |

| the_tile_of_a_foreign_room_is_not_found_even_when_asked_for_directly | FEHLT | Fremde Zone 404 getestet (UA_reports::history_foreign_and_unknown_zone_are_the_same_404), aber nicht: fremde Zone mit ungültigem from/to/resolution muss 404 bleiben (nie 422, sonst Existenzhinweis). Code prüft Sichtbarkeit zuerst. Typ: Test |

| the_tile_speaks_the_language_of_the_dwelling | FEHLT | Mieter-Wortlaut (Gemessen / Zielwert ohne Sonnenabsenkung / Tatsächlicher Zielwert, Erklärung zur Heizanforderung) ist in der Svelte-Seite nicht vorhanden; technische Reihenbezeichnungen für beide Profile. Typ: Funktion |



### tests/test_tenant_views.py (26)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_read_only_room_says_so_instead_of_showing_nothing | FEHLT | Flag can_edit=False getestet (UA_schedule::read_only_and_tenant_views_flags); Text "ansehen, aber nicht ändern" (Inspector.svelte) und Hinweis auf bearbeitbaren Raum nicht geprüft. Typ: Test |

| a_room_taken_away_afterwards_no_longer_appears_in_the_absence_banner | FEHLT | _absence_state listet absence_zones(entry) ungefiltert; ein inzwischen entzogenes zone.read blendet den Raumnamen im laufenden Abwesenheits-Banner nicht aus. Namens-Leck. Typ: Funktion (und Test) |

| a_room_with_fewer_than_two_setpoints_says_what_is_missing | ENTFÄLLT | Sperre gehört zum vereinfachten Tages-Editor (zwei Zeiten), der in /ui-api nicht existiert; Plan wird über Blöcke gemalt |

| a_room_without_a_state_row_is_skipped_by_the_notice | ERSETZT | UA_rooms::list_zone_without_state_or_decision |

| a_room_without_any_switch_time_can_be_set_up_by_the_tenant | FEHLT | Vereinfachtes "Einrichten" (zwei Zeiten, wärmerer Sollwert zuerst) gibt es nicht; Mieter auf leerem Plan nur über Paint/Punkte, dort ungetestet (UA_schedule::empty_plan_has_no_segments nur GET). Typ: Funktion |

| a_running_absence_is_visible_and_can_be_ended_in_one_step | ERSETZT | UA_schedule::absence_lifecycle_for_tenants und ::absence_end_closes_every_running_bracket (Anzeige mit mehreren Räumen nicht eigens) |

| a_running_override_is_shown_and_can_be_ended | ERSETZT | UA_rooms::override_duration_and_next_switch und ::cancel_override |

| a_stale_measurement_is_explained_as_an_effect_not_a_cause | FEHLT | Hinweis "Messwert möglicherweise nicht aktuell / Heizung regelt weiter" nur als Hilfetext (help/explanations.ts); RoomCard zeigt Sensorstatus-Label des Geräts statt neutralem Effekt-Hinweis; Altersschwelle ungetestet. Typ: Funktion |

| a_tenant_without_any_room_gets_a_readable_page_not_an_error | FEHLT | Konto ohne Räume: bootstrap/rooms leer, kein Fehler, nicht getestet (siehe tenant_history). Typ: Test |

| an_administrator_does_not_reach_the_tenant_pages | GEÄNDERT | Zeitplan entscheidet allein schedule.manage je Raum, nicht das Profil (Modul-Doku ui_api_schedule); Heizzeit ohne Profilwächter. Nur Abwesenheit bleibt Mieter-only: UA_schedule::wrong_profile_is_403 |

| an_open_window_is_reported_as_the_effect_it_has | ERSETZT | Front rooms-logic::"roomStatus: Reihenfolge Sensor, Fenster, Aus, Heizanforderung" (Fenster nur ohne Sensorproblem). Wortlaut jetzt "Fenster offen" |

| an_unknown_period_falls_back_instead_of_failing | GEÄNDERT | Unbekannter Zeitraum -> 422 statt Rückfall auf 7 Tage: UA_reports::heating_time_access (period=5 -> 422 validation) |

| an_unparseable_zone_id_is_not_found_either | GEÄNDERT | Typisierte Pfad-/Query-Id: 422 statt 404 (UA_reports::history_input_limits, zone_id=x); kein Existenzhinweis |

| every_link_carries_the_ingress_prefix | ENTFÄLLT | HTML-Verweise; Präfix in test_spa_views::shell_with_ingress_prefix |

| every_period_renders_only_visible_rooms | ERSETZT | UA_reports::heating_time_access (zones nur eigene Zone; fremde Zone 404) |

| no_page_ever_mentions_a_foreign_room | ERSETZT | UA_rooms::list_shows_only_visible_zones, test_ui_api::bootstrap_lists_only_visible_zones, UA_reports::heating_time_access, UA_schedule::hidden_and_unknown_zone_are_identical_404 |

| the_administrator_start_page_stays_the_plant_view | ENTFÄLLT | Startseiten-Markup (tc-sidenav, Wortlaut); Kachel-Trennung: UA_dashboard::a_tenant_never_gets_plant_tiles |

| the_adoption_appears_even_when_seen_from_a_read_only_room | FEHLT | copy-week erlaubt Quelle mit nur zone.read (Ziel schedule.manage), aber show_schedule liefert copy_targets nur wenn can_edit; vom nur lesbaren Raum aus ist die Übernahme nicht anbietbar, und ein Test mit Quelle ohne schedule.manage fehlt. Typ: Funktion und Test |

| the_dry_run_is_spelled_out_as_text | FEHLT | Daten control_armed getestet (UA_reports::heating_time_counts_seconds_and_marks_dry_run); Text "hätte" und Abwesenheit von kWh/Euro/Kosten-Beschriftung in HeatingTime.svelte ungeprüft. Typ: Test |

| the_edit_control_is_a_control_not_a_line_of_text | ENTFÄLLT | CSS-Klasse tc-dayedit, summary-Markup |

| the_forecast_matches_the_domain | ERSETZT | UA_schedule::preview_over_midnight_and_week_ring, ::preview_across_spring_forward, ::preview_across_fall_back |

| the_live_poll_is_quiet_and_uses_the_server_interval | FEHLT | poll_interval_seconds in /ui-api/rooms aus Setting.shadow_interval_seconds nicht getestet; Front rooms-poller prüft nur Timer/Tab-Verhalten. Typ: Test |

| the_next_switch_line_comes_from_the_domain | FEHLT | UA_rooms::detail_has_modes_next_switch_vacation prüft next_switch nur gegen die Menge {21.0,17.0}, nicht Gleichheit mit domain next_switch(). Typ: Test |

| the_schedule_lands_on_a_room_the_tenant_may_actually_edit | FEHLT | ScheduleView wählt zones[0] (erster lesbarer Raum), nicht den ersten bearbeitbaren; bootstrap.zones trägt kein can_edit. Im Betrieb gemeldeter Fehler kann wiederkehren. Typ: Funktion |

| the_schedule_of_a_foreign_room_cannot_be_opened | ERSETZT | UA_schedule::hidden_and_unknown_zone_are_identical_404 und ::tenant_hidden_zone_is_404_identical_to_unknown |

| the_tenant_shell_carries_the_stale_page_handling_and_the_loading_bar | ENTFÄLLT | HX-Stale-Page/Ladebalken (HTMX); veraltete Sitzung als JSON: UA_schedule::writes_without_csrf_are_403_and_change_nothing (stale), Front api::"403 mit stale..." |



### tests/test_ui_profile.py (4)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_tenant_cannot_open_an_admin_page_even_with_the_permission | ERSETZT | Je Bereich wrong_profile trotz Rechten: UA_access::tenant_profile_is_refused_before_the_right_is_checked (users, groups, tokens, kiosk-tokens), UA_devices::tenant_profile_is_refused_everywhere (devices, controllers, device-commands), UA_zones::management_belongs_to_the_plant_view, UA_operations (settings, operation, arming, interfaces), UA_reports (audit, relay-wear, changes), UA_schedule::wrong_profile_is_403 (modes, vacation). Ausnahme /statistics: Heizzeit für beide Profile (GEÄNDERT, Modul-Doku ui_api_reports) |

| the_group_page_offers_the_interface_choice | ERSETZT | UA_access::group_list_gives_areas_zones_and_permissions (ui_profiles == ["admin","tenant"], ui_profile je Gruppe), ::create_and_delete_group, ::ui_profile_switch_is_strict |

| the_guard_does_not_replace_the_permission_check | ERSETZT | UA_access::each_endpoint_demands_exactly_its_own_permission und ::admin_endpoints_without_rights_are_403_and_account_stays_open (Admin-Profil ohne Recht -> 403 forbidden) |

| the_profile_cannot_be_switched_from_the_client | FEHLT | Kein Test, dass Abfrageparameter ui_profile, Kopfzeile X-UI-Profile oder Cookie ui_profile an /ui-api das Profil nicht ändern (Profil kommt aus Gruppe, UA_access::account_profile prüft nur das Ergebnis). Typ: Test |

## Teil E (44 Tests)



Kürzel: UAS = tests/test_ui_api_schedule.py, UAR = tests/test_ui_api_reports.py, UAA = tests/test_ui_api_access.py, UAZ = tests/test_ui_api_zones.py, UAO = tests/test_ui_api_rooms.py.



### tests/test_vacation_views.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| the_page_needs_zone_read | ERSETZT | UAS::test_missing_permission_is_403_and_changes_nothing (Index GET /ui-api/vacation, Recht zone.read, ohne Recht 403 forbidden); 401 ohne Sitzung: UAS::test_every_endpoint_without_session_is_json_401 |

| the_page_shows_no_vacation_by_default | ERSETZT | UAS::test_vacation_lifecycle_across_the_spring_transition (leer: vacation None, can_manage True) |

| the_form_is_hidden_without_the_permission | ERSETZT | UAS::test_vacation_lifecycle_across_the_spring_transition (Leser mit nur zone.read: can_manage False; Formular-Markup entfällt, Regel steckt in can_manage) |

| the_form_is_shown_with_the_permission | ERSETZT | UAS::test_vacation_lifecycle_across_the_spring_transition (can_manage True mit vacation.manage) |

| cancelling_ends_the_running_vacation | FEHLT | Lücke: Audit-Ereignis (object_type vacation, action update) beim Beenden wird in /ui-api nicht geprüft; UAS prüft nur ChangeLog vacation_cancel und cancelled_at. Funktion vorhanden (domain.schedule.cancel_vacation ruft audit.record). Typ: Test |



### tests/test_web_oauth_consent.py (1)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| login_returns_to_consent | ERSETZT | Kette aus tests/test_legacy_redirects.py::test_oauth_consent_without_session_goes_through_the_spa_login (Consent ohne Sitzung -> /app/login?return_to=Consent), tests/test_ui_api_auth.py::test_password_login_returns_only_the_server_checked_target (return_to Consent wird zurückgegeben) und frontend/tests/auth-login.test.ts (requestedReturnTo, replyTarget). Offener-Redirect-Fall: test_password_login_drops_every_unsafe_target |



### tests/test_web_tokens_presets.py (3)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| preset_selects_owned_permissions | ERSETZT | UAA::test_token_list_shows_state_grants_presets_and_never_secrets (presets liefert nur eigene anlagenweite Rechte: read_only=[zone.read], read_write=[zone.read]) und UAA::test_preset_is_limited_to_the_rights_the_issuer_holds |

| token_list_shows_oauth_source_and_client | ERSETZT | UAA::test_revoking_an_oauth_token_blocks_its_refresh_family (Liste: source == oauth, client_name == Anwendung) |

| zone_scoped_permission_can_be_selected_without_widening | FEHLT | Lücke: (a) available[] mit zone_id-Eintrag für ein zonengebundenes Recht wird nicht geprüft (UAA vergleicht nur Codes eines anlagenweiten Besitzers); (b) der gespeicherte ApiTokenPermission.zone_id bei Ausgabe aus gehaltenem Zonenrecht wird nicht geprüft, test_a_token_can_never_carry_more_than_its_issuer prüft nur 201. Die Nicht-Ausweitung selbst ist abgedeckt (UAA::test_a_token_can_never_carry_more_than_its_issuer). Typ: Test |



### tests/test_zone_history_chart.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| partial_route_and_zone_isolation | FEHLT | Anonym 401, fremde und unbekannte Zone gleiches 404, ohne Leserecht 404: UAR::test_history_and_csv_without_session_are_401, test_history_foreign_and_unknown_zone_are_the_same_404, test_history_without_any_read_right_is_404_not_an_existence_hint. Mieter 403 -> GEÄNDERT (UAR::test_history_tenant_profile_may_read_own_zone, Verlauf ist zone.read je Zone, ui_api_reports.py-Docstring). Lücke: ungültige Eingabe auf fremder Zone muss 404 statt 422 liefern (erst Recht, dann Eingabe); Code tut das (_visible_zone vor _moment), kein Test. Typ: Test |

| invalid_periods_are_rejected_without_a_server_error | ERSETZT | UAR::test_history_input_limits (Parameter period ist durch from/to/resolution ersetzt; ungültig -> 422 validation mit Feld, nie 500) und UAR::test_history_31_days_is_allowed_with_bucket_cap |

| page_places_the_chart_above_the_form_with_accessible_summary | ENTFÄLLT | Reihenfolge im HTML, role=img, aria-describedby, hx-get: reines Markup. Summentext (Ist/Sonnenabsenkung Minuten) ist Domäne zone_history_chart.build_chart und dort getestet (nicht übersprungene Tests derselben Datei) |

| the_chart_escapes_the_zone_name | ENTFÄLLT | HTML-Escaping; JSON liefert zone_name unverändert (UAR::test_history_gaps_stay_gaps_and_values_are_exact), Svelte escapt beim Rendern standardmäßig |

| every_layout_draws_inside_its_own_view_box | ENTFÄLLT | Server-SVG-Geometrie (viewBox, x/y/d-Grenzen); das Diagramm wird jetzt im Browser aus JSON gezeichnet |



### tests/test_zone_history_honesty.py (13)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_changing_setback_is_labelled_with_its_range_not_its_maximum | FEHLT | Lücke: Bucket liefert nur solar_setback_k = max(); kein Minimum, die Oberfläche (formatSetback) zeigt bei wechselnder Absenkung im Fach den Höchstwert statt Bereich (history_report._bucket). Typ: Funktion |

| a_setback_shorter_than_a_pixel_gets_a_marker_but_no_stretched_band | FEHLT | Lücke: HistoryChart.svelte zeichnet Solar-/Heiz-/Lückenrechtecke mit Math.max(1, Breite) (also Mindestbreite) plus 4-px-Balken; Regel "kein verbreitertes Band, dafür Marker" ist nicht umgesetzt und nicht getestet (kein Komponententest). Typ: Funktion |

| a_ten_minute_setback_in_seven_days_has_a_thin_band_and_a_marker | FEHLT | Lücke: wie zuvor, dünnes Band samt Dreiecksmarker in der neuen Grafik weder vorhanden noch getestet; nur Datenwert solar_setback_k pro Fach vorhanden. Typ: Funktion |

| compressed_windows_show_the_range_of_each_section_and_say_so | ERSETZT | Daten: UAR::test_history_bucket_mean_min_max_and_setpoint_changes (Mittel, Min, Max je Fach, kein Mittel über Sollwertsprung); Legendentext "Min./Max." in series.ts ohne Regelgehalt |

| drawn_widths_carry_no_minimum_that_would_claim_time | FEHLT | Lücke: Zeichenbreiten ohne Mindestbreite; HistoryChart.svelte setzt Math.max(1, w) bei Lücken, Solar, Heizung und Math.max(1.5, ..) bei Höhe. Weder Regel noch Test. Typ: Funktion |

| legacy_rows_are_marked_unknown_and_never_reported_as_no_setback | ERSETZT | UAR::test_history_gaps_stay_gaps_and_values_are_exact (Altzeile: solar_setback_k None, nicht "0"; Sollwertfehlen bleibt null) und frontend/tests/history-scales.test.ts "formatSetback: unbekannt (null) ist nicht keine (0)" |

| the_curve_legend_says_up_to_and_not_a_fixed_length | ENTFÄLLT | Legendentext; die UI zeigt die Fachbreite aus resolution_seconds (UAR::test_history_gaps_stay_gaps_and_values_are_exact: 60 s) und "Mittel aus N Messungen"; keine Regel außerhalb des Textes |

| the_legend_of_a_mixed_window_has_no_statement_of_none | ERSETZT | Je Fach null (unbekannt) vs "0" (keine): UAR::test_history_gaps_stay_gaps_and_values_are_exact + history-scales.test.ts (formatSetback). Mischung innerhalb eines Fachs (max über nur bekannte Zeilen) nicht eigens getestet, Randfall |

| the_legend_of_the_24_hour_window_names_three_minutes | GEÄNDERT | 24 h löst jetzt in 120 s auf (history_report.resolve_resolution), Beleg UAR::test_resolve_resolution_rules; Legendentext "3 Minuten" entfällt |

| the_legend_of_the_heat_lane_names_a_share_per_section_not_a_duration | ERSETZT | Anteil je Fach als heat_percent: UAR::test_history_bucket_mean_min_max_and_setpoint_changes (== 25); series.ts-Beschreibung "Anteil der Regelentscheidungen" |

| the_legend_shows_the_last_measurement_with_its_time_not_a_bucket_mean | GEÄNDERT | Anzeige zeigt "Letzter Wert im Ausschnitt" mit Zeit (whenOf) und kennzeichnet Fachmittel ausdrücklich ("Mittel aus N Messungen", Min./Max.), HistoryChart.svelte; Auswahl des letzten Fachs mit Messwert: history-scales.test.ts "lastIndexInView" |

| the_spike_bar_reaches_every_variant_of_the_page | ENTFÄLLT | SVG-Pfadgeometrie je Variante; neue Grafik zeichnet Min/Max-Band bzw. Linie bei Einzelfach selbst (Daten per UAR getestet) |

| the_stamp_reaches_the_legend_of_the_page | GEÄNDERT | Statt "(Stand ...)"-Stempel je Legendenwert zeigt die UI die Zeit des gewählten bzw. letzten Fachs für alle Werte (whenOf in HistoryChart.svelte); Stempellogik der Domäne bleibt in nicht übersprungenem test_setpoint_legend_carries_a_stamp_when_the_data_is_stale_and_not_otherwise |



### tests/test_zone_history_hover.py (12)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_card_carries_parseable_hover_data_and_the_drawing_sizes | ENTFÄLLT | Eingebettetes data-history-data-JSON, data-left/-right, keine Script-Tags: HTML-Mechanik; die Daten kommen jetzt als JSON von /ui-api/history (UAR::test_history_gaps_stay_gaps_and_values_are_exact) |

| a_card_without_data_has_no_hover_data | ERSETZT | UAR::test_history_future_is_not_a_gap_and_empty_past_is (leerer Zeitraum: rows 0, alle Fächer leer, Lücke gemeldet); Hinweistext entfällt |

| every_drawing_carries_its_own_plot_measures | ENTFÄLLT | data-left/right/top/bottom je SVG-Layout; Layouts gibt es in der Svelte-Grafik nicht |

| hatch_patterns_get_one_id_per_tile | ENTFÄLLT | Eindeutige SVG-Pattern-IDs je Kachel; Svelte vergibt uid je Instanz |

| navigation_and_the_zone_list_point_to_the_overview | ERSETZT | tests/test_ui_api.py::test_bootstrap_navigation_follows_grants (Navigation folgt den Rechten) und tests/test_zone_history_hover.py::test_the_navigation_entry_is_bound_to_the_right_the_view_checks (nicht übersprungen: Eintrag zone.manage/any_zone). aria-current und Zonenlistenlink entfallen als Markup |

| overview_answers_like_the_zone_list_without_the_right | GEÄNDERT | Verlauf ist jetzt zone.read je Zone (ui_api_reports.py-Docstring), die Seite listet alle sichtbaren Räume aus bootstrap.zones (HistoryPage.svelte); Sichtbarkeit: tests/test_ui_api.py::test_bootstrap_lists_only_visible_zones |

| overview_is_a_page_of_its_own_and_not_mistaken_for_a_zone | ENTFÄLLT | Route-/Titel-/h1-Markup der Serverseite; /zones/history leitet um (tests/test_legacy_redirects.py) |

| overview_is_closed_to_tenants_and_anonymous_visitors | GEÄNDERT | Anonym 401: UAR::test_history_and_csv_without_session_are_401. Mieter sind nun zugelassen (zone.read je Zone): UAR::test_history_tenant_profile_may_read_own_zone, Beleg ui_api_reports.py-Docstring und HistoryPage "Gleiche Seite für beide Profile" |

| overview_lists_only_the_zones_each_user_may_manage | GEÄNDERT | Liste = sichtbare Räume (zone.read) statt zone.manage: tests/test_ui_api.py::test_bootstrap_lists_only_visible_zones, UAR::test_history_foreign_and_unknown_zone_are_the_same_404; Namen fremder Zonen kommen nicht vor (test_bootstrap_lists_only_visible_zones) |

| overview_rejects_an_unknown_period_and_passes_the_valid_one_to_every_tile | ERSETZT | UAR::test_history_input_limits (ungültige from/to/resolution -> 422 validation); Zeitraum-Auswahl (24h/3d/7d) ist jetzt clientseitig (history-zoom.test.ts, periodMs in history-scales.test.ts) |

| overview_tile_of_a_foreign_zone_is_refused_even_when_asked_for_directly | FEHLT | Fremde Zone gleiches 404 wie unbekannte: UAR::test_history_foreign_and_unknown_zone_are_the_same_404 (beide Endpunkte). Lücke: ungültige Eingabe (period/from) auf fremder Zone liefert 404 statt 422 und nennt Zonennamen nicht; kein Test (Code: _visible_zone vor _moment). Typ: Test |

| the_card_on_the_zone_page_keeps_its_ids_and_the_tile_gets_its_own | ENTFÄLLT | HTML-IDs, aria-describedby, Zeitraumschalter, Link auf Zone: reines Markup |



### tests/test_zone_views.py (5)



| Alttest | Urteil | Ersatz bzw. Begründung (bei FEHLT: was fehlt, Typ) |

|---|---|---|

| a_zone_permission_for_another_zone_yields_404 | ERSETZT | UAZ::test_invisible_and_unknown_zone_are_the_identical_404 (GET room-form?zone_id, PUT, delete-impact, DELETE, report auf fremde Zone = 404, identisch zur unbekannten) plus UAZ::test_manage_on_another_zone_does_not_reach_this_zone (sichtbar, aber manage nur anderswo -> 403). Hinweis: Sichtbarkeit hängt jetzt an zone.read (ui_api_rooms._visible_zone), "nur zone.manage ohne zone.read" ist nicht getestet |

| editing_a_zone_shows_the_values_and_saves_the_change | ERSETZT | UAZ::test_form_options_carry_the_current_values_of_an_existing_room (Formularwerte), UAZ::test_save_changes_the_sent_fields_only_and_audits und test_save_changes_every_field_and_can_clear_the_source_device (Speichern, Audit update); zonengebundenes manage am eigenen Raum: Schlussassert in test_manage_on_another_zone_does_not_reach_this_zone (PUT 200) |

| the_delete_confirmation_names_every_dependency | FEHLT | Lücke: UAZ::test_delete_impact_counts_what_goes_with_the_room prüft nur setpoints=1 (übrige Zähler 0); Zähler schedule_points, devices, overrides, shadow_decisions werden nie > 0 geprüft; auch nicht mit zonengebundenem manage (nur anlagenweit). Funktion vorhanden (delete-impact liefert alle fünf). Typ: Test |

| the_empty_zone_form_needs_an_installation_wide_permission | FEHLT | Lücke: GET /ui-api/room-form ohne zone_id mit nur zonengebundenem zone.manage -> 403 ist nicht getestet (nur POST /ui-api/rooms in test_manage_on_another_zone_does_not_reach_this_zone und GET mit nur zone.read); anlagenweit 200 gedeckt (UAZ::test_form_options_for_a_new_room). Code: _need in room_form ist anlagenweit. Typ: Test |

| the_zone_list_shows_only_visible_zones | ERSETZT | UAO::test_list_shows_only_visible_zones (zone.read nur für eine Zone, Name der fremden fehlt) und tests/test_ui_api.py::test_bootstrap_lists_only_visible_zones |

## Freigabe zum Löschen

Zweite, strenge Paritätsprüfung, 2026-10-16, Worktree `ui/cutover-redirects` (nach den Blockern 1–11). Methode: jede Zeile, die im ersten Abgleich `teilweise` oder `fehlt` war, gegen den heutigen Code (`frontend/src/lib`, `thermoctl/web/ui_api*.py`, `legacy_redirects.py`) neu geprüft; zusätzlich jedes Alt-Template (`templates/*.html`), jede Alt-JS-Datei (`static/*.js`) und jede Route der `*_views.py` einzeln gelesen und gegen die neue Oberfläche gehalten. **Gelesen und gegrept, nichts ausgeführt**: kein Testlauf, kein Browser, keine Darstellungsprüfung (CLAUDE.md verlangt, die Anwendung nach einem sichtbaren Teilschritt einmal wirklich zu öffnen -- das steht aus).

**Zähler (Funktionsparität je Alt-Seite, Detail in `FUNKTIONSMATRIX.md`):**

| | Erster Abgleich | Jetzt |
|---|---|---|
| Geprüfte Funktionen | 118 | 120 (2 im zweiten Durchgang neu aufgenommen) |
| vorhanden | 80 | 105 |
| bewusst anders (begründet) | 13 | 13 |
| teilweise | 17 | **2** |
| fehlt | 8 | **0** |

Alle 25 früheren `teilweise`/`fehlt` sind erledigt (Anlagen-Statusbereich `PlantStatus.svelte`, Raumsprunglinks `RoomJumps.svelte`, Direktlinks `?zone`/`?period`/`?ansicht`/`?tab` in `legacy_redirects.py` und `lib/preselect.ts`, Mieter-Einrichten und Zwei-Zeiten-Tag `DayTimesPanel.svelte`/`SetupDialog.svelte`, Letzter Wert der Bediengeräte, Homebridge-Kopieren, Schaltprotokoll-Art, Relaisverschleiß-Tabelle, Karte ein-/ausklappen, Texte, Rechte-Matrix-Sperre, interne Links). Auch die 18 Einträge „Typ: Funktion“ der FEHLT-Liste (Abschnitt 8) sind im Code erledigt: Meross-Hinweis am Riegel, PI-Modellrechnung samt Verhältnis, Außentemperatur, Datenfluss-Hinweis, Quelltextlink im Kiosk, Zeitzone im Kiosk-Ablauf, Mieter-Verlaufstexte und -Wortlaut, Abwesenheits-Banner ohne fremde Raumnamen, Einrichten, Sensor-Effekt-Hinweis, Übernahme aus nur lesbarem Raum, Wochenplan landet auf bearbeitbarem Raum, Absenkungsbereich und Dreieck statt Mindestbreite. Ebenso sind die Query-Parameter-Zeilen der Umleitungstabelle erledigt.

**Noch teilweise (konkret, alle klein):**

1. **Wochenplan (Verwaltung), Schaltpunkt/Zeitraum ohne Zeiger anlegen.** Alt: Formular „Schaltpunkt anlegen“ (Tag, Uhrzeit, Modus) und „Zeit malen“ (Tag, Von, Bis, bis 24:00). Neu: Anlegen nur durch Ziehen mit dem Pinsel (`WeekGrid.svelte`, `ScheduleEditor.svelte`); per Tastatur lassen sich vorhandene Blöcke verschieben, kürzen, löschen und ändern (`schedule/keyboard.ts`, `Inspector.svelte`), neue entstehen so nicht. Das Tageszeiten-Formular (`DayTimesPanel.svelte`) deckt nur Tage mit 0 oder 2 Zeiten ab. Folge: Ohne Zeiger/Touch ist ein dritter Schaltpunkt oder ein Zeitraum nicht setzbar (Barrierefreiheit; nicht sicherheitsrelevant).
2. **Notbetrieb-Aktortabelle (`RoomDecisions.svelte`).** Alt (`control.html`): Spalten Takt-Phase mit Frist, Takt aus Kennlinie oder Festtakt, Sensor-Timeout. Neu zeigt Phase, Takt, Übergabe- und Rückstellungstext, Ersatzquelle, Rückkehrfortschritt, Außenwert -- aber nicht `phase_deadline_at`, `cycle_source`, `sensor_timeout_seconds` (die Server liefern sie bereits, `operations/types.ts`). Nachvollziehbarkeit der Regelung (Grundsatz 5/7), nicht Bedienung.

**Urteil: freigabefähig nein -- knapp.** Begründung: Die Freigabe des Projektinhabers gilt nur, „wenn wirklich alle Funktionen in der neuen UI verfügbar sind“; zwei Funktionen sind es nur teilweise. Beide sind klein (jeweils ein Formular bzw. drei Anzeigefelder mit vorhandenen Daten), kein `fehlt` mehr, kein Blocker bei Auth, Rechten oder Regelwirkung. Nach Nachbau von 1 und 2 (und einer Gegenprüfung durch einen anderen Agenten) ist die Funktionsparität erreicht, und ich würde `freigabefähig ja` sagen -- unter diesen Bedingungen, die **nicht** Funktionen betreffen, aber vor dem physischen Löschen stehen müssen:

- **Tests:** 72 Einträge „Typ: Test“ in Abschnitt 8 (Funktion da, Ersatztest fehlt). Wer die Alt-Tests löscht, löscht deren einzigen Beleg; die Rechte-/Sichtbarkeitsfälle zuerst (Grundsatz 7). Die FEHLT-Liste ist sonst unverändert.
- **Einmal wirklich öffnen** (Darstellung, Ingress-Präfix, Kiosk, Mieter- und Admin-Konto) -- diese Prüfung hat nur den Quelltext gelesen; Tests belegen nach CLAUDE.md nicht, dass die Seiten stimmen.
- **Bleibt bewusst stehen** (nicht löschen): `oauth_views.py`/`oauth_consent.html`, `passkey_views.py`, `spa_views.py`, `kiosk_invalid.html` (ungültiger Kiosk-Token), `kiosk_views.py::kiosk_entry` (Einlösung/Cookie), die Basis-Templates, die diese Seiten brauchen (`base_plain.html`, `base_core.html`), und `legacy_redirects.py`.
- **Aufräumen beim Löschen:** `comp/rooms/ActuationPanel.svelte` ist ungenutzt; `Placeholder.svelte`/`legacyHref` greifen für kein Navigationsziel mehr (nur unbekannte Server-Pfade) und sollten mit entfallen.
- **Offene Entscheidung, keine Alt-Funktion:** Audit-Lücken im Bestand (Offene Frage 1) bestehen unverändert; die neue Oberfläche nutzt dieselben Schreibwege.
