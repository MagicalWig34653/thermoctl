# thermoctl — Rahmenentwurf

Stand: 2026-08-28 · Status: Rahmen abgestimmt, Teilprojekte noch nicht spezifiziert

Dies ist der **Gesamtrahmen**, nicht die Spezifikation einer Implementierung. Er hält die
bereits getroffenen Entscheidungen samt Begründung fest und zerlegt das Vorhaben in fünf
Teilprojekte. Jedes Teilprojekt bekommt seinen eigenen Zyklus aus Spezifikation → Plan →
Umsetzung und dabei ein eigenes Dokument in diesem Verzeichnis.

Das abzulösende System ist in [`../../bestandsaufnahme-altsystem.md`](../../bestandsaufnahme-altsystem.md)
beschrieben. Wer hier weiterarbeitet, sollte das zuerst lesen.

## 1. Ausgangslage

Die bestehende Heizungssteuerung besteht aus vier Python-Services in einem Homelab-Repo mit
neun weiteren, fachfremden Services, plus einer PHP-Oberfläche in einem zweiten Projekt ohne
Versionskontrolle. Gekoppelt sind sie über eine gemeinsame MariaDB-Datenbank ohne
Migrationswerkzeug. Die Grundkonfiguration — welcher Raum welchen Sensor und welches Ventil
hat — wird ausschließlich per SQL-Client gepflegt.

## 2. Ziele

Vom Nutzer benannt, in dieser Reihenfolge gewichtet:

1. Vollständige Konfiguration über eine Weboberfläche statt per SQL.
2. Ein Projekt statt zweier getrennter Welten, mit eigenem Container.
3. Nichts hart verdrahtet — keine festen Geräte-IDs, Adressen oder Zugangsdaten im Code.
4. Authentifizierung in der Oberfläche.
5. Nachvollziehbar debuggbar.
6. Veröffentlichbar als Self-Hosting-Projekt.

## 3. Getroffene Entscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| **Name** | `thermoctl` | Funktional statt bildhaft, `-ctl`-Konvention wie `systemctl`/`kubectl`. Auf PyPI frei. |
| **Ausrichtung** | Eigenständiger Dienst; Home Assistant optional | Nächstliegend am bestehenden Aufbau. HA bleibt Konsument per MQTT, wird aber keine Voraussetzung — das System läuft auch ohne. |
| **Zielgruppe** | Veröffentlichbar (Self-Hosting), **nicht** mandantenfähig | Fremde Nutzer sollen es ohne Zutun des Autors aufsetzen können. Mehrere getrennte Haushalte in einer Instanz sind ausdrücklich kein Ziel. |
| **Backend** | Python, FastAPI, SQLAlchemy + Alembic | Python ist gesetzt (Zigbee2MQTT-/Meross-Anbindung existiert dort bereits). Alembic, weil ein veröffentlichtes Projekt Schema-Migrationen braucht. |
| **Datenbank** | Wahl beim Setup: SQLite (Standard) oder MariaDB | SQLite senkt die Einstiegshürde für Fremde auf null — kein Datenbankserver nötig. MariaDB für Betreiber mit vorhandener Instanz. |
| **Frontend** | Jinja + HTMX + Bootstrap, server-gerendert | Kein npm, kein Build-Schritt, ein Prozess, ein Container. Fehler passieren an einer Stelle statt in zwei Ebenen — adressiert direkt „schlecht zu debuggen". Bootstrap ist gesetzte Nutzerpräferenz. |
| **Schnittstellen** | Drei dünne Adapter über einer gemeinsamen Domänenlogik: HTMX-Views, REST-API, MCP-Server | Verhindert drei divergierende Implementierungen derselben Regeln. |
| **Auth** | Vorhanden und verpflichtend | Modell noch offen — gehört in Teilprojekt 1. |
| **Umstieg** | Parallelbetrieb im Schattenbetrieb → Vergleich → Cutover mit Rückfallebene | Die Wohnung muss warm bleiben, und das Altsystem hat keine Tests. Der Neubau trifft erst Entscheidungen ohne zu schalten; erst wenn sie über Tage mit dem Altsystem übereinstimmen, wird umgeschaltet. |

### Ausdrücklich verworfen

- **Home Assistant als Geräte-Layer** (alle Geräte nur über HA ansprechen): weniger
  Gerätecode, aber HA würde zur harten Voraussetzung. Verworfen zugunsten der
  Eigenständigkeit.
- **HA-Integration oder -Add-on statt eigenem Produkt**: größere Reichweite, aber Bindung
  an HAs Ökosystem und Konventionen. Verworfen.
