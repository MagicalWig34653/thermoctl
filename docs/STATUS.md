# Stand

Letzte Aktualisierung: 2026-08-30

## Der Code spricht Englisch — die Prosa zur Hälfte

Bezeichner, Modul- und Testdateinamen, Web-Endpunkte, MQTT-Topics, MCP-Werkzeuge und
Formularfelder sind englisch. **Der sichtbare Text bleibt deutsch** — nachgewiesen, nicht
behauptet: Ein Vergleich aller Zeichenkettenliterale vor und nach der Umstellung zeigt
unter `thermoctl/` **keine einzige** Änderung. Was sich geändert hat, sind Assertion-Texte
in `tests/`.

Kommentare, Docstrings und **alle 794 Testnamen** sind übersetzt. Ein Testname ist eine
Zusicherung: `test_rule6_exactly_on_the_switch_on_threshold_does_not_switch_on_yet` sagt
dasselbe wie vorher, nicht weniger. Ein allgemeinerer Name wäre grün geblieben und hätte
beim nächsten Lesen niemandem mehr verraten, was hier eigentlich zugesichert ist.

Englisch sind damit: Bezeichner, Datei- und Modulnamen, Web-Endpunkte, MQTT-Topics,
MCP-Werkzeuge, Formularfelder, Spaltennamen, Kommentare, Docstrings und alle 794 Testnamen.
Deutsch geblieben ist, was ein Mensch liest — und ein Zitat: Der Defekt des Altsystems
steht als `if ist < soll: an, sonst aus` im Test, weil das der Quelltext von dort ist.

**HTML und CSS sprechen jetzt auch Englisch.** Element-Kennungen, CSS-Klassen,
`data-`-Attribute, die Vorlagen selbst (27 Dateien), die vier JavaScript-Dateien und die
Jinja-Makros darin (`geraetekarte` → `device_card`, `flussbild` → `flow_diagram`) tragen
englische Namen. Dieser Schritt hat keinen Testschutz: Eine Klasse steht gleichzeitig in
Vorlage, Stylesheet und JavaScript, und wer eine Stelle vergisst, bekommt kein rotes
Testergebnis, sondern eine Seite, die still anders aussieht. Genau das passierte zweimal:
Das JavaScript las `dataset.aenderbar`, während die Vorlage schon `data-editable` schrieb —
der Ziehhinweis im Zeitplan verschwand ersatzlos. Und das Formular zum Anlegen eines
Schaltpunkts schickte `name="uhrzeit"`, während die View `time_of_day` liest; angelegt
wurde stillschweigend nichts. Beides fand kein Test, sondern der Vergleich von
Bildschirmfotos vor und nach der Umstellung und ein Klick im Browser.

Der zweite Fall hat eine Ursache, die bleibt: Die Makros in `form.html` leiten `name=` und
`id=` aus ihrem ersten Argument ab. Wer eine View umbenennt und die Vorlage vergisst,
bricht das Formular, ohne eine einzige Zeile Formularcode anzufassen. Dagegen steht jetzt
ein Wächtertest in `tests/test_smoke_test.py`, der die Feldnamen aus dem *gerenderten*
Formular zieht und nur Werte beisteuert — er wurde von beiden Seiten gegengeprüft: Er wird
rot, wenn die Vorlage abweicht, und er wird rot, wenn die View abweicht.

Zehn der elf geprüften Seiten sind pixelgleich zu den Aufnahmen davor; die elfte
unterscheidet sich in einem Satz, der eine Zeitspanne nennt.

Die letzten deutschen Bezeichner im Schema sind weg: `user_passkey.bezeichnung` heißt
`label`, `passkey_challenge.zeremonie` heißt `ceremony` (Migration `f2c6d90a41b8`, mit
einem Test, der die Daten über beide Richtungen hinweg verfolgt — `batch_alter_table` baut
die Tabelle unter SQLite neu, und eine misslungene Kopie sähe hinterher richtig aus und
wäre leer). Dazu `NAMENSKONVENTION` → `NAMING_CONVENTION` und der JSON-Schlüssel der
Passkey-Registrierung.

