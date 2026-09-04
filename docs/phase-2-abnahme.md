# Abnahme Teilprojekt 2 — Auswertung der echten Betriebsdaten

Stand: 2026-09-04

**Anonymisiert.** Zonen- und Gerätenamen aus der Datenbank enthalten Raum- und
Bewohnerbezüge und wurden für diesen Bericht durch `Zone A` bis `Zone F` ersetzt.
Die Zuordnung ist absichtlich nicht dokumentiert. IP-Adressen, MAC-Adressen,
Gerätekennungen und Seriennummern wurden nicht übernommen.

## Was geprüft wurde

Grundlage ist ein vollständiger Export der Produktivdatenbank (MariaDB), eingespielt in
eine separate Auswertungsdatenbank und ausschliesslich lesend ausgewertet. Der Export
selbst wurde nicht verändert und ist nicht Teil dieses Repositories.

Geprüft wurden die drei Abnahmekriterien aus Abschnitt 4 von
[`inbetriebnahme-schattenbetrieb.md`](inbetriebnahme-schattenbetrieb.md): plausible
Ist-Temperaturen je Zone, nachvollziehbare Schattenentscheidungen, und der Abgleich mit
dem Altsystem. Herangezogen wurden die Tabellen `zone`, `measurement`,
`shadow_decision`, `device_command`, `zone_state`, `zone_setpoint`, `schedule_point`,
`setting` und `audit_event`.

**Der Beobachtungszeitraum ist kurz.** Die früheste Zone wurde am 2026-08-31 um 15:41 Uhr
angelegt, der Datenbankauszug stammt vom 2026-09-04 um 07:37 Uhr — das sind knapp
3 Tage 16 Stunden. Fünf der sechs Zonen existieren erst seit dem 2026-09-02, also knapp
zwei Tage. Die Anleitung spricht von „mehreren Tagen"; das untere Ende dieser Spanne ist
erreicht, mehr nicht.

## Wichtiger Befund vorab: Es war kein reiner Schattenbetrieb

Bevor die drei Kriterien einzeln behandelt werden, ein Befund, der die Auswertung aller
drei einfärbt: **Die Anlage wurde während des gesamten für diesen Bericht verfügbaren
Datenbestands überwiegend nicht im Schattenbetrieb gefahren, sondern scharf.**

Der Audit-Log zeigt den Zeitpunkt eindeutig:

```
2026-09-01 19:20:06  arm  setting  Regelung scharf geschaltet — Sollwertausgabe
                                    erst nach Neustart freigegeben
```

Zu diesem Zeitpunkt existierte nur Zone A (angelegt 2026-08-31 15:41 Uhr) — sie hatte
damit rund 27,5 Stunden echten Schattenbetrieb, bis zum nächsten Neustart des Dienstes
sogar rund 42,6 Stunden (der erste tatsächlich ausgeführte Schaltbefehl für Zone A datiert
auf 2026-09-02 10:18:12 Uhr — das Scharfschalten wirkt laut Audit-Text erst nach einem
Neustart).

Die Zonen B, C und D wurden alle **nach** dem Scharfschalten angelegt, zwischen
2026-09-02 10:41 und 10:42 Uhr. Ihre ersten echten Aktor-Befehle folgten binnen
Minuten:

| Zone | angelegt | erster echter Befehl | Abstand |
|---|---|---|---|
| B | 10:41:17 | 10:43:16 | 2 Minuten |
| D | 10:42:07 | 10:46:17 | 4 Minuten |
| C | 10:41:43 | 10:46:17 | 4,5 Minuten |

Für diese drei Zonen gab es also **keine einzige Minute** dokumentierten
Schattenbetriebs — die erste je für sie getroffene Regelentscheidung ging bereits an den
echten Aktor. Zonen E und F haben keinen Aktor zugeordnet und liefen die ganze Zeit über
in der Betriebsart „Aus" (siehe Kriterium 2); für sie stellt sich die Frage nicht, weil
nie geschaltet wurde.

**Das betrifft, was dieser Bericht überhaupt noch belegen kann.** Die Abnahme von
Teilprojekt 2 soll den *Schattenbetrieb* bewerten — aber der Datenbestand enthält davon
für drei der vier Zonen mit Aktor praktisch nichts, für die vierte gut anderthalb Tage.
Was stattdessen fast durchgehend vorliegt, sind Daten aus dem bereits scharfen Betrieb,
der laut `roadmap.md` seit `v0.3.0` ohnehin läuft und dessen Cutover-Abnahme (Phase 4)
ein eigenes, hier nicht behandeltes Thema ist. Die folgenden drei Abschnitte werten
trotzdem aus, was an Schattenprotokoll (`shadow_decision`) vorliegt — das wird
unabhängig vom scharfen Zustand fortlaufend geschrieben —, aber der knappe zeitliche
Rahmen relativiert jede Aussage über „mehrtägige Beobachtung im Schattenbetrieb".

