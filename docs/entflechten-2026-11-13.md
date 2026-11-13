# Entflechten-Analyse 2026-11-13

Nur Analyse (Skill `entflechten-analyse`), kein Code geändert. Schwerpunkt Backend (Regelung, Schema/Validierung,
DB-Zugriff); Frontend und Add-on nur, wo Logik doppelt liegt. Bewusste Entscheidungen aus `CLAUDE.md`/`STATUS.md`
(Svelte-SPA, drei Adapter, Regelkette in der Domäne, bekannte Folgearbeit „synchrone DB-Arbeit in `async def`“) sind keine Befunde.

## Graph-Zusammenfassung

Aus `ast`-Import-Scan (`thermoctl/`, ~45 000 Zeilen) und `git log -n 300`:

- **Fan-in** (wer wird am meisten importiert): `db.models.lookup` (54), `db.base` (49), `db.models.zone` (48), `config` (27),
  `domain.authz`/`domain.principal` (je 26). Stabiler Kern, erwartbar.
- **Fan-out** (wer importiert am meisten): `app` (67), `mcp.server` (28), `web.ui_api_operations` (27), `web.ui_api_access` (27),
  `services.publishing` (27), `api.routes` (25).
- **Schichten:** `web→domain` 121, `web→db` 88, `domain→db` 123, `services→domain` 37. Rückwärts: `domain→integrations` (4 Importe in
  `domain/interfaces.py:30-31`, `domain/data_flow.py:53-54`), `domain→auth` (3 Dateien: `administration`, `kiosk`, `oauth`).
- **Größte Dateien:** `services/shadow_run.py` 2153, `services/publishing.py` 1866, `domain/schedule.py` 1863, `app.py` 1561,
  `web/ui_api_operations.py` 1491, `mcp/server.py` 1448.
- **Gemeinsam geändert** (Commits, ohne Tests): `app.py` in 34 von 300 Commits, davon oft mit `config.py` (5), `web/ui_api_access.py` (3),
  `web/ui_api_rooms.py` (3) und `services/retention.py` (3). `domain/zone_settings.py` (20) und `domain/control.py` (18) ändern sich
  häufig – zusammen mit `web/shared/zone_parameters.py`, `api/schemas.py`, `db/models/zone.py` (siehe Lösung 1).
- Im Frontend liegen die Parametergrenzen **nicht** doppelt: Min/Max/Schritt kommen mit `limits` vom Server
  (`web/ui_api_operations.py`). Im Add-on stehen sie nur im CHANGELOG. Kein Befund dort.

## Befunde

Fehlerart: **VK** versteckte Kopplung, **UK** unnötige Kopplung, **ÜF** überflüssig, **SF** Sonderfall.

