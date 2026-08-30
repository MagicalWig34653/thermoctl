# Stand

Letzte Aktualisierung: 2026-08-29

## Wo wir stehen

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen, veröffentlicht als `v0.1.0` |
| 1a — Nacharbeiten | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | **gebaut**; der Nachweis über mehrere Tage braucht die Anlage |
| 3 — Konfigurations-Oberfläche | **abgeschlossen**, Abnahmekriterium nachgewiesen |
| 4 — Regelkreis und Cutover | Logik und Tests stehen, **nichts ist scharf** |
| 5 — Integrationen und Veröffentlichung | bis auf zwei Punkte erledigt |

`thermoctl` liest Sensoren, führt eine Messwert-Historie, erkennt ausgefallene Sensoren,
meldet Störungen, und schreibt für jede Zone auf, **was es schalten würde und warum** —
ohne je etwas zu schalten. Eine vollständige Anlage lässt sich über die Oberfläche
einrichten, ohne einen einzigen SQL-Befehl.

## Zahlen

Vom Controller selbst nachgeprüft, nicht aus Berichten übernommen:

| | |
|---|---|
| Tests | 923, grün unter SQLite **und** MariaDB |
| Testabdeckung | 98,78 %, Mindestschwelle 97 % in der CI |
| Ruff, mypy strict | ohne Befund, 74 Quelldateien |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken |
| CI und Container | grün |

## Der Trockenlauf ist abgesichert, nicht zugesagt

- `setting.control_armed` steht auf `false` und wird nirgends gesetzt.
- Jeder Aktor prüft ihn als Erstes.
- Der MQTT-Client verweigert das Veröffentlichen zusätzlich, solange er nicht scharf gebaut
  wurde — auch wenn ein Aufrufer es ausdrücklich verlangt.
- Tests belegen beides **und** den Gegenbeweis: Ein scharf gebauter Client sendet wirklich.
  Ohne den belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn das Senden
  gar nicht gebaut wäre.

## Was in diesem Lauf entstanden ist

**Phase 2** — Nutzlast-Auswertung gegen die echten Anlagendaten, Geräteerkennung aus
`bridge/devices` (erkennt Ventile und Fensterkontakte, ohne je eine Zustandsnachricht von
ihnen gesehen zu haben), MQTT-Client mit TLS und Wiederverbindung, Ingest samt
Messwert-Historie und Aufbewahrung, Störungserkennung, Fensterkontakte, Aktor-Adapter im
Trockenlauf hinter zwei Riegeln, Schattenprotokoll, Geräteübersicht.

**Die Regelentscheidung** ist aus Phase 4 vorgezogen — sonst hätte das Schattenprotokoll
nichts zu protokollieren. Hysterese, Mindestschaltdauer, Fensterpause, Frostschutz bei
Sensorausfall, als reine Funktion mit 33 Tests, darunter der Defekt des Altsystems
ausdrücklich vorgeführt.

**Was seither dazugekommen ist.** Ein **Thermostat** je Zone auf der Startseite, das den
Sollwert des gerade geltenden Modus verstellt — dauerhaft, nicht als Übersteuerung; das
**Anlagenbild** (`/anlage`), das den Weg Sensor → Zone → Aktor zeigt und die Lücken darin
benennt; **Heizzeiten** (`/statistik`) je Zone und Tag, aus den Abständen im
Schattenprotokoll gerechnet und mit gekappten Ausfalllücken; die
**Schnittstellen-Übersicht** (`/schnittstellen`), die für jede Gegenstelle sagt, ob sie
eingerichtet ist, ob sie läuft und woher jeder Wert kommt; eine neue **Rechtevergabe** mit
einem Formular je Gruppe statt sechzehn Einzelklicks; und **Gerätezuordnung per Ziehen** auf
das Flussbild.

**Drei Fehler behoben, die nur beim Benutzen auffielen:** Die Kopfleiste verschwand auf
sechs Seiten, sobald man sie über das Menü ansteuerte (`hx-boost` schickt bei jeder
Navigation `HX-Request`, sechs Ansichten hielten das für einen Teilaustausch). Auf schmalen
Geräten ließ sich die ganze Seite seitwärts schieben. Und das dunkle Schema folgt jetzt
allein dem Betriebssystem, auch während die Seite offen ist.

**Die Oberfläche ist neu gestaltet und neu aufgeteilt.** Farbe ist darin kein Schmuck,
sondern Messwert: Die ganze Fläche ist Schiefer und Papier, und die einzigen gesättigten
Töne im Programm sind Kupfer (warm) und Stahlblau (kühl). Temperaturen stehen in einem
eigenen dicktengleichen Register, damit Zahlen untereinander vergleichbar sind, und mit
Komma statt Punkt. Ein dunkles Schema gibt es jetzt wirklich — vorher stand
`data-bs-theme="auto"` im Markup, ein Wert, den Bootstrap nicht kennt.