Eine weitere Beobachtung dazu: Die Tabelle `device_command` enthält für den gesamten
Zeitraum **ausschliesslich** den Ergebniscode `executed` (366 von 366 Einträgen) — kein
einziger `suppressed` (der Code, den ein im Trockenlauf unterdrückter Befehl bekäme) und
kein `failed`. Der Trockenlauf-Pfad existiert im Code (`services/publishing.py`,
Ergebniscode `SUPPRESSED`) und ist dort begründet, aber **diese Betriebsdaten zeigen
kein einziges Beispiel dafür, dass er in der Praxis tatsächlich einen Befehl
zurückgehalten hat.** Das kann daran liegen, dass der Regelzyklus für Zone A vor dem
Scharfschalten noch nicht durchgehend lief — der erste Eintrag in `device_command`
überhaupt stammt erst vom 2026-09-02, also nach der Scharfschaltung. Belegt ist der
Trockenlauf-Riegel damit an dieser Stelle nur durch Code und Tests, nicht durch
Felddaten.

## Kriterium 1 — Laufen plausible Ist-Temperaturen ein?

Sechs Zonen, sechs Temperaturquellen (Zigbee2MQTT-Sensoren). Messwerte je Zone im
Beobachtungszeitraum (2026-08-31 bis 2026-09-04):

| Zone | Messwerte | mittlerer Abstand | Median-Abstand | längste Lücke | Wertebereich |
|---|---|---|---|---|---|
| A | 6.529 | 49 s | 30 s | 14,3 Min | 22,4–24,9 °C |
| B | 3.935 | 81 s | 30 s | 12,0 Min | 22,3–25,3 °C |
| C | 5.692 | 56 s | 10 s | 11,4 Min | 21,4–25,3 °C |
| D | 541 | 9,8 Min | 10 Min | 20,0 Min | 21,6–23,9 °C |
| E | 689 | 7,7 Min | 6,4 Min | 22,0 Min | 22,7–26,3 °C |
| F | 5.012 | 63 s | 10 s | 10,0 Min | 22,6–29,9 °C |

Alle Werte liegen in einem für eine Wohnung im September plausiblen Bereich. Keine
unmöglichen Sprünge gefunden (Prüfgrenze: mehr als 3 K Änderung innerhalb von 10 Minuten
— kein einziger Fall in allen sechs Zonen).

**Zwei Auffälligkeiten:**

1. **Zone D und Zone E melden deutlich seltener** als die anderen vier — im Mittel alle
   8–10 Minuten statt alle 30–80 Sekunden. Das kann am Sensortyp liegen (batteriebetriebene
   Zigbee-Sensoren melden oft nur bei Wertänderung plus einem festen Heartbeat), ist aber
   nicht aus den Daten allein zu klären.
2. **Zone E hat sechs eingefrorene Phasen von 3 bis 4,8 Stunden Länge**, in denen der
   gemeldete Wert exakt gleich blieb — genau das Muster, das die Inbetriebnahme-Anleitung
   ausdrücklich als verdächtig nennt. Die Phasen häufen sich auffällig auf die frühen
   Morgenstunden (z. B. 2026-09-01 05:57–10:45, 2026-09-03 05:58–10:47) und einmal auf den
   späten Abend (2026-09-03 20:44 bis 2026-09-04 00:44). Das sieht nach einem
   wiederkehrenden Muster aus, nicht nach Zufall — ob Sensorausfall, ein Gerät im
   Stromsparmodus, oder tatsächlich eine ungewöhnlich stabile Raumtemperatur, lässt sich aus
   den Daten nicht entscheiden. Erwähnenswert: Die längste gemessene Lücke in Zone E liegt
   bei nur 22 Minuten, weit unter dem Störungs-Timeout von 30 Minuten
   (`default_sensor_timeout_seconds` = 1800 s) — die eingefrorenen Phasen bestehen aus
   dicht getakteten, aber identischen Werten, sie wären als „veraltet" nicht erkannt worden,
   weil ja regelmässig ein (gleicher) Wert eintraf.