| # | Stelle | Art | Befund |
|---|--------|-----|--------|
| B1 | `domain/control.py:62` (`LIMITS`), `domain/zone_settings.py:380` (`PARAMETERS`) | VK | Grenzen der Hysterese (0,0001 – 5,0) und der Mindestzeiten (30 – 7200 s) stehen zweimal; der Kommentar in `zone_settings.py:374` verlangt „gleich wie global“, nichts erzwingt es. Die Hysterese-Änderung 0.24.0 musste beide plus Spalten anfassen. |
| B2 | `domain/zone_settings.py:299` (`validate_hysteresis`) vs. `domain/control.py:216` (`check_number`) | VK | Zwei Prüfwege für denselben Wert: Zone prüft Untergrenze und Schritt selbst, global läuft über `check_number`. Die Meldungen unterscheiden sich („mindestens …“ / „vier Nachkommastellen“). |
| B3 | `db/models/zone.py:37-58` (CheckConstraints PI), `domain/zone_settings.py:~422-440` | VK | PI-Bereiche und Schritte stehen in DB-Constraints **und** `PARAMETERS`; der Kommentar dort nennt es selbst („three places would otherwise drift“). Kein Test, der beide gegeneinander hält. |
| B4 | `api/schemas.py:94-96,115-116` | VK | REST-Schema prüft nur `ge=0` für Hysterese und Mindestzeiten; die echte Grenze (≥ 0,0001 / ≥ 30) greift erst tiefer in der Domäne. Wer nur das Schema liest, sieht eine falsche Grenze (OpenAPI-Dokumentation lügt). |
| B5 | `web/shared/zone_parameters.py:19` und `:35` | VK/SF | `GANZZAHLEN` wird definiert und dann neu zugewiesen; die Liste der Ganzzahlfelder ist von Hand gepflegt, obwohl `ParameterDescription.integral` (`zone_settings.py`) dasselbe weiß. |
| B6 | `web/ui_api_operations.py:326` (`FELDER[:7]`) | SF | Positionelles Slice auf ein Tupel; wer ein Feld in `FELDER` einfügt, verschiebt die Auswahl lautlos. |
| B7 | `domain/zone_settings.py:374` | ÜF | Kommentar verweist auf `domain/steuerung.GRENZEN` – das Modul gibt es nicht mehr (heute `control.LIMITS`). |
| B8 | `domain/interfaces.py:30-31`, `domain/data_flow.py:53-54` | UK | Die Domäne importiert aus `integrations` (Meross, MQTT-Topics, Thermostat-Payload). Stabiler Kern hängt an Volatilerem; Domäne ist ohne Integrationspaket nicht testbar/importierbar. |
| B9 | `domain/administration.py`, `domain/kiosk.py`, `domain/oauth.py` | UK | Domäne importiert `thermoctl.auth.*` (Sessions/Tokens/Passwörter); Auth-Mechanik und Rechtelogik greifen ineinander. |
| B10 | `services/shadow_run.py` (2153 Z., 35 Funktionen) | UK | Name „Schattenbetrieb“, enthält aber den scharfen Regelzyklus: PI-Gates (`:1146`), Notbetrieb (`:844`), Ventilschutz (`:1772`), Zonenverarbeitung (`:1869`), Persistenz. Ein Modul, mehrere Änderungsgründe; Name führt in die Irre. |
| B11 | `services/publishing.py` (1866 Z.) | UK | Aufzeichnen **und** Schalten **und** MQTT-Veröffentlichung in einem Modul (Fan-out 27). |
| B12 | `domain/control.py`, `control_loop.py`, `controller.py`, `controller_channels.py`, `pi_control.py` | VK | Fünf „control“-Module mit völlig verschiedener Bedeutung: Betriebszustand/Standardwerte, Regelentscheidung, Wandtaster, Taster-Kanäle, PI-Regler. Wer „die Regelung“ sucht, landet zuerst im falschen. |
| B13 | `app.py` (1561 Z., Fan-out 67, 34 Änderungen in 300 Commits) | UK | Enthält neben der App-Erstellung die Schleifen (`_shadow_loop` `:571`), den Notfall-Dispatch (`:226-375`) und Meross-Abgleich (`:519`). Änderungsmagnet; Dienste-Logik hängt an der Web-App. |
| B14 | `web/ui_api_*.py` → `db` (88 Importe) | UK | Adapter laden und schreiben teils direkt über Modelle statt über die Domäne (Grundsatz 6 verlangt „dünn“). Nicht einzeln geprüft; Dateien mit 900–1500 Zeilen sind der Hinweis. |
| B15 | `app.py` Schleifen (laut STATUS) | SF | Synchrone DB-Arbeit in `async def`; bekannt und dokumentiert, daher **kein neuer Befund** – nur zur Vollständigkeit (Folgearbeit mit `asyncio.to_thread`, sicherheitsrelevant). |

**Sonderfälle / Tests, die gesucht wurden:** keine `TODO/FIXME/HACK` im Quelltext, keine Konfliktkopien (`* 2.*`), `time.sleep` nirgends,
`asyncio.sleep` nur in Schleifen (`app.py:646`, `observability.py:78`). Übersprungene Tests (`tests/test_migrations.py:328,740`,
`test_migration_lock.py:82`, `test_endpoint_coverage.py:52`, `test_ui_api_schedule_concurrency.py:35`) sind begründet (nur SQLite bzw. nur
MariaDB, nur Volllauf) – kein Befund. `except Exception` kommt in `actuators.py` (5), `app.py` (5), `publishing.py` (3) vor; im
Stichproben-Scan folgte darauf kein `pass`/`continue`, einzeln **nicht** geprüft → Teil von Lösung 5.

## Lösungen (über den Befunden)

Reihenfolge nach Nutzen zu Risiko. Jede Karte: Branch, erst Tests für das bestehende Verhalten, dann Umbau, volle Suite
(SQLite **und** MariaDB, Ruff, mypy, Alembic), `--no-ff`-Merge. Alles Sicherheitsrelevante liest die Hauptsession gegen (Grundsatz 7).

### L1 – Eine Quelle für Parametergrenzen (behebt B1, B2, B3, B4, B7)

Neuentwurf: `ParameterDescription` ist die einzige Beschreibung (Name, Label, Einheit, Min, Max, Schritt, vererbt?). `LIMITS`
(global) wird aus derselben Tabelle gebildet (Präfix `default_`), `validate_hysteresis` und `check_number` werden eine Funktion, REST-Schema
liest Grenzen daraus (oder prüft nicht doppelt und dokumentiert die echten Grenzen). Ein Test hält die DB-`CheckConstraint`s gegen die Tabelle.
- Nutzen: hoch – die jüngste Änderung (0.24.0) war genau dieser Streuschaden; künftige Grenzänderungen an einer Stelle.
- Risiko: **mittel** (Validierung der Regelparameter ist regelnah; Fehlermeldungstexte dürfen sich ändern).
- Tests: `test_zone_settings*`, `test_control*`, `test_api*` (Grenzfälle), `test_migrations` (Constraints), Browsertest Raumtechnik.
- Sichtbares Verhalten: nein, außer vereinheitlichten Fehlermeldungen – vorher mit Lino klären.

