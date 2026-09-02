# Ohne Schattenbetrieb scharf schalten

Diese Anleitung ist für den Projektinhaber. Der Rahmenentwurf sieht vor, die Anlage erst
nach mehreren Tagen Schattenbetrieb gegen das Altsystem scharf zu schalten. Wer diesen
Vergleich überspringt und direkt scharf schaltet, tut das ohne die Sicherheit, die der
Vergleich gegeben hätte — das ist eine bewusste Entscheidung, keine Abkürzung ohne Folgen.
Diese Anleitung ersetzt den Vergleich nicht, sie sagt, worauf stattdessen zu achten ist.

## 1. Was vorher zu prüfen ist

**Auf `Geräte` und im Anlagenbild:** Welches Gerät ist welcher Zone als was zugeordnet.
Vier Rollen kommen dafür infrage, und jede verhält sich nach dem Scharfschalten anders:

1. **Selbstregelndes Thermostatventil** (Fähigkeit `thermostat`, `self_regulating`
   gesetzt). Es bekommt den aufgelösten Sollwert als Temperaturvorgabe und regelt selbst
   dagegen. Kein Ein/Aus geht hinaus, nur eine Zieltemperatur.
2. **Gewöhnlicher Aktor** über Zigbee2MQTT (Rolle „Aktor", Fähigkeit `switch`, nicht
   selbstregelnd). Er bekommt genau das Ein/Aus, das die Regelung für seine Zone gerade
   entschieden hat.
3. **Meross-Steckdose.** Bekommt ebenfalls Ein/Aus, aber über eine Cloud-Anmeldung, die
   bei Bedarf erneuert wird. Ist die Anmeldung gerade nicht möglich, wird nicht
   geschaltet — jeder betroffene Zyklus trägt stattdessen einen gescheiterten Versuch ins
   Schaltprotokoll ein, ohne den übrigen Betrieb aufzuhalten.
4. **Zigbee2MQTT-Thermostatventil ohne Selbstregelung** (Fähigkeit `thermostat`, aber
   `self_regulating` nicht gesetzt — thermoctls eigene Hysterese entscheidet, nicht das
   Ventil selbst). Es bekommt den aufgelösten Sollwert und, wo das Gerät das anbietet,
   zusätzlich ein `heat`/`off`. Bietet es das nicht an, wird es stattdessen im Aus-Fall auf
   seinen niedrigsten Sollwert gefahren — ein sichtbarer, aber langsamerer Weg, „aus" zu
   erreichen.

Für jede Zone lohnt der Blick, welche dieser vier Rollen ihr Aktor tatsächlich hat — auf
der Geräte-Zuordnungsseite der Zone steht das neben der Anschluss- und Fähigkeitenliste,
mit demselben Bild wie im Anlagenbild.

**Auf `Betrieb`:** Was die Regelung für jede Zone gerade entscheiden würde — Ist-Wert,
Sollwert samt Begründung, ob geheizt würde. Das ist derselbe Blick, den der Schattenbetrieb
über mehrere Tage gegeben hätte, nur an einem einzigen Moment. Vor dem Scharfschalten lohnt
es, ihn für jede Zone zu einer Zeit zu prüfen, in der eindeutig ist, was gelten sollte —
etwa nachts, wenn „würde heizen: nein" gelten muss, wenn das nicht der Fall sein soll.

**Auf der Zeitplan-Seite jeder Zone:** Ob die Schaltpunkte tatsächlich das ergeben, was
beabsichtigt ist. Ein Zeitplan, der auf dem Papier richtig aussieht, aber einen Punkt am
falschen Wochentag oder mit vertauschtem Modus trägt, fällt im Schattenbetrieb über Tage
auf — hier fällt er erst auf, wenn die Zone entsprechend heizt oder eben nicht.

**Eine fachliche Entscheidung ausdrücklich bestätigen:** Fällt ein Sensor aus (Zustand
„veraltet" statt „ok"), regelt die Anlage nicht ab, sondern auf einen Frostschutz-Sollwert
weiter. Das ist Absicht — Begründung unter „Bei ausgefallenem Sensor wird auf Frostschutz
geregelt, nicht abgeschaltet" in `offene-entscheidungen.md` —, wurde aber nie an der echten
Anlage beobachtet. Wer diesem Verhalten nicht zustimmt, klärt das vor dem Scharfschalten,
nicht danach.

## 2. Die Reihenfolge des Scharfschaltens

1. **Auf `Betrieb`, „Scharf schalten …"** anklicken, eine Begründung eintragen (Pflicht)
   und bestätigen. Die Seite zeigt danach sofort „Scharf, Neustart fehlt" — der beim
   Prozessstart gebaute zweite Riegel ist noch zu, es geht noch nichts hinaus.