Keine Zone überschritt in diesem Zeitraum den Sensor-Timeout: In allen 18.571
Schattenentscheidungen ist `temperature_c` durchgehend gesetzt, keine einzige NULL. Das
bedeutet nicht zwingend, dass die Störungserkennung nie ausgelöst hat — nur, dass beim
Zeitpunkt jeder protokollierten Entscheidung ein Messwert innerhalb der Timeout-Frist
vorlag. Die Zustände `veraltet` und `keine_quelle` (`sensor_status`) sind in den
Rohdaten nicht historisiert, nur der aktuelle Stand in `zone_state` — zum Zeitpunkt des
Exports standen alle sechs Zonen auf `ok`.

## Kriterium 2 — Sind die Schattenentscheidungen nachvollziehbar?

18.571 protokollierte Entscheidungen, verteilt auf vier Ergebniscodes:

| Ergebniscode | Anzahl | Anteil |
|---|---|---|
| `unveraendert` | 18.527 | 99,76 % |
| `gesperrt_mindestdauer` | 40 | 0,22 % |
| `aus` | 2 | 0,01 % |
| `heizen` | 2 | 0,01 % |

**Es fand in diesem Zeitraum praktisch kein Heizen statt** — Anfang September ist es
warm genug, dass keine Zone real unter ihren Sollwert fiel. Die einzigen vier
Zustandswechsel (`heizen`/`aus`) traten alle in Zone A auf, am 2026-09-01 und
2026-09-02, und lassen sich eindeutig als **manueller Test der Zeitplan-Funktion**
identifizieren, nicht als organisches Verhalten: Der Sollwert für den Modus „Nacht" stand
zum Zeitpunkt der Heizentscheidung auf 25,5 °C — deutlich über dem aktuell hinterlegten
Wert von 18,0 °C für denselben Modus und dieselbe Zone. Wenige Minuten später wurde der
Sollwert für den Modus „Tag" ebenfalls kurzzeitig auf 25,0 °C angehoben und dann wieder
auf den heutigen Wert (18,5 °C) zurückgesetzt — erkennbar daran, dass innerhalb *einer*
laufenden Sperrfrist (`gesperrt_mindestdauer`, ausgelöst durch die Mindestschaltdauer von
300 Sekunden) der protokollierte Sollwert zwischen zwei aufeinanderfolgenden Zeilen
sprang. Das deckt sich mit dem Verlauf der Zonen-Konfiguration am selben Tag (Audit-Log:
Zone A wurde von 2026-08-31 bis 2026-09-02 mehrfach umkonfiguriert) und ist plausibel als
absichtlicher Funktionstest zu werten, nicht als Fehlverhalten der Regel. Damit lässt
sich aus den Daten **keine einzige echte, bedarfsgetriebene Heizentscheidung**
nachweisen — was für die Jahreszeit nicht überrascht, aber bedeutet, dass die
Hysterese- und Mindestschaltdauer-Logik im Feld noch nicht unter realer Heizlast
beobachtet wurde.

Schaltwechsel je Zone und Tag: **0 in allen sechs Zonen an allen Tagen**, mit der einzigen
Ausnahme der vier oben beschriebenen Testwechsel in Zone A. Die konfigurierte
Mindestschaltdauer (durchgängig 300 s, aus der globalen Vorgabe geerbt, keine Zone hat
einen eigenen Wert) wurde entsprechend nur in diesem Testfall wirksam.

**Zeitpläne — zwei von sechs Zonen haben gar keinen:**

| Zone | Zeitplanpunkte | Betriebsart | Aktor vorhanden |
|---|---|---|---|
| A | 10 | Automatik | ja |
| B | 28 | Automatik | ja (+ selbstregelndes Ventil) |
| C | 14 | Automatik | ja |
| D | 14 | Automatik | ja |
| E | 0 | **Aus** | nein |
| F | 0 | **Aus** | nein |

Zonen E und F sind dauerhaft auf Betriebsart „Aus" gestellt und haben keinen
Zeitplan hinterlegt; ihre Schattenentscheidung lautet deshalb im gesamten Zeitraum
durchgehend „Betriebsart Aus — Frostschutz" bzw. „Kein Zeitplan hinterlegt —
Frostschutz". Für diese beiden Zonen hat die automatische Regelung — Zeitplan,
Hysterese, Mindestschaltdauer — im Beobachtungszeitraum **kein einziges Mal** gegriffen.
Das ist an sich kein Fehler (beide Zonen haben keinen Aktor, insofern konsequent), aber
es bedeutet: Von sechs Zonen haben nur vier die Regellogik überhaupt durchlaufen, und nur
eine davon (Zone A, testweise) tatsächlich eine Zustandsänderung ausgelöst.

