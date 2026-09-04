# Roadmap

Stand: 2026-09-03

Diese Roadmap führt zusammen, was der Rahmenentwurf in fünf Teilprojekte zerlegt hat, und
konkretisiert es zu Features und Aufgaben. Sie ersetzt den Rahmenentwurf nicht — bei
Widerspruch gilt er (siehe `CLAUDE.md`, „Technischer Rahmen").

**Was verbindlich ist und was nicht:** Die Phasen und ihre Reihenfolge stammen aus dem
abgestimmten Rahmenentwurf. Die einzelnen Aufgaben darin sind ein Vorschlag; sie werden je
Phase im eigenen Zyklus aus Brainstorming, Spezifikation und Plan geschärft, so wie es bei
Phase 1 geschehen ist. Features, die aus der unverbindlichen
[Ideensammlung](technisches_konzept.md) stammen, sind als solche gekennzeichnet.

## Überblick

| Phase | Inhalt | Zustand | Nutzen am Ende |
|---|---|---|---|
| 1 | Fundament | **umgesetzt**, `v0.1.0` | Nichts sichtbar, aber alles Weitere hängt daran |
| 1a | Nacharbeiten | **umgesetzt** | Oberfläche benutzbar |
| 2 | Geräte-Anbindung im Schattenbetrieb | **gebaut**, läuft seit dem 2026-09-02 an der echten Anlage | Belegt gegen die echte Anlage, dass die Daten stimmen |
| 3 | Konfigurations-Oberfläche | **umgesetzt**, seither erweitert | Ende der SQL-Pflege — ab hier im Alltag nützlich |
| 4 | Regelkreis und Cutover | schaltet seit `v0.3.0` scharf an der echten Anlage; Ablösung des Altsystems offen | Altsystem ablösen |
| 5 | Integrationen und Veröffentlichung | teilweise vorgezogen | Für Fremde aufsetzbar |

Die Reihenfolge war ursprünglich so gedacht, dass der Teil, der eine echte Heizung schalten
soll, zuletzt kommt und erst mit Vergleichsdaten aus einem mehrtägigen Schattenbetrieb gegen
das Altsystem beginnt (Phase 2). **Der Projektinhaber hat diesen Vergleichsbetrieb bewusst
übersprungen** — thermoctl lief als Erstes direkt scharf an der echten Heizung, mit dem
Altsystem als Rückfallebene. Details und Begründung in [STATUS.md](STATUS.md).

---

## Phase 1 — Fundament ✅

Abgeschlossen am 2026-08-28. 22 Aufgaben, 142 Tests, Details in [STATUS.md](STATUS.md).

Geliefert: Datenmodell ohne Hardcoding, Alembic-Migrationen, Konfiguration aus Umgebung und
Datenbank, Benutzer mit auf Zonen einschränkbaren Rechten, Sitzungen und API-Tokens,
strukturiertes Logging mit Maskierung, Audit-Protokoll, Einrichtungsassistent, REST-Adapter,
Container und CI gegen beide Datenbanken.

## Phase 1a — Nacharbeiten

Aus dem Abschlussreview und dem ersten Ausprobieren im Browser. Klein, aber vor Phase 2 zu
erledigen.

- [x] Bootstrap und HTMX lokal einbinden; Templates auf ein gemeinsames Grundgerüst bringen
      *(der Rahmenentwurf setzt beides voraus, keine Aufgabe hatte es verlangt)*
- [x] Eingabefehler führen zum Formular zurück statt zu `500`
- [x] Globaler `Forbidden`-Handler statt Übersetzung in jeder Route
- [x] **Startseite gebaut** — Anmeldung, Abmeldung und Navigation zeigten auf `/`, das es
      nicht gab. Beim ersten Öffnen im Browser aufgefallen, nicht durch Tests.
- [x] Rauchtest und Endpunktabdeckung ergänzt, Testabdeckung von 93 auf 99 %
- [x] Jedes Review führt die Testsuite selbst aus *(Regel in CLAUDE.md)*
- [x] Gemeinsame CSRF-Abhängigkeit statt Prüfung von Hand je Route — hängt jetzt am Router,
      dazu ein Wächtertest über alle zustandsändernden Routen
- [x] Test für das Startverhalten: genau ein Einrichtungs-Token, beim zweiten Start keines mehr
- [x] **Zwei Wächtertests repariert**, die seit dem FastAPI-Versionssprung nichts mehr
      prüften: Seit FastAPI 0.141 legt `include_router()` keine flache Routenliste mehr
      an, wodurch beide Wächter (`test_endpoint_coverage.py`, `test_csrf.py`) nur noch
      `/healthz` sahen und grün blieben, weil sie nichts mehr prüften

---

## Phase 2 — Geräte-Anbindung im Schattenbetrieb

**Ziel:** Beweisen, dass Gerätedaten und Adressierung stimmen. In dieser Phase war die
Regelung unscharf: Es wurden keine Sollwerte an Ventile gesendet und Ein/Aus-Entscheidungen
erreichten keinen Aktor.

### Features

- Sensor-Ingest aus Zigbee2MQTT: Ist-Temperaturen fortlaufend in die Zonen schreiben
- Geräteerkennung: bekannte Zigbee2MQTT-Geräte einlesen und nach Fähigkeiten klassifizieren
  *(Idee aus dem Konzept-Dokument)*
- Aktor-Adapter für Zigbee-Ventile — vollständig, aber im Trockenlauf
- Meross-Anbindung — Geräteerkennung **und** Schaltweg, gegen ein echtes Konto geprüft.
  Der Schattenzyklus gleicht die Geräteliste stündlich ab; der Schaltweg verwendet MQTT.
  Ungeprüft bleibt das erste echte Schalten an der Anlage.
- Fensterkontakte als Zustandsquelle *(Idee aus dem Konzept-Dokument)*
- Sensor-Timeout: ein ausbleibender Messwert wird als Störung erkannt, nicht ignoriert
- Messwert-Historie mit begrenzter Aufbewahrung
- Schattenprotokoll: Was **würde** geschaltet, mit Begründung — die Grundlage für den
  Vergleich in Phase 4

### Aufgaben

Geschärft im eigenen Zyklus aus Spezifikation und Plan, wie bei jedem Teilprojekt.

- [x] 1 Schema und Migration (Messwerte, Zonenzustand, Gerätezustand, Schattenprotokoll)
- [x] 2 Nutzlast-Auswertung, gebaut gegen die echten Anlagendaten
- [x] 3 Geräteklassifikation aus `bridge/devices`
- [x] 4 MQTT-Client: Verbindung, TLS, Wiederverbindung, Topic-Zuschnitt
- [x] 5 Ingest und Aufbewahrung
- [x] 6 Regelentscheidung — Hysterese, Mindestschaltdauer, Fensterpause *(aus Phase 4
      vorgezogen, weil das Schattenprotokoll sonst nichts zu protokollieren hätte)*
- [x] 7 Störungserkennung bei ausbleibenden Messwerten
- [x] 8 Aktor-Adapter im Trockenlauf, hinter zwei Riegeln
- [x] 9 Schattenlauf
- [x] 10 Geräteübersicht und lesende Endpunkte

**Was nur der Projektinhaber abschließen kann:** der Nachweis über mehrere Tage echten
Betriebs. Er braucht Laufzeit an der Anlage — Zugangsdaten in `.env`,
`THERMOCTL_MQTT_ENABLED=true`, und dann Geduld.

### Risiken

- **Der MQTT-Topic-Vertrag des Altsystems ist gewachsen, nicht entworfen.** Ob die
  alten Topics übergangsweise mitbedient werden, entscheidet diese Phase.
- Die Meross-Cloud-API ist eine Fremdabhängigkeit ohne Zusicherung.
- Zugangsdaten für Broker und Meross gehören in die Konfiguration, niemals ins Repo.

**Fertig, wenn** über mehrere Tage plausible Ist-Temperaturen aller Zonen einlaufen und das
Schattenprotokoll nachvollziehbare Entscheidungen zeigt.

---

## Phase 3 — Konfigurations-Oberfläche

**Ziel:** Das Hauptärgernis beseitigen — Räume, Geräte und Zeitpläne über die Oberfläche
pflegen statt per SQL-Client.

### Features

- Zonen anlegen, ändern, löschen
- Geräte zuordnen: Messquelle, Aktoren, Fensterkontakte, Bediengeräte
- **Gerätetausch ohne Konfigurationsverlust** — Zone behält Sollwerte und Zeitplan
  *(Idee aus dem Konzept-Dokument)*
- Sollwert-Modi frei anlegen, je Zone mit Temperatur belegen
- Zeitplan-Editor auf Basis der Schaltpunkte, mit Wochenansicht
- Zeitplan von einer anderen Zone übernehmen *(Idee aus dem Konzept-Dokument)*
- Übersteuern: bis zur nächsten Schaltung, für eine Dauer, oder dauerhaft
- Regelparameter je Zone, mit sichtbarem Rückfall auf den globalen Standard
- Benutzer, Gruppen und Rechte pflegen; Tokens ausstellen und widerrufen
- Audit-Protokoll durchsuchen
- Verständliche Zustands- und Fehlermeldungen statt Rohdaten
  *(Idee aus dem Konzept-Dokument)*

### Aufgaben

Geschärft im eigenen Zyklus aus Spezifikation und Plan, wie bei jedem Teilprojekt.

- [x] 1 Gemeinsame Formularbausteine, Fehlerdarstellung am Feld
- [x] 2 Zonenverwaltung
- [x] 3 Gerätezuordnung samt Tausch
- [x] 4 Modi und Sollwerte
- [x] 5 Zeitplan-Editor mit Wochenansicht und Übernahme von einer anderen Zone
- [x] 6 Übersteuerung aus der Oberfläche
- [x] 7 Regelparameter je Zone, mit sichtbarem Rückfall auf den globalen Standard
- [x] 8 Benutzer-, Gruppen- und Tokenverwaltung *(in der Hauptsession, Grundsatz 7)*
- [x] 9 Audit-Ansicht mit Filtern und Blätterung
- [x] 10 Übersichtsseite mit Ist, Soll samt Begründung, Zustand und letzter Entscheidung

**Fertig, wenn** eine vollständige Anlage ohne einen einzigen SQL-Befehl eingerichtet werden
kann. **Nachgewiesen am 2026-08-29** an einer frischen Instanz: Einrichtung, zwei Zonen,
eigener Modus, Sollwerte, Zeitplan samt Übernahme, Regelparameter, Übersteuerung, Benutzer,
Gruppe und Token — alles über die Oberfläche, kein SQL.

---

## Phase 4 — Regelkreis und Cutover

**Ziel:** Regelung freigeben, Aktoren verdrahten und das Altsystem ablösen.

### Features

- Regelschleife mit **Hysterese und Mindestschaltdauer** — im Altsystem ein echter Defekt:
  dort schaltet das Ventil am Sollwert in jedem Zyklus um
- Betriebsarten Automatik, Manuell, Aus (Aus = Frostschutz, nicht stromlos)
- Fensterpause mit Wiederanlaufverzögerung
- Rückfall auf Frostschutz bei Sensorausfall
- Nachvollziehbare Schaltentscheidungen: warum wurde geschaltet oder nicht
- ~~Vergleichsbetrieb gegen das Altsystem, mit Abweichungsbericht~~ — **auf Wunsch des
  Projektinhabers übersprungen**, siehe unten
- Scharfschalten mit dem Altsystem als Rückfallebene
- Datenübernahme aus `rooms`, `thermostate`, `heizung_conf`

### Aufgaben

Vier davon wurden zunächst bei unscharfer Regelung gebaut. Inzwischen gilt die dreistufige
Freigabe: unscharf nur Protokoll, scharf vor Neustart weiterhin keine Ausgabe, scharf nach
Neustart Sollwerte an selbstregelnde Thermostatventile und Ein/Aus-Befehle an gewöhnliche
Aktoren.

- [x] 1 Regelentscheidung als reine Funktion, umfassend getestet *(in Phase 2 vorgezogen)*
- [x] 2 Hysterese und Mindestschaltdauer
- [x] 3 Fensterpause und Sensorausfall
- [x] 4 Schaltprotokoll mit Begründung *(als Schattenprotokoll)*
- [x] ~~5 Vergleichsbetrieb~~ — **übersprungen.** Ursprünglich geplant: mehrtägiger
      Schattenbetrieb gegen das Altsystem, dann scharf mit Abweichungsbericht. Geändert
      auf Wunsch des Projektinhabers — thermoctl lief als Erstes direkt scharf an der
      echten Heizung, das Altsystem als Rückfallebene. Details in [STATUS.md](STATUS.md).
- [~] 6 Datenübernahme — die Umwandlung des Stundenrasters steht als reine Funktion, die
      Übernahme selbst braucht die Altdatenbank
- [x] 7 Scharfschalten hinter einem Schalter, jederzeit umkehrbar
- [ ] 8 Ablösung: Heizungsteil aus `vm130-nginx`, die vier Skripte aus dem Alt-Repo —
      läuft parallel als Rückfallebene, noch nicht abgeschaltet

**`setting.control_armed` allein belegt keine körperliche Wirkung.** Die beim Start
gebauten zweiten Riegel müssen ebenfalls offen sein; erst dann erreichen Sollwerte und
Ein/Aus-Befehle die verdrahteten Aktoren.

### Risiken

- **In dieser Phase soll eine echte Wohnung geheizt werden.** Fehler haben körperliche Folgen; die Regellogik
  ist besonders sorgfältig zu prüfen.
- Die Umwandlung des unregelmäßigen Stundenrasters des Altsystems in Schaltpunkte ist
  ungeklärt und braucht echte Daten.
- **Bis der Cutover abgeschlossen ist, bleibt `vm130-nginx` unverändert die Rückfallebene.**

**Fertig, wenn** thermoctl über eine Heizperiode zuverlässig regelt und das Altsystem
abgeschaltet ist.

---

## Phase 5 — Integrationen und Veröffentlichung

**Ziel:** Alles, was „veröffentlichbar" praktisch bedeutet.

### Features

- Home-Assistant-Anbindung über MQTT-Discovery, mit sauber entworfener Topic-Struktur
- MCP-Server als dritter Adapter auf die dann stabile Domänenlogik
- Öffentliche API-Dokumentation
- Setup-Assistent für Fremde: Datenbankwahl, Broker, erste Zone
- Self-Hosting-Dokumentation, Beispiel-Compose, Aktualisierungspfad
- Benachrichtigungen bei Störungen
- Repository öffentlich schalten

### Aufgaben

- [x] 1 Neue MQTT-Topic-Struktur samt Discovery — **gebaut und angeschlossen**; das
      Veröffentlichen hängt am selben Riegel wie das Schalten, siehe [mqtt.md](mqtt.md)
- [ ] 2 Altes Topic-Schema abkündigen — *wartet auf den Cutover*
- [x] 3 MCP-Server — 16 Werkzeuge über derselben Domänenlogik, [Doku](mcp.md)
- [x] 4 API-Dokumentation — [docs/api.md](api.md)
- [ ] 5 Setup-Assistent erweitern — *gehört zu Phase 3*
- [x] 6 Self-Hosting-Dokumentation und Beispiel-Compose — [docs/self-hosting.md](self-hosting.md)
- [x] 7 Benachrichtigungswege — Log immer, Webhook optional; gemeldet wird der Wechsel
- [x] 8 Sicherheitsdurchsicht — [docs/sicherheitsdurchsicht.md](sicherheitsdurchsicht.md), vor dem Öffentlichschalten erneut durchzugehen
- [ ] 9 Repository öffentlich schalten — **Entscheidung des Projektinhabers**

Vorgezogen wurde, was nicht auf Phase 4 wartet.

**Fertig, wenn** jemand ohne Zutun des Autors eine eigene Instanz aufsetzen kann.

---

## Nach Phase 3 dazugekommen

Nicht in den ursprünglichen Phasen vorgesehen, auf Zuruf gebaut. Alles davon steht und ist
gegen beide Datenbanken geprüft; was noch an der echten Anlage nachzuweisen ist, steht in
[STATUS.md](STATUS.md).

- [x] **Bediengeräte konfigurierbar** — jedes Zigbee2MQTT-Gerät unter `/controllers`:
      lesbare Merkmale auf Sollwert oder Betriebsart legen, schreibbare mit Sensor-,
      Zonen- oder festen Werten belegen. Tastenbelegung dort statt an der Zone.
- [x] **Zigbee-Heizkörperthermostate (WT-A03E, BTH-RA)** — als Aktor zuweisbar; ein
      Thermostatventil ist kein Schalter und wird über Sollwert und, wo vorhanden,
      `system_mode` gefahren.
- [x] **Thermostatventile können selbst regeln** — dann schreibt thermoctl nur Soll- und,
      wo das Gerät es annimmt, Ist-Temperatur; das Ventil regelt damit gegen den Raum
      statt gegen den Heizkörper, an dem sein eigener Fühler sitzt.
- [x] **Sonnenprognose-Absenkung** — senkt den Sollwert, wenn in den nächsten Stunden Sonne
      zu erwarten ist. Je Zone gewichtet, begrenzt, Frostschutz bleibt Untergrenze.
      Optional und ab Werk aus.
- [x] **Wandtablet-Dashboard** unter `/kiosk` — mit widerrufbarem Kiosk-Token statt ohne
      Anmeldung, siehe [self-hosting.md](self-hosting.md#7a-ein-wandtablet).
- [x] **Alles außer der Prosa auf Englisch** — Bezeichner, Endpunkte, MQTT-Topics,
      Vorlagen, CSS, Kommentare und Testnamen. Sichtbarer Text bleibt deutsch.
- [x] **Testabdeckung auf 100 %** — jede Zeile geprüft oder mit begründeter Ausnahme; die
      CI-Schwelle steht entsprechend.
- [x] **PI-Regelung (Beta)**, seit `v0.5.0`, je Zone einschaltbar, ab Werk aus — ein
      Proportional-Integral-Regler statt Hysterese, nur für gewöhnliche Schaltaktoren.
      Seit `v0.6.0` ist die Eignungsprüfung gerätegenau: ein selbstregelndes
      Thermostatventil schließt PI nicht mehr für die ganze Zone aus, sondern darf
      neben einem Schaltaktor stehen. Siehe
      [self-hosting.md](self-hosting.md#6a-pi-regelung-beta-und-relaisverschleiss).
- [x] **Relaisverschleiß-Statistik** unter `/relay-wear`, seit `v0.5.0` — Schaltspiele je
      Gerät und Tag mit Jahreshochrechnung, auch ohne PI nützlich. Die zugrundeliegende
      angenommene Relais-Lebensdauer ist seit `v0.6.0` einstellbar
      (`assumed_relay_lifetime_operations`, Vorgabe 500.000) — ausdrücklich eine
      Annahme, keine Herstellerangabe.
- [x] **Browsertests**, seit `v0.6.0`, ausschließlich örtlich unter `browser_tests/` —
      dreizehn Playwright-Tests, die prüfen, was ein HTTP-Test nicht sieht: Stylesheet,
      Browserkonsole, echte Zeigergesten. Nicht in der CI und nicht in der gewöhnlichen
      Suite.

---

## Was über alle Phasen gilt

- **Nichts hart verdrahtet.** Keine Geräte-IDs, Namen, Adressen oder Zugangsdaten im Code.
- **Keine Secrets im Repo**, auch nicht als Beispiel.
- **Beide Datenbanken**, jede Schemaänderung als Migration.
- **Domänenlogik gehört nicht in Adapter** — eine Regel, einmal implementiert.
- **Debuggbarkeit ist ein Ziel**, kein Nebenprodukt.
- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unangetastet.**
