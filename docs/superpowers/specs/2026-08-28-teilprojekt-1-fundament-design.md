# Teilprojekt 1 — Fundament

Stand: 2026-08-28 · Status: Spezifikation, noch nicht umgesetzt

Konkretisiert Teilprojekt 1 aus dem [Rahmenentwurf](2026-08-28-thermoctl-neubau-design.md).
Der Rahmen — FastAPI, SQLAlchemy, Alembic, SQLite oder MariaDB, Jinja/HTMX/Bootstrap,
eigener Container, Home Assistant optional — gilt hier unverändert und wird nicht wiederholt.

Voraussetzung zum Verständnis: [Bestandsaufnahme des Altsystems](../../bestandsaufnahme-altsystem.md),
insbesondere Abschnitt 4 (Fallstricke im Ist-Schema). Diese Spezifikation löst die Fallstricke
1 und 3 bis 8; Fallstrick 2 (fehlende Hysterese) ist Regellogik und gehört in Teilprojekt 4 —
das Schema hier hält die dafür nötigen Parameter aber schon bereit.

## 1. Ziel und Abgrenzung

Teilprojekt 1 baut alles, woran die übrigen vier hängen: Datenmodell, Migrationen,
Konfiguration ohne Hardcoding, Benutzer mit Rechten, Sitzungen und Tokens, Logging und
Audit, Container, CI. Nichts davon steuert eine Heizung, und nichts davon spricht mit
einem Gerät.

**Nicht Teil dieses Teilprojekts:**

| Ausgeklammert | Gehört nach |
|---|---|
| MQTT-Anbindung, Zigbee2MQTT, Meross, Geräteerkennung | 2 |
| Messwert-Historie und ihre Aufbewahrung | 2 |
| Pflegeoberflächen für Zonen, Geräte und Zeitpläne | 3 |
| Regellogik, Hysterese, Mindestschaltdauer, Fensterpause | 4 |
| Datenübernahme aus `rooms`/`thermostate`/`heizung_conf` | 4 |
| MCP-Server, HA-Discovery, öffentliche API-Doku, Self-Hosting-Doku | 5 |

Das Schema modelliert Geräte, Rollen und Regelparameter dennoch bereits vollständig.
Sie nachträglich einzuziehen hieße, Migrationen auf Daten zu schreiben, die dann schon
produktiv sind — und im Fall der Regelparameter jede Stelle der Regellogik erneut anzufassen.

Die Oberfläche beschränkt sich in diesem Teilprojekt auf Anmeldung, Einrichtungsassistent
und die Verwaltung von Benutzern, Gruppen und Tokens. Alles Fachliche wird in TP1
ausschließlich über Migrationen und den Assistenten befüllt.

`docs/technisches_konzept.md` ist **unverbindlich** (Beschluss vom 2026-08-28). Es beschreibt
ein Zielbild mit Home Assistant als Einstiegspunkt, was der Rahmenentwurf ausdrücklich
verworfen hat. Übernommen wurden daraus vier Punkte, die das Schema betreffen: Regelparameter
je Zone, Fensterkontakte als Geräterolle, die Betriebsart Automatik/Manuell/Aus sowie
Sensor-Timeout und Temperatur-Offset.

## 2. Datenmodell

### 2.1 Regeln, die für jede Tabelle gelten

Aus der Doppelunterstützung SQLite/MariaDB folgt, was das Schema **nicht** benutzen darf,
und drei Punkte, die leicht übersehen werden:

- Kein `ENUM`, kein `SET`, keine JSON-Spalte als Datenmodell. Feste Wertemengen werden
  Nachschlagetabellen mit `code`-Spalte.
- Keine reservierten Wörter als Tabellennamen. `group` ist in beiden Systemen reserviert,
  die Gruppentabelle heißt deshalb `access_group`.
- Keine partiellen Indizes — SQLite und MariaDB behandeln sie verschieden. Wo eine
  Kardinalität erzwungen werden muss, steht sie als Fremdschlüsselspalte statt als
  gefilterter eindeutiger Index (siehe `zone.temperature_source_device_id`).
- Alle Zeitstempel werden in UTC gespeichert, zeitzonenlos. MariaDB `DATETIME` trägt keine
  Zonenangabe; eine gemischte Ablage fällt erst bei der Zeitumstellung auf.
