# Stand

Letzte Aktualisierung: 2026-09-02

**Diese Datei sagt, was jetzt gilt — sonst nichts.** Wie es dazu kam, welche Fehler wie
gefunden wurden und warum etwas so entschieden ist, steht in [verlauf.md](verlauf.md).
Die Trennung gibt es, seit ein Freigabe-Review vier Aussagen in dieser Datei widerlegen
konnte: Sie war auf über tausend Zeilen gewachsen und enthielt gleichzeitig aktuelle und
längst überholte Angaben — „nichts ist scharf", „1024 Tests, 98,55 %", „`control_armed`
wird nirgends gesetzt", „es gibt keine Geräteerkennung für Meross". Alles vier stimmte
einmal und stand noch da.

## v0.3.0 — die Fassung, in der thermoctl schaltet

Alle vier Aktorwege sind verdrahtet: Zigbee2MQTT-Schalter, Zigbee-Thermostatventile,
Meross-Steckdosen, selbstregelnde Ventile. Bis 0.2.2 landete jede Regelentscheidung im
Schattenprotokoll und sonst nirgends.

**Der Trockenlauf bleibt die Vorgabe**, und das Scharfschalten wirkt erst nach einem
Neustart — der zweite Riegel wird beim Prozessstart gebaut. Die Anleitung dafür steht in
[scharfschalten.md](scharfschalten.md).

**Kein Vergleichsbetrieb.** Der mehrtägige Schattenbetrieb gegen das Altsystem wurde auf
Wunsch des Projektinhabers übersprungen. Dieser Code läuft als Erstes an einer echten
Heizung.

### Mutationstest, Runde 1

Erstmals gemessen, was die Tests wirklich prüfen, statt nur welche Zeilen sie ausführen —
auf `domain/control_loop.py` und `services/shadow_run.py`:

| | Mutanten | überlebt |
|---|---:|---:|
| `control_loop.py` | 137 | 15 = 11 % |
| `shadow_run.py` | 367 | **180 = 49 %** |

Die 49 Prozent sind die Zahl, wegen der diese Runde gemacht wurde. Sie ist nicht so
schlimm, wie sie aussieht — 127 der Überlebenden sind Mutationen an Typangaben und
SQLAlchemy-Ausdrücken, die kein Verhalten ändern —, aber **64 waren echte Testlücken**
und sind jetzt mit inhaltlichen Tests geschlossen. Übrig bleiben vier nachweislich
gleichwertige Mutanten, jeder einzeln begründet abgelegt.

Bezogen auf die 242 tatsächlich wirksamen Mutanten: 4 überleben, also 1,7 Prozent.
**Echte Fehler in der Regellogik: keine.** Alle Befunde waren Lücken in den Tests, nicht
im Code — das ist die beruhigende Hälfte des Ergebnisses.

Wiederholbar über `cosmic-ray-control-loop.toml` und `cosmic-ray-shadow-run.toml`; die
Einzelbewertung steht in `cosmic-ray-stage1-assessment.md`. Bewusst nicht in der CI — zu
langsam für jeden Lauf.

## Wo das Projekt steht

Die Hauptnavigation zeigt angemeldeten Benutzern nur Ziele, die sie tatsächlich öffnen
können. Zonenfilternde Übersichten erscheinen bereits bei einem passenden Recht für eine
einzelne Zone; anlagenweite Seiten erst bei einem anlagenweiten Recht. Die Zuordnung von
Ziel und Recht steht zentral in `web/navigation.py`, und ein Wächtertest vergleicht sie mit
der tatsächlichen Rechteprüfung der Zielansicht. Das Kiosk hat weiterhin seine getrennte
Navigation und sein eigenes Rechtemodell.

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
| Tests | 1556 unter SQLite, 1555 plus ein Skip unter MariaDB |
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

## Ein Fehler in der Regelkette — der Ventilschutz-Marker hob den Taktschutz auf

Der erste echte Fehler in der Regellogik, den dieses Projekt gefunden hat. Alles zuvor
waren Testlücken.

Die Ausnahme von der Mindestschaltdauer hing an `valve_protection_active`. Dieses Feld
sagt aber nur, dass der **Marker** gesetzt ist, nicht dass der Ventilschutz auch noch
entscheidet. Verliert Regel 7 mitten im Lauf — durch Übersteuerung, Betriebsart „aus"
oder Sensorausfall —, bleibt der Marker bis zum Ablauf der Laufdauer gesetzt. In diesem
Fenster entschied die gewöhnliche Hysterese, **und die Mindestschaltdauern waren
ausgehebelt**: bei 60 Sekunden Zykluszeit und 10 Minuten Laufdauer bis zu zehn
ungebremste Schaltvorgänge an einem echten Relais. Genau der Taktschutz, den das
Altsystem nicht hatte.