**Widersprüche zwischen Zeitplan und Entscheidung, wie die Anleitung sie als Beispiel
nennt** („heizt nachts, obwohl Zeitplan Nacht sagt"): Systematisch geprüft wurde, ob
`would_heat=1` bei einer Ist-Temperatur über dem Sollwert vorkommt (vier Fälle, alle
Teil des oben beschriebenen Zeitplan-Tests in Zone A, durch die Sollwert-Umkonfiguration
mitten in einer laufenden Sperrfrist erklärbar) und ob `would_heat=0` vorkommt, während
die Ist-Temperatur mehr als 1 K unter dem Sollwert liegt (kein einziger Fall). Ein
inhaltlicher Widerspruch zwischen Zeitplan und Entscheidung wurde nicht gefunden.

**Ein Befund, der niemand gesucht hat — die Begründungstexte sind bei „unverändert"
systematisch irreführend.** Für den Fall „Zustand bleibt" formuliert
`thermoctl/domain/control_loop.py` (Zeilen 247–258 und 278–288) unabhängig vom
tatsächlichen Abstand zum Sollwert immer denselben Satz:

> Ist *X* °C innerhalb der Hysterese um Soll *Y* °C ± 0.10K (…) — Zustand bleibt.

Das stimmt nur, wenn *X* tatsächlich nahe an *Y* liegt. Ausgewertet über alle 18.527
`unveraendert`-Entscheidungen: **in jeder einzelnen** liegt die Ist-Temperatur weiter als
die konfigurierte Hysterese (0,10 K) vom Sollwert entfernt — im Mittel 5,9 K, im
Extremfall 11,4 K. Bei Zonen E und F etwa steht dort „innerhalb der Hysterese um Soll
16,0 °C ± 0,10 K", während die Ist-Temperatur tatsächlich bei 22–24 °C liegt, also rund
7–8 K darüber. Die eigentliche Regelentscheidung ist in diesen Fällen richtig — die Zone
bleibt zu Recht aus, weil sie längst zu warm ist, nicht weil sie zufällig im
Hysteresefenster liegt —, aber der protokollierte *Grund* behauptet etwas anderes als das,
was tatsächlich zutrifft. Kein Test im Repository prüft diesen Satz gegen einen Fall mit
grossem Abstand zum Sollwert; er ist offenbar nie mit einem realistischen
„längst zu warm"-Szenario verglichen worden. Das betrifft **99,76 % aller bisher
geschriebenen Schattenentscheidungen** und ist damit kein Rand-, sondern der Regelfall.
Es handelt sich um einen Text-, keinen Regel-Fehler — die physische Entscheidung war in
allen geprüften Fällen korrekt —, aber es untergräbt genau das, was Kriterium 2 verlangt:
dass jede Entscheidung ihren *zutreffenden* Grund trägt.

## Kriterium 3 — Deckt sich das mit dem Altsystem?

**Entfällt.** Der Projektinhaber hat den Vergleichsbetrieb bewusst übersprungen und ist
direkt auf den scharfen Betrieb übergegangen (dokumentiert in `STATUS.md`/`roadmap.md`).
Es liegen keine Altsystem-Daten aus demselben Zeitraum vor, gegen die sich die
`shadow_decision`-Einträge spiegeln liessen. Es wurde kein Ersatzverfahren konstruiert,
um diese Lücke rechnerisch zu füllen — das wäre eine Behauptung, keine Prüfung.

Die praktische Folge: **Keine der 18.571 Regelentscheidungen wurde je gegen eine zweite,
unabhängige Quelle geprüft.** Weder gegen das Altsystem (übersprungen) noch — wie oben
gezeigt — nennenswert gegen einen tatsächlichen Trockenlauf (die meisten Zonen hatten
keinen). Die einzige Prüfung, die stattgefunden hat, ist die interne Konsistenz der
Regel gegen sich selbst (Ist-Wert, Sollwert, Zeitplan, Hysterese — alle aus derselben
Datenbank, von derselben Regel erzeugt).

## Sonstiges — das Schaltprotokoll (`device_command`)

366 Einträge, davon 352 `setpoint`-Befehle an das selbstregelnde Thermostatventil in
Zone B (Nutzlast enthält jeweils den kompensierten Sollwert plus die aktuelle
Fernbedienungstemperatur — Format und Kadenz plausibel für ein Zigbee-Heizkörperthermostat)
und 14 `switch`-Befehle an gewöhnliche Aktoren in den Zonen A, B, C und D. Alle 366
Einträge tragen den Ergebniscode `executed`; keiner `failed`. Wie oben ausgeführt fehlt
`suppressed` vollständig — kein Beleg für einen tatsächlich zurückgehaltenen Befehl in
diesem Datenbestand.

Die letzten `switch`-Befehle datieren auf 2026-09-03 17:09 Uhr; danach blieb der Zustand
in allen vier Zonen mit Aktor unverändert (siehe Kriterium 2), sodass kein weiterer
Schaltbefehl nötig war — kein Hinweis auf einen Ausfall des Schaltwegs, nur auf
Bedarfslosigkeit.

## Was die Daten nicht hergeben

- **Keinen Beleg für „mehrtägigen Schattenbetrieb".** Für drei von vier Zonen mit Aktor
  lag die Schattenphase bei null Minuten, für die vierte bei rund anderthalb Tagen.
- **Keinen Beleg für echtes Heizverhalten unter Last.** Der Beobachtungszeitraum lag im
  spätsommerlichen Wetter; alle Zonen blieben durchgehend über ihrem Sollwert. Hysterese
  und Mindestschaltdauer wurden nur in einem manuellen Test einmal durchlaufen, nie unter
  echtem Heizbedarf.
- **Keinen Vergleich mit dem Altsystem** (Kriterium 3, siehe oben — bewusste
  Entscheidung, nicht Datenlücke).
- **Keine Erklärung für die eingefrorenen Werte in Zone E.** Ob Sensorfehler,
  Stromsparverhalten des Geräts oder echte thermische Stabilität — das lässt sich aus
  den vorliegenden Tabellen nicht entscheiden, dafür bräuchte es Zugriff auf das Gerät
  selbst oder den Zigbee2MQTT-Log.
- **Keine Historie der Sensorzustände** (`veraltet`/`keine_quelle`) über die Zeit — nur
  der aktuelle Stand ist gespeichert, kein Protokoll vergangener Wechsel.
- **Keinen Nachweis, dass der Trockenlauf-Riegel im Feld je gegriffen hat** — im
  verfügbaren Datenbestand ausschliesslich `executed`-Einträge, kein `suppressed`.

## Fazit

**Teilprojekt 2 kann auf Grundlage dieser Daten nicht als abgenommen gelten — nicht,
weil die Technik sichtbar versagt, sondern weil die Abnahme etwas anderes geprüft hat,
als sie sollte.**

Was die Daten zeigen, ist überwiegend nicht der in Abschnitt 4 der
Inbetriebnahme-Anleitung beschriebene Schattenbetrieb, sondern der bereits scharfe
Betrieb von Phase 4 — für fünf der sechs Zonen ab ihrer jeweils ersten Sekunde. Insofern
beantwortet dieser Bericht eher eine Frage zu Phase 4 als zu Teilprojekt 2. Innerhalb
dessen, was sich trotzdem auswerten lässt:

- **Kriterium 1** (plausible Ist-Temperaturen) ist überwiegend erfüllt: Werte in
  plausiblem Bereich, keine unmöglichen Sprünge, ausreichende Meldefrequenz in vier von
  sechs Zonen. Offen bleibt die wiederholt eingefrorene Zone E — genau der Fall, den die
  Anleitung als prüfenswert benennt, und der hier ungeklärt bleibt.
- **Kriterium 2** (nachvollziehbare Entscheidungen) ist in der Substanz erfüllt — die
  Regel selbst hat in keinem geprüften Fall falsch entschieden —, aber die
  Begründungstexte sind im Regelfall (99,76 % aller Entscheidungen) sachlich ungenau. Das
  ist genau die Art Fehler, die laut `CLAUDE.md` bereits zweimal grundlegende Probleme
  durch Tests und Reviews hat rutschen lassen, weil niemand die Anwendung mit einem
  konkreten, realitätsnahen Fall angesehen hat.
- **Kriterium 3** entfällt ersatzlos, mit der Konsequenz, dass keine einzige der 18.571
  Entscheidungen je gegen eine zweite Quelle geprüft wurde.

Vor einer Abnahme wären aus dieser Auswertung heraus mindestens zu klären: die
Begründungstexte bei „unverändert" (eine Zeile Code, siehe oben), die Ursache der
eingefrorenen Werte in Zone E, und — wichtiger als beides — ob die Fragestellung der
Abnahme angesichts des bereits laufenden scharfen Betriebs noch die aus Abschnitt 4 der
Anleitung ist, oder ob sie an die tatsächliche Lage angepasst werden muss.