- Jede `varchar`-Spalte bekommt eine ausdrückliche Länge. SQLite ignoriert sie, MariaDB nicht.

### 2.2 Zonen

`zone` ersetzt `rooms` und `thermostate` gemeinsam. Die Trennung im Altsystem war faktisch
1:1 und trug keine Bedeutung (Fallstricke 6 und 8).

```
zone
  id                              PK
  name                            varchar(64)  NOT NULL UNIQUE   -- technisch, stabil
  display_name                    varchar(128) NOT NULL          -- angezeigt, änderbar
  operating_mode_id               FK operating_mode NOT NULL
  temperature_source_device_id    FK device NULL
  sort_order                      int NOT NULL default 0
  hysteresis_k                    numeric(4,2) NULL
  min_on_seconds                  int NULL
  min_off_seconds                 int NULL
  sensor_timeout_seconds          int NULL
  temperature_offset_k            numeric(4,2) NULL
  window_resume_delay_seconds     int NULL
  created_at / updated_at         datetime NOT NULL
```

Die sechs Regelparameter sind **nullbar**: leer heißt „gilt der globale Standard aus
`setting`". So steht jeder Wert genau einmal irgendwo, und eine Änderung des Standards
wirkt auf alle Zonen, die ihn nicht ausdrücklich überschrieben haben.

`temperature_source_device_id` ist eine Spalte und keine Zuordnungszeile, weil eine Zone
genau eine maßgebliche Messquelle hat. Als Spalte erzwingt das Schema diese Kardinalität
selbst; als Zeile in `zone_device` bräuchte es einen gefilterten eindeutigen Index, den
die beiden Datenbanken unterschiedlich behandeln.

`operating_mode` ist eine Nachschlagetabelle mit `auto`, `manual` und `off` und ersetzt
`autoHeatControl set('true','false')` (Fallstrick 3). **`off` bedeutet Frostschutz, nicht
stromlos** — die Zone wird weiter geregelt, aber gegen den Modus aus
`setting.frost_protection_mode_id`. Welcher Modus das ist, steht ausschließlich dort und
nicht zusätzlich als Merkmal am Modus selbst; zwei Quellen für dieselbe Aussage geraten
auseinander.

### 2.3 Sollwerte und Zeitpläne

```
setpoint_mode                     -- frei anlegbar: Tag, Nacht, Frostschutz, Urlaub, …
  id  PK
  code                  varchar(32)  NOT NULL UNIQUE
  name                  varchar(64)  NOT NULL
  sort_order            int NOT NULL default 0
  is_builtin            boolean NOT NULL default false   -- nicht löschbar

zone_setpoint
  zone_id, setpoint_mode_id        PK (beide)
  temperature_c         numeric(4,1) NOT NULL

schedule_point
  id  PK
  zone_id               FK zone NOT NULL
  weekday               int NOT NULL     -- 1 = Montag … 7 = Sonntag, wie isoweekday()
  minute_of_day         int NOT NULL     -- 0 … 1439, lokale Zeit
  setpoint_mode_id      FK setpoint_mode NOT NULL
  UNIQUE (zone_id, weekday, minute_of_day)
```

Ein Schaltpunkt gilt **bis zum nächsten** — wie bei klassischen Heizungsreglern. Daraus
folgt, dass es weder Lücken noch Überlappungen geben kann, also auch keinen ungültigen
Zustand, den eine Validierung erst abfangen müsste. Der zuletzt vor dem Wochenanfang
liegende Punkt wirkt über die Wochengrenze hinweg; die Suche nach dem geltenden Punkt läuft
rückwärts und rollt notfalls auf den Sonntag zurück. Eine Zone ohne jeden Schaltpunkt fällt
auf den Frostschutz-Modus.

Ersetzt `temperatureTargetNightHours` (Fallstrick 1): statt eines positionell interpretierten
JSON-Blobs mit acht Slots und Stunden als Strings sind es Zeilen, minutengenau statt
stundengenau. Ein typischer Tag braucht zwei bis vier davon statt 24 Rasterfeldern.

