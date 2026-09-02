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
| 4 — Regelkreis und Cutover | Alle sieben Aktortypen der echten Anlage verdrahtet: Zigbee2MQTT-Aktoren, Meross-Steckdosen, Zigbee2MQTT-Thermostatventile. Kreuzreview der Verdrahtung fand drei Befunde, alle behoben — siehe unten |
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

## Kreuzreview der Aktorverdrahtung — drei Befunde, alle behoben

Der Projektinhaber hat den mehrtägigen Schattenbetrieb übersprungen; die Verdrahtung
läuft als Erstes an einer echten Heizung (vier Meross-Steckdosen, drei
Zigbee2MQTT-Thermostatventile). Ein Kreuzreview prüfte deshalb vor dem ersten
scharfen Betrieb noch einmal gezielt, ob geschaltet wird, wenn es soll — mit
„Nein" beantwortet. Alle drei Befunde sind jetzt behoben:

- **Ein gescheiterter Befehl wurde nie wiederholt** (schwer). Der Zwischenspeicher
  „nur bei Änderung senden" schrieb bei jedem Ausgang, auch bei einem gescheiterten
  — der nächste Zyklus mit unveränderter Entscheidung übersprang das Gerät dann
  komplett, kein neuer Versuch, kein neuer Log-Eintrag. Bei einer kalten, dauerhaft
  unterversorgten Zone (deren Entscheidung sich gerade *nicht* ändert) hätte das im
  Januar ein eingefrorenes Rohr bedeutet. Jetzt trägt der Zwischenspeicher das
  Ergebnis im Schlüssel: ein gescheiterter Befehl wird jeden scharfen Zyklus erneut
  versucht, aber nur einmal pro Ausfallepisode geloggt. Begründung in
  [offene-entscheidungen.md](offene-entscheidungen.md).
- **Die Entwertung der Meross-Sitzung war nie verdrahtet** (schwer). `app.py` übergab
  `meross_transport`, aber nicht `meross_session_cache`, an den
  Veröffentlichungszyklus — `invalidate_meross_session()` war damit im echten
  Betrieb unerreichbar, eine tote Meross-Verbindung blieb bis zu sechs Stunden als
  „gültig" im Zwischenspeicher stehen. Jetzt durchgereicht, mit einem Test, der den
  tatsächlichen Aufruf in `app.py` prüft, nicht nur die Funktion isoliert.
- **Der Meross-Weg hatte nur einen Riegel** (mittel). `MqttClient` friert seinen
  eigenen Riegel beim Start ein; der Meross-Weg (`MerossSwitch`) prüfte nur den
  Laufzeit-Riegel. Jetzt bekommt `MerossSwitch` denselben eingefrorenen Riegel
  (`app.state.sending_allowed`, wiederverwendet statt eines zweiten unabhängigen
  Werts). Begründung in [offene-entscheidungen.md](offene-entscheidungen.md).

Zusätzlich abgesichert: eine Regression, bei der `ensure_transport()` in die offene
Schreibtransaktion des Schattenzyklus verschoben wird (genau der Fehler, der einmal
die SQLite-Datei 40 Sekunden gesperrt hatte) — bei der die gesamte Suite trotzdem grün
blieb. Ein neuer Test prüft jetzt die Eigenschaft selbst („keine Schreibtransaktion
offen während des Netzaufrufs"), nicht die Aufrufreihenfolge im Quelltext.

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
Behebung der vier gemeldeten Anzeigefehler):

| | |
|---|---|
| Tests | 1491 unter SQLite, 1490 plus ein Skip unter MariaDB |
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

## Vier gemeldete Anzeigefehler behoben

- **Meross-Geräte galten dauerhaft als „hat sich noch nie gemeldet"**, obwohl der
  stündliche Abgleich sie fand und die Wolke sie als online meldete. Ursache:
  `web/device_views.py` sah für jedes Gerät nur `device_health.last_payload_at`
  an — das schreibt ausschliesslich die Zigbee2MQTT-Aufnahme
  (`services/ingest.py`) bei einer eingehenden MQTT-Nachricht, und ein
  Meross-Gerät schickt nie eine. Die Übersicht liest jetzt für ein Meross-Gerät
  `device.last_seen_at` (geschrieben vom stündlichen Abgleich,
  `services/meross_discovery.py::save_devices`) und wählt die dazu passende
  Formulierung: „abgeglichen" statt „gemeldet" — Stille bedeutet bei Meross, dass
  der eigene Abgleich nicht lief oder die Wolke das Gerät nicht mehr nennt, nicht,
  dass das Gerät selbst schweigt. Die Stille-Schwelle des selbstberichtenden
  Sensors (`default_sensor_timeout_seconds`) passt nicht zu einem stündlichen
  Abgleich; Meross bekam eine eigene, in `domain/device_survey.py`
  begründete Schwelle (`MEROSS_SILENT_AFTER_SECONDS`, zwei Abgleichzyklen — ein
  einzelner ausgefallener Durchlauf soll nicht sofort als defektes Gerät
  erscheinen). `MerossDevice.online` bleibt bewusst ohne eigene Spalte: es
  entscheidet weiterhin nur, ob `last_seen_at` bei diesem Durchlauf vorrückt, und
  ein dauerhaft offline gemeldetes Gerät wird dadurch — korrekt — irgendwann
  selbst als still erkannt.
- **Die Statistik schnitt Tage an UTC-Mitternacht** — ein lokaler Tag begann
  dadurch um 01:00 beziehungsweise 02:00 in `Europe/Berlin`. Neu:
  `domain/time.py::local_day_start_utc` liefert die UTC-Entsprechung einer
  lokalen Tagesgrenze; `domain/statistics.py::heating_periods` bucketiert danach
  statt nach UTC-Datum, `web/control_views.py` baut die Abfragegrenze ebenso.
  Gegen die Sommerzeit getestet: ein Tag mit 23 beziehungsweise 25 Stunden wird
  vollständig und richtig einem einzigen lokalen Kalendertag zugeschlagen.
- **Der Datumsfilter des Auditprotokolls deutete lokale Eingaben als
  UTC-Tagesgrenzen** — dieselbe Klasse Fehler, dieselbe Lösung
  (`local_day_start_utc`) in `web/audit_views.py`.
- **Eine laufende Übersteuerung erschien als „gerade eben"** — die Startseite
  beschrieb ihr **Ende** mit dem Vergangenheitsfilter `age`, der jeden
  Zukunftszeitpunkt als „gerade eben" las. `age_in_words`
  (`web/__init__.py`) unterscheidet jetzt Vergangenheit („vor 3 Minuten") von
  Zukunft („noch 42 Minuten"); geprüft, dass `age` sonst nirgends auf einen
  Zukunftszeitpunkt trifft — `override.ends_at` auf der Startseite war die
  einzige Stelle.

## Sensorstörungsmeldungen

Sensorstörungen und ihre Entwarnung erreichen jetzt neben Log und optionalem Webhook auch
Home Assistant: je Zone als binärer Problemsensor mit Meldungstext in den Attributen. Bei
einem veralteten Temperaturwert nennt die Meldung ausdrücklich, dass die Zone bis auf
Weiteres gegen ihren konkreten Frostschutz-Sollwert regelt. Der Meldeweg ist unabhängig
von den beiden Schalt-Riegeln und läuft nach Abschluss der Datenbanktransaktion.

## Offen, unabhängig von der Freigabe

**Zeitzone — fachliche Grenzfehler, keine Anzeigefragen:**

## Offen, unabhängig von der Freigabe

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