`decide()` berechnet `protection_allowed` jetzt einmal oberhalb von Regel 5; beide Regeln
teilen sich dieselbe Bedingung für „der Schutzlauf gewinnt gerade". Der echte Schutzlauf
bleibt befreit, auch nach einem Neustart, wo der persistierte Marker die einzige
Erinnerung an ihn ist. Ein zweiter Fall hat sich mit erledigt: Kommt die Übersteuerung
während des Laufs, bleibt der Heizkörper jetzt bis zum Ablauf der Mindest-**Einschalt**dauer
an, statt sofort abzuschalten.

### Die Regelkette als vollständige Zustandstabelle

`tests/test_control_loop_state_table.py` prüft die Vorrangkette über acht Achsen
erschöpfend: 3.888 Rohkombinationen, **2.376 erreichbare geprüft**, 1.512 begründet
ausgeschlossen (ohne Quelle gibt es keinen Messwert relativ zur Hysterese; ein Sensor
`ok` oder `veraltet` hat einen). Die Erwartung ist von Hand aus der Spezifikation
übertragen, nicht aus `decide()` abgeleitet.

Ihre Prüfkraft ist gemessen, nicht behauptet — vier gezielte Sabotagen an
`control_loop.py`:

| Sabotage | gefangen | Zeilen |
|---|---|---:|
| Regel 5 wieder nur vom Marker ausgenommen | ja | 100 |
| Obere Hysteresekante `>` statt `>=` | ja | 24 |
| Übersteuerung aus `protection_allowed` entfernt | ja | 44 |
| Fenster-offen hinter die Mindestschaltdauer verschoben | ja | 340 |

Die zweite Zeile ist der Grund für die beiden Messwert-Ausprägungen genau **auf** den
Bandkanten: Vorher lagen alle Messwerte 1,0 K neben dem Sollwert bei 0,5 K Hysterese, und
eine vertauschte Vergleichsrichtung an der Kante blieb unbemerkt.

## Die Anlage läuft scharf — und was der Projektinhaber daraufhin entschieden hat

**Seit dem 2026-09-02 läuft v0.3.0 an der echten Heizung**, zum Zeitpunkt dieser Notiz
seit rund fünf Stunden, ohne Anlass zum Abschalten. Das Altsystem läuft im
Parallelbetrieb weiter und bleibt die Rückfallebene.

Auf die offenen Punkte hat der Projektinhaber entschieden:

| Punkt | Entscheidung | Stand |
|---|---|---|
| Ventilschutz wurde zu dauerhaftem Heizen | Mindest-Einschaltdauer gilt für einen Schutzlauf nicht | umgesetzt |
| Aufbewahrung `shadow_decision` | ein Jahr | umgesetzt, Vorgabe 365 Tage |
| PI-Regelung | optional je Zone, nur Schaltaktoren, zunächst Beta | Spezifikation liegt vor, **nicht gebaut** |
| MQTT-Broker | EMQX mit Authentifizierung und Rechten ist vorhanden | dokumentiert, nicht erzwingbar |
| Repository öffentlich | erst mit einer zumutbaren, getesteten Fassung | offen |

### Sicherheitsbefunde: sechs behoben

Aus der Durchsicht vom 2026-09-02, zusätzlich zu den vier Rechtefehlern:

- **Anmeldeversuche konnten den Regelzyklus anhalten.** `time.sleep` und Argon2id liefen
  im async-Handler auf derselben Ereignisschleife wie die Regelung. Jetzt `await` und ein
  Thread. Der Fehlversuchszähler ist gedeckelt.
- **Ein Passwortwechsel beendet jetzt die anderen Sitzungen**, die eigene bleibt. Dazu
  „Andere Sitzungen beenden" unter `/users` — den Weg hatte der Docstring von
  `set_password` behauptet, ohne dass es ihn gab.
- **Das Einrichtungs-Token läuft nach einer Stunde ab.** Es steht weiterhin absichtlich im
  Log — das ist der einzige Kanal dafür —, aber eines aus einem alten Log legt keinen
  Administrator mehr an.
- **Meross bestätigt jetzt den Zustand, nicht nur den Befehl.** Namespace und Signatur der
  Antwort werden geprüft, und wo sie einen Schaltzustand mitliefert, muss er zum Befehl
  passen. Eine nicht verifizierbare Bestätigung gilt als Fehlschlag und wird wiederholt.
