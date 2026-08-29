# Teilprojekt 3 — Konfigurations-Oberfläche

Stand: 2026-08-29. Konkretisiert Phase 3 der [Roadmap](../../roadmap.md).

## 1. Ziel

Das Hauptärgernis beseitigen: Räume, Geräte und Zeitpläne werden über die Oberfläche
gepflegt statt per SQL-Client. **Fertig, wenn eine vollständige Anlage ohne einen einzigen
SQL-Befehl eingerichtet werden kann.**

Das ist der Punkt, ab dem `thermoctl` im Alltag nützlich ist — vorher ist es ein Fundament
mit einer Leseansicht.

## 2. Was diese Phase besonders macht

Bis hierher entstand fast nur Code, den niemand ansieht. Ab jetzt entsteht das, was der
Betreiber täglich benutzt. Zwei Konsequenzen:

- **Jeder sichtbare Teilschritt wird wirklich geöffnet.** Nicht „der Test ist grün",
  sondern die Seite im Browser. In diesem Projekt sind dreimal grundlegende Fehler durch
  alle Tests und Reviews gerutscht (fehlende Startseite, nicht eingebundenes Stylesheet,
  eine Vorlagen-Umgebung mit relativem Pfad, die nur außerhalb des Containers funktioniert).
  Alle drei fielen beim Benutzen auf, keiner im Test.
- **Verständliche Meldungen sind Teil der Aufgabe, nicht Politur.** Ein `IntegrityError`
  als Fehlerseite ist ein unfertiges Formular.

## 3. Bausteine, die alle Ansichten teilen

Zuerst gebaut, weil sonst jede Ansicht ihre eigene Variante erfindet:

- **Formularbausteine** als Jinja-Makros: Textfeld, Zahlenfeld, Auswahl, Umschalter,
  jeweils mit Beschriftung, Hilfetext und Fehlermeldung am Feld.
- **Ein gemeinsamer Umgang mit Eingabefehlern**: Ungültige Eingabe führt zurück ins
  Formular, mit erhaltenen Werten und einer Meldung am betroffenen Feld — nie zu `500`,
  nie zu einer leeren Maske.