2. **Den Dienst neu starten.** Dieser Schritt ist keine Formsache: Der zweite Riegel wird
   ausschliesslich beim Start des Prozesses aus dem zu diesem Zeitpunkt gespeicherten
   Zustand gebaut und danach nicht mehr angefasst — nicht einmal, wenn man später auf
   `Betrieb` scharf schaltet, ohne neu zu starten. **Das ist die Stelle, an der es aussieht,
   als sei etwas kaputt:** Die Oberfläche meldet „scharf", das Schaltprotokoll bleibt leer,
   und nichts deutet auf einen fehlenden Schritt hin, ausser der Chip „Scharf, Neustart
   fehlt" auf genau dieser Seite.
3. **Reihenfolge beachten.** Ein Neustart *vor* dem Scharfschalten bringt den Dienst im
   Trockenlauf hoch — der Riegel bleibt zu, und es braucht einen zweiten Neustart, diesmal
   nach dem Scharfschalten, damit er sich öffnet. Richtig ist: erst scharf schalten, dann
   neu starten.
4. Nach dem Neustart zeigt `Betrieb` „Scharf und neu gestartet" — Sollwerte an
   selbstregelnde Thermostatventile werden jetzt versendet, ebenso Ein/Aus an die übrigen
   drei Wege oben.

## 3. Was in der ersten Stunde normal aussieht

Der Zwischenspeicher, der „nur bei Änderung senden" entscheidet, startet mit jedem
Prozessneustart leer. **Der erste Regelzyklus nach dem Neustart sendet deshalb für jedes
zugeordnete Aktor-Gerät einmal unbedingt** — unabhängig davon, ob sich gegenüber vorher
etwas geändert hat. Danach geht nur noch bei einer tatsächlichen Änderung der Entscheidung
etwas hinaus. Wie oft das grundsätzlich passieren könnte, sagt der Regelzyklus auf
`Einstellungen → Regelvorgaben` (standardmässig eine Minute); real geschieht es viel
seltener, weil sich eine Ein/Aus- oder Sollwert-Entscheidung nur selten ändert.

**Normal:**
- Innerhalb des ersten Regelzyklus nach dem Neustart erscheint für jedes als Aktor
  zugeordnete Gerät genau ein Eintrag im Schaltprotokoll.
