# thermoctl — Bestandsaufnahme der WebUI

Stand: 2026-09-06, gelesen aus `thermoctl/web/` (21 View-Module, 40 Templates),
`thermoctl/web/navigation.py`, `thermoctl/web/static/` und `docs/STATUS.md`.
Zweck dieser Datei: Ausgangsmaterial für ein UI-Redesign — was es heute gibt, unter
welchem Recht es erreichbar ist und welche Absicht hinter der jeweiligen Lösung steht.

## 1. Technischer Rahmen der Oberfläche

- Server-gerenderte Jinja-Templates, Bootstrap 5 (lokal mitgeliefert, kein CDN),
  HTMX. Kein Build-Schritt, kein npm.
- `base.html` setzt `hx-boost="true"` auf den `<body>`: jede Navigation ist ein
  Teil-Austausch des Rumpfes. Skripte gehören deshalb in den `<head>` — im Rumpf
  würden sie bei jedem Boost erneut ausgeführt (Bootstrap registrierte seine
  Menübehandlung sonst doppelt, das Menü ging auf und im selben Klick wieder zu).
- Alle HTML-Router tragen `include_in_schema=False`. Die OpenAPI-Beschreibung ist der
  Vertrag der REST-Schnittstelle; sonst stünde unter `/docs` neben jedem echten
  Endpunkt eine Formularroute, deren „Try it out" eine echte Änderung auslöst.
- Eigene Assets: `thermoctl.css`, `assignment.js`, `device_filter.js`,
  `homebridge_copy.js`, `loading_indicator.js`, `passkey.js`, `permissions.js`,
  `schedule.js`. Vendor: Bootstrap, HTMX, Swagger-UI.

### Querschnittliches

| Thema | Umsetzung |
|---|---|
| CSRF | `csrf_protection` als Router-Dependency; HTMX holt den Token per `htmx:configRequest` aus dem Cookie, Kiosk-Formulare über ein verstecktes Feld (`kiosk_csrf_protection`) |
| Farbschema | folgt allein dem Betriebssystem (`prefers-color-scheme`, inline im `<head>`, damit nichts hell aufblitzt). **Kein eigener Umschalter** — wäre eine dritte Einstellung für etwas, das jedes Gerät schon kennt, und ginge beim nächsten Browser verloren |
| Ladeanzeige | `#tc-loading-bar`, global auf jeder angemeldeten Seite und auf Anmeldung/Einrichtung; erscheint erst nach 400 ms, respektiert `prefers-reduced-motion`; unterdrückt für stille Poll-Anfragen (`data-tc-quiet-poll`). Nicht am Kiosk |
| Veraltete Seite | Antwort mit `HX-Stale-Page` blendet ein Banner mit Neu-laden-Knopf ein, statt dass ein Bedienelement stumm nichts tut. Bewusst kein automatisches Reload (Schleifengefahr) |
| Ingress-Präfix | `url_prefix` in jedem Template, `web/urls.py::prefixed` / `cookie_path`; pro Anfrage entschieden, nicht pro Prozess |
| Navigation | eine einzige Tabelle `web/navigation.py` (Pfad → Label → Recht → Endpunkt), gefiltert über `visible_navigation(principal)`; ein Wächtertest hält Tabelle und Endpunktprüfungen zusammen |
| Sprache | benutzersichtbarer Text mit echten Umlauten; Bezeichner und maschinell gelesene Schlüssel bleiben ASCII |

### Navigationstabelle (heutiger Stand)

Abschnitt „main":

| Pfad | Label | Recht | Geltungsbereich |
|---|---|---|---|
| `/zones` | Zonen | `zone.read` | je Zone |
| `/devices` | Geräte | `device.read` | anlagenweit |
| `/control` | Betrieb | `zone.read` | anlagenweit |

Abschnitt „settings":

| Pfad | Label | Recht |
|---|---|---|
| `/settings` | Regelvorgaben | `zone.read` (Ändern: `setting.manage`) |
| `/modes` | Sollwert-Modi | `mode.manage` |
| `/interfaces` | Schnittstellen | `setting.manage` |
| `/controllers` | Bediengeräte | `device.read` (je Zone) |
| `/users` | Benutzer | `user.manage` |
| `/groups` | Gruppen | `group.manage` |
| `/tokens` | API-Tokens | `token.self` |
| `/kiosk-tokens` | Kiosk-Tokens | `token.manage` |
| `/audit` | Protokoll | `audit.read` |
| `/device-commands` | Schaltprotokoll | `audit.read` |
| `/relay-wear` | Relaisverschleiß | `audit.read` |