**Die Startseite ist eine Statustafel.** Eine Zeile je Zone, und je Zone ein **Tagesplan
als Band**: der heutige Zeitplan über 24 Stunden, eingefärbt nach Solltemperatur, mit
einem Strich für *jetzt*. Gilt der Plan gerade nicht — Betriebsart „Aus" oder eine laufende
Übersteuerung —, wird das Band entkräftet und beschriftet, statt einen Plan zu behaupten,
der nicht wirkt. Weggefallen sind die beiden Zählkacheln, darunter die Zahl der
**Benutzer**: Wie viele Konten es gibt, sagt über eine Heizung nichts.

**Neu aufgeteilt:** Was täglich gebraucht wird, steht offen in der Leiste (Start, Zonen,
Geräte, Betrieb); was selten gebraucht wird, liegt unter Einstellungen. `/steuerung` heißt
jetzt *Betrieb* und trägt nur noch den Betriebszustand — die Regelvorgaben sind nach
`/einstellungen` gezogen. Die fünf Seiten einer Zone (Zeitplan, Sollwerte, Geräte,
Regelparameter, Zonendaten) haben einen gemeinsamen Kopf und sind damit ein Ort statt fünf.

**Zeitpläne lassen sich ziehen.** Ein Balken der Wochenansicht geht mit der Maus auf eine
andere Zeit oder einen anderen Tag, im 15-Minuten-Raster; ein Klick übernimmt Tag und
Uhrzeit ins Anlege-Formular. Das Ziehen ist eine zweite Bedienart derselben Änderung, keine
eigene Schnittstelle: Beim Loslassen geht dasselbe Formular hinaus, das ohne JavaScript von
Hand ausgefüllt würde — gleicher CSRF-Schutz, gleiche Rechteprüfung, gleiche Fehlerdarstellung.
Ohne JavaScript bleibt der Zeitplan vollständig bedienbar.

**Die Anlage lässt sich jetzt auch bedienen, nicht nur einrichten.** `/steuerung` zeigt
den Betriebszustand, was die Regelung gerade für jede Zone entscheiden würde, und die
globalen Vorgaben, von denen jede Zone erbt. Scharfschalten hat ein **eigenes Recht**
(`control.arm`) statt unter `setting.manage` mitzulaufen — wer Zeitzone und
Aufbewahrungsdauer pflegen darf, soll die Heizung nicht nebenbei scharf schalten können.
Es verlangt eine Begründung, die ins Audit-Protokoll geht, und ist mit einem Klick
umkehrbar. **Der zweite Riegel bleibt unberührt:** `MqttClient(schalten_erlaubt=…)` wird
beim Bau des Clients gesetzt, nicht von hier.

**Vor der Einrichtung** führen `/` und `/login` zur Einrichtungsseite, statt ein Anmeldeformular zu zeigen, an dem sich mangels Benutzer niemand anmelden kann. Nach abgeschlossener Einrichtung gilt wieder der gewohnte Weg über `/login`; `/setup` ist dann dauerhaft geschlossen.

**Phase 3** — Formularbausteine, Zonenverwaltung, Gerätezuordnung samt Tausch, Modi und
Sollwerte, Zeitplan-Editor mit Wochenansicht, Übersteuern, Regelparameter je Zone,
Benutzer-/Gruppen-/Tokenverwaltung, Audit-Ansicht, Übersichtsseite.

**Aus Phase 5 vorgezogen** — MCP-Server als dritter Adapter, API-Dokumentation,
Self-Hosting-Anleitung, Beispiel-Compose, Benachrichtigungen bei Störungen,
[Sicherheitsdurchsicht](sicherheitsdurchsicht.md), und die REST-Endpunkte, mit denen die
drei Adapter wieder auf demselben Stand sind.

**Aus Phase 4 vorgebaut, ohne etwas scharf zu schalten** — die Umwandlung des alten
Stundenrasters in Schaltpunkte (die Roadmap führte sie als ungeklärt; sie ist jetzt eine
reine Funktion mit einer Rückprobe über alle 168 Wochenstunden) und die lesende Grundlage
des Vergleichsbetriebs.

## Zehn Fehler, die alle Tests und Reviews passiert hatten

Alle in diesem Lauf gefunden:

1. **Zwei Wächtertests prüften nichts.** Seit FastAPI 0.141 verschachtelt `include_router()`
   die Routen; beide Wächter fanden nur noch `/healthz` und waren grün, weil sie leer
   liefen.
2. **Der Testlauf hing an einer zufällig vorhandenen `.env`.** Örtlich grün, in der CI wäre
   der nächste Lauf rot geworden.
