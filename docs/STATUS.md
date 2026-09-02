# Stand

Letzte Aktualisierung: 2026-09-02

**Diese Datei sagt, was jetzt gilt — sonst nichts.** Wie es dazu kam, welche Fehler wie
gefunden wurden und warum etwas so entschieden ist, steht in [verlauf.md](verlauf.md).
Die Trennung gibt es, seit ein Freigabe-Review vier Aussagen in dieser Datei widerlegen
konnte: Sie war auf über tausend Zeilen gewachsen und enthielt gleichzeitig aktuelle und
längst überholte Angaben — „nichts ist scharf", „1024 Tests, 98,55 %", „`control_armed`
wird nirgends gesetzt", „es gibt keine Geräteerkennung für Meross". Alles vier stimmte
einmal und stand noch da.

## Wo das Projekt steht

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen, veröffentlicht als `v0.1.0` |
| 1a — Nacharbeiten | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | gebaut; der Nachweis über mehrere Tage braucht die echte Anlage |
| 3 — Konfigurations-Oberfläche | abgeschlossen |
| 4 — Regelkreis und Cutover | Alle sieben Aktortypen der echten Anlage verdrahtet: Zigbee2MQTT-Aktoren, Meross-Steckdosen, Zigbee2MQTT-Thermostatventile |
| 5 — Integrationen und Veröffentlichung | Meross und Zeitplan-Bedienung erledigt, Freigabe von 0.2.2 offen |

## Was geschaltet wird — genau

Diese Frage ist mehrfach zu grob beantwortet worden, in beide Richtungen. Der Stand:

- **`setting.control_armed` steht in einer neuen Anlage auf `false`.** Solange bleibt
  jeder Weg zu einem Aktor zu.
- **`/control/arm` kann es öffnen**, mit eigenem Recht `control.arm`. Es ist also nicht
  wahr, dass „weiterhin nichts geschaltet wird" — das war eine Zusicherung ohne Deckung.
- **Der MQTT-Client trägt einen zweiten Riegel, der beim Start gebaut wird.** Wer scharf
  schaltet, muss den Dienst neu starten, bevor überhaupt etwas hinausgeht.
- **Sind beide Riegel offen**, bekommen selbstregelnde Thermostatventile ihren Sollwert
  veröffentlicht, **und jetzt auch ein gewöhnlicher (nicht selbstregelnder) Aktor an
  Zigbee2MQTT sein Ein/Aus** (`services/publishing.py::_send_actuator_switches`, neu):
  Rolle `actuator`, ohne `self_regulating`, mit der Fähigkeit `switch` — er bekommt, was
  `shadow_run.cycle()` zuletzt für seine Zone entschieden hat, nur bei Änderung, mit
  einem Eintrag im Schaltprotokoll für jeden Versuch (ausgeführt, unterdrückt oder
  gescheitert).
- **Ein Meross-Aktor bekommt jetzt ebenfalls seinen Befehl.** Das Schalten dort braucht
  eine Anmeldung gegen die Meross-Cloud; die läuft jetzt zwischengespeichert und
  außerhalb jeder Datenbanktransaktion (`services/meross_session.py`,
  `app.py::_shadow_loop`) — genau die Stelle, an der eine frühere Fassung die ganze
  SQLite-Datei bis zu 40 Sekunden gesperrt hatte. Lehnt die Cloud die Anmeldung ab, oder
  ist keine hinterlegt, schreibt jeder betroffene Zyklus stattdessen einen
  `failed`-Eintrag ins Schaltprotokoll, ohne den Zyklus selbst anzuhalten. Begründung
  und die gewählte Gültigkeitsdauer der Sitzung in
  [offene-entscheidungen.md](offene-entscheidungen.md).
- **Ein Zigbee2MQTT-Thermostatventil ohne `self_regulating`** (Fähigkeit `thermostat`
  statt `switch`, von thermoctls eigener Hysterese statt eigener Regelung gesteuert)
  bekommt jetzt ebenfalls seinen Befehl, über `Zigbee2MqttThermostat`: den aufgelösten
  Zonensollwert und, wo das Gerät `system_mode` als beschreibbar meldet, `heat`/`off`
  dazu. Ein Gerät ohne `system_mode` (Bosch BTH-RA) wird stattdessen auf seinen
  niedrigsten Sollwert gefahren. Der frühere Blocker-Eintrag vom 2026-09-01 in
  [offene-entscheidungen.md](offene-entscheidungen.md) ist als behoben vermerkt.

## Das Schaltprotokoll