Nicht in der Navigation, aber erreichbar: `/`, `/plant`, `/statistics`, `/passkeys`,
`/kiosk`, `/login`, `/setup` sowie alle Unterseiten einer Zone.

## 2. Seiten im Einzelnen

### Einrichtung und Anmeldung

- **`/setup`** — Erstinbetriebnahme: erster Benutzer, Anzeigename, Passwort, Zeitzone.
  Dauerhaft 404, sobald ein Benutzer existiert (nicht nur versteckt).
- **`/login`, `/logout`** — Passwortanmeldung. Fehlversuchszähler im Prozessspeicher,
  verzögernd; **kein** Kontosperren (in einem Einfamilienhaus vor allem ein bequemer
  Weg, sich selbst auszusperren). Das Dict ist gedeckelt, damit erfundene Namen es
  nicht wachsen lassen.
- **`/passkeys`** plus `passkey/{registration,authentication}/{options,verify}` und
  `/passkeys/{id}/remove` — WebAuthn. Jeder Fehlschlag sieht gleich aus (gleicher
  Status, gleicher Text); die Unterscheidung steht nur im Audit-Protokoll. Ohne
  konfigurierte Relying-Party-Id existieren die Routen gar nicht.

### Startseite `/` — der Statuszettel

Beantwortet genau eine Frage: *tut das Haus gerade, was ich gesagt habe?* Alles, was
diese Frage nicht beantwortet, gehört woandershin (zwei Zählkacheln — Anzahl Zonen,
Anzahl Benutzer — wurden aus genau diesem Grund entfernt).

Nicht angemeldet → Weiterleitung auf `/login`, ohne Benutzer → `/setup`; ausdrücklich
kein 401, weil wer die Adresse eintippt ein Anmeldeformular sehen soll.

Je Zone:
- Ist-Wert, aufgelöster Sollwert **samt Begründung**, Sensorzustand, letzte
  Schattenentscheidung.
- Tagesspur des Zeitplans als farbiger Balken mit „Jetzt"-Marke (dieselbe Zerlegung
  wie die Wochenansicht, aus der Domäne — keine zweite Fassung im Browser).

Anlagenbanner (die drei/fünf Dinge, ohne die eine Anzeige nicht vertrauenswürdig ist):
Aktorfreigabe `control_armed`, MQTT-Riegel (`sending_allowed`), Brückenerreichbarkeit,
stumme Sensoren, festhängende Messwerte (**eigenes** Banner, weil die Zone dabei normal
weiterregelt), sowie ein **Bereitschaft**-Chip, sobald diese Instanz im
Aktiv-Bereitschafts-Verbund nicht führt.

Bedienelemente, je Zone rechtegesteuert:
- **Thermostat ± 0,5 K** (`setpoint.write`) — ändert den Sollwert des *laufenden
  Modus* dauerhaft, ausdrücklich **keine** Übersteuerung; die Seite zeigt daneben,
  welcher Modus gerade angepasst wird. Der Schritt wird serverseitig gegen den
  aktuellen Wert gerechnet, damit zwei Klicks zwei Schritte sind.
- **Übersteuern anlegen** (`override.create`): dauerhaft / bis zum nächsten
  Schaltpunkt / für X Minuten.
- **Übersteuerung aufheben** (`override.cancel`).
- Verweis auf die Zonen-Parameter (`zone.manage`).

Liveaktualisierung: `hx-get`/`hx-select`/`hx-swap="outerHTML"` auf `#tc-live`, Intervall
aus `setting.shadow_interval_seconds` (nicht hart verdrahtet — häufiger abzufragen als
sich ein Wert ändern kann, wäre verschwendete Last hinter dem Ingress-Proxy),
`[!document.hidden]` pausiert im Hintergrund. Aufgeklappte Bereiche und begonnene
Eingaben überleben per `hx-preserve`.

### Zonen

- **`/zones`** Liste · **`/zones/new`** · **`/zones/{id}`** bearbeiten ·
  **`/zones/{id}/delete`** mit Abhängigkeitsprüfung.
  Felder: technischer Name, Anzeigename, Betriebsart, Sortierung, Temperaturquelle.
- **`/zones/{id}/parameters`** (`zone.manage`) — Regelparameter je Zone: Hysterese,
  Mindest-Ein-/Ausschaltdauer, Sensor-Timeout, Temperatur-Offset, Solargewinnfaktor,
  Ventilschutz, PI-Regelung samt Eignungsprüfung und Bestätigungsfeld. Zeigt die
  effektiven Werte (Zone vs. Anlagenvorgabe) nebeneinander.
