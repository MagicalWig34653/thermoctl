# Zeitplan-Bedienung: Malen statt Schieben

Entwurf, 2026-08-31. **Nicht umgesetzt** — Vorschlag zur Entscheidung.

Anlass: „Wenn du eine Idee hast, wie man die Drag-and-Drop-Zeitplankonfiguration
revolutionieren könnte, wäre das schön. Weil auch das einfache Hin-und-her-Schieben ist
nicht so schön."

## Warum sich das Schieben falsch anfühlt

Es liegt nicht an der Umsetzung. Es liegt daran, **dass das Ding, das man anfasst, nicht
das Ding ist, das sich ändert.**

In der Datenbank ist ein Zeitplan eine Liste von Schaltpunkten: „Montag 06:30 → Komfort".
Ein Punkt gilt bis zum nächsten. Im Kopf eines Menschen ist ein Tag dagegen eine Folge von
**Abschnitten**: „von 6:30 bis 8:00 warm, danach bis 17:00 abgesenkt".

Die Wochenansicht zeigt Abschnitte — Balken. Bearbeitet werden aber Punkte. Zieht man
einen Balken nach unten, verschiebt man seinen Anfang; der Balken **darüber** wird dabei
länger, ohne dass man ihn angefasst hätte. Man greift eine Kachel und eine andere ändert
sich mit. Genau das ist das unangenehme Gefühl, und keine Politur am Ziehen behebt es.

Drei weitere Reibungen kommen dazu:

- **Anlegen und Verschieben sind zwei getrennte Welten.** Neue Punkte entstehen nur im
  Formular unten. Eine Woche aufzubauen heißt: Formular, Formular, Formular, dann ziehen.
- **Ein Tag lässt sich nicht auf einen anderen übertragen.** Eine Woche besteht meist aus
  fünf gleichen Tagen und zwei anderen — eingetippt wird sie siebenmal. („Von anderer Zone
  übernehmen" gibt es, von einem anderen *Tag* nicht.)
- **Pixelgenaues Ziehen erzeugt 06:27.** Niemand will 06:27.

## Der Vorschlag

**Die Wochenansicht wird eine Malfläche.** Über dem Raster steht eine Palette der
vorhandenen Modi; einer ist aktiv. Man zieht mit der Maus über eine Tagesspalte, und der
überstrichene Zeitraum bekommt diesen Modus. Losgelassen wird einmal gespeichert.

Damit verschwindet der Begriff „Schaltpunkt" von der Oberfläche. Der Nutzer sagt nicht
mehr „setze einen Punkt um 6:30 und einen um 8:00", sondern „Montag 6:30 bis 8:00 ist
Komfort". Die Punkte entstehen daraus — in der Datenbank bleibt alles, wie es ist.

Vier Eigenschaften gehören dazu, sonst trägt die Idee nicht:

1. **Raster von 15 Minuten.** Beim Ziehen rastet die Auswahl ein und die Zeitspanne steht
   als Beschriftung mit („06:30–08:00"). Kein 06:27 mehr.
2. **Einen Tag auf andere übertragen.** Je Tagesspalte ein Knopf: „auf Mo–Fr", „auf alle
   Tage". Das ist die eine Handlung, die eine Wochenplanung von sieben Eingaben auf zwei
   verkürzt.
3. **Rückgängig.** Übermalen löscht Abschnitte auf einen Zug — das muss sich mit einem
   Klick zurückholen lassen. Ein Schritt genügt.
4. **Ohne JavaScript bleibt alles bedienbar.** Das Formular „Schaltpunkt anlegen" und die
   Liste bleiben, wie sie sind. Das Malen ist ein zweiter Weg zur selben Änderung, nie der
   einzige — dieselbe Regel, unter der schon das Ziehen steht.

Nebenbei erledigt sich damit auch der zweite Wunsch: **Den Modus eines Abschnitts ändert
man, indem man ihn übermalt.** (Der eigene Auftrag dafür läuft trotzdem — er baut den Weg
für die Liste und für den Betrieb ohne JavaScript, und der bleibt in jedem Fall nötig.)

## Was daran das Riskante ist

Nicht die Oberfläche, sondern die eine Funktion darunter: **aus „Montag 6:30–8:00 ist
Komfort" die minimale Menge an Punkten zu berechnen**, die anzulegen, zu verschieben und
zu löschen ist. Sie kann mit einer Geste einen halben Tagesplan ändern. Ein Fehler darin
heizt oder heizt nicht — Grundsatz 7 gilt hier genauso wie für die Regelschleife, auch
wenn nichts unmittelbar schaltet.

Sie gehört deshalb in die Domäne, nicht in den Adapter, und braucht Tests für die Fälle,
die man beim Bauen nicht von selbst bedenkt:

- Ein Zeitraum, der über Mitternacht hinausgeht (oder gar nicht erst zugelassen wird).
- Ein Zeitraum, der einen bestehenden Abschnitt vollständig überdeckt.
- Ein Zeitraum, der genau einen bestehenden trifft — dann darf nichts passieren.
- Ein Zeitraum vor dem ersten Punkt der Woche: Der Wochenplan ist ein Ring, daher gilt
  dort der letzte Modus der Vorwoche. Frostschutz ist nur der Hintergrund, wenn der Plan
  gar keinen Punkt enthält.
- Derselbe Modus wie schon vorhanden: nichts tun, keinen Audit-Eintrag schreiben.
- Ein leerer Zeitplan, in den zum ersten Mal gemalt wird.

## Die billige Alternative

Falls das zu groß ist: **die Trennlinie ziehen statt den Balken.** Man greift die Kante
zwischen zwei Abschnitten — also genau das, was ein Schaltpunkt wirklich ist — und
verschiebt sie. Dann stimmt wenigstens wieder überein, was man anfasst und was sich
ändert. Das ist ein Nachmittag Arbeit statt mehrerer.

Was es **nicht** löst: das Anlegen bleibt im Formular, das Übertragen zwischen Tagen bleibt
aus, und eine Woche bleibt sieben Eingaben. Es nimmt die Irritation weg, nicht die Mühe.

## Empfehlung

Malen. Der Aufwand steckt in einer Domänenfunktion und einem Skript, beides überschaubar
und beides gut testbar — und es ist der einzige der beiden Wege, der die Wochenplanung
tatsächlich kürzer macht statt nur angenehmer.

**Zu entscheiden vom Projektinhaber**, bevor das in einen Auftrag geht.
