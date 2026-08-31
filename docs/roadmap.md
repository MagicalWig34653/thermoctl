# Roadmap

Stand: 2026-08-29

Diese Roadmap führt zusammen, was im [Rahmenentwurf](superpowers/specs/2026-08-28-thermoctl-neubau-design.md)
in fünf Teilprojekte zerlegt ist, und konkretisiert es zu Features und Aufgaben. Sie ersetzt
den Rahmenentwurf nicht — bei Widerspruch gilt er.

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
| 2 | Geräte-Anbindung im Schattenbetrieb | **gebaut**, Laufzeit an der Anlage offen | Belegt gegen die echte Anlage, dass die Daten stimmen |
| 3 | Konfigurations-Oberfläche | **umgesetzt**, seither erweitert | Ende der SQL-Pflege — ab hier im Alltag nützlich |
| 4 | Regelkreis und Cutover | Logik gebaut, **nichts scharf** | Heizt wirklich; Altsystem wird abgelöst |
| 5 | Integrationen und Veröffentlichung | teilweise vorgezogen | Für Fremde aufsetzbar |

Die Reihenfolge ist nicht beliebig: Der Teil, der eine echte Heizung schaltet, kommt bewusst
zuletzt und erst mit Vergleichsdaten aus Phase 2.

---

## Phase 1 — Fundament ✅

Abgeschlossen am 2026-08-28. 22 Aufgaben, 142 Tests, Details in
[STATUS.md](STATUS.md) und im [Implementierungsplan](superpowers/plans/2026-08-28-teilprojekt-1-fundament.md).

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
      prüften — siehe [offene-entscheidungen.md](offene-entscheidungen.md)

---

## Phase 2 — Geräte-Anbindung im Schattenbetrieb

**Ziel:** Beweisen, dass Gerätedaten und Adressierung stimmen, **ohne die Heizung
anzufassen**. Es wird gelesen und protokolliert, nichts geschaltet.

### Features

- Sensor-Ingest aus Zigbee2MQTT: Ist-Temperaturen fortlaufend in die Zonen schreiben
- Geräteerkennung: bekannte Zigbee2MQTT-Geräte einlesen und nach Fähigkeiten klassifizieren
  *(Idee aus dem Konzept-Dokument)*
- Aktor-Adapter für Zigbee-Ventile — vollständig, aber im Trockenlauf
- Meross-Anbindung — Geräteerkennung **und** Schaltweg, gegen ein echtes Konto geprüft.
  Der Schattenzyklus gleicht die Geräteliste stündlich ab; geschaltet wird über MQTT.
  Ungeprüft bleibt bis Phase 4 nur das erste echte Schalten
- Fensterkontakte als Zustandsquelle *(Idee aus dem Konzept-Dokument)*
- Sensor-Timeout: ein ausbleibender Messwert wird als Störung erkannt, nicht ignoriert
- Messwert-Historie mit begrenzter Aufbewahrung
- Schattenprotokoll: Was **würde** geschaltet, mit Begründung — die Grundlage für den
  Vergleich in Phase 4

### Aufgaben

Geschärft im eigenen Zyklus, siehe [Spezifikation](superpowers/specs/2026-08-29-teilprojekt-2-geraete-schattenbetrieb-design.md)
und [Plan](superpowers/plans/2026-08-29-teilprojekt-2-geraete-schattenbetrieb.md).

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

- **Der MQTT-Topic-Vertrag ist gewachsen, nicht entworfen** (siehe Bestandsaufnahme). Ob die
  alten Topics übergangsweise mitbedient werden, entscheidet diese Phase.
- Die Meross-Cloud-API ist eine Fremdabhängigkeit ohne Zusicherung.
- Zugangsdaten für Broker und Meross gehören in die Konfiguration, niemals ins Repo.

**Fertig, wenn** über mehrere Tage plausible Ist-Temperaturen aller Zonen einlaufen und das
Schattenprotokoll nachvollziehbare Entscheidungen zeigt, ohne dass je geschaltet wurde.

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

Geschärft im eigenen Zyklus, siehe [Spezifikation](superpowers/specs/2026-08-29-teilprojekt-3-konfigurationsoberflaeche-design.md)
und [Plan](superpowers/plans/2026-08-29-teilprojekt-3-konfigurationsoberflaeche.md).

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

**Ziel:** Der Teil, der wirklich heizt. Kommt zuletzt und mit Vergleichsdaten aus Phase 2.

### Features

- Regelschleife mit **Hysterese und Mindestschaltdauer** — im Altsystem ein echter Defekt:
  dort schaltet das Ventil am Sollwert in jedem Zyklus um
- Betriebsarten Automatik, Manuell, Aus (Aus = Frostschutz, nicht stromlos)
- Fensterpause mit Wiederanlaufverzögerung
- Rückfall auf Frostschutz bei Sensorausfall
- Nachvollziehbare Schaltentscheidungen: warum wurde geschaltet oder nicht
- Vergleichsbetrieb gegen das Altsystem, mit Abweichungsbericht
- Scharfschalten mit dem Altsystem als Rückfallebene
- Datenübernahme aus `rooms`, `thermostate`, `heizung_conf`

### Aufgaben

Vier davon sind bereits gebaut — **ohne dass etwas scharf geschaltet wäre.** Der Auftrag für
den autonomen Lauf deckte das ausdrücklich: „Bau die Logik und die Tests, aber schalte
nichts scharf."

- [x] 1 Regelentscheidung als reine Funktion, umfassend getestet *(in Phase 2 vorgezogen)*
- [x] 2 Hysterese und Mindestschaltdauer
- [x] 3 Fensterpause und Sensorausfall
- [x] 4 Schaltprotokoll mit Begründung *(als Schattenprotokoll)*
- [~] 5 Vergleichsbetrieb — die lesende Grundlage steht, die Ablage der Altwerte ist eine
      offene Entscheidung, siehe [offene-entscheidungen.md](offene-entscheidungen.md)
- [~] 6 Datenübernahme — die Umwandlung des Stundenrasters steht als reine Funktion, die
      Übernahme selbst braucht die Altdatenbank
- [ ] 7 Scharfschalten hinter einem Schalter, jederzeit umkehrbar
- [ ] 8 Ablösung: Heizungsteil aus `vm130-nginx`, die vier Skripte aus dem Alt-Repo

**`setting.control_armed` steht auf `false` und wurde in keinem dieser Schritte gesetzt.**

### Risiken

- **Hier wird eine echte Wohnung geheizt.** Fehler haben körperliche Folgen; die Regellogik
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
- [x] 3 MCP-Server — sieben Werkzeuge über derselben Domänenlogik, [Doku](mcp.md)
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

---

## Was über alle Phasen gilt

- **Nichts hart verdrahtet.** Keine Geräte-IDs, Namen, Adressen oder Zugangsdaten im Code.
- **Keine Secrets im Repo**, auch nicht als Beispiel.
- **Beide Datenbanken**, jede Schemaänderung als Migration.
- **Domänenlogik gehört nicht in Adapter** — eine Regel, einmal implementiert.
- **Debuggbarkeit ist ein Ziel**, kein Nebenprodukt.
- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unangetastet.**