- **`/zones/{id}/setpoints`** — Sollwerte je Sollwert-Modus.
- **`/zones/{id}/devices`** — Geräte der Zone: Rolle zuordnen, lösen, Gerät tauschen,
  Messquelle wählen, Selbstregelung setzen, Tastenbelegung binden. Enthält das
  Anlagenbild der Zone.

Gemeinsamer Kopf über allen Zonen-Unterseiten: `zone_header.html`.

### Zeitplan `/zones/{id}/schedule`

Wochenraster aus echten Schaltpunkt-Zeilen (nicht dem positionell interpretierten
JSON-Blob des Altsystems). Lokale Zeit; ein Balken gilt bis zum nächsten Schaltpunkt.

- **Vorschau der nächsten 24 Stunden** oberhalb des Rasters, sichtbar schon mit
  `zone.read`. Gerechnet in der Domäne (`domain/schedule.py::schedule_forecast`) über
  dieselbe Rangfolge wie zur Laufzeit — Betriebsart Aus schlägt alles, dann eine
  laufende Übersteuerung bis zu ihrem Ende, dann der Zeitplan, zuletzt Frostschutz.
  Kann daher nie etwas zeigen, was nicht tatsächlich einträte. Der gerade geltende
  Abschnitt ist hervorgehoben; Färbung über `color-mix` gegen `--warmth`/`--cool`.
- Bearbeiten (`schedule.js`): Schaltpunkt anlegen, löschen, verschieben, Modus eines
  Punktes wechseln, **Intervall malen** (`schedule/paint`), **Tag kopieren**
  (`copy-day`), **Rückgängig** über einen signierten Undo-Token,
  **von anderer Zone übernehmen** (`schedule/adopt`).

### Betrieb und Vorgaben — bewusst zwei Seiten

Beide arbeiten auf derselben `setting`-Zeile und wurden trotzdem getrennt: wer prüfen
will, ob die Anlage scharf ist, scrollte vorher an neun Zahlenfeldern vorbei — und wer
eine Vorgabe ändern wollte, landete zuerst auf dem Scharfschalt-Knopf.

- **`/control` (`zone.read`)** — Betriebszustand: sind beide Riegel offen, was
  entscheidet die Regelung gerade, je Zone Zustand / Entscheidung / Sollwert.
  Knopf `/control/arm` mit eigenem Recht `control.arm`. Die Seite sagt ausdrücklich,
  dass der beim Prozessstart gebaute MQTT-Riegel erst nach einem Neustart wirkt —
  sonst sucht jemand stundenlang den Fehler.
  Überschriften heute: „Betrieb", „Aktorausgabe freigegeben", „Der MQTT-Riegel ist noch
  zu", „Die Regelung entscheidet, schaltet aber nicht".
- **`/settings`** (Anzeige `zone.read`, Ändern `setting.manage`) — Regelvorgaben:
  Hysterese, Mindestschaltdauern, Zykluszeit, Aufbewahrung, Zeitzone,
  Solarprognose-Standort (an/aus plus Koordinaten). Dazu:
  - die vier **Störungsmeldungsschalter**: Sensorstörung, Brücke/Broker weg,
    Schaltbefehl gescheitert, festhängender Messwert (alle ab Werk an),
  - der **Webhook-Testknopf** — schickt eine gekennzeichnete Testmeldung über denselben
    Weg wie eine echte und zeigt Statuscode, Dauer und im Fehlerfall den Grund
    unmittelbar auf der Seite; ohne hinterlegten Webhook nicht angeboten, 10 s
    Mindestabstand,
  - der **Zustellzustand**: wann zuletzt versucht, mit welchem Ergebnis; „noch nie
    versucht" ist ein eigener Zustand.

### Geräte und Anlage