- **Multi-Tenant**: deutlich größerer Aufwand bei Auth, Datenmodell und Isolation, ohne
  erkennbaren Nutzen für den Anwendungsfall.
- **SPA (Vue/React)**: kann aussehen wie die server-gerenderte Variante (das „Android-Aussehen"
  kommt von Material Design, nicht von den Frameworks), rechtfertigt aber Build-Kette und
  zweite Ebene für dieses UI nicht.

### Aus der Datenbankwahl folgende Zwänge

Weil SQLite **und** MariaDB unterstützt werden, gilt für das gesamte Schema:

- Keine `ENUM`- oder `SET`-Spalten — stattdessen echte Constraints oder Nachschlagetabellen.
- Keine JSON-Spalten als Datenmodell — Zeitpläne werden als Zeilen modelliert, nicht als Blob.
- Keine datenbankspezifischen Funktionen im Anwendungscode.
- Alle Schemaänderungen ausschließlich über Alembic-Migrationen.

## 4. Zerlegung in Teilprojekte

Das Vorhaben ist zu groß für eine Spezifikation. Fünf Teile, in dieser Reihenfolge:

### 1 — Fundament
Repo-Struktur, Container, Konfigurationsmodell ohne Hardcoding, neues normalisiertes Schema
mit Alembic-Migrationen, Benutzer und Authentifizierung, API-Tokens, Logging- und
Debugging-Grundlage.

Nichts davon ist sichtbar, alles andere hängt daran. Hier stirbt das Hardcoding.

*Offene Fragen:* Was ersetzt `rooms`/`thermostate`/`heizung_conf` — bleibt die 1:1-Trennung
zwischen Raum und Thermostat, oder wird das eine Entität? Wie werden Zeitpläne modelliert
(feste Stundenraster wie heute oder freie Zeitfenster)? Auth-Modell: ein Benutzer oder
mehrere mit Rollen? Sitzungen für die Oberfläche und getrennte Tokens für API und MCP?

### 2 — Geräte-Anbindung (Schattenbetrieb)
Sensor-Ingest aus Zigbee2MQTT, Aktor-Adapter für Meross-Steckdosen und Zigbee-Heizkörper­ventile,
konfigurationsgetrieben statt fest verdrahtet. Vollständig implementiert, aber im
**Trockenlauf**: Es wird gelesen und protokolliert, nichts geschaltet.

Beweist gegen die echte Anlage, dass Gerätedaten und Adressierung stimmen, ohne die Heizung
anzufassen.

### 3 — Konfigurations-WebUI
Räume, Sensoren, Ventile und Zeitpläne über Bootstrap/HTMX pflegen statt per SQL.

Das Hauptärgernis des Nutzers. Ab hier ist `thermoctl` schon nützlich, obwohl es noch nicht heizt.

### 4 — Regelkreis und Cutover
Regellogik mit Zeitplänen, Tag-/Nacht-Sollwerten, **Hysterese und Mindestschaltdauer**
(fehlt im Altsystem und ist dort ein echter Defekt). Vergleichsbetrieb gegen das Altsystem,
dann Scharfschalten mit dem Altsystem als Rückfallebene. Danach Ablösung: Heizungsteil aus
der PHP-Oberfläche und die vier Skripte aus dem Alt-Repo entfernen.

Der einzige Teil, der wirklich heizt — kommt bewusst zuletzt und erst mit Vergleichsdaten aus 2.

### 5 — Integrationen und Veröffentlichung
Home-Assistant-MQTT-Discovery, MCP-Server, öffentliche API-Dokumentation, Setup-Assistent,
Self-Hosting-Dokumentation, Benachrichtigungen.

Alles, was „veröffentlichbar" praktisch bedeutet. Der MCP-Server landet hier und nicht früher,
weil er ein weiterer Adapter auf eine API ist, die erst stabil sein muss.

**Wichtige Abgrenzung:** „Veröffentlichbar" wird in Teilprojekt 1 als *Eigenschaft* verankert
(keine festen IDs, keine Secrets im Code, Migrationen von Anfang an), aber die Arbeit *fürs
Publikum* — Setup-Assistent, Dokumentation — kommt erst in 5. Andersherum würde monatelang
für Fremde gebaut, bevor es für den Autor funktioniert.

## 5. Nächster Schritt

Teilprojekt 1 im Detail ausbrainstormen — Schwerpunkt Datenmodell und Auth-Modell —, dann
Spezifikation, dann Implementierungsplan.