- **Der Webhook folgt keiner Weiterleitung mehr** und gibt den `Authorization`-Header
  damit nicht an ein fremdes Ziel weiter.
- **Sichere Cookies** waren bereits vollständig umgesetzt; alle sechs Cookies beachten den
  Schalter. Was fehlt, ist `THERMOCTL_SECURE_COOKIES=true` in der Installation. Neu ist
  ein Wächter, der jeden `set_cookie`-Aufruf im Quelltext über den AST prüft.

**Weiterhin offen und benannt:** der MQTT-Befehlsweg (Broker-Sache, siehe
[mqtt.md](mqtt.md)), die Vorgabe-Bindung an `0.0.0.0` ohne TLS (der Betrieb hier läuft
hinter einem Reverse Proxy), und dass ein `SET` ohne anschließendes `GET` nicht beweist,
dass ein Relais physisch geschaltet hat.

## Runde 6 — ein Refactoring, das seine Prüfkraft nachweist

`_process_zone` in der Regelschleife ist in drei benannte Funktionen zerlegt:
`_override_active`, `_advance_valve_protection`, `_apply_decision_to_state`. Woran die
Vermischung erkennbar war: Die lokale Variable `protection_started` wurde an zwei Stellen
mit unterschiedlicher Bedeutung benutzt — einmal, um zu entscheiden, ob eine Schutzfahrt
geschlossen wird, einmal roh als `valve_protection_active` für `decide()`. Dieser
Unterschied ist jetzt ein benannter Rückgabewert mit Begründung.

**Die Bedingung der Runde ist gemessen, nicht behauptet:** vorher 4 überlebende Mutanten,
nachher 4 — und es sind dieselben vier, die beiden aus `_process_zone` mitgewandert in die
beiden herausgezogenen Funktionen. Zusätzlich hat das Kreuzreview beide Fassungen gegen
dieselbe Datenbanklage laufen lassen (kein `ZoneState`, laufender Schutzlauf, gerade
abgelaufener Lauf, Übersteuerung, offenes Fenster, Sensorausfall) und `ShadowDecision` wie
`ZoneState` verglichen: identisch. Alle unberührten Funktionen sind AST-gleich.

Die drei übrigen Kandidaten (`app.py`, die fünf Frontend-Skripte, die Formularauswertung
in zwei Views) sind bewusst nicht angefasst.

### Ein zweiter Fehler in der Regelkette, noch offen

Gefunden beim Kreuzreview, nicht von einem Test — wie schon der erste am selben Tag.
**Ist die Mindest-Einschaltdauer einer Zone länger als ihr Ventilschutzlauf, wird der
zeitlich begrenzte Lauf zu dauerhaftem Heizen.** Beides sind Einstellungen je Zone, die
Kombination ist zulässig. Mit der Vorgabe tritt der Fehler nicht auf. Er ist in der
Hauptsession nachgestellt, samt Gegenprobe, und steht mit drei Lösungswegen in
[offene-entscheidungen.md](offene-entscheidungen.md). **Nicht behoben** — er gehört nicht
in ein reines Refactoring.

### Zwei Werkzeugfallen, beide bezahlt

- **`cosmic-ray exec` darf nie im Vordergrund unter einem Werkzeug-Timeout laufen.** Ein
  Abbruch mitten in einer Mutation überspringt die Wiederherstellung im `finally` und
  hinterlässt **mutierten Produktionscode ohne Fehlermeldung**. Ein Folgelauf maß dann
  gegen die kaputte Datei und lieferte ein vollständiges, aber ungültiges Ergebnis.
- **iCloud legt im Documents-Ordner Kopien der Form `test_deviation 2.py` an.** Sie sind
  alte Stände; `.gitignore` hält sie aus dem Repository, pytest sammelte sie aber ein und
  ließ die Suite an Zusicherungen scheitern, die vor Wochen einmal gestimmt haben. Beide
  Male sah es zuerst wie ein echter Fehlschlag aus. `--ignore-glob` in `pyproject.toml`
  fängt das jetzt ab, nachgewiesen mit einer absichtlich fehlschlagenden Dublette.

## Sicherheitsdurchsicht 2026-09-02 — vier Rechtefehler behoben

Die Durchsicht wurde nicht fortgeschrieben, sondern **noch einmal von vorn** geführt, mit
dem, was seit Teilprojekt 3 dazugekommen ist. Sie steht in
[sicherheitsdurchsicht-2026-09-02.md](sicherheitsdurchsicht-2026-09-02.md). Behoben und
mit Regressionstests belegt (`tests/test_security_review_2026_09_02.py`) sind vier
Befunde, alle drei ersten mit körperlicher Wirkung:

