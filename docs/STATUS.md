# Stand

Letzte Aktualisierung: 2026-09-01

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
| 4 — Regelkreis und Cutover | Logik und Tests stehen, die Verdrahtung fehlt (siehe unten) |
| 5 — Integrationen und Veröffentlichung | Meross und Zeitplan-Bedienung erledigt, Freigabe von 0.2.2 offen |

## Was geschaltet wird — genau

Diese Frage ist mehrfach zu grob beantwortet worden, in beide Richtungen. Der Stand:

- **`setting.control_armed` steht in einer neuen Anlage auf `false`.** Solange bleibt
  jeder Weg zu einem Aktor zu.
- **`/control/arm` kann es öffnen**, mit eigenem Recht `control.arm`. Es ist also nicht
  wahr, dass „weiterhin nichts geschaltet wird" — das war eine Zusicherung ohne Deckung.
- **Der MQTT-Client trägt einen zweiten Riegel, der beim Start gebaut wird.** Wer scharf
  schaltet, muss den Dienst neu starten, bevor überhaupt etwas hinausgeht.
- **Danach bewegt sich genau eines:** Selbstregelnde Thermostatventile bekommen ihren
  Sollwert veröffentlicht, und das bewegt ein Ventil. **Normale Ein/Aus-Entscheidungen
  gehen nirgendwohin** — weder `Zigbee2MqttValve` noch `MerossSwitch` werden im
  Produktivcode konstruiert. Das ist die offene Arbeit von Phase 4.

## Zahlen

Selbst nachgeprüft, nicht aus Berichten übernommen (Stand 2026-09-01):

| | |
|---|---|
| Tests | 1390 unter SQLite, 1389 plus ein Skip unter MariaDB |
| Testabdeckung | 100 %, Mindestschwelle 100 % in der CI |
| Ruff, mypy strict | ohne Befund, 101 Quelldateien |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken |
| Container | baut; eine echte 0.2.0-Datenbank wurde darin hochgezogen, `/healthz` meldet 0.2.2 |

**Die Suite liest `THERMOCTL_TEST_DATABASE_URL`**, nicht `THERMOCTL_DATABASE_URL`. Wer
die zweite setzt, läuft unbemerkt gegen SQLite und bekommt trotzdem einen grünen Lauf.

## v0.2.2 — noch nicht freigegeben

Zwei Freigabe-Reviews haben den Stand abgelehnt, beide zu Recht. Was daraus behoben ist,
steht im [CHANGELOG](../CHANGELOG.md); was noch offen ist, hier:

- **Rückgängig im Zeitplan nimmt einen veralteten Schnappschuss an**, wenn dazwischen
  über die gewöhnliche Punktbearbeitung A→B→A geändert wurde. Die Revision zählt nur
  Audit-Ereignisse vom Typ `schedule`, die normale Bearbeitung schreibt aber
  `schedule_point`.
- **Das Formular „Schaltpunkt anlegen" hat kein CSRF-Feld** und ist ohne JavaScript
  nicht absendbar. Der Test, der das prüfen sollte, ergänzt den Header selbst.
- **Tote CSS-Klassen** (`t-marke`, `t-leise` in `settings.html`) verändern still die
  Darstellung der Sonnenabsenkung.

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