## Konfigurierbare Bediengeräte

Zigbee2MQTT-Merkmale aus `bridge/devices` werden jetzt mit Zugriff, Typ, Einheit,
Wertebereich und Auswahlwerten gespeichert. Unter `/controllers` lassen sich lesbare
Merkmale auf Sollwert oder Betriebsart einer Zone und schreibbare Merkmale auf
Sensor-/Zonentemperatur, Zonensollwert oder einen festen Wert legen. Die Tastenbelegung
wird dort bearbeitet; die Zonenseite verweist nur noch auf die neue Stelle.

Schreibkanäle sind doppelt abgesichert: Die Domäne nimmt sie ausschließlich für Geräte an,
die Bediengerät und **nirgends Aktor** sind, und der Veröffentlichungszyklus prüft dieselbe
Bedingung vor jedem Versand erneut.

Die zweite Hälfte dieser Bedingung kam bei der Gegenlesung dazu und ist der Grund, warum es
sie gibt: Zu prüfen, ob ein Gerät *irgendwo* Bediengerät ist, reicht nicht. Ein Thermostat
kann in einer Zone Aktor sein und in einer anderen als Bediengerät hängen — es zeigt ja
einen Sollwert an. Ein Schreibkanal auf sein `occupied_heating_setpoint` wäre dann als
bloße Anzeige angemeldet und bewegte trotzdem ein Ventil, mit `switches=False` an beiden
Riegeln des Trockenlaufs vorbei. Die MQTT-Nachricht trägt `switches=False` und wird nur bei einem
geänderten Wert (oder nach Prozessneustart) gesendet. Lesekanäle verwenden wie
Tastendrücke den Messzeitpunkt als Wiederholungsschutz.

Was nur der Projektinhaber kann: Am echten Bediengerät prüfen, ob `sensor: external` und
`external_temperature` tatsächlich auf dem Display erscheinen und ob ein am Gerät
verstellter Sollwert zurück in die Zone gelangt.

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
| Tests | 1024, grün unter SQLite **und** MariaDB |
| Testabdeckung | 98,55 %, Mindestschwelle 97 % in der CI |
| Ruff, mypy strict | ohne Befund, 90 Quelldateien |
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
das Flussbild — in beide Richtungen: Ein Gerät geht aus dem Vorrat auf eine Stufe und von
dort auch wieder zurück, was es aus der Zone nimmt. Beides im Browser nachgestellt, für die
Messquelle (eine Spalte an der Zone) wie für eine Rolle (eine eigene Zeile).

**Die Geräteseite beantwortet jetzt die Frage, mit der man hinkommt.** Vorher stand dort
eine Tabelle mit neun gleich gewichteten Spalten, in der die meisten Zellen ein
Gedankenstrich waren; bei vierunddreißig Geräten fand man darin nichts. Jetzt trägt jedes
Gerät seinen Befund im Klartext — offline, seit zwei Tagen still, Batterie bei 8 %,
schwacher Funk —, das Auffällige steht in einem eigenen Block oben, und ein Suchfeld
filtert nach Name, Modell, Fähigkeit oder Zone. Die Schwelle, ab der ein Gerät als stumm
gilt, ist dieselbe, nach der die Regelung einen Sensor aufgibt: eine zweite Zahl hieße,
dass die Liste ein Gerät für gesund hält, das die Regelung schon abgeschrieben hat.
Weggelassen ist alles, was sich auf jeder Zeile wiederholte — ein Kärtchen „Batteriestand"
neben der Prozentzahl sagt nichts, was die Zahl nicht sagt, und „ohne Zone" bei jedem der
vierunddreißig Geräte steht jetzt einmal oben als Zahl.

**Home Assistant bedient jetzt wirklich eine Zone.** Bis hierher gab es dort einen
Thermostat und sonst nichts. Jetzt ist jede Zone ein eigenes Gerät mit dreizehn Entitäten:
Thermostat, **Boost**-Knopf, letzte Schaltung und nächster Moduswechsel als Zeitstempel, je
Modus eine Solltemperatur und je Regelparameter eine Zahleneingabe. Was ein Befehl bewirkt,
steht in `domain/fernbedienung.py` — dieselben Funktionen mit denselben Grenzen, die auch
die Oberfläche benutzt.