`minute_of_day` ist ein Integer und kein `TIME`, weil Integer über beide Datenbanken
identisch vergleicht und sortiert. Die Zeit ist **lokale Zeit** in der unter
`setting.timezone` konfigurierten Zone — die Nachtabsenkung soll sich bei der Zeitumstellung
nicht verschieben. Alles übrige bleibt UTC.

### 2.4 Übersteuerung

```
zone_override
  id  PK
  zone_id               FK zone NOT NULL
  setpoint_mode_id      FK setpoint_mode NULL     -- genau eines von beiden
  temperature_c         numeric(4,1) NULL
  starts_at             datetime NOT NULL         -- UTC
  ends_at               datetime NULL             -- NULL = dauerhaft, bis aufgehoben
  cancelled_at          datetime NULL
  created_at            datetime NOT NULL
  created_by_user_id    FK user NULL
  created_by_token_id   FK api_token NULL
  source_id             FK actor_source NOT NULL
  CHECK ((setpoint_mode_id IS NULL) <> (temperature_c IS NULL))
```

Drei Enden beim Anlegen: bis zum nächsten Schaltpunkt, für eine gewählte Dauer, oder
dauerhaft. In den ersten beiden Fällen wird der End-Zeitpunkt **beim Anlegen konkret
ausgerechnet** und als Zeitstempel abgelegt, nicht als Regel. Damit steht in der Datenbank
immer, wann Schluss ist, statt dass jeder Regelzyklus es neu herleiten muss — und eine
Zeitplanänderung verschiebt eine laufende Übersteuerung nicht rückwirkend.

Je Zone ist höchstens eine Übersteuerung aktiv (`ends_at` in der Zukunft oder NULL, und
`cancelled_at` leer); eine neue beendet die vorige, indem sie `cancelled_at` setzt. Zeilen
werden **nie gelöscht** — sie sind die Historie, aus der später hervorgeht, warum eine Zone
von ihrem Zeitplan abwich.

### 2.5 Geräte

Aktoren sind ausdrücklich offen: jeder über Zigbee2MQTT auffindbare Schaltaktor und
Meross-Schalter allgemein, nicht nur die heute verbauten MSS710. Deshalb trennt das Modell,
**wie** ein Gerät erreicht wird, von dem, **wozu** es in einer Zone dient.

```
integration           -- Nachschlagetabelle: zigbee2mqtt, meross
device
  id  PK
  integration_id      FK integration NOT NULL
  external_id         varchar(191) NOT NULL     -- friendly name bzw. Geräte-UUID
  display_name        varchar(128) NOT NULL
  model               varchar(128) NULL
  is_enabled          boolean NOT NULL default true
  first_seen_at / last_seen_at   datetime NULL
  UNIQUE (integration_id, external_id)

device_capability          -- temperature, switch, setpoint_display, contact, battery
device_capability_link     -- device_id, capability_id   (n:m)
device_role                -- actuator, window_contact, controller
zone_device
  id  PK
  zone_id, device_id, device_role_id     FK, NOT NULL
  sort_order          int NOT NULL default 0
  UNIQUE (zone_id, device_id, device_role_id)
```

`varchar(191)` für `external_id`, weil MariaDB unter `utf8mb4` bei 191 Zeichen die Grenze
indizierbarer Schlüssellänge erreicht.

Ein Gerät kann mehrere Fähigkeiten und mehrere Rollen haben — ein Aqara W100 misst die
Temperatur und dient zugleich als Bediengerät. Eine Zone hat beliebig viele Aktoren,
Fensterkontakte und Bediengeräte, aber genau eine Messquelle (2.2). Damit wird
`valveIdRadiatorList` zu echten Zeilen (Fallstricke 5 und 8), und ein Gerätetausch ist eine
geänderte Zuordnung — auch quer über Anbindungen hinweg, ohne dass Sollwerte, Zeitplan
oder Regelparameter der Zone berührt werden.

In TP1 entsteht von alldem nur das Schema. Befüllt wird `device` durch die Geräteerkennung
aus TP2, gepflegt über die Oberfläche ab TP3.

### 2.6 Benutzer, Gruppen, Berechtigungen