### L2 – Feldlisten aus der Beschreibung ableiten (B5, B6)

`GANZZAHLEN`, `FELDER`, `PI_FELDER` aus `PARAMETERS` (Eigenschaft `integral`, Gruppe) ableiten; `FELDER[:7]` durch benannte Gruppe ersetzen.
- Nutzen: mittel, klein und schnell; Voraussetzung/erster Schritt von L1. Risiko: **klein**. Tests: `test_ui_api_operations*`, Zonenparameter-Formulartests. Sichtbar: nein.

### L3 – Domäne ohne Rückimporte aus Integrationen/Auth (B8, B9)

Gebrauchte Namen (Topics, Thermostat-Payload, Meross-Konfig-Prüfung) als schmale Schnittstelle (Protocol oder übergebene Werte) in die Domäne;
`interfaces.py`/`data_flow.py` bekommen die Werte hereingereicht. Auth-Hilfen (`auth.*`) entweder in die Domäne ziehen oder per Parameter.
Import-Test (`domain` importiert weder `integrations` noch `web/api/mcp`) sichert es dauerhaft.
- Nutzen: mittel (Testbarkeit, klare Schichten). Risiko: **mittel**. Tests: `test_data_flow*`, `test_interfaces*`, Auth-/OAuth-Tests. Sichtbar: nein.

### L4 – Regelzyklus aus `shadow_run.py` herauslösen und benennen (B10, B11)

`cycle`/`_process_zone` plus PI-Gates, Notbetrieb, Ventilschutz in eigene Module (`services/control_cycle/…`); `shadow_run` bleibt, was der Name
sagt, oder entfällt. `publishing.py` in Aufzeichnung, Schalten, MQTT-Veröffentlichung trennen. Reine Verschiebung, **kein** Logikumbau im selben Schritt.
- Nutzen: hoch (größte Änderungs- und Lesehürde), Risiko: **groß** (Regelkette, Grundsatz 7; Mutationsläufe `mutation/cosmic-ray-*.toml`
  müssen mitziehen). Tests: gesamte Regelungs-/Schatten-/Notbetriebstests, Referenztest `_previous_state`, Mutation vorher/nachher gleich.
  Vorgehen in kleinen Verschiebe-Commits. Sichtbar: nein. **Erst nach L1–L3 und mit Linos Freigabe.**

### L5 – `app.py` entlasten, Fehlerbehandlung der Schleifen prüfen (B13, B15-Rand)

Schleifen, Notfall-Dispatch und Meross-Abgleich in `services/` (z. B. `services/background.py`); `app.py` bleibt Zusammenbau. Dabei jedes
`except Exception` einzeln ansehen (verschluckt oder protokolliert?). `asyncio.to_thread` bleibt eigene, spätere Karte.
- Nutzen: mittel, Risiko: **mittel** (Lebenszyklus, Notbetriebsbenachrichtigung). Tests: `test_app*`, Lifespan-/Notfall-Tests, `test_smoke_test`. Sichtbar: nein.

### L6 – Regel-Module sprechend benennen (B12)

Umbenennen ohne Logikänderung: `control.py` → Betriebszustand/Standardwerte, `control_loop.py` → Regelentscheidung, `controller*` → Wandtaster,
`pi_control.py` bleibt. Importe per Skript anpassen, kurze Modulübersicht in `docs/`.
- Nutzen: klein bis mittel (Orientierung), Risiko: **klein** (rein mechanisch; Mutations-TOMLs und Docs verweisen auf Pfade). Tests: gesamte Suite. Sichtbar: nein.

## Später (nicht als Karte)

- **Adapter ohne direkten DB-Zugriff** (B14): `web/ui_api_*` schrittweise auf Domänenfunktionen; erst Stichprobe, welche Endpunkte Logik enthalten.
- **`asyncio.to_thread` für Regelschleife/Ingest/Aufräumlauf** (B15): bereits in `STATUS.md` als Folgearbeit; eigene Sicherheitsprüfung.
- Weitere große Dateien (`domain/schedule.py` 1863, `mcp/server.py` 1448, `domain/change_log.py` 1318): nicht untersucht, bei Bedarf eigene Analyse.
- Frontend: nicht untersucht über die Grenzwert-Prüfung hinaus.

## Nicht geprüft

Laufzeitverhalten, Testlauf (keine Tests ausgeführt – nur Doku), Add-on-Repository außer Grenzwert-Suche, `api/routes.py` Zeile für Zeile.