3. **Die Startseite baute eine eigene Vorlagen-Umgebung mit relativem Pfad.** Im Container
   liegt das Paket in `site-packages` und das Arbeitsverzeichnis ist `/app` — dort hätte
   genau die Seite gefehlt, auf die Anmeldung und Navigation zeigen.
4. **Ein zu kurzes Passwort hinterließ eine halb angelegte Einrichtung**, weil die Prüfung
   erst nach den ersten Schreibzugriffen kam.
5. **Der Downgrade der Phase-2-Migration scheiterte nur unter MariaDB** — dort wird der
   Index für einen Fremdschlüssel gebraucht.
6. **Die Frostschutz-Sperre griff in keiner echten Anlage.** Der Einrichtungsassistent legt
   den Frostschutzmodus als eingebauten Modus an, und die allgemeine Sperre kam zuerst —
   also bekam genau der wichtigste Modus die nichtssagende Meldung. Der zugehörige Test war
   grün, weil seine Fixture einen Zustand herstellte, den es in keiner Instanz gibt.
7. **`shadow_decision.zone_id` hatte als einziger Zonenbezug keine Kaskade**, wodurch sich
   eine Zone nicht mehr löschen ließ, sobald ein Schattenlauf für sie gelaufen war.

8. **Die Sollwertgrenze für Übersteuerungen galt nicht überall.** Sie stand dreimal
   verschieden da — und der MCP-Server prüfte gar nicht. Übersteuerungen sind genau die
   Eingabe, die in Phase 4 ungefiltert in die scharfe Regelentscheidung fließt. Die
   Prüfung liegt jetzt in der Domäne.
9. **Die Fehlerklassen waren eingefrorene Dataclasses.** Python hängt einer Ausnahme beim
   Werfen ihren Traceback an, und eine eingefrorene Dataclass verweigert das — die
   Ausnahme kam als `FrozenInstanceError` an, sobald sie tief genug durchgereicht wurde.

10. **Ein Start ohne Migrationslauf endete in einem Traceback statt in einer Auskunft.**
    Der Container migriert im Entrypoint, ein lokaler `uvicorn`-Aufruf nicht. Wer die
    Datenbankdatei verschob und startete, bekam sechzig Zeilen SQLAlchemy mit
    `no such table: user` als Kern — richtig, aber ohne einen Hinweis darauf, dass eine
    Migration fehlt. Gefunden hat es der Projektinhaber, nicht die Suite: Kein Test
    startete je gegen eine leere Datenbank, weil jede Fixture ihr Schema selbst anlegt.
    Der Dienst prüft den Schemastand jetzt vor der ersten Abfrage und nennt den Befehl;
    ein veraltetes Schema fällt dabei ebenfalls auf, statt später an einer fehlenden Spalte.

Dazu vier Korrekturen aus der Gegenlesung in der Hauptsession: Frostschutz statt Abschalten
bei Sensorausfall, ein MQTT-Wiederverbindungsabstand, der monoton wuchs, eine doppelt
implementierte Zeitberechnung in zwei Adaptern, und ein Meldungsversand, der den Zyklustakt
verzögert hätte.

## Offen

**Was nur der Projektinhaber kann:**

- **Phase 2 wirklich abschließen** — Schritt für Schritt in
  [inbetriebnahme-schattenbetrieb.md](inbetriebnahme-schattenbetrieb.md). Die Anlage muss
  über mehrere Tage laufen:
  `THERMOCTL_MQTT_ENABLED=true` samt Zugangsdaten in `.env`, dann Geduld. Erst dann steht
  fest, dass plausible Ist-Temperaturen aller Zonen einlaufen und das Schattenprotokoll
  nachvollziehbare Entscheidungen zeigt.
- **Die Frostschutz-Entscheidung bestätigen**, bevor Phase 4 scharf schaltet — siehe
  [offene-entscheidungen.md](offene-entscheidungen.md). Sie hat körperliche Folgen.
- **Meross-Zugangsdaten hinterlegen.** Der Adapter ist gebaut, sein Nutzlastaufbau ist eine
  begründete Annahme und nie gegen ein echtes Konto gelaufen.
- **Das Repository öffentlich schalten** — ausdrücklich seine Entscheidung.

**Was als Nächstes ansteht:**

- Phase 4: Vergleichsbetrieb entwerfen (Dauer, Auflösung, Bericht), dann das Schema dafür
  bauen — nicht umgekehrt. Datenübernahme aus dem Altschema. Scharfschalten hinter einem
  Schalter, jederzeit umkehrbar.
- Phase 5, Rest: altes Topic-Schema abkündigen (wartet auf den Cutover). Die
  Home-Assistant-Anbindung ist angeschlossen — sie meldet die Zonen an, veröffentlicht
  ihren Zustand und nimmt Sollwert und Betriebsart entgegen, sobald die Regelung scharf
  ist.
- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unverändert die Rückfallebene.**