```
user
  id  PK
  username        varchar(64) NOT NULL UNIQUE
  display_name    varchar(128) NOT NULL
  password_hash   varchar(255) NOT NULL
  is_active       boolean NOT NULL default true
  created_at, last_login_at

access_group        id, name UNIQUE, description, is_builtin
user_access_group   user_id, access_group_id            PK (beide)

permission
  id  PK
  code            varchar(64) NOT NULL UNIQUE
  description     varchar(255) NOT NULL
  is_zone_scoped  boolean NOT NULL      -- darf auf eine Zone eingeschränkt werden?

group_permission
  id  PK
  access_group_id  FK access_group NOT NULL
  permission_id    FK permission NOT NULL
  zone_id          FK zone NULL          -- NULL = anlagenweit
  UNIQUE (access_group_id, permission_id, zone_id)
```

Berechtigungen sind eine feste, per Migration gepflegte Liste — sie gehören zum Code, nicht
zu den Nutzdaten:

| Code | zonenbezogen | Bedeutung |
|---|---|---|
| `zone.read` | ja | Zonen und ihren Zustand sehen |
| `zone.manage` | ja | Zonen anlegen, ändern, löschen |
| `setpoint.write` | ja | Sollwerte je Modus ändern |
| `schedule.manage` | ja | Zeitpläne ändern |
| `override.create` | ja | übersteuern |
| `override.cancel` | ja | fremde Übersteuerung aufheben |
| `device.read` | ja | Geräte und Zuordnungen sehen |
| `device.manage` | ja | Geräte zuordnen, tauschen, entfernen |
| `mode.manage` | nein | Sollwert-Modi anlegen und ändern |
| `setting.manage` | nein | globale Einstellungen ändern |
| `user.manage` | nein | Benutzer verwalten |
| `group.manage` | nein | Gruppen und Rechte verwalten |
| `token.self` | nein | eigene Tokens ausstellen und widerrufen |
| `token.manage` | nein | fremde Tokens verwalten |
| `audit.read` | nein | Audit-Protokoll einsehen |

`is_zone_scoped` verhindert sinnlose Kombinationen (`user.manage` für das Bad) sowohl in der
Oberfläche als auch bei der Prüfung: eine Zuordnung mit `zone_id` auf einer nicht
zonenbezogenen Berechtigung wird abgewiesen.

Der Einrichtungsassistent legt vier Gruppen als Beispiele an, alle danach frei änderbar:

| Gruppe | Rechte |
|---|---|
| Verwaltung | alle, anlagenweit |
| Bedienung | `zone.read`, `setpoint.write`, `override.create`, `override.cancel`, `token.self` |
| Nur lesen | `zone.read`, `device.read` |
| Integration | `zone.read` — gedacht als Besitzer von Tokens für Home Assistant |

### 2.7 Sitzungen und Tokens

```
session
  id  PK
  user_id        FK user NOT NULL
  token_hash     varchar(64) NOT NULL UNIQUE
  created_at, expires_at, last_seen_at    datetime
  revoked_at     datetime NULL
  user_agent     varchar(255) NULL
  ip_address     varchar(45)  NULL

api_token
  id  PK
  user_id        FK user NOT NULL
  name           varchar(128) NOT NULL
  prefix         varchar(16) NOT NULL UNIQUE   -- zum Wiedererkennen, unkritisch
  token_hash     varchar(64) NOT NULL UNIQUE
  created_at, expires_at NULL, last_used_at NULL, revoked_at NULL

api_token_permission
  id, api_token_id, permission_id, zone_id NULL
  UNIQUE (api_token_id, permission_id, zone_id)
```

Ein Token bekommt einen **eigenen, engeren Rechteumfang** als sein Besitzer. Beim Ausstellen
wird geprüft, dass der Umfang eine Teilmenge der effektiven Rechte des Besitzers ist; ein
Home-Assistant-Token darf dann nur lesen, auch wenn es einem Verwalter gehört. Verliert der
Besitzer später ein Recht, verliert das Token es bei der Prüfung ebenfalls — die Teilmengen-
Bedingung gilt zur Laufzeit, nicht nur beim Anlegen.

### 2.8 Einstellungen, Audit, Setup