Vier Entscheidungen darin:

- **Der Thermostat verstellt den Modus, nicht „jetzt gerade".** Vorher legte er eine
  Übersteuerung an; die wäre nach dem nächsten Schaltpunkt weg gewesen, und der Regler
  spränge scheinbar von selbst zurück.
- **Boost zieht die nächste Schaltung vor** — was ohnehin käme, gilt ab sofort und genau
  bis zu dem Zeitpunkt, an dem es planmäßig gekommen wäre. Ein Boost auf einen festen Wert
  müsste raten, wie warm und wie lange.
- **Der Trockenlauf steht nicht mehr im Zonennamen.** Home Assistant leitet die
  Entitätskennung beim ersten Auftauchen aus dem Namen ab: Eine Zone, die zuerst im
  Trockenlauf erschien, hieß danach für immer `climate.thermoctl_zone_1_trockenlauf`. Jetzt
  sagt es eine eigene Entität für den ganzen Dienst, und die Discovery-Nutzlast ist
  trocken und scharf Byte für Byte gleich — am Broker nachgemessen.
- **Alles Bleibende geht mit retain hinaus**, und ein Befehl wird sofort beantwortet statt
  erst im nächsten Regelzyklus.

**Bediengeräte tun etwas.** Die Rolle `controller` war bis hierher eine Zuordnung ohne
Wirkung: Ein Gerät an der Wand konnte zugeordnet werden, aber ein Tastendruck fiel durch —
das Feld `action` stand nicht in der Feldzuordnung. Jetzt landet jeder Druck als Messwert
in der Datenbank, und was er auslöst, steht in `controller_binding`.

**Es wird nicht geraten, sondern zugehört.** Wie ein Gerät seine Tasten nennt, entscheidet
Zigbee2MQTT je Modell — `single_plus`, `button_1_single`, `up_open`. Eine Tabelle dieser
Namen im Quelltext wäre harte Verdrahtung (Grundsatz 1) und für jedes Gerät falsch, das
noch nicht darin steht. Stattdessen zeigt die Oberfläche unter *Zone → Geräte →
Tastenbelegung*, welche Aktionen dieses Gerät **wirklich** geschickt hat: einmal jede Taste
drücken, neu laden, zuordnen. Belegbar sind fünf Dinge — wärmer, kälter (Schrittweite je
Taste), nächste Schaltung vorziehen, Aus, Automatik. Mehr gehört nicht auf einen Knopf, an
dem man im Vorbeigehen nicht sieht, was man tut.

Zwei Dinge, die dabei auffielen: Eine **behaltene Nachricht** wird bei jeder Neuverbindung
erneut zugestellt — ohne Vergleich des Messzeitpunkts löste ein Wackelkontakt denselben
Tastendruck immer wieder aus. Und eine **Schrittweite mit zwei Nachkommastellen** erzeugte
Sollwerte, die die Domäne ablehnt; die Spalte trägt jetzt eine Stelle, so viel wie ein
Sollwert auch.

Was **nicht** gebaut ist: das Display eines Bediengeräts mit Werten aus thermoctl zu
speisen. Unter welchem Schlüssel Zigbee2MQTT eine externe Temperatur für den W100
entgegennimmt, ist ohne das Gerät in der Hand nicht zu verifizieren.

**Boost und die Regelparameter gibt es auch über REST und MCP.** Die Logik liegt in
`domain/fernbedienung.py` und `domain/zone_settings.py`, die Adapter sind dünn —
Grundsatz 6. Neu sind `POST /api/v1/zones/{id}/boost`,
`PUT /api/v1/zones/{id}/parameters/{name}` sowie die MCP-Werkzeuge `boost`,
`regelparameter_lesen` und `regelparameter_setzen`.