- **`/devices` (`device.read`)** — Geräteübersicht mit Befunden (Batterie,
  Funkqualität, Verfügbarkeit, Stille). Auffälliges zuerst, denn die Frage beim
  Aufrufen ist fast immer „ist etwas kaputt?". Getrennt in „notable" und
  „unremarkable", dazu die Zahl der Geräte ohne Zone. Filter über `device_filter.js`.
  Meross-Steckdosen werden am stündlichen Cloud-Abgleich gemessen, nicht am
  MQTT-Eingang (sonst stünde dort dauerhaft „hat sich noch nie gemeldet").
- **`/plant` (`device.read`)** — dasselbe als **Anlagenbild** (`flow_diagram.html`):
  welches Gerät tut was, wo; inklusive „Ohne Zone" und Brückenzustand.
- **`/controllers` (`device.read`, je Zone)** — Bediengeräte: „Was ankommt", „Was
  hingeschickt wird", Kanalkonfiguration, Tastenbelegung, Temperaturquellen.

### Sollwert-Modi

**`/modes`**, `/modes/new`, `/modes/{id}`, `/modes/{id}/delete` (`mode.manage`), mit
Löschsperre, solange ein Modus verwendet wird (`mode_delete.html` erklärt, warum).

### Schnittstellen `/interfaces` (`setting.manage`)

Was von außen angebunden ist und ob es wirklich läuft (MQTT-Broker, Webhook,
Meross-Konto). `setting.manage` statt `zone.read`, weil die Seite Brokeradressen,
Webhook-Ziele und Kontonamen nennt.

Dazu je sichtbarer Zone ein fertiger **Homebridge-`mqtt-thing`-Block** zum Kopieren.
Die Topics baut die Domäne über dieselben Funktionen wie die Veröffentlichung selbst —
kein zweites Abschreiben, ein Wächtertest hält das gerenderte HTML dagegen. Der
Abschnitt zeigt nur Zonen, für die zusätzlich `zone.read` vorliegt. **Zugangsdaten
stehen nie darin** — nur Platzhalter plus der Hinweis, dass Homebridge einen eigenen
Brokerzugang mit engen Rechten braucht. Der Kopierknopf kommt ohne Bibliothek aus und
sagt es, wenn die Zwischenablage-Schnittstelle mangels sicherem Kontext scheitert.

### Auswertungen

- **`/statistics` (`zone.read`)** — Heizzeiten je Zone und Tag über 7 / 30 / 90 Tage.
  Kein Freitext-Datumsfeld: die Frage lautet „diese Woche" oder „diesen Monat". Alle
  Zonen gleich skaliert, weil genau ihr Vergleich der Zweck der Stapelung ist. Im
  Trockenlauf sagt die Seite ausdrücklich, dass es „hätte geheizt" bedeutet.
- **`/relay-wear` (`audit.read`)** — Schaltspiele und Relaisverschleiß je Gerät, mit
  Jahreshochrechnung gegen die angenommene Lebensdauer, gleiche Zeiträume. Bewusst
  getrennt von den Heizzeiten: Heizentscheidungen sind Zonenzustand (`zone.read`),
  Befehlshistorie ist Protokoll (`audit.read`).

### Protokolle

- **`/audit` (`audit.read`)** — Audit-Protokoll, filterbar nach Datum, 50 Einträge je
  Seite.
- **`/device-commands` (`audit.read`)** — Schaltprotokoll: jeder Befehl, der hinausging,
  im Trockenlauf unterdrückt oder verworfen wurde — Zeitpunkt, Zone, Gerät, Nutzlast,
  Ergebnis, Begründung, Auslöser. Filter nach Datum, Zone, Ergebnis. Ein Eintrag
  überlebt das Löschen oder Umbenennen seiner Zone bzw. seines Geräts.

### Benutzerverwaltung

- **`/users` (`user.manage`)** — anlegen, aktiv schalten, Gruppe setzen, Passwort
  setzen; dazu „Eigenes Passwort ändern" und „Andere Sitzungen beenden".
- **`/groups` (`group.manage`)** — Gruppen anlegen/löschen, Rechtematrix nach
  `PERMISSION_AREAS` (`permissions.js`).
- **`/tokens` (`token.self`)** — eigene API-Tokens ausstellen und widerrufen; Klartext
  wird genau einmal gezeigt.
- **`/kiosk-tokens` (`token.manage`)** — Kiosk-Token je Zonenmenge, nur-lesend oder mit
  Bedienung, mit Ablauf. Bewusst getrennt von `/tokens`: ein Kiosk-Token benennt Zonen
  und einen Bedienschalter statt eines Rechtecodes und geht an ein Gerät, nicht an eine
  Person.

### Kiosk `/kiosk`

Die Wandtablett-Ansicht, ohne Anmeldung. Einstieg über `/kiosk/{token}`, einmal
geöffnet und als Lesezeichen gespeichert; danach lebt der Klartext nur im Cookie, und
jeder weitere Besuch geht über das nackte `/kiosk` (deshalb leitet der Einstieg um,
statt direkt zu rendern — so trägt auch kein `Referer` den Token).

- Zonenraster mit Ist/Soll, Heizanzeige, Sensorzustand.
- Bedienung je nach Token: Sollwert ± 0,5 K (`setpoint.write`), **Boost**
  (`override.create`), **Übersteuerung aufheben** (`override.cancel`, nur sichtbar
  wenn eine läuft — sie kann die eines anderen sein).
- Eigenes, schlankes Layout (`base_plain.html`); AGPL-§13-Quelltextverweis knapp in
  der Kopfzeile statt einer Fußzeile, weil die Fläche dem Zonenraster gehört und das
  Tablett aus Distanz angesehen wird. Kein Ladebalken. Selbsttätiger Nachlader alle
  20 Sekunden (feste Zahl, anders als die Startseite).
- `kiosk_invalid.html` für ungültige/abgelaufene Token.

**Wichtig fürs Redesign:** Jeder Lese- und Schreibzugriff des Kiosks geht durch
dieselben Domänenfunktionen und dieselbe `Principal`/`visible_zones`/
`has_permission`-Maschinerie wie die angemeldete Oberfläche. Ein Kiosk-Token ist ein
Principal mit engeren `grants`, kein paralleler Zugangsweg.

## 3. Template-Inventar

Layout und Bausteine: `base.html`, `base_plain.html`, `form.html`, `empty.html`,
`zone_header.html`, `stale_page.html`, `flow_diagram.html`.

Seiten: `start`, `login`, `setup`, `passkeys`, `zones`, `zone_form`, `zone_delete`,
`parameter`, `setpoints`, `schedule`, `schedule_adopt`, `schedule_point_delete`,
`device_assignment`, `devices`, `plant`, `controllers`, `modes`, `mode_form`,
`mode_delete`, `control`, `settings`, `interfaces`, `statistics`, `relay_wear`,
`audit`, `device_commands`, `users`, `groups`, `tokens`, `kiosk_tokens`, `kiosk`,
`kiosk_invalid`.

## 4. Was beim Redesign nicht verlorengehen darf

1. **Die Startseite beantwortet eine Frage.** Kacheln, die sich leicht berechnen
   lassen, aber nichts über die Heizung sagen, sind dort schon einmal entfernt worden.
2. **Thermostat ≠ Übersteuerung.** Beides ist ein Alltagswunsch; beides mit demselben
   Bedienelement zu erledigen wäre der sicherste Weg, das Falsche zu treffen. Die
   Trennung samt Beschriftung „welcher Modus wird angepasst" ist Absicht.
3. **Die drei Riegel müssen sichtbar bleiben** (`control_armed`, MQTT-Riegel beim
   Prozessstart, Führungsrolle im Verbund) — samt dem Hinweis, dass der MQTT-Riegel
   einen Neustart braucht. Eine Bereitschaftsinstanz, die aussieht wie die aktive, ist
   genau der Fehlerfall, den der Chip ausschließen soll.
4. **Trockenlauf muss angeschrieben sein.** Statistik und Schaltprotokoll sagen heute
   dazu, dass es „hätte geheizt" bzw. „unterdrückt" heißt.
5. **Rechte werden im Endpunkt geprüft**, die Navigationstabelle ist nur Anzeige.
   Ein Redesign darf Sichtbarkeit ändern, nie die Prüfung ersetzen.
6. **Zonennamen sind rechtepflichtig**, auch auf anlagenweiten Seiten: `/interfaces`
   und `/relay-wear` filtern zusätzlich über `visible_zones`. Das war ein Befund der
   Sicherheitsdurchsicht, kein Detail.
7. **Keine zweite Fassung von Domänenlogik im Browser.** Tagesspur, Zeitplan-Vorschau,
   Thermostatschritt, Temperaturgrenzen kommen alle aus der Domäne bzw. vom Server;
   ein `min="5"` im Markup wäre eine zweite Wahrheit.
8. **Kein Build-Schritt, kein npm, keine CDN-Abhängigkeit** — technischer Rahmen,
   nicht Gewohnheit.
9. **Der stale-page-Hinweis und die stille Poll-Anfrage** sind aus dem Betrieb
   gemeldete Fehler, keine Zierde: ohne sie tut ein Bedienelement stumm nichts bzw.
   blitzt jede Minute grundlos ein Ladebalken auf.

## 5. Bekannte Lücken (Stand heute)

- Zeitplan-Vorschau, Schaltprotokoll und Relaisverschleiß gibt es nur in der WebUI;
  REST und MCP hinken dort bewusst hinterher.
- Kein Freitext-Zeitraum in den Auswertungen (bewusst).
- Kein Farbschema-Umschalter (bewusst).