```
setting                    -- genau eine Zeile, CHECK (id = 1)
  id, timezone varchar(64), polling_interval_seconds,
  default_hysteresis_k, default_min_on_seconds, default_min_off_seconds,
  default_sensor_timeout_seconds, default_window_resume_delay_seconds,
  frost_protection_mode_id FK setpoint_mode, session_lifetime_seconds, updated_at

actor_source               -- Nachschlagetabelle: web, api, mcp, cli, system
audit_event
  id, occurred_at, source_id FK actor_source,
  actor_user_id NULL, actor_token_id NULL,
  action varchar(64), object_type varchar(64), object_id varchar(64) NULL,
  summary varchar(255), detail text NULL

setup_token                -- Einmal-Token für den Einrichtungsassistenten
  id, token_hash, created_at, consumed_at NULL
```

`setting` ersetzt `heizung_conf` (Fallstrick 4): typisierte Spalten statt Text-EAV. Eine
neue Einstellung ist eine Alembic-Migration statt eines Strings, der erst zur Laufzeit als
Fehler auffällt. `lastSeen` aus dem Altsystem entfällt — Lebenszeichen gehören ins Logging
und in TP2, nicht in die Konfiguration.

Voreinstellungen der Initialmigration: Zeitzone `Europe/Berlin`, Regelzyklus 30 s, Hysterese
0,3 K, Mindest-Ein- und Ausschaltdauer je 300 s, Sensor-Timeout 1800 s,
Fenster-Wiederanlaufverzögerung 120 s, Sitzungsdauer 14 Tage.

## 3. Authentifizierung und Autorisierung

**Passwörter:** Argon2id über `argon2-cffi`, Parameter aus der Bibliotheksvorgabe, Hash mit
eingebetteten Parametern, damit spätere Verschärfungen ohne Schemaänderung möglich sind.
Mindestlänge 12 Zeichen, keine Zusammensetzungsregeln.