Zwei Entscheidungen dabei: Ein **einzelner** Parameter statt nur des PUT auf alle sechs —
wer sonst nur die Hysterese ändern will, müsste erst alle lesen und wieder mitschicken und
schriebe dabei jeden geerbten Wert als Zonenabweichung fest. Und `regelparameter_lesen`
liefert die **Grenzen mit**: Ohne sie wäre für ein Sprachmodell jeder Schreibversuch ein
Versuch, „0,05 Kelvin Hysterese" sieht so plausibel aus wie „0,5".

**Drei Fehler, die dabei aufgefallen sind:**

1. **Die Moduswahl in Home Assistant tat scheinbar nichts.** Die Climate-Karte dort ist
   nicht optimistisch — sie wartet auf den Zustand. Der kam erst im nächsten Regelzyklus,
   also sprang die eben gewählte Betriebsart eine Minute lang zurück.
2. **`betriebsart_setzen` setzte den Fremdschlüssel statt der Beziehung.** Ein bereits
   geladenes `zone.operating_mode` blieb damit alt, und die neue Sofortantwort hätte den
   *alten* Modus gemeldet. Gefunden vom Test der Sofortantwort, nicht beim Lesen.
3. **`ende_der_naechsten_schaltung` griff zur echten Uhr**, während der Aufrufer mit einem
   übergebenen Zeitpunkt rechnete. Für den Boost hieß das: Die Übersteuerung endete
   irgendwann, nur nicht an ihrem Schaltpunkt. Fiel unter MariaDB zusätzlich auf, weil
   dort `DATETIME` sekundengenau ist und zwei Übersteuerungen derselben Sekunde beliebig
   sortierten — jetzt entscheidet die Kennung.

**Der Zeitplan-Editor riss das Gitter unter der Maus weg.** Ein Klick holte das
Anlege-Formular mit `scrollIntoView` heran; wer zwei Punkte nacheinander setzen wollte,
klickte beim zweiten Mal auf dieselbe Bildschirmstelle und traf eine völlig andere Uhrzeit.
Im Browser gemessen: ein Klick verschob das Gitter um 377 px, bei rund 415 px für den ganzen
Tag also um mehr als zwölf Stunden. `focus({preventScroll: true})` genügte nicht — der
Aufruf scrollte in Chromium trotzdem, gemessen 377 gegen 0 ohne ihn. Statt zu scrollen
markiert ein Klick die Stelle jetzt im Gitter selbst, dort wo die Maus ist.

**Vier Bezeichnungen tragen ihre Umlaute** — „Verbindungsqualität", „Beleuchtungsstärke",
„Bediengerät", „Weboberfläche". Sie standen seit den Nachschlagetabellen transliteriert da
und stehen auf jeder Gerätekarte, in der Rollenspalte und in jeder Audit-Zeile. Die
Migration ändert nur, was noch den alten Wortlaut trägt: Wer eine Bezeichnung von Hand
angepasst hat, behält seine.

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
- **Öffentliches Dashboard für ein Wandtablet** (Monitoring und Bedienung). Achtung,
  Grundsatz 4: Eine wirklich unauthentifizierte Seite widerspricht ihm. Vorschlag ist ein
  langlebiges, widerrufbares Kiosk-Token mit engem Rechtesatz — vor dem Bau bestätigen.
- **Zigbee-Heizkörperthermostate (WT-A03E)** anbinden. Ein Thermostatventil ist kein
  Schalter: Es braucht `system_mode` und `occupied_heating_setpoint` statt `state: ON`,
  und beides bewegt ein Ventil — also `switches=True` und beide Riegel des Trockenlaufs.
- **Optional: Sonnenprognose-Absenkung.** In der Übergangszeit soll eine Dachwohnung
  morgens die Sonne nutzen statt der Heizung. Braucht eine Prognosequelle, eine
  Zonen-Eigenschaft (welche Zone bekommt wie viel Sonne) und eine begrenzte Absenkung —
  physisch wirksam, also Grundsatz 7.

- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unverändert die Rückfallebene.**