- Danach lange Zeit **keine** neuen Einträge für ein Gerät, dessen Zone durchgehend im
  selben Zustand bleibt (etwa dauerhaft „aus", weil der Sollwert erreicht ist). Das ist
  keine Störung, sondern die Deduplizierung — Stille heisst hier „nichts hat sich
  geändert", nicht „nichts wird mehr versucht".
- Ein gescheiterter Befehl wird **in jedem** scharfen Zyklus erneut versucht, aber nur
  beim **Wechsel** des Ergebnisses erneut protokolliert. Ein einzelner `failed`-Eintrag,
  der seit einer Weile derselbe bleibt, bedeutet also „seither ununterbrochen
  gescheitert", nicht „einmal gescheitert und dann aufgegeben".

**Nicht normal:**
- Ein als Aktor zugeordnetes Gerät, das auch nach mehreren Regelzyklen **keinen einzigen**
  Eintrag im Schaltprotokoll hat. Der erste Zyklus sollte es unbedingt erfasst haben.
- Ein `suppressed`-Eintrag, der **nach** dem Neustart entsteht. Unterdrückt wird nur im
  Trockenlauf — ein solcher Eintrag nach dem Neustart bedeutet, dass ein Riegel entgegen
  der Anzeige auf `Betrieb` doch noch zu ist.

## 4. Wie man das Schaltprotokoll liest

Unter `Einstellungen → Schaltprotokoll`, mit Filtern nach Zeitraum, Zone und Ergebnis.

**Ein unauffälliger Abend** zeigt für jede Zone mit aktivem Aktor wenige Einträge —
typischerweise die Schaltpunkte des Zeitplans, an denen sich die Entscheidung tatsächlich
geändert hat, dazu gelegentlich eine Übersteuerung. Jeder Eintrag trägt `executed`, dazu
den Grund (etwa „Zeitplan" oder der Text einer Übersteuerung).

**Anzeichen für ein Problem:**
- Wiederholte `failed`-Einträge für dasselbe Gerät über mehrere Abende hinweg, ohne dass
  je wieder `executed` erscheint. Ein Kreuzreview der Verdrahtung fand kurz vor dem ersten
  scharfen Betrieb genau zwei Fehler, die sich so gezeigt hätten, wären sie nicht vorher
  behoben worden: Ein gescheiterter Befehl wurde nicht mehr wiederholt, sobald sich die
  zugrundeliegende Entscheidung nicht änderte — eine dauerhaft unterversorgte, aber in
  ihrer Entscheidung unveränderte Zone hätte einen einzigen alten `failed`-Eintrag gezeigt
  und wäre danach für immer verstummt. Und die verworfene Cloud-Sitzung einer
  Meross-Steckdose wurde nicht erneuert, was bis zu sechs Stunden lang zu genau dem
  gleichen Bild geführt hätte. Beide Ursachen sind behoben; ein Muster, das genauso
  aussieht, ist heute entweder ein echtes, andauerndes Problem am Gerät oder am Netz, oder
  ein Rückfall, der es wert ist, gemeldet zu werden.
- Ein Eintrag, dessen `reason` nicht zu dem passt, was man erwartet hätte — etwa
  „Zeitplan", wo eine laufende Übersteuerung stehen sollte. Das deutet eher auf eine
  falsche Zuordnung von Zeitplan oder Modus hin als auf einen technischen Fehler.
- Ein als Aktor zugeordnetes Gerät, das über Tage überhaupt nicht im Protokoll auftaucht,
  obwohl seine Zone laut `Betrieb` zwischen Heizen und Nicht-Heizen wechselt.

## 5. Wie man schnell zurückkommt

Auf `Betrieb`, „Zurück in den Trockenlauf" — derselbe Knopf, mit dem scharf geschaltet
wurde, **sofort wirksam, ohne Neustart**. Das ist die schnellste Rückwärtsbewegung, die es
gibt.

**Was dabei mit den Geräten passiert: nichts, von selbst.** Der Trockenlauf hält nur davon
ab, künftig neue Befehle zu senden — er schaltet kein einziges Gerät zurück. Ein Relais,
das im Moment des Zurücknehmens offen ist (die Heizung also läuft), **bleibt offen**, bis
es jemand anspricht: von Hand, über die ursprüngliche Steuerung des Geräts, oder dadurch,
dass die Anlage später erneut scharf geschaltet wird und eine neue Entscheidung sendet.
Wer in den Trockenlauf zurückgeht, weil etwas nicht stimmt, prüft im selben Moment, welche
Geräte laut Schaltprotokoll zuletzt eingeschaltet wurden, und schaltet sie bei Bedarf
händisch ab.

## 6. Woran man merkt, dass etwas nicht schaltet, obwohl es soll

Die gefährlichere Richtung: Eine Zone, die laut `Betrieb` heizen würde, tut es in
Wirklichkeit nicht, und niemand bemerkt es, weil nichts laut protokolliert wird, was
schweigt.

- **Ist-Temperatur und Entscheidung widersprechen sich über längere Zeit.** `Betrieb`
  zeigt „würde heizen: ja", die Ist-Temperatur der Zone bewegt sich aber nicht in Richtung
  Sollwert. Das ist der zuverlässigste Hinweis, weil er unabhängig vom Schaltprotokoll ist.
- **Der jüngste Eintrag im Schaltprotokoll für das betroffene Gerät ist alt**, obwohl sich
  die Entscheidung seither laut `Betrieb` geändert haben müsste. Ein Gerät, das „ein"
  bleiben soll, aber dessen letzter Eintrag „aus" war und lange zurückliegt, hat den
  Wechsel möglicherweise nie gesendet bekommen.
- **Stille beweist nicht, dass wiederholt wird.** Wegen der Deduplizierung sieht ein
  Gerät, das seit einer Stunde erfolglos denselben Befehl versucht, im Protokoll genauso
  aus wie eines, dessen letzter Versuch zufällig auch vor einer Stunde lag und seither
  nicht mehr nötig war. Bei Verdacht hilft nur der Blick ins Anwendungs-Log
  (`docker compose logs thermoctl`) daneben — dort steht, ob überhaupt ein Sendeversuch
  unternommen wird, und mit welchem technischen Fehler er scheitert.
- **Ein Meross-Gerät, das über mehr als ein, zwei Regelzyklen hinweg denselben Fehler
  zeigt**, deutet eher auf ein Konto- oder Netzwerkproblem hin als auf eine vorübergehende
  Störung — die Anmeldung wird bei Bedarf zwar erneuert, kann aber an einer abgelehnten
  Cloud-Anmeldung oder einer nicht erreichbaren Steckdose scheitern, ohne dass die Anlage
  das selbst beheben kann.
- **Der Chip auf `Betrieb` zeigt weiterhin „Scharf, Neustart fehlt".** Der naheliegendste
  Grund für „nichts schaltet, obwohl scharf geschaltet wurde" ist schlicht ein
  ausstehender Neustart — siehe Abschnitt 2.

## Was hier bewusst nicht steht

Diese Anleitung beschreibt nicht, wie der mehrtägige Vergleich gegen das Altsystem
nachträglich noch geführt werden könnte — er entfällt mit dieser Entscheidung, statt sich
im Nachhinein zu wiederholen. Sie nennt auch keine konkreten Werte für Hysterese,
Mindestschaltdauer oder Regelzyklus: Diese stehen bereits, mit ihren Grenzen, auf
`Einstellungen → Regelvorgaben` und hängen von der jeweiligen Anlage ab, nicht von diesem
Text.
