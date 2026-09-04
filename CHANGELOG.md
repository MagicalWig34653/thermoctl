# Änderungen

Alle nennenswerten Änderungen an `thermoctl`, neueste zuerst. Das Format folgt lose
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen
[semantischer Versionierung](https://semver.org/lang/de/).

Der Stand im Einzelnen — was gebaut ist, was an der echten Anlage noch aussteht und warum
etwas so entschieden wurde — steht in [docs/STATUS.md](docs/STATUS.md).

---

## Unveröffentlicht

### Hinzugefügt

- thermoctl läuft jetzt unter einem konfigurierbaren Pfadpräfix (`THERMOCTL_ROOT_PATH`)
  — Voraussetzung für den Betrieb als Home-Assistant-Add-on hinter dessen Ingress-Proxy.
  Weiterleitungen, Cookies, Vorlagen, eigenes JavaScript und statische Dateien tragen
  den Präfix jetzt durchgehend; ohne gesetztes Präfix ändert sich nichts. Details in
  [docs/STATUS.md](docs/STATUS.md).
- Docker-Abbild wird jetzt für `linux/amd64` und `linux/arm64` gebaut (Home-Assistant-
  Add-on-Vorbereitung), `armv7` bewusst nicht.
- `docker/entrypoint.sh` übersetzt eine vorhandene `/data/options.json` (Home-Assistant-
  Supervisor) in `THERMOCTL_*`-Umgebungsvariablen; ohne die Datei ändert sich am
  bisherigen `docker compose`-Betrieb nichts, eine vom Betreiber gesetzte Variable hat
  immer Vorrang.

---

## 0.6.1 — 2026-09-04

**Die erste Fassung, die Fremde zu Gesicht bekommen.** Sie macht das Repository
veröffentlichbar — vor allem, weil es jetzt überhaupt eine Lizenz hat — und räumt eine
Reihe von Spuren aus der Entwicklung beim Projektinhaber aus Oberfläche und Dokumentation.
Am Regelverhalten selbst ändert diese Fassung nur eine protokollierte Begründung, keine
Entscheidung.

### thermoctl steht unter der AGPL-3.0

Bisher hatte das Projekt gar keine Lizenz — damit galt striktes Urheberrecht, und niemand
hätte die Software rechtmäßig benutzen dürfen. Das war der einzige harte Blocker vor einer
Veröffentlichung. Die Wahl fällt auf die AGPL, weil `thermoctl` eine Weboberfläche ist:
Ohne den Netzwerk-Zusatz aus §13 könnte jemand den Code nehmen, eine gehostete
Heizungssteuerung als Bezahldienst betreiben und nie etwas zurückgeben — unter MIT wie
unter gewöhnlicher GPL gleichermaßen zulässig, weil die Software dabei nie weitergegeben
wird. `LICENSE` ist der unveränderte Wortlaut von gnu.org; `pyproject.toml` trägt die
Lizenzangabe nach PEP 639, und das gebaute Paket weist `License-Expression:
AGPL-3.0-only` nach. Dazu, wie es §13 AGPL für netzseitig genutzte Software verlangt: Die
Grundvorlagen `base.html` und `base_plain.html` tragen jetzt eine Fußzeile mit
Lizenzangabe und der Repository-Adresse — sichtbar auf jeder Seite, die eine der beiden
Vorlagen nutzt, einschließlich der Anmeldeseite. **Offen:** Das eigenständige
Kiosk-Dashboard (`kiosk.html`) erbt keine der beiden Grundvorlagen und hat noch keinen
eigenen Hinweis.

### Fix: die protokollierte Begründung bei „unveraendert" nannte oft nicht den echten Sachverhalt

Beide Zweige der Entscheidung `unveraendert` in `decide()` schrieben unabhängig vom
tatsächlichen Abstand denselben Satz „... innerhalb der Hysterese um Soll ... ± hK ... —
Zustand bleibt.", wurden aber auch dann erreicht, wenn der Messwert weit jenseits der
*gegenüberliegenden* Bandkante lag — die Entscheidung (Zustand halten) war dabei stets
richtig, nur der Text falsch. An 18.527 echten `unveraendert`-Entscheidungen lag keine
einzige tatsächlich im Band; mittlerer Abstand 5,90K, grösster 11,40K. Die Begründung
unterscheidet jetzt „echt im Band" von „jenseits der gegenüberliegenden Kante, Zustand
bleibt, weil er schon lief bzw. schon aus war". **Keine Entscheidung hat sich geändert**
(unveränderte 2.376-Kombinationen-Tabelle in `tests/test_control_loop_state_table.py`).
**Alteinträge im Schattenprotokoll behalten den bisherigen, falschen Text** — sie werden
nicht nachträglich korrigiert. Wer ältere `unveraendert`-Einträge liest, muss das wissen:
Der protokollierte Grund war dort systematisch falsch, die getroffene Entscheidung nicht.

### Oberfläche von Umstiegs-Jargon bereinigt

`control.html` nannte den Trockenlauf-Vergleich einmal „gegen das Altsystem" und einmal
„Schattenbetrieb", und die Checkliste vor dem Scharfschalten versprach, das Altsystem bleibe
Rückfallebene — für einen Betreiber ohne Altsystem alles ohne Bedeutung. Beide Stellen nennen
den Zustand jetzt einheitlich „Trockenlauf"; der Altsystem-Punkt ist ersatzlos gestrichen, der
Vergleichssatz durch eine für jeden Betreiber gültige Aussage ersetzt (Entscheidungen lassen
sich beobachten, bevor sie etwas schalten). Der nie erreichte Schnittstellen-Zustand
`not_built` ist aus Domänenlogik, Vorlage und Test entfernt. Zusätzlich ist der reale
Rechnername des Altsystems (`vm130-nginx`) aus allen Fundstellen in der Dokumentation
durch eine neutrale Umschreibung ersetzt.

### Wirkungswächter erkennt deutsche Komposita ohne Trennzeichen

`tests/test_user_visible_effect_texts.py` prüfte deutsche Komposita bisher nur, wenn sie
namentlich im Vokabular standen — die führende Wortgrenze (`\b`) vor jedem Substantiv
verhinderte, dass „Zirkulationspumpe" oder „Ölbrenner" als Treffer für „pumpe" bzw.
„brenner" erkannt wurden. Die führende Grenze fällt jetzt bei den Substantiven, die
typischerweise als Zweitglied auftreten (Ventil, Aktor, Heizkörper, Heizkreis,
Fußbodenheizung, Stellantrieb, Boiler, Brenner, Pumpe) sowie bei den Verb-/Adjektivstämmen
`heiz`, `wärm`/`waerm` und `warm`, sodass ein Teilstring-Treffer jedes Kompositum fängt.
`schalt` und `geschaltet` behalten ihre führende Grenze bewusst — ohne sie würde
„Schaltfläche" (eine UI-Schaltfläche, keine physische Schaltbehauptung) mitgefangen. Die
dadurch neu gemeldeten 27 Fundstellen wurden durchgesehen und in
`tests/approved_physical_vocabulary.json` eingetragen; keine davon war eine falsche
Wirkbehauptung. **Ausserdem:** Der Wächter durchsuchte bisher das Dateisystem statt die
Versionsverfolgung — als die Bauprozess-Dokumentation und die Altsystem-Bestandsaufnahme
das Repository verliessen (siehe unten), meldete er deren Vorkommen fälschlich als
ungeprüft, statt sie als nicht mehr vorhanden zu behandeln. Er fragt jetzt `git`, was
tatsächlich verfolgt wird, und fällt ohne `git` darauf zurück, alles zu prüfen statt
stillschweigend nichts.

### Bauprozess-Dokumentation und Altsystem-Bestandsaufnahme verlassen das Repository

Verlauf, getroffene Entscheidungen, Spezifikationen, Pläne und das interne
Umbenennungswerkzeug beschreiben ausschliesslich den eigenen Bauprozess; die
Altsystem-Bestandsaufnahme beschreibt ein fremdes, reales System mit vollständigem
Schema, MQTT-Topic-Vertrag und einer privaten IP-Adresse. Beides eignet sich nicht für
ein öffentliches Repository und ist entfernt — die Dateien bleiben lokal liegen und
stehen in `.gitignore`. Rund fünfzig Verweise darauf im übrigen Repository sind aufgelöst,
statt auf nicht mehr vorhandene Dateien zu zeigen.

### Bekannt: Teilprojekt 2 gilt an der echten Anlage noch nicht als abgenommen

Ein Abgleich der eigenen Anlage des Projektinhabers gegen die drei Abnahmekriterien aus
`docs/inbetriebnahme-schattenbetrieb.md` (Bericht: `docs/phase-2-abnahme.md`) ergab: Der
mehrtägige Schattenbetrieb vor dem Scharfschalten wurde für fünf von sechs Zonen
tatsächlich übersprungen, sodass Teilprojekt 2 auf dieser Grundlage formal nicht als
abgenommen gilt. Betroffen ist die Nachweisführung, nicht die Regellogik — in keinem
geprüften Fall hat die Regelung falsch entschieden; der oben beschriebene
Begründungstext-Fehler wurde bei genau dieser Auswertung gefunden.

### Zu beachten beim Umstieg

- **Keine Schemaänderung.** Diese Fassung bringt keine neue Migration mit; `alembic
  upgrade head` bleibt bei der letzten Revision aus 0.6.0 (`635612893955`) stehen. Ein
  Rückweg auf 0.6.0 braucht deshalb keinen Datenbank-Downgrade.

---

## 0.6.0 — 2026-09-03

**Nacharbeiten an der PI-Regelung aus 0.5.0**, ausgelöst durch eine reale Anlage des
Projektinhabers: ein Raum mit einem selbstregelnden Heizkörperthermostat neben einer
Meross-Steckdose.

### PI-Eignungsprüfung ist jetzt gerätegenau

Bisher schloss ein einziges selbstregelndes Thermostatventil in einer Zone PI für die
ganze Zone aus, unabhängig davon, was sonst noch daran hing. Ein selbstregelndes Ventil
bekommt seinen Sollwert aber über einen eigenen Weg und sieht die PI-Entscheidung nie —
`switch_commands()` und `thermostat_commands()` filtern beide mit
`ZoneDevice.self_regulating.is_(False)`, ein selbstregelndes Ventil taucht in keiner der
beiden Befehlslisten auf. **Ein selbstregelndes Ventil darf jetzt neben einem
Schaltaktor in derselben Zone stehen**; PI steuert dann nur noch den Schaltaktor.

**Was weiterhin ausschliesst:** ein Thermostatventil **ohne** eigene Regelung. Das
bekommt die PI-Entscheidung sehr wohl, als Sollwertsprünge aus „heizen ja/nein" — dafür
wäre die schnelle Taktung von PI falsch.

### Angenommene Relaislebensdauer ist einstellbar

Bisher stand die Zahl fest im Code, mit 100.000 Schaltungen. Jetzt eine anlagenweite
Einstellung, Vorgabe **500.000**, Grenzen 1.000 bis 10.000.000. Der Warnhinweis am
PI-Schalter rechnet live gegen den eingestellten Wert: Aus „rund 2,6 Relaislebensdauern
im Jahr" im ungünstigsten Fall werden bei der neuen Vorgabe rund 0,53.

**Die Zahl bleibt ausdrücklich eine Annahme, keine Herstellerangabe** — öffentliche
Meross-Daten nennen keine Lebensdauer. Gerade weil sie jetzt einstellbar ist, bleibt
wichtig, dass hier eine Annahme geändert wird und keine Messung.

### Zu beachten beim Umstieg

- **Eine Migration** (`635612893955`) legt die neue Einstellung an und setzt sie auf
  500.000. Das Container-Abbild führt sie beim Start selbst aus. Der Rückweg auf 0.5.0
  braucht wie immer vorher einen einmaligen `alembic downgrade` mit dem neuen Abbild —
  eine Revision zurück, auf `d2f4a7c91e63`:

  ```bash
  docker compose stop thermoctl
  docker compose run --rm --no-deps --entrypoint alembic thermoctl downgrade d2f4a7c91e63
  ```

  Danach in `compose.yml` die Marke des Dienstes von `:latest` auf `:0.5.0` ändern und
  starten — siehe [„Aktualisieren und zurückgehen"](docs/self-hosting.md#6-aktualisieren-und-zurückgehen).
- **Nichts schaltet sich von selbst ein.** PI bleibt je Zone aus.

### Dokumentation nachgezogen

PI und Relaisverschleiss standen bisher nur in `STATUS.md`. README, `docs/api.md`,
`docs/mcp.md` und `docs/self-hosting.md` kennen sie jetzt.

### Browsertests (nur örtlich)

Dreizehn Playwright-Tests prüfen, was ein HTTP-Test nicht sieht — Stylesheet-Wirkung,
Browserkonsole, Anmeldung über hx-boost, Zeitplan-Zeichnen, den PI-Schalter, Sichtbarkeit
von Menüpunkten nach Recht, das Kiosk-Dashboard. Anlass war `CLAUDE.md`: Zweimal sind
grundlegende Fehler durch alle Tests gerutscht und erst beim Öffnen der Seite aufgefallen.
**Sie laufen nicht in der CI und nicht in der gewöhnlichen Suite** — wer nichts tut, merkt
nichts davon, zahlt aber auch keine Startzeit. In der Oberfläche selbst wurde dabei kein
Fehler gefunden.

---

## 0.5.0 — 2026-09-02

**Die Fassung mit der PI-Regelung — als Beta, je Zone einschaltbar, aus als Vorgabe.**
Wer nichts einschaltet, bekommt exakt das Verhalten von 0.4.0: Die PI-Entscheidung wird
für eine Zone ohne den Schalter nicht einmal berechnet, und die Regelkette darunter ist
unverändert.

### PI-Regelung (Beta)

Ein Zweipunktregler pendelt um den Sollwert; der Integralanteil beseitigt genau das. Das
ist der ehrliche Nutzen, und alles andere, was man sich davon erhofft — weniger
Verbrauch, schnelleres Aufheizen — folgt daraus nicht.

- **Nur für gewöhnliche Schaltaktoren.** Thermostatventile und selbstregelnde Ventile
  bleiben ausgeschlossen: Sie haben einen eigenen Regler, und PI darüber wären zwei
  Integratoren auf derselben Regelstrecke. Eine Zone, die sich nicht eignet, sagt das
  **vor** dem Einschalten mit Grund, statt still auf Hysterese zurückzufallen.
- **Zeitproportional**, mit eigenen, kürzeren Mindestschaltdauern. Der Projektinhaber hat
  entschieden, dass für PI die Genauigkeit die Mindestdauer überbieten darf — eine lange
  Mindestdauer rundet einen kleinen Tastgrad sonst um 25 Prozent hoch (0,05 wird zu
  0,0625). Mit 60 Sekunden wird er exakt getroffen.
- **Die sieben Vorrangregeln behalten absoluten Vorrang.** Fenster offen, Frostschutz,
  Sensorausfall, Übersteuerung, Boost, Ventilschutz — jede sagt weiterhin „nicht heizen",
  und der Integrator wird je Regel einzeln behandelt, damit kein Windup entsteht. Ein
  Wächtertest zwingt jeden Ergebniscode der Regelkette zu einer ausdrücklichen
  Einordnung, damit ein künftig ergänzter nicht stillschweigend durchfällt.
- **Ausschalten neutralisiert den Reglerzustand vollständig.** Ein späteres
  Wiedereinschalten beginnt nicht mit Werten aus einem früheren Versuch.

**Der Preis steht in der Oberfläche, am Schalter und nicht in einer Fußnote:** PI schaltet
deutlich häufiger und verkürzt die Lebensdauer des Schaltaktors spürbar. Hinter einem
Info-Zeichen die Rechnung — ungünstigster Fall 262.800 Schaltspiele im Jahr, bei
30 Prozent Tastgrad simulierte 157.680, Hysterese höchstens 52.560. Mit einer
ausdrücklich austauschbaren Annahme von 100.000 Betätigungen sind das rund 2,6
Relaislebensdauern im Jahr; eine Meross-Herstellerangabe dazu gibt es nicht, und das
steht dabei. Für einen Kessel oder Verdichter ohne eigenen Taktschutz ist PI ungeeignet.

### Relaisverschleiß

Eine neue Seite unter `/relay-wear` zeigt Schaltspiele je Gerät und Tag mit
Jahreshochrechnung — auch ohne PI nützlich, denn Verschleiß entsteht auch durch
Hysterese, nur langsamer. Gezählt wird der **bestätigte Zustandswechsel**; ein Befehl,
der denselben Zustand nochmal setzt, ist keiner, und ein gescheiterter Befehl zählt
nicht, weil niemand weiß, ob er Hardware bewegt hat. Das ergibt eine belastbare
Untergrenze statt eines unsicheren Versuchs, der wie gemessener Verschleiß aussieht.
Die Seite verlangt `audit.read` und zeigt nur Geräte aus lesbaren Zonen.

### Zu beachten beim Umstieg

- **Eine Migration** (`d2f4a7c91e63`) legt die PI-Felder an und setzt jede bestehende
  Zone auf „PI aus". Das Container-Abbild führt sie beim Start selbst aus. Der Rückweg
  auf 0.4.0 braucht wie immer vorher einen einmaligen `alembic downgrade` mit dem neuen
  Abbild — siehe [„Aktualisieren und zurückgehen"](docs/self-hosting.md).
- **Nichts schaltet sich von selbst ein.** PI ist je Zone aus, bis jemand es einschaltet.

### Prüfkraft

`domain/pi_control.py` wurde mit `cosmic-ray` gemessen: 664 Mutanten, zunächst 53
Überlebende, nach dem Schließen von 45 echten Testlücken noch 8 — jeder einzeln als
gleichwertig begründet. Dabei kam eine Falle heraus, die alle Mutationsläufe betraf: Die
Konfigurationen verweisen relativ auf `.venv/bin/python`, das es in einem Worktree nicht
gibt, und ein vollständig gescheiterter Lauf meldete daraufhin **null Überlebende** — ein
perfektes Ergebnis, das keines war. Jede Konfiguration warnt jetzt davor.

---

## 0.4.0 — 2026-09-02

**Wer 0.3.0 scharf betreibt, sollte aktualisieren.** Diese Fassung behebt die ersten
beiden echten Fehler in der Regelkette. Beide lagen an der Grenze zwischen Ventilschutz
und Mindestschaltdauern, beide konnten das Verhalten von Relais und Ventilen verändern,
und beide wurden erst gefunden, nachdem die Anlage bereits an einer echten Heizung lief.

### Das Wichtigste zuerst

- **Ein gesetzter Ventilschutz-Marker hob die Mindestschaltdauern auch dann noch auf,
  wenn der Schutzlauf gar nicht mehr entschied.** Das betraf Zonen mit eingeschaltetem
  Ventilschutz, wenn eine Übersteuerung, die Betriebsart „aus“ oder ein Sensorausfall
  einen laufenden Schutzlauf verdrängte. Bis zum Ende der voreingestellten zehn Minuten
  konnte ein Relais dadurch ohne seinen Taktschutz umschalten; bei einer Minute
  Zykluszeit waren bis zu zehn Schaltvorgänge möglich. Jetzt gilt die Ausnahme nur,
  solange der Ventilschutz den Ein-Zustand tatsächlich verlangt.
- **Ein Ventilschutzlauf konnte danach zu regulärem Heizen werden.** Das betrifft nur
  eine Zone, deren Mindest-Einschaltdauer **länger** ist als ihr Ventilschutzlauf: Nach
  dem Lauf hielt die Mindestdauer das Ventil offen, danach übernahm die Hysterese den
  Ein-Zustand als gewöhnliches Heizen. Mit den Vorgabewerten ist die Anlage von diesem
  zweiten Fehler **nicht betroffen**: 300 Sekunden Mindest-EIN sind kürzer als der
  600 Sekunden lange Schutzlauf. Wer die Werte umgekehrt eingestellt hat, ist betroffen
  und sollte aktualisieren. Ein Schutzlauf endet jetzt mit seiner eigenen Laufdauer;
  die Mindest-Einschaltdauer für reguläres Heizen bleibt erhalten.

Beide Fehler sind durch eine vollständige Zustandstabelle der Regelkette abgesichert.
Sie prüft 2.376 erreichbare Kombinationen aus den Vorrangregeln, statt nur einzelne
Beispiele nachzustellen.

### Zu beachten beim Umstieg

- **Zwei Migrationen** laufen im Container beim Start von selbst: Die erste ersetzt den
  Index der Schattenhistorie, die zweite (`f6a9d4c12b70`) ergänzt die Aufbewahrungsfrist.
  Der Rückweg auf 0.3.0 braucht vor dem Wechsel des Abbilds einen einmaligen manuellen
  Downgrade; die kopierbare Reihenfolge steht unter
  [„Aktualisieren und zurückgehen“](docs/self-hosting.md#6-aktualisieren-und-zurückgehen).
- **Das Schattenprotokoll wird jetzt voreingestellt 365 Tage aufbewahrt.** Wer eine
  andere Frist braucht, stellt sie vor der ersten täglichen Bereinigung um. Bei zehn
  Zonen und einem Zyklus je Minute entspricht ein Jahr gemessen rund 811 MiB.

### Neu

- **Eigene Aufbewahrungsfrist für das Schattenprotokoll.** Der bisher unbegrenzt
  wachsende Entscheidungsverlauf wird täglich und blockweise bereinigt; einstellbar sind
  1 bis 3.650 Tage, Vorgabe sind 365. Messwerte behalten ihre unabhängige Frist,
  Audit- und Schaltprotokoll bleiben unbegrenzt. Der verdichtete Heiznachweis für den
  Ventilschutz bleibt erhalten, sodass gelöschte Einzelentscheidungen dessen Fälligkeit
  nicht verändern. Diese neue Betriebseinstellung ist der Grund für 0.4.0 statt 0.3.1.

### Sicherheit und Rechte

- **Vier Wege reichten für zonenbeschränkte Personen weiter als erlaubt.** Über einen
  Bediengerätekanal ließ sich eine fremde Zone verändern; die Tastenbelegung eines
  geteilten Reglers wirkte auch in nicht erlaubten Zonen; ein Kiosk-Token galt außerhalb
  der engen Kioskoberfläche als vollwertiges REST- und MCP-Token; und die
  Bediengeräteseite zeigte die gesamte Geräteliste auch ohne `device.read`. Alle vier
  Wege verlangen jetzt die Rechte für das tatsächlich betroffene Ziel.
- **Anmeldeversuche halten den Regelzyklus nicht mehr an.** Wartezeit und teure
  Passwortprüfung blockierten zuvor dieselbe Ereignisschleife wie die Heizentscheidung;
  der Fehlversuchsspeicher ist nun außerdem begrenzt.
- **Ein Passwortwechsel beendet jetzt alle anderen Sitzungen.** Die aktuelle Sitzung
  bleibt bestehen. Unter `/users` gibt es zusätzlich „Andere Sitzungen beenden“, ohne
  dass dafür das Passwort geändert werden muss.
- **Das Einrichtungs-Token läuft nach einer Stunde ab.** Ein altes oder weitergegebenes
  Log kann damit nicht Wochen später noch zur Einrichtung des ersten Verwalters dienen;
  nach Ablauf erzeugt ein Neustart ein neues Token.
- **Meross-Antworten werden nicht mehr allein anhand der Nachrichtenkennung als Erfolg
  verbucht.** Signatur und Befehlsart müssen passen; enthält die Antwort Kanal und
  Schaltzustand, müssen auch sie dem gesendeten Befehl entsprechen. Ein Fehlschlag wird
  im nächsten Zyklus erneut versucht. Eine leere `SETACK`-Antwort bleibt gültig, weil
  die echte Hardware so antwortet; eine zusätzliche Zustandsabfrage ist nicht gebaut.
- **Der Webhook folgt keiner Weiterleitung mehr.** Damit kann ein übernommenes Ziel den
  Bearer-Token nicht per HTTP-Weiterleitung zu einem anderen Host oder einer internen
  Adresse mitnehmen.
- **Ein Wächter prüft jeden neuen Sitzungscookie auf die Einstellung für sichere
  Cookies.** `THERMOCTL_SECURE_COOKIES` bleibt aus Rücksicht auf die Ersteinrichtung per
  HTTP in der Vorgabe auf `false`; hinter TLS gehört es ausdrücklich auf `true`.

### Betrieb und Einordnung

- **Ein passender Index verkürzt den Regelzyklus bei großer Schattenhistorie deutlich.**
  Gemessen mit 20 Zonen und einem Jahr Historie sank er unter SQLite von 3.656 auf
  891 ms. Der alte Einzelindex auf dem Zeitpunkt fiel weg, weil keine Betriebsabfrage
  ihn ohne gleichzeitige Eingrenzung auf eine Zone benutzt.
- **Diese Korrekturdichte ist das Ergebnis eines Komplettreviews in sieben Runden.**
  Geprüft wurden unter anderem Zusicherungen, wiederkehrende Fehlermuster, Rechte,
  Sicherheitsgrenzen, Datenbankabfragen und die Aussagekraft der Tests. Die Aufzählung
  oben ist deshalb kein Bündel zufälliger Einzelkorrekturen, sondern der Ertrag dieses
  Reviews.

### Bekannte Einschränkungen

- **Der MQTT-Befehlsbaum ist ein vollwertiger Steuerweg ohne thermoctls Rechtemodell.**
  Wer dort veröffentlichen darf, kann die ganze Anlage steuern; die Absicherung liegt
  beim Broker. [docs/mqtt.md](docs/mqtt.md#den-broker-absichern-er-ist-eine-vertrauensgrenze)
  enthält die benötigten Topic-Rechte und ein EMQX-Beispiel. Das ist benannt, nicht im
  Dienst behoben.
- **Sichere Cookies sind weiterhin nicht die Vorgabe.** Wer thermoctl hinter TLS
  betreibt, muss `THERMOCTL_SECURE_COOKIES=true` selbst setzen. Der Dienst warnt beim
  Start vor einer unsicheren Netzbindung, kann die richtige Einstellung aber nicht
  erraten.

## 0.3.0 — 2026-09-02

**Die Fassung, in der thermoctl anfängt zu schalten.** Bis 0.2.2 landete jede
Regelentscheidung im Schattenprotokoll und sonst nirgends — die Adapter waren gebaut,
getestet und mit nichts verbunden. Jetzt sind alle vier Aktorwege verdrahtet:
Zigbee2MQTT-Schalter, Zigbee-Thermostatventile, Meross-Steckdosen und selbstregelnde
Ventile.

**Der Trockenlauf bleibt die Vorgabe.** Wer schalten will, öffnet `/control/arm` (eigenes
Recht `control.arm`) **und startet den Dienst neu** — der zweite Riegel wird beim
Prozessstart gebaut. Erst danach geht etwas hinaus.

### Zu beachten beim Umstieg

- **Zwei Migrationen** laufen im Container beim Start von selbst; ein örtlich gestarteter
  Dienst verweigert bei altem Schema den Start und will `alembic upgrade head`.
- **Nach dem Scharfschalten sendet der erste Zyklus für jedes Gerät einmal unbedingt.**
  Was ein Gerät gerade tut, ist nach einem Neustart nicht bekannt, und ein Relais, das
  offen ist, bleibt offen, wenn niemand es anspricht.
- **Ein gescheiterter Befehl wird in jedem Zyklus erneut versucht**, ohne Backoff. Ins
  Protokoll kommt er nur beim Wechsel des Ergebnisses — sonst wäre es nach einem Tag
  unlesbar.
- **Die Navigation zeigt nur noch, wofür jemand das Recht hat.** Wer bisher Einträge sah,
  die ihn abwiesen, sieht sie nicht mehr. Das ist keine Rechteänderung, nur eine ehrliche
  Anzeige.
- **Es gibt eine Anleitung für die erste scharfe Nacht:**
  [docs/scharfschalten.md](docs/scharfschalten.md). Wer den Vergleichsbetrieb überspringt,
  sollte sie gelesen haben.

### Neu

- **Alle vier Aktorwege verdrahtet.** Ein selbstregelndes Ventil bekommt nie zusätzlich
  einen Ein/Aus-Befehl; ein Gerät mit beiden Fähigkeiten läuft über den Schalterweg, nicht
  über beide. Ein Thermostatventil ohne `system_mode` geht über seinen Mindestsollwert aus
  und regelt dann auf fünf Grad weiter — eines mit `system_mode` schliesst wirklich. Der
  Unterschied wird aus dem Datenmodell gelesen, nicht geraten.
- **Schaltprotokoll.** Jeder Befehl an ein Gerät: wann, welches Gerät, welche Zone, was
  gesendet, mit welchem Ergebnis, warum, wodurch ausgelöst. Es überlebt das Löschen von
  Zone und Gerät und unterliegt bewusst keiner Aufbewahrungsfrist. Lesbar in der
  Oberfläche, über REST und über MCP.
- **Sensorstörungen melden sich an Home Assistant**, mit einer eigenen Problem-Entität je
  Zone, zusätzlich zum bestehenden Webhook. Der Text nennt jetzt die Folge samt Zahl:
  „Die Zone regelt die Heizung bis auf Weiteres gegen den Frostschutz-Sollwert von
  16 °C." Die Meldung geht auch im Trockenlauf hinaus — sonst erführe niemand von einer
  Störung, solange die Anlage noch nicht scharf ist.
- **Die Gruppe eines Benutzers lässt sich ändern**, mit Audit-Eintrag und einem Riegel
  gegen das Aussperren des letzten Verwalters.

### Behoben

- **Ein gescheiterter Schaltbefehl wurde nie wiederholt.** Der Zwischenspeicher merkte
  sich Entscheidung und Riegel, nicht das Ergebnis. Ein einziger Netzfehler hätte eine
  Zone bis zur nächsten Änderung der Heizentscheidung kalt gelassen — bei einer
  unterversorgten Zone also unbegrenzt. Gefunden im Kreuzreview.
- **Die Entwertung der Meross-Sitzung war nie verdrahtet**: Eine tote Verbindung galt bis
  zu sechs Stunden als gültig. Zusammen mit dem vorigen Punkt hätte ein Netzfehler alle
  Meross-Aktoren stundenlang stillgelegt.
- **Der Meross-Weg hatte nur einen Riegel** statt zwei. Jetzt trägt er denselben beim
  Start eingefrorenen Wert wie der MQTT-Client.
- **Die Statistik schnitt Tage an UTC-Mitternacht**, der Protokollfilter ebenso — keine
  Anzeigefragen, sondern falsche Gruppierungen. Eine laufende Übersteuerung erschien als
  „gerade eben" statt „noch 42 Minuten".
- **Meross-Geräte galten dauerhaft als „hat sich noch nie gemeldet".**
- **Die Navigation zeigte Einträge, die den Benutzer abwiesen.**

### Bekannte Einschränkung

- Der Wirkungswächter erkennt deutsche Komposita ohne Trennzeichen nicht
  (`Zirkulationspumpe`, `Ölbrenner`) und kennt reine Temperaturaussagen nicht. Keiner
  dieser Begriffe kommt heute im Projekt vor; es ist eine Lücke im Prüfnetz für künftige
  Texte.
- **Kein Vergleichsbetrieb.** Der mehrtägige Schattenbetrieb gegen das Altsystem wurde auf
  Wunsch des Projektinhabers übersprungen. Dieser Code läuft als Erstes an einer echten
  Heizung.

### Im Einzelnen

**Vier gemeldete Anzeigefehler behoben — einer davon eine falsche Aussage, nicht nur
eine unschöne.** Ein Meross-Gerät stand dauerhaft als „hat sich noch nie gemeldet",
obwohl der stündliche Abgleich es regelmässig fand: Die Geräteübersicht sah nur
`device_health.last_payload_at` an, das ausschliesslich die Zigbee2MQTT-Aufnahme
schreibt — ein Meross-Gerät schickt nie eine MQTT-Nachricht. Sie liest jetzt für
Meross-Geräte `device.last_seen_at` und wählt die passende Formulierung
(„abgeglichen" statt „gemeldet"), mit einer eigenen, grosszügigeren Stille-Schwelle
für den stündlichen Abgleich (`MEROSS_SILENT_AFTER_SECONDS`, zwei Abgleichzyklen)
statt der für einen selbstberichtenden Sensor gedachten. Die Statistik und der
Datumsfilter des Auditprotokolls schnitten Tage an UTC-Mitternacht — ein lokaler Tag
begann dadurch um 01:00 oder 02:00 in `Europe/Berlin`; beide benutzen jetzt
`domain/time.py::local_day_start_utc` und sind gegen die Sommerzeit getestet (ein
Tag mit 23 beziehungsweise 25 Stunden). Die Startseite beschrieb das Ende einer noch
laufenden Übersteuerung mit dem Vergangenheitsfilter `age` und zeigte „gerade eben"
statt der verbleibenden Zeit; `age` unterscheidet jetzt Vergangenheit („vor 3
Minuten") von Zukunft („noch 42 Minuten").

**Kreuzreview der Aktorverdrahtung: drei Befunde, alle behoben.** Vor dem ersten
scharfen Betrieb an einer echten Heizung prüfte ein Kreuzreview gezielt, ob geschaltet
wird, wenn es soll — mit „Nein" beantwortet, zwei der drei Befunde in der gefährlichen
Richtung. **Ein gescheiterter Aktorbefehl wurde nie wiederholt:** Der Zwischenspeicher
„nur bei Änderung senden" trug das Ergebnis nicht im Schlüssel, sodass ein gescheiterter
Versuch beim nächsten Zyklus mit unveränderter Entscheidung komplett übersprungen wurde
— kein neuer Versuch, kein neuer Log-Eintrag. Bei einer kalten, dauerhaft
unterversorgten Zone (deren Entscheidung sich gerade nicht ändert) hätte das im Januar
ein eingefrorenes Rohr bedeutet; jetzt wird jeden scharfen Zyklus erneut versucht, aber
nur einmal pro Ausfallepisode geloggt. **Die Entwertung der Meross-Sitzung war nie
verdrahtet:** `app.py` übergab `meross_transport`, aber nicht `meross_session_cache`, an
den Veröffentlichungszyklus, sodass eine tote Verbindung bis zu sechs Stunden als
„gültig" stehen blieb; jetzt durchgereicht. **Der Meross-Weg hatte nur einen Riegel:**
`MerossSwitch` prüft jetzt zusätzlich denselben beim Start eingefrorenen Riegel wie der
MQTT-Client. Zusätzlich abgesichert:
eine Regression, bei der der Meross-Anmeldeaufruf in eine offene Schreibtransaktion
verschoben wird (der Fehler, der einmal die SQLite-Datei 40 Sekunden gesperrt hatte) —
ein neuer Test prüft jetzt die Eigenschaft selbst, nicht die Aufrufreihenfolge.

**Meross und Zigbee2MQTT-Thermostatventile sind jetzt verdrahtet — beide zuvor offenen
Fälle.** Ein Meross-Aktor bekommt seinen Befehl über eine bei Bedarf erneuerte,
zwischengespeicherte Cloud-Sitzung (`services/meross_session.py`), signiert **ausserhalb**
jeder Datenbanktransaktion — genau die Stelle, an der eine frühere Fassung die SQLite-Datei
bis zu 40 Sekunden gesperrt hatte. Lehnt die Cloud die Anmeldung ab, bekommt jeder
betroffene Aktor diesen Zyklus einen `failed`-Eintrag im Schaltprotokoll, ohne den Zyklus
selbst anzuhalten. Ein Zigbee2MQTT-Thermostatventil ohne `self_regulating` (Fähigkeit
`thermostat` statt `switch`, von thermoctls eigener Hysterese statt eigener Regelung
gesteuert) bekommt jetzt über `Zigbee2MqttThermostat` den aufgelösten Zonensollwert und,
wo das Gerät `system_mode` als beschreibbar meldet, `heat`/`off` dazu — ein Gerät ohne
`system_mode` (Bosch BTH-RA) wird stattdessen auf seinen niedrigsten Sollwert gefahren.
Beide Wege gehen durch dieselben zwei Riegel und dasselbe Schaltprotokoll wie der
bestehende Zigbee2MQTT-Schaltweg.

**Gewöhnliche Aktoren sind jetzt mit dem Regelkreis verdrahtet.** Bisher erreichte eine
Ein/Aus-Entscheidung kein Gerät: `Zigbee2MqttValve` und `MerossSwitch` waren gebaut,
getestet und nirgends im Produktivcode konstruiert. Jetzt schaltet ein Zigbee2MQTT-Aktor
(Rolle `actuator`, ohne `self_regulating`, mit der Fähigkeit `switch`) das, was die
Regelung zuletzt für seine Zone entschieden hat — hinter denselben zwei Riegeln wie der
Sollwert-Weg (`setting.control_armed` und der beim Start gebaute MQTT-Riegel), nur bei
einer Änderung gesendet, und mit einem Eintrag im Schaltprotokoll für jeden Versuch.
**Meross ist davon ausdrücklich nicht erfasst** — das Schalten dort braucht eine Anmeldung
gegen die Meross-Cloud, die nicht aus einer offenen Datenbanktransaktion heraus abgewartet
werden darf (genau dieser Fehler hat in dieser Fassung schon einmal die ganze
SQLite-Datei gesperrt); ein Meross-Aktor bekommt bis auf Weiteres einen `failed`-Eintrag
im Schaltprotokoll statt eines Befehls. Ein weiterer, zu diesem Zeitpunkt noch offener
Fall: ein Zigbee2MQTT-Thermostatventil ohne `self_regulating`.

**Neu: das Schaltprotokoll.** Bislang gab es zwei Aufzeichnungen — was die Regelung
entschieden hat (`shadow_decision`) und was ein Mensch getan hat (`audit_event`) —, aber
keine für das Dritte: was wirklich an ein Gerät hinausging. Die neue Ansicht unter
„Einstellungen → Schaltprotokoll" (`/device-commands`) zeigt zu jedem gesendeten Befehl
Zeitpunkt, Zone, Gerät, Nutzlast, Ergebnis (ausgeführt, im Trockenlauf unterdrückt,
gescheitert) und die Begründung der Regelung, gefiltert nach Zone, Ergebnis und Zeitraum.
Der bestehende Sollwert-Weg an selbstregelnde Thermostatventile schreibt jetzt dorthin,
statt nur ins flüchtige Anwendungsprotokoll. Anders als das Schattenprotokoll überlebt ein
Eintrag das Löschen oder Umbenennen seiner Zone oder seines Geräts, weil ein Schaltbefehl
selten und einzeln beweiskräftig ist und deshalb nicht mit der Zone verschwinden soll.

---

## 0.2.2 — 2026-08-31

Eine Fassung, die vor allem geradezieht, was 0.2.0 behauptet hat: Meross ist jetzt
wirklich angebunden statt halb, die Sonnenabsenkung lässt sich wieder einschalten, und
zwei Fehler, die dem Bedienenden als Zufall erschienen (willkürliches Abgemeldetwerden,
eine Seite, aus der es keinen Ausweg gab), sind gefunden und behoben. Dazu ein neues
Stück Funktion: der Ventilschutz.

**Der Trockenlauf ist die Vorgabe und wird von zwei Riegeln gehalten.** Was danach
passiert, ist mehrfach zu grob beschrieben worden — hier genau:

- In einer neuen Anlage steht `setting.control_armed` auf `false`; jeder Weg zu einem
  Aktor ist zu.
- `/control/arm` kann das öffnen, mit eigenem Recht `control.arm`. „Geschaltet wird
  weiterhin nichts" war deshalb eine Zusicherung ohne Deckung und stand hier zu Unrecht.
- Der MQTT-Client trägt einen zweiten Riegel, der **beim Start** gebaut wird. Ohne
  Neustart geht auch danach nichts hinaus.
- Sind der gespeicherte Riegel und der beim Start gebaute MQTT-Riegel offen, bekommen
  **selbstregelnde Thermostatventile** ihren Sollwert veröffentlicht. **Normale
  Ein/Aus-Entscheidungen gehen nirgendwohin** — weder
  `Zigbee2MqttValve` noch `MerossSwitch` werden im Produktivcode konstruiert. Das ist die
  offene Arbeit von Phase 4.

### Zu beachten beim Umstieg

- **Im Container läuft eine Migration beim Start von selbst** (Ventilschutz je Zone samt
  der Betriebszeitstempel dazu). Ein örtlich gestarteter Dienst migriert nicht selbst und
  verweigert mit einem alten Schema den Start. Der Weg von 0.2.0 aufwärts ist gegen SQLite
  und MariaDB durchgespielt.
- **Wer Meross-Zugangsdaten hinterlegt hat, bekommt jetzt Geräte** — der Abgleich läuft
  im ersten Schattenzyklus nach dem Start (also nach dem eingestellten Intervall, Vorgabe
  eine Minute) und danach stündlich. Es entstehen Gerätezeilen, die es vorher nicht gab.
- **Der Schattenzyklus startet nun auch ohne `THERMOCTL_MQTT_ENABLED=true`**, wenn ein
  vollständiges Meross-Konto hinterlegt ist. Wer beides nicht nutzt, merkt keinen
  Unterschied.
- **Eine fremde Seite kann einen angemeldeten Besucher jetzt abmelden.** Bewusster Tausch
  dafür, dass eine veraltete Seite keine Sackgasse mehr ist; ein Konto wird damit nicht
  übernommen. Siehe *Behoben*.

### Neu

- **Ventilschutz je Zone.** Nach einer einstellbaren Zeit ohne reguläres Heizen erzeugt
  die Regelkette für eine einstellbare Dauer eine `Decision(heating=True)` und hält sie im
  Schattenprotokoll fest. Dafür ist kein Ein/Aus-Aktor verdrahtet; die Funktion bewegt
  derzeit kein Ventil. Sie ist standardmäßig aus; Konfiguration gibt es in Oberfläche,
  REST und MCP.

- **Meross-Anbindung, beide Hälften.** Bisher gab es nur einen Schaltadapter und kein
  Gerät, auf das er gepasst hätte — Geräte entstanden ausschliesslich aus der
  Zigbee2MQTT-Liste, eine Meross-Steckdose konnte in der Anlage gar nicht auftauchen.
  Jetzt gleicht der Schattenzyklus die Geräteliste des Kontos stündlich ab und legt
  gefundene Steckdosen an. Die Zuordnung hängt an der `uuid`, nicht am Namen: Wer in der
  Meross-App umbenennt, verliert seine Zuordnung nicht. Gelöscht wird nie — ein Gerät,
  das die Wolke gerade nicht nennt, ist meist offline.

### Behoben

- **Rückgängig erkennt jetzt auch gewöhnliche Schaltpunktänderungen.** Die Revision
  eines Zonenzeitplans umfasst neben Malen, Tagesübertragung und Übernahme auch Anlegen,
  Verschieben, Löschen und Moduswechsel einzelner Punkte. Dadurch wird ein alter
  Schnappschuss auch nach einem Moduswechsel A→B→A sicher abgewiesen.

- **Ändernde Formulare funktionieren ohne JavaScript.** Alle POST- und `hx-post`-Formulare
  erhalten ein verstecktes CSRF-Feld; zuvor schlug unter anderem „Schaltpunkt anlegen“
  ohne den von JavaScript gesetzten Header mit 403 fehl. Ein Wächtertest schützt alle
  gegenwärtigen und künftigen Formulare vor derselben Lücke.

- **Die Sonnenabsenkung verwendet wieder die beabsichtigten CSS-Klassen.** Zwei deutsche
  Umbenennungsreste hatten keine Regel und veränderten die Darstellung still. Ein
  projektweiter Test gleicht nun alle vollständigen `t-`-/`tc-`-Vorlagenklassen mit
  `thermoctl.css` ab.

- **Reguläres Heizen verliert nach einem Ventilschutzlauf nicht mehr seine Hysterese.**
  Sobald die normale Regelung das Heizen übernimmt, wird der Schutzmarker beendet. Ein
  folgender Messwert innerhalb der Hysterese lässt die Ein-Entscheidung damit wie
  vorgesehen bestehen; reine Schutzläufe behalten den Marker bis zu ihrem regulären Ende.

- **Angezeigte Uhrzeiten verwenden die konfigurierte Zeitzone.** Die Kiosk-Uhr,
  die Jetztmarkierung des Tagesplans auf der Startseite sowie Ablaufzeiten von API-
  und Kiosk-Tokens rechnen die intern als naive UTC geführte Zeit erst für die
  Anzeige um; Sommerzeit und abweichende Zeitzonen werden dabei berücksichtigt.
- **SQLite sperrt Bedienanfragen nicht mehr während externer Netzabrufe.** Anmeldung und
  Geräteliste von Meross werden jetzt ohne offene Datenbanksitzung geholt und erst danach
  kurz gespeichert. Auch Open-Meteo wird nicht mehr aus der bereits schreibenden
  Sitzung des Schattenzyklus abgewartet. Damit kann die Fortschreibung einer
  Bedienersitzung parallel committen, statt mit `database is locked` in 401 oder 500 zu
  enden.
- **Ventilschutz hält nun exakt die eingestellte Dauer ein.** Die Mindest-Einschaltdauer
  wird nicht mehr als regulärer Heiznachweis missverstanden und verlängert oder verkürzt
  keinen Schutzlauf. Der nächste Abstand beginnt am tatsächlichen Abschluss; gleiche
  Dauer und gleicher Abstand erzeugen deshalb keinen endlosen Lauf. Betriebszeitstempel
  behalten auch unter MariaDB ihre Mikrosekunden, und leere Alt-Historien werden nur
  einmal verdichtet. Gemeinsame Obergrenzen gelten nun in Web, REST und MCP.
- **Die Sonnenabsenkung liess sich nicht einschalten.** Das Formular schickt seit 0.2.0
  `value="yes"`, die Auswertung verglich weiter gegen `"on"` — den Vorgabewert eines
  Browsers für eine Checkbox ohne `value`. Der Haken wurde gesetzt, gespeichert und war
  danach wieder weg. Gemeldet aus dem Betrieb.
- **Der Meross-Schaltweg war falsch geraten.** Der Adapter postete an
  `/v1/Device/devControl` — diesen Pfad gibt es nicht, die Wolke antwortet mit 404. Auch
  die Anmeldung lag daneben: Sie verlangt einen signierten Umschlag, kein Formular, und
  das Passwort MD5-gehasht statt im Klartext. Beides ist ersetzt und gegen ein echtes
  Konto geprüft; geschaltet wird über MQTT, wie Meross es tatsächlich tut.

### Geändert

- **Die Schnittstellenseite meldet Meross nicht mehr als „noch nicht gebaut".** Sie sagt
  jetzt, was geprüft ist (Anmeldung, Geräteliste, das Lesen und Schalten eines
  Gerätezustands über `SETACK`) und was noch nicht kommt (die Verdrahtung in den
  Regelkreis, Teilprojekt 4). „running" zeigt sie erst, wenn ein Abgleich tatsächlich
  ein Gerät gefunden hat — Zugangsdaten allein zeigen „configured".

### Kreuzreview der Meross-Anbindung

- **Nicht mehr jedes Meross-Gerät gilt als Schalter.** Nur die `mss`-Modellfamilie
  (Steckdosen) bekommt die Fähigkeit `switch`; Hubs, Lampen, Thermostatventile und
  Sensoren im selben Konto erscheinen weiter als Gerätezeile, aber ohne diesen
  Anspruch. Die gemeldete Kanalzahl wird jetzt mitgeführt statt verworfen.
- **Der Meross-Abgleich hält den Schattenzyklus nicht mehr auf.** Er läuft entkoppelt
  in einer eigenen Sitzung statt innerhalb der Transaktion des Zyklus.
- **Der Meross-Abgleich läuft jetzt auch ohne lokales MQTT** — vorausgesetzt, ein
  vollständiges Konto (E-Mail und Passwort) ist hinterlegt.

### Bekannte Einschränkung

- **Der Wirkungswächter erkennt deutsche Komposita ohne Trennzeichen nicht** —
  `Zirkulationspumpe`, `Ölbrenner` — und kennt reine Temperaturaussagen („die
  Raumtemperatur steigt") nicht. Heute kommt keiner dieser Begriffe im Projekt vor; es
  ist eine Lücke im Prüfnetz für künftige Texte, kein falscher Text im jetzigen Stand.

### Bedienung

- **Zeitpläne lassen sich als Zeiträume malen.** Über dem Wochenraster steht eine Palette
  der Modi; man überstreicht einen Zeitraum in einer Tagesspalte, und er bekommt diesen
  Modus. Dazu „auf Mo–Fr" und „auf alle Tage" je Tagesspalte und ein einstufiges
  Rückgängig. Das bisherige Ziehen von Schaltpunkten bleibt vollständig erhalten — die
  Palette schaltet zwischen beiden um. Ohne JavaScript bleibt alles über das Formular
  darunter bedienbar.
- **Der Modus eines Schaltpunkts lässt sich direkt im Zeitplan ändern**, statt den Punkt
  zu löschen und neu anzulegen. Die Liste nennt den Modus jetzt überhaupt erst; der
  Punkt behält seine Kennung, und das Protokoll bekommt einen Eintrag „Modus geändert"
  statt zweier unzusammenhängender.
- **Eine veraltete Seite ist keine Sackgasse mehr.** Vorher wies der CSRF-Schutz alles
  ab — auch das Abmelden, und nach dem Löschen der Cookies auch das Anmelden. Sichtbar
  wurde das als rohes `{"detail":"Ungueltiges CSRF-Token"}`. Jetzt räumen Anmelden und
  Abmelden die Cookies und führen auf das Anmeldeformular, gewöhnliche Formulare
  bekommen eine lesbare Seite, und Bedienelemente mit htmx zeigen einen Hinweis mit
  Knopf zum Neuladen. Am Schutz selbst ändert sich nichts.
- **`APP_SECRET` heißt jetzt zutreffend, was er ist**: öffentlich seit Jahren durch
  Reverse Engineering, keine vom Hersteller dokumentierte Konstante.

## 0.2.0 — 2026-08-31

Der Sprung von „Fundament" zu „im Alltag benutzbar". Räume, Geräte, Sollwerte und
Zeitpläne lassen sich vollständig über die Oberfläche pflegen; REST, MCP und Home
Assistant sprechen dieselbe Domänenlogik.

**Geschaltet wird weiterhin nichts.** Der Trockenlauf steht, und das ist Absicht: Erst
kommt der Vergleichsbetrieb gegen die bestehende Anlage.

### Zu beachten beim Umstieg

- **`THERMOCTL_MQTT_PRAEFIX` heißt jetzt `THERMOCTL_MQTT_PREFIX`.** Wer die Variable in
  seiner `.env` stehen hat, zieht sie nach — sonst greift stillschweigend die Vorgabe
  `thermoctl`, und der Dienst veröffentlicht unter einem anderen Topic-Zweig als bisher.
- **Der MQTT-Themenbaum ist englisch**: `thermoctl/zones/<id>/state/…` statt
  `thermoctl/zonen/<id>/zustand/…`, ebenso `command` statt `befehl` und
  `availability` statt `verfuegbarkeit`. Wer eigene Abonnenten gebaut hat, passt sie an.
  Dasselbe gilt für die Web-Endpunkte (`/zones` statt `/zonen`) und die REST-Pfade.
- **Das MCP-Werkzeug heißt `override`**, nicht `override_zone`; die Dokumentation nannte
  zeitweise den falschen Namen.
- **15 Migrationen** laufen beim Start von selbst. Der Weg von 0.1.0 aufwärts ist gegen
  SQLite und MariaDB durchgespielt.
- **Abbilder vor 0.2.0 waren unbrauchbar** — ihnen fehlten alle Vorlagen und statischen
  Dateien (siehe *Behoben*). Wer 0.1.0 nie zum Laufen bekam: Das war der Grund.

### Neu

- **Konfigurations-Oberfläche.** Zonen, Geräte, Sollwert-Modi, Zeitpläne, Regelparameter,
  Benutzer, Gruppen, Tokens und das Audit-Protokoll — alles ohne SQL-Client.
- **Geräte per Ziehen und Ablegen** zuordnen, und wieder heraus. Dazu ein Anlagenbild,
  das zeigt, welches Gerät wo etwas tut, und benennt, was einer Zone fehlt.
- **Zeitplan-Editor** mit Wochenansicht; Schaltpunkte lassen sich ziehen, ein Klick
  belegt das Anlegeformular vor. Zeitpläne sind von anderen Zonen übernehmbar.
- **Zigbee-Heizkörperthermostate** (WT-A03E, BTH-RA) als Aktor. Ein Thermostatventil ist
  kein Schalter: Es wird über Sollwert und, wo vorhanden, `system_mode` gefahren.
- **Selbstregelnde Ventile.** Wahlweise regelt das Thermostat selbst, und thermoctl
  schreibt ihm nur Soll- und — wo das Gerät es annimmt — die anderswo gemessene
  Ist-Temperatur. Der eigene Fühler eines Thermostats sitzt am Heizkörper und misst
  mehrere Grad zu warm; mit einem Wandfühler regelt es gegen den Raum.
- **Bediengeräte frei konfigurierbar.** Tastendrücke aufzeichnen und belegen, Merkmale
  eines beliebigen Zigbee2MQTT-Geräts auf Zonenwerte legen oder von dort lesen.
- **Sonnenprognose-Absenkung** (optional, ab Werk aus). Verspricht die Vorhersage Sonne
  in den nächsten Stunden, sinkt der Sollwert — je Zone gewichtet, begrenzt, und niemals
  unter den Frostschutz.
- **Wandtablet-Dashboard** unter `/kiosk`, hinter einem widerrufbaren Kiosk-Token mit
  engem Rechtesatz statt ohne Anmeldung.
- **Home-Assistant-Anbindung** über MQTT-Discovery: je Zone ein eigenes Gerät mit
  Thermostat, Boost, Sollwert je Modus und den Regelparametern.
- **REST-Schnittstelle und MCP-Server** auf demselben Stand wie die Oberfläche, mit
  Swagger unter `/docs`.
- **Passkey-Anmeldung** zusätzlich zum Passwort.
- **Schattenlauf**: für jede Zone wird protokolliert, was geschaltet **würde** und warum.
- **Störungserkennung** bei ausbleibenden Messwerten, gemeldet ins Log und optional an
  einen Webhook.

### Bekannte Lücke

- **Meross-Schaltsteckdosen sind nicht nutzbar.** Gebaut ist nur die schaltende Hälfte:
  Der Adapter kann eine bekannte Steckdose ein- und ausschalten, aber es gibt keine
  Geräteerkennung für Meross. Geräte entstehen ausschliesslich aus der
  Zigbee2MQTT-Geräteliste, und von Hand anlegen lässt sich keines — eine Meross-Steckdose
  taucht also gar nicht erst auf. Die Schnittstellenseite sagt das jetzt auch so, statt
  „Eingerichtet" zu melden, sobald Zugangsdaten hinterlegt sind.

### Behoben

- **Das Container-Abbild enthielt die halbe Anwendung nicht.** Ohne `package-data`
  installiert setuptools nur `.py`-Dateien; alle Vorlagen und statischen Dateien fehlten,
  und der Dienst startete gar nicht erst. Der CI-Schritt „Docker-Image-Build" weist das
  nicht nach — er beweist, dass sich ein Abbild bauen lässt, nicht dass es läuft.
- **Die Navigationsleiste zeigte auf jeder Seite ins Leere.** `/zonen`, `/geraete` und
  `/steuerung` waren nach der Endpunkt-Umstellung 404; die Anwendung sah in Ordnung aus,
  solange man Adressen direkt eintippte.
- **Ein doppelt genanntes Gerätemerkmal legte die ganze Geräteliste lahm.** Nach dem
  Fehler kam kein Gerät der Brücke mehr an.
- **Nach dem Zurückgehen im Browser ließ sich nichts mehr ziehen.** htmx stellt die Seite
  aus seinem Verlaufsspeicher wieder her — Attribute überleben das, Ereignisbehandler
  nicht.
- Das Scharfschalt-Formular, die Übersteuerung, die Token-Gültigkeit und die Modusauswahl
  im Zeitplan schickten Feldnamen oder Werte, die ihre View nicht mehr kannte.
- Die Knöpfe im Kiosk-Dashboard wurden mit „Ungueltiges CSRF-Token" abgewiesen.
- Der Frostschutz greift jetzt auch in einer echten Anlage; das Schattenprotokoll folgt
  der Zone beim Löschen; ein Schema-Vergleich beim Start ersetzt den Traceback aus der
  Tiefe.
- **Die MQTT-Wiederverbindung bremst wieder.** Der Abstand wurde zurückgesetzt, sobald
  irgendeine Nachricht ankam — und Zigbee2MQTT stellt auf `bridge/state` eine retained
  Nachricht bei *jedem* Verbindungsaufbau zu. Wer gleich danach hinausgeworfen wurde,
  verband sich im Sekundentakt neu, mit vollem Traceback je Versuch. Jetzt entscheidet,
  wie lange eine Verbindung gehalten hat. Bricht sie dreimal sofort ab, schreibt der
  Dienst die häufigste Ursache aus: zwei Clients mit derselben Kennung.

### Geändert

- **Alles außer der Prosa ist englisch**: Bezeichner, Datei- und Modulnamen, Endpunkte,
  MQTT-Topics, Vorlagen, CSS-Klassen, Kommentare und Testnamen. Der sichtbare Text bleibt
  deutsch.
- **Der Sollwert aus Home Assistant verstellt den geltenden Modus**, nicht mehr eine
  Übersteuerung — sonst spränge der Regler nach dem nächsten Schaltpunkt scheinbar von
  selbst zurück.
- Sollwerte dürfen bis −20 °C, und die Grenze steht nur noch an einer Stelle.
- Die Testabdeckung liegt bei 100 %: jede Zeile geprüft oder mit begründeter Ausnahme im
  Quelltext. Die CI-Schwelle steht entsprechend.

---

## 0.1.0 — 2026-08-28

Fundament. Datenmodell, Migrationen für SQLite und MariaDB, Authentifizierung und
Rechtemodell, Konfiguration, strukturiertes Logging, Container und CI.

Nach außen war davon nichts zu sehen — alles Weitere hängt daran.