**Sitzungen:** serverseitig. Der Cookie enthält ein Zufallsgeheimnis mit 256 Bit; gespeichert
wird nur dessen SHA-256. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` konfigurierbar (hinter
TLS ein, im Heimnetz ohne TLS aus). Verlängerung bei Aktivität, Abmelden setzt `revoked_at`.

**CSRF:** jedes zustandsändernde Formular trägt ein Token; HTMX sendet es über einen
Standard-Header. Geprüft wird für alle Anfragen, die per Cookie authentifiziert sind —
Token-Anfragen brauchen es nicht, da sie kein Cookie mitschicken.

**Tokens:** Format `tctl_<prefix>_<geheimnis>`, Geheimnis 256 Bit. Gespeichert wird SHA-256
des Geheimnisses, nicht Argon2id: bei 256 Bit Zufall trägt ein langsamer Hash nichts bei,
muss aber bei jeder API-Anfrage berechnet werden. Der Klartext erscheint genau einmal beim
Anlegen. Jedes Token einzeln widerrufbar, `last_used_at` macht ungenutzte sichtbar.

**Prüfung an einer Stelle.** Die Domänenlogik stellt zwei Funktionen bereit, und nur diese:

```python
authz.require(principal, "schedule.manage", zone=zone)   # wirft, wenn nicht erlaubt
authz.visible_zones(principal, "zone.read")              # Menge erlaubter Zonen
```

`principal` kapselt Benutzer **oder** Token samt effektivem Rechteumfang, sodass die
Adapter — HTMX-Views, REST, später MCP — nicht wissen müssen, womit sie es zu tun haben.
Jede Liste und jede API-Antwort filtert über `visible_zones`. Das ist der Punkt, an dem
zonenbezogene Rechte still lecken, wenn man ihn irgendwo vergisst; genau deshalb liegt er
in der Domänenlogik und nicht in den Adaptern (Grundsatz 6).

**Einrichtungsassistent:** erreichbar nur, solange kein Benutzer existiert. Beim ersten Start
ohne Benutzer erzeugt der Dienst ein Einmal-Token, schreibt es ins Log und legt seinen Hash
in `setup_token` ab; der Assistent verlangt es. Ohne diesen Schutz gewinnt im ungünstigen
Fall der Erste im Netz. Der Assistent legt den ersten Verwalter, die vier Beispielgruppen
und die `setting`-Zeile an; danach ist er dauerhaft geschlossen und das Token verbraucht.

**Anmeldeversuche** werden protokolliert und pro Benutzer verzögert gedrosselt. Eine
Kontosperre gibt es nicht — sie wäre in einem Einhaushalt-System vor allem eine bequeme
Möglichkeit, sich selbst auszusperren.

## 4. Konfiguration

Zweiteilung nach Zuständigkeit:

| Aus Umgebung / `.env` | Aus der Datenbank (`setting`) |
|---|---|
| `THERMOCTL_DATABASE_URL` | Zeitzone |
| `THERMOCTL_SECRET_KEY` | Regelzyklus |
| `THERMOCTL_BIND_HOST` / `_PORT` | Hysterese, Mindest-Ein-/Ausschaltdauer |
| `THERMOCTL_LOG_LEVEL` / `_LOG_FORMAT` | Sensor-Timeout, Fenster-Verzögerung |
| `THERMOCTL_SECURE_COOKIES` | Frostschutz-Modus, Sitzungsdauer |

In der Umgebung steht, was Secret ist oder vor der Datenbank gebraucht wird; alles Fachliche
kommt aus der Datenbank und ist damit später in der Oberfläche pflegbar (Ziel 1). Gelesen
über `pydantic-settings`, ohne Vorgabewert für `SECRET_KEY` und `DATABASE_URL` — fehlen sie,
startet der Dienst nicht. Ein Vorgabe-Secret wäre genau der Fallback-Wert, den Grundsatz 2
verbietet. `.env.example` enthält Namen und Erläuterungen, aber keine Werte.

## 5. Logging und Nachvollziehbarkeit

Strukturiertes JSON-Logging auf stdout, ein Ereignis je Zeile, mit durchgehender
Anfrage-ID über alle Schichten. Beim Start werden die wirksamen Einstellungen protokolliert —
**ohne** Secrets; die Datenbank-URL erscheint ohne Zugangsdaten. Ein Filter maskiert
Passwörter, Token und Cookies; er wird eigens getestet, weil ein durchgerutschtes Secret im
Log Grundsatz 2 verletzt.

`audit_event` verzeichnet, was Wochen später noch beantwortbar sein soll: Anmeldung und
Fehlversuch, Ausstellung und Widerruf von Tokens, Änderungen an Benutzern, Gruppen und
Rechten, an Zonen, Geräten, Zeitplänen und Einstellungen, sowie jede Übersteuerung — je mit
Urheber, Zeitpunkt und Adapter. Geschrieben in derselben Transaktion wie die Änderung, damit
kein Eintrag zu einer Änderung existiert, die nicht stattfand. In TP4 hängen die
Schaltentscheidungen an derselben Struktur.

## 6. Aufbau des Projekts

```
thermoctl/
  config.py          Umgebungseinstellungen (pydantic-settings)
  logging.py         JSON-Logging, Anfrage-ID, Maskierung
  db/                Engine, Session, SQLAlchemy-Modelle, Repositories
  domain/            Entitäten und Regeln — kennt keinen Adapter
    authz.py         require() und visible_zones()
    schedule.py      geltender Schaltpunkt, Sollwertauflösung
    settings.py      Zonenwert mit Rückfall auf globalen Standard
  auth/              Passwörter, Sitzungen, Tokens, Setup-Token
  web/               FastAPI-App, Jinja-Templates, HTMX-Views
  api/               REST-Adapter
  cli.py             Verwaltungskommandos