Neu: `device_command` zeichnet jeden Befehl auf, der an ein Gerät hinausging oder im
Trockenlauf unterdrückt oder verworfen wurde — Zeitpunkt, Zone, Gerät, Nutzlast, Ergebnis,
Begründung, Auslöser. Der bestehende Sollwert-Weg an selbstregelnde Thermostatventile
(`services/publishing.py::_send_self_regulating_valves`) schreibt dorthin; Ansicht unter
„Einstellungen → Schaltprotokoll" (`/device-commands`, Recht `audit.read`). Anders als
`shadow_decision` überlebt ein Eintrag das Löschen oder Umbenennen seiner Zone oder seines
Geräts (`SET NULL` plus Namens-Momentaufnahme statt CASCADE), und unterliegt keiner
automatischen Aufbewahrung — Begründung in
[offene-entscheidungen.md](offene-entscheidungen.md). REST und MCP ziehen noch nicht nach;
das war eine bewusste, im Auftrag benannte Entscheidung für diese Runde, keine Lücke, die
übersehen wurde.

Der Anlass: Der Projektinhaber will den Schattenbetrieb überspringen und direkt scharf
schalten. Damit ist dieses Protokoll die einzige Stelle, an der später nachvollziehbar ist,
was an der Heizung passiert ist — es stand deshalb vor der Verdrahtung der Aktoren (Phase 4)
an, nicht danach.

## Zahlen

Selbst nachgeprüft, nicht aus Berichten übernommen (Stand 2026-09-02, nach der
Meross- und Thermostatventil-Verdrahtung):

| | |
|---|---|
| Tests | 1473 unter SQLite, 1472 plus ein Skip unter MariaDB |
| Testabdeckung | 100 %, Mindestschwelle 100 % in der CI |
| Ruff, mypy strict | ohne Befund, 105 Quelldateien |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken |
| Container | baut; eine echte 0.2.0-Datenbank wurde darin hochgezogen, `/healthz` meldet 0.2.2 |

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`. Wer
die zweite setzt, läuft unbemerkt gegen SQLite und bekommt trotzdem einen grünen Lauf.

## v0.2.2 — freigegeben

**Sieben Freigabe-Reviews, sechs Ablehnungen, das siebte gibt frei.** Was die sechs
gefunden haben, steht im [CHANGELOG](../CHANGELOG.md) und ausführlich in
[verlauf.md](verlauf.md). Der Verlauf in Kürze: ein Regelungsfehler, eine
CSRF-Lücke in *jedem* ändernden Formular, ein Rückgängig, das einen veralteten
Schnappschuss annahm — und viermal dieselbe Klasse, Texte, die eine Ventilbewegung
versprechen, die es nicht gibt.

Das siebte Review hat sechs gezielte Mutationen über Regelung, Zeitplan, CSRF, Anzeige
und Rechte gesetzt; jedes Mal wurde **der inhaltlich richtige** Test rot. Dazu die
Angriffsliste gegen den Wirkungswächter (18 von 18) und die Migrationskette vorwärts,
rückwärts, vorwärts.

**Ein Befund blieb, bewusst offen gelassen:** Der Wirkungswächter erkennt deutsche
Komposita ohne Trennzeichen nicht (`Zirkulationspumpe`, `Ölbrenner`) und kennt reine
Temperaturaussagen („die Raumtemperatur steigt") gar nicht. Nachgeprüft: Keiner dieser
Begriffe kommt heute irgendwo vor — es ist also kein falscher Text, sondern eine Lücke im
Netz für künftige. Das gehört in die nächste Fassung, nicht in diese.

## Offen, unabhängig von der Freigabe

**Zeitzone — fachliche Grenzfehler, keine Anzeigefragen:**

- Die Statistik bildet Abfragegrenzen und Tages-Buckets an UTC-Mitternacht; ein lokaler
  Tag beginnt dadurch um 01:00 beziehungsweise 02:00.
- Der Datumsfilter des Auditprotokolls deutet lokale Eingaben als UTC-Tagesgrenzen.
- Die Startseite beschreibt das Ende einer laufenden Übersteuerung mit dem
  Vergangenheitsfilter `age` und zeigt deshalb „gerade eben".

**Geplant, nicht begonnen:** ein Komplettreview des Projekts — Mutationstest,
Zusicherungs-Audit, die Regelkette als Ganzes, Musterjagd, Messen vor Optimieren.
Der Plan steht in
[superpowers/plans/2026-09-01-komplettreview.md](superpowers/plans/2026-09-01-komplettreview.md).

**Was nur der Projektinhaber kann:**

- **Phase 2 wirklich abschliessen** — Schritt für Schritt in
  [inbetriebnahme-schattenbetrieb.md](inbetriebnahme-schattenbetrieb.md). Die Anlage muss
  über mehrere Tage laufen, bevor feststeht, dass plausible Ist-Temperaturen einlaufen
  und das Schattenprotokoll nachvollziehbare Entscheidungen zeigt.
- **Die Frostschutz-Entscheidung bestätigen**, bevor Phase 4 scharf schaltet — siehe
  [offene-entscheidungen.md](offene-entscheidungen.md). Sie hat körperliche Folgen.
- **Entscheiden, ob das Repository öffentlich werden soll.**
- **Den Tag setzen**, wenn 0.2.2 freigegeben ist — ein Tag veröffentlicht ein Abbild.