- **`device.manage` für eine Zone erreichte fremde Zonen.** Das Kanalformular prüfte
  das Bediengerät gegen die eigenen Zonen, die eingesandte Zielzone aber gar nicht. Ein
  bewusst auf eine Zone beschränkter Nutzer konnte einen fremden Raum abschalten — bei
  jeder Drehung am Regler erneut.
- **Eine Tastenbelegung wirkt in allen Zonen des Bediengeräts**, geprüft wurde nur eine.
  Der geteilte Flurregler reichte damit in jedes Zimmer, an dem er ebenfalls hängt.
  Jetzt braucht es das Recht für jede betroffene Zone.
- **Kiosk-Token galten als vollwertige REST- und MCP-Token.** Die Kioskoberfläche
  verstellt in festen Schritten; über REST setzte dasselbe Token jeden Sollwert und eine
  unbefristete Übersteuerung auf 35 Grad. Die enge Bedienfläche war die
  Sicherheitseigenschaft, und nur das Kiosk hat sie durchgesetzt. REST und MCP weisen
  Kiosk-Token jetzt ab.
- **Die Bediengeräteseite zeigte den gesamten Gerätebestand.** Gerätenamen tragen hier
  Raum- und Bewohnerbezüge. Die Seite verlangt jetzt `device.read` — das Recht, das die
  Navigation immer schon behauptet hat — und die Liste ist zonengefiltert.

**Nicht behoben, bewusst offen** — mit Begründung in der Durchsicht: der MQTT-Befehlsweg
(wer auf dem Broker veröffentlichen darf, steuert die Anlage ohne Konto — das ist eine
Frage der Broker-Konfiguration, nicht des Codes), die Meross-Bestätigung ohne
Zustandsprüfung, unbegrenzte Cloud-Antworten, das Einrichtungs-Token im Log,
Sitzungswiderruf beim Passwortwechsel und die Webhook-Weiterleitung mit `Authorization`.
Für die letzte hält ein ausdrücklich als offen benannter Test die Lücke fest.

## Offen

**Das Komplettreview ist durch.** Alle sieben Runden sind abgeschlossen. Der Plan mit sieben Runden steht in
[superpowers/plans/2026-09-01-komplettreview.md](superpowers/plans/2026-09-01-komplettreview.md).

| Runde | Zustand |
|---|---|
| 1 — Mutationstest | Stufe 1 und 2 abgeschlossen, im Kreuzreview |
| 2 — Zusicherungs-Audit | abgeschlossen |
| 3 — die Regelkette als Zustandstabelle | abgeschlossen, **ein Fehler gefunden** |
| 4 — Musterjagd | abgeschlossen |
| 5 — Messen vor Optimieren | abgeschlossen |
| 6 — Aufräumen | abgeschlossen, **ein Bestandsfehler gefunden** |
| 7 — Sicherheit von vorn | abgeschlossen, **vier Rechtefehler gefunden** |

**Runde 1, Stufe 2** hat vier weitere Domänendateien gemessen: `schedule.py` (138
überlebende Mutanten), `device_assignment.py` (14), `statistics.py` (12), `deviation.py`
(1). 115 wirksame Mutationen sind mit inhaltlichen Tests erschlagen, 50 als nachweislich
gleichwertig begründet abgelegt. **Echte Fehler in der Produktionslogik: wieder keine** —
über inzwischen sechs gemessene Dateien war jeder Befund eine Lücke in den Tests.

**Die drei Zeitzonen-Grenzfehler sind behoben** — Statistik und Auditfilter rechnen
Tagesgrenzen über `local_day_start_utc` in der konfigurierten Zeitzone, und der
`age`-Filter unterscheidet Zukunft von Vergangenheit, statt beides zu „gerade eben" zu
verschmelzen.

**Was nur der Projektinhaber entscheiden kann:**

- **Proportional-Integral-Regelung: ja, nein, oder erst messen?** Die Bewertung steht in
  [superpowers/specs/2026-09-02-pi-regelung-bewertung.md](superpowers/specs/2026-09-02-pi-regelung-bewertung.md).
  Empfehlung: erst messen — die Daten dafür liegen seit 0.3.0 vor.
- **Phase 2 wirklich abschliessen** — Schritt für Schritt in
  [inbetriebnahme-schattenbetrieb.md](inbetriebnahme-schattenbetrieb.md). Die Anlage muss
  über mehrere Tage laufen, bevor feststeht, dass plausible Ist-Temperaturen einlaufen
  und das Schattenprotokoll nachvollziehbare Entscheidungen zeigt.
- **Entscheiden, ob das Repository öffentlich werden soll.**
