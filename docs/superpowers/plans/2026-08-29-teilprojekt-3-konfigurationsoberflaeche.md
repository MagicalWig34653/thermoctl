# Implementierungsplan — Teilprojekt 3

Zur [Spezifikation](../specs/2026-08-29-teilprojekt-3-konfigurationsoberflaeche-design.md).
Stand: 2026-08-29.

## Global Constraints

1. **Kein Build-Schritt.** Jinja, HTMX, Bootstrap — beides liegt lokal unter
   `thermoctl/web/static/vendor/`. Kein npm, kein CDN.
2. **Die gemeinsame Vorlagen-Umgebung** aus `thermoctl.web` benutzen, nie eine eigene
   `Jinja2Templates`-Instanz anlegen. `tests/test_architektur.py` hält das nach — der
   Grund steht dort.
3. **CSRF** kommt vom Router (`APIRouter(dependencies=[Depends(csrf_schutz)])`).
   `tests/test_csrf.py` wird rot, wenn eine ändernde Route ohne ihn dazukommt.
4. **Rechte** über `require()` und `visible_zones()`, dieselben Codes wie im REST-Adapter.
   Fremde Zone ⇒ `404`, nicht `403`.
5. **Domänenlogik gehört nicht in die Ansicht.** Fehlt eine Domänenfunktion, ist das ein
   Blocker, kein Anlass, die Regel in der Ansicht zu schreiben.
6. **Eingabefehler führen ins Formular zurück**, mit erhaltenen Werten und einer Meldung am
   Feld. Nie `500`, nie eine leere Maske. Passwörter fließen nie in eine Antwort zurück.
7. **Jede neue Seite gehört in `GESCHUETZTE_SEITEN`** in `tests/test_rauchtest.py` und
   braucht einen Test, der ihren Endpunkt wirklich aufruft.
8. **Nach jedem sichtbaren Teilschritt die Anwendung wirklich starten und die Seite
   öffnen.** Dreimal ist genau hier ein grundlegender Fehler durchgerutscht.
9. Trockenlauf gilt weiter: nichts wird geschaltet, `control_armed` bleibt `false`.

## Aufgaben

### 1. Gemeinsame Formularbausteine — *zuerst, blockiert alles Weitere*
Jinja-Makros für Textfeld, Zahlenfeld, Auswahl, Umschalter; Fehlermeldung am Feld;
Löschbestätigung mit Angabe der abhängigen Objekte; ein Muster für „Formular erneut
anzeigen mit Fehler", das alle Ansichten benutzen. Dazu die Behandlung von
`PasswordTooShort` an einer Stelle, die von mehreren Formularen aufgerufen werden kann.

### 2. Zonenverwaltung
Anlegen, ändern, löschen samt Bestätigung. Recht `zone.manage`.

### 3. Gerätezuordnung und Gerätetausch
Zuordnen und lösen je Rolle; **Tausch als eigener Vorgang**, der Sollwerte, Zeitplan und
Regelparameter der Zone unangetastet lässt und die Historie des alten Geräts nicht
umschreibt. Recht `device.manage`. Der Tausch bekommt einen Test, der genau das belegt:
vorher/nachher denselben Zeitplan und dieselben Sollwerte.

### 4. Modi und Sollwerte
Modi anlegen und ändern (`mode.manage`), Sollwerte je Zone und Modus (`setpoint.write`).
Eingebaute Modi und der Frostschutzmodus lassen sich nicht löschen — mit Begründung in der
Meldung, nicht nur mit einem ausgegrauten Knopf.

### 5. Zeitplan-Editor
Wochenansicht als Balken, Punkte anlegen, verschieben, löschen; Übernahme von einer
anderen Zone. Recht `schedule.manage`. Baut auf `thermoctl/domain/schedule.py` auf.

### 6. Übersteuern aus der Oberfläche
Ruft `uebersteuerung_anlegen()` und `uebersteuerung_aufheben()` — dieselben Funktionen wie
der REST-Adapter. Rechte `override.create` und `override.cancel`.

### 7. Regelparameter je Zone
Sechs Felder, leer = geerbt. Die Ansicht **zeigt den geerbten Wert an**, statt ein leeres
Feld. Recht `zone.manage`.

### 8. Benutzer, Gruppen, Tokens vervollständigen
Benutzer anlegen und deaktivieren, Passwort ändern, Gruppen und Rechte pflegen, Tokens
ausstellen und widerrufen. Der Tokenklartext erscheint genau einmal, mit Hinweis.
**Sicherheitsrelevant — wird in der Hauptsession gegengelesen.**

### 9. Audit-Ansicht
Filter nach Zeitraum, Akteur, Objekt; Blätterung. Recht `audit.read`.

### 10. Übersichtsseite
Je Zone Ist, Soll, Sensorzustand, Betriebsart, laufende Übersteuerung und die letzte
Schattenentscheidung samt Begründung.

## Reihenfolge

1 zuerst. Danach parallel: 2, 4, 8, 9. Dann 3, 5, 7 (bauen auf 2 auf), zuletzt 6 und 10
(bauen auf allem auf).

## Review

Kreuzweise wie bisher; jedes Review führt die Suite selbst aus, gegen beide Datenbanken,
dazu Ruff und mypy. Aufgabe 8 zusätzlich in der Hauptsession.