migrations/          Alembic
tests/
docker/
.github/workflows/
```

Die Abhängigkeitsrichtung ist einseitig: `web` und `api` kennen `domain`, `domain` kennt
keinen Adapter. Eine Regel wird einmal implementiert (Grundsatz 6). Ein Test hält diese
Richtung fest, weil sie sonst schleichend aufweicht.

## 7. Tests

- **Beide Datenbanken.** Die gesamte Testsuite läuft gegen SQLite und gegen MariaDB.
  Unterschiede zeigen sich fast immer im Schema — Schlüssellängen, Groß-/Kleinschreibung
  bei Vergleichen, Zeitstempelverhalten — und wenn das erst beim Umzug der echten Daten
  auffällt, blockiert es den Cutover.
- **Migrationen vorwärts und rückwärts**, auf beiden Datenbanken, plus eine Prüfung, dass
  die Modelle keine Migration schuldig geblieben sind (`alembic check`).
- **Berechtigungen als Matrix:** je Berechtigung, je Rolle, anlagenweit und zonenbezogen,
  über Sitzung und über Token. Ausdrücklich enthalten: dass `visible_zones` in Listen und
  API-Antworten wirkt, und dass ein Token nicht mehr darf als sein Besitzer.
- **Sicherheit im Detail:** CSRF-Schutz greift, Setup-Assistent nach Abschluss geschlossen,
  Setup-Token nur einmal verwendbar, Log-Maskierung greift, Passwort-Hashes tauchen nirgends
  in Antworten auf.
- **Zeitpläne** über Wochengrenze, an der Zeitumstellung, ohne jeden Schaltpunkt, sowie
  Übersteuerungen an ihrem Ende.

## 8. Container und CI

Ein Dockerfile in mehreren Stufen, Betrieb als Nicht-root, SQLite-Datei in einem Volume.
Der Container führt beim Start `alembic upgrade head` aus — bei einer selbst gehosteten
Anwendung ist eine vergessene Migration sonst ein wiederkehrender Betriebsfehler.

Das GitHub-Repository wird **privat** angelegt und erst in TP5 öffentlich geschaltet. Ein
versehentlich committetes Secret ist in einer öffentlichen Historie nicht mehr zurückzuholen.

Zwei Workflows:

| Workflow | Auslöser | Inhalt |
|---|---|---|
| `ci.yml` | jeder Push, jeder Pull Request | Ruff, Typprüfung, pytest gegen SQLite **und** MariaDB (Service-Container), Alembic vorwärts/rückwärts |
| `docker.yml` | Push auf `main` | Testimage bauen, Marke `sha-<kurzer Commit>` |
| `docker.yml` | Git-Tag `v*` | Release-Image, Marken `<version>` und `latest` |

`latest` entsteht **ausschließlich aus einem Git-Tag**, nie aus einem Push auf `main`. Wer
das Image betreibt, zieht sonst mit jedem Zwischenstand einen unfertigen Stand — und bei
einer selbst gehosteten Heizungssteuerung ist das Image der Rückfallpunkt, wenn ein Update
schiefgeht. Testimages sind über ihren Commit eindeutig ansprechbar und lassen sich damit
gezielt ausprobieren, ohne dass jemand sie versehentlich erwischt.

## 9. Reihenfolge der Umsetzung

Jeder Schritt endet mit grüner CI und einem Commit; Einzelheiten regelt der
Implementierungsplan.

1. Projektgerüst, Konfiguration, Logging, Dockerfile, beide CI-Workflows — mit einem
   Rumpfdienst, damit die CI von Beginn an etwas prüft, das laufen kann.
2. Datenbankanbindung, Alembic, Nachschlagetabellen, Testaufbau gegen beide Datenbanken.
3. Schema Anlage: Zonen, Modi, Sollwerte, Schaltpunkte, Übersteuerungen, Geräte.
4. Schema Identität: Benutzer, Gruppen, Berechtigungen, Sitzungen, Tokens, Audit.
5. Domänenlogik: `authz`, Sollwertauflösung, Zonenwert mit Rückfall auf den Standard.
6. Adapter: Anmeldung, Einrichtungsassistent, Verwaltung von Benutzern, Gruppen und Tokens
   als HTMX-Views; dieselben Vorgänge als REST-Endpunkte.

Schritt 3 und 4 sind voneinander unabhängig und können parallel an zwei Agents gehen; ab
Schritt 5 hängt alles zusammen.

## 10. Offene Punkte

- **Datenübernahme** aus `rooms`, `thermostate` und `heizung_conf` ist bewusst nicht Teil
  dieses Teilprojekts. Die Zuordnung ist überwiegend geradlinig; unklar bleibt, wie aus dem
  Stundenraster des Altsystems Schaltpunkte werden, wenn das Raster unregelmäßig ist. Wird
  in TP4 entschieden, wenn echte Daten vorliegen.
- **Die alte MQTT-Topic-Struktur** wird von thermoctl nicht bedient. Ob sie übergangsweise
  zusätzlich veröffentlicht wird, entscheidet TP2.
- **`vm130-nginx` bleibt unangetastet** und ohne Versionskontrolle. Bis zum abgeschlossenen
  Cutover ist es die Rückfallebene; erst danach wird der Heizungsteil entfernt.
