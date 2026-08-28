# Roadmap

Stand: 2026-08-28

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
| 1 | Fundament | **umgesetzt** | Nichts sichtbar, aber alles Weitere hängt daran |
| 1a | Nacharbeiten | läuft | Oberfläche benutzbar, Auflagen des Reviews erledigt |
| 2 | Geräte-Anbindung im Schattenbetrieb | offen | Belegt gegen die echte Anlage, dass die Daten stimmen |
| 3 | Konfigurations-Oberfläche | offen | Ende der SQL-Pflege — ab hier im Alltag nützlich |
| 4 | Regelkreis und Cutover | offen | Heizt wirklich; Altsystem wird abgelöst |
| 5 | Integrationen und Veröffentlichung | offen | Für Fremde aufsetzbar |

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

- [ ] Bootstrap und HTMX lokal einbinden; Templates auf ein gemeinsames Grundgerüst bringen
      *(der Rahmenentwurf setzt beides voraus, keine Aufgabe hatte es verlangt)*
- [ ] Eingabefehler führen zum Formular zurück statt zu `500`
- [ ] Globaler `Forbidden`-Handler statt Übersetzung in jeder Route
- [ ] Gemeinsame CSRF-Abhängigkeit statt Prüfung von Hand je Route — **vor Phase 3**, dort
      entstehen die ändernden Ansichten
- [ ] Test für das Startverhalten: genau ein Einrichtungs-Token, beim zweiten Start keines mehr

---

## Phase 2 — Geräte-Anbindung im Schattenbetrieb

**Ziel:** Beweisen, dass Gerätedaten und Adressierung stimmen, **ohne die Heizung
anzufassen**. Es wird gelesen und protokolliert, nichts geschaltet.

### Features

- Sensor-Ingest aus Zigbee2MQTT: Ist-Temperaturen fortlaufend in die Zonen schreiben
- Geräteerkennung: bekannte Zigbee2MQTT-Geräte einlesen und nach Fähigkeiten klassifizieren
  *(Idee aus dem Konzept-Dokument)*
- Aktor-Adapter für Meross-Schalter und Zigbee-Ventile — vollständig, aber im Trockenlauf
- Fensterkontakte als Zustandsquelle *(Idee aus dem Konzept-Dokument)*
- Sensor-Timeout: ein ausbleibender Messwert wird als Störung erkannt, nicht ignoriert
- Messwert-Historie mit begrenzter Aufbewahrung
- Schattenprotokoll: Was **würde** geschaltet, mit Begründung — die Grundlage für den
  Vergleich in Phase 4

### Aufgaben (Vorschlag)

1. MQTT-Anbindung: Verbindung, Wiederverbindung, TLS, Zugangsdaten aus der Konfiguration
2. Topic-Vertrag lesen: `zigbee2mqtt/<gerät>` abonnieren, Nutzlasten robust auswerten
3. Messwert-Modell und Ingest-Pfad samt Zonenzuordnung
4. Geräteerkennung und Klassifikation nach Fähigkeiten
5. Meross-Adapter (Cloud-API) im Trockenlauf
6. Zigbee-Ventil-Adapter im Trockenlauf
7. Fensterkontakte einlesen
8. Sensor-Timeout und Störungszustände
9. Schattenprotokoll mit nachvollziehbarer Begründung je Entscheidung
10. Geräteübersicht in der Oberfläche (lesend)

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

### Aufgaben (Vorschlag)

1. Gemeinsame Formularbausteine, Fehlerdarstellung, CSRF-Abhängigkeit
2. Zonenverwaltung
3. Gerätezuordnung samt Tausch
4. Modi und Sollwerte
5. Zeitplan-Editor
6. Übersteuerung aus der Oberfläche
7. Regelparameter je Zone
8. Rechte- und Tokenverwaltung vervollständigen
9. Audit-Ansicht
10. Übersichtsseite mit Ist, Soll und Zustand je Zone

**Fertig, wenn** eine vollständige Anlage ohne einen einzigen SQL-Befehl eingerichtet werden
kann.

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

### Aufgaben (Vorschlag)

1. Regelentscheidung als reine Funktion, umfassend getestet
2. Hysterese und Mindestschaltdauer
3. Fensterpause und Sensorausfall
4. Schaltprotokoll mit Begründung
5. Vergleichsbetrieb und Abweichungsbericht
6. Datenübernahme aus dem Altschema
7. Scharfschalten hinter einem Schalter, jederzeit umkehrbar
8. Ablösung: Heizungsteil aus `vm130-nginx`, die vier Skripte aus dem Alt-Repo

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

### Aufgaben (Vorschlag)

1. Neue MQTT-Topic-Struktur samt Discovery
2. Altes Topic-Schema abkündigen
3. MCP-Server
4. API-Dokumentation
5. Setup-Assistent erweitern
6. Self-Hosting-Dokumentation
7. Benachrichtigungswege
8. Sicherheitsdurchsicht vor der Veröffentlichung
9. Repository öffentlich schalten

**Fertig, wenn** jemand ohne Zutun des Autors eine eigene Instanz aufsetzen kann.

---

## Was über alle Phasen gilt

- **Nichts hart verdrahtet.** Keine Geräte-IDs, Namen, Adressen oder Zugangsdaten im Code.
- **Keine Secrets im Repo**, auch nicht als Beispiel.
- **Beide Datenbanken**, jede Schemaänderung als Migration.
- **Domänenlogik gehört nicht in Adapter** — eine Regel, einmal implementiert.
- **Debuggbarkeit ist ein Ziel**, kein Nebenprodukt.
- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unangetastet.**