- **Bestätigung vor dem Löschen**, mit Angabe dessen, was daran hängt („Diese Zone hat 4
  Schaltpunkte und 2 zugeordnete Geräte").
- **CSRF** hängt bereits am Router (`csrf_schutz`); neue Router erben das und der Wächter
  in `tests/test_csrf.py` hält es nach.

## 4. Die Ansichten

### 4.1 Zonen

Anlegen, ändern, löschen. Beim Löschen wird gefragt, was daran hängt. Eine Zone trägt
Name, Anzeigename, Betriebsart, Sortierung und die Messquelle.

### 4.2 Gerätezuordnung — und der Gerätetausch

Geräte werden Zonen in einer Rolle zugeordnet: Messquelle, Aktor, Fensterkontakt,
Bediengerät.

**Der Gerätetausch ist die eigentliche Anforderung.** Geht ein Thermostat kaputt, soll das
neue an seine Stelle treten, **ohne dass Sollwerte, Zeitplan oder Regelparameter verloren
gehen**. Im Altsystem hing die Konfiguration am Gerätenamen; ein Tausch bedeutete, alles
neu einzutragen. Hier hängt sie an der Zone, und der Tausch ist ein Vorgang mit einem
Knopf: altes Gerät raus, neues rein, alles andere bleibt. Die Messwert-Historie des alten
Geräts bleibt erhalten und wird nicht umgeschrieben — sie gehört dem Gerät, nicht der Zone.

### 4.3 Modi und Sollwerte

Sollwert-Modi frei anlegen (Tag, Nacht, Urlaub, …), je Zone mit einer Temperatur belegen.
Die eingebauten Modi und der als Frostschutz markierte lassen sich nicht löschen — der
Frostschutz ist die Rückfallebene der Regelung.

### 4.4 Zeitplan-Editor

Wochenansicht auf Basis der Schaltpunkte. Ein Punkt gilt bis zum nächsten; die Woche ist
ein Ring, deshalb kann es weder Lücken noch Überlappungen geben. Die Ansicht zeigt das als
durchgehende Balken, nicht als Liste von Zeilen — eine Liste beantwortet die Frage „was
gilt Dienstag um 3 Uhr?" nicht.

**Zeitplan von einer anderen Zone übernehmen** ist ein eigener Knopf. Wer sechs Zimmer
gleich takten will, trägt nicht sechsmal dasselbe ein.

### 4.5 Übersteuern

Aus der Übersicht heraus: bis zur nächsten Schaltung, für eine Dauer, oder dauerhaft. Die
Domänenfunktion dafür existiert und wird von der REST-Schnittstelle bereits benutzt — die
Oberfläche ruft dieselbe auf.

### 4.6 Regelparameter je Zone

Hysterese, Mindestschaltdauer, Sensor-Timeout, Sensorkalibrierung, Wiederanlaufverzögerung.
Leer heißt „globaler Standard", und die Ansicht **zeigt den geerbten Wert an**, statt ein
leeres Feld — sonst weiß niemand, was gerade gilt.

### 4.7 Benutzer, Gruppen, Tokens

Die Leseansichten stehen. Es fehlen die ändernden Wege: Benutzer anlegen und deaktivieren,
Passwort ändern, Gruppen und Rechte pflegen, Tokens ausstellen und widerrufen.

Beim Ausstellen erscheint der Klartext **genau einmal** — mit einem Hinweis, dass er nicht
wiederholbar ist. Ein Token, das man später nachschlagen kann, ist keines.

`PasswordTooShort` wird bisher nur im Einrichtungsformular gefangen. Die Passwortänderung
braucht dieselbe Behandlung; ein generischer Handler geht nicht, weil die Meldung in das
jeweilige Formular zurück muss. *(Auflage aus dem Abschlussreview von Teilprojekt 1.)*

### 4.8 Audit-Ansicht

Durchsuchbar nach Zeitraum, Akteur, Objekt. Das Protokoll beantwortet „wer hat das
geändert?" — aber nur, wenn man es lesen kann.

### 4.9 Übersichtsseite

Je Zone: Ist, Soll, Sensorzustand, Betriebsart, laufende Übersteuerung und die letzte
Schattenentscheidung mit ihrer Begründung. Das ist die Seite, die man morgens öffnet.

## 5. Rechte

Jede ändernde Ansicht prüft dasselbe Recht wie der entsprechende REST-Endpunkt. Es gibt
keine Ansicht, die mehr darf als die Schnittstelle — sonst wäre die Rechteprüfung eine
Frage des Weges statt des Benutzers.

Zonenbezogene Rechte gelten auch hier: Wer nur eine Zone pflegen darf, sieht auch nur sie,
und ein Zugriff auf eine fremde Zone ist `404`, nicht `403`.

## 6. Was bewusst nicht dazugehört

- **Kein Build-Schritt, kein npm.** Jinja, HTMX, Bootstrap — so entschieden im
  Rahmenentwurf.
- **Kein Schalten.** Der Trockenlauf gilt weiter; die Regelung wird in Phase 4 scharf.
- **Keine Datenübernahme aus dem Altschema.** Sie braucht echte Daten und gehört zu Phase 4.

## 7. Fertig, wenn

Eine leere Instanz lässt sich allein über die Oberfläche in Betrieb nehmen: Benutzer
anlegen, Zonen anlegen, Geräte zuordnen, Modi und Sollwerte setzen, Zeitplan eintragen,
Regelparameter anpassen — und die Übersichtsseite zeigt danach für jede Zone einen
plausiblen Zustand. Ohne einen einzigen SQL-Befehl.
