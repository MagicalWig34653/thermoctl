# Auftrag: thermoctl WebUI vollständig überarbeiten

Überarbeite die bestehende thermoctl-Weboberfläche umfassend anhand der beiden bereitgestellten HTML-Referenzdemos:

- `thermoctl_admin_final.html`
- `thermoctl_mieter_final.html`

Nutze außerdem die vorhandene Bestandsaufnahme der WebUI als fachliche und technische Quelle:

- `thermoctl — Bestandsaufnahme der WebUI.md`

Die Demos definieren primär Informationsarchitektur, Layout, Informationsdichte, responsives Verhalten, Textebene und Interaktionskonzept. Sie sind **keine Implementierungsvorlage für JavaScript oder Domänenlogik**. Demo-JavaScript darf insbesondere nicht als neue fachliche Logik in den Browser übernommen werden.

Das Ergebnis soll eine produktionsreife neue WebUI sein und keine zusätzliche Demo neben der bestehenden Oberfläche.

Arbeite den Auftrag vollständig durch. Bleibe nicht bei einem Konzept, einer Analyse oder einem teilweise umgebauten Dashboard stehen.

---

# 1. Zentrale Ziele

Die Anwendung benötigt künftig zwei klar unterschiedliche WebUI-Erlebnisse:

1. **Administrator-Oberfläche**
   - vollständiger Anlagenbetrieb
   - Diagnose
   - Konfiguration
   - Geräte
   - Benutzer und Rechte
   - Schnittstellen
   - Protokolle
   - technische Informationen

2. **Mieter-Oberfläche**
   - ausschließlich wohnungs- und komfortbezogene Funktionen
   - verständliche Alltagssprache
   - nur zugewiesene bzw. sichtbare Zonen
   - Temperatur
   - Zeitplan
   - temporäre Änderungen
   - Heizzeit
   - persönliche Kontofunktionen
   - relevante Störungshinweise
   - keine technischen Anlagenparameter

Aktuell existiert diese Unterscheidung noch nicht. Sie muss im Datenmodell, in der Navigation, bei den HTML-Routen und in den Templates sauber eingeführt werden.

Die bestehende Permission-/Principal-Infrastruktur bleibt dabei die eigentliche Autorisierungsgrundlage.

---

# 2. Vor Beginn der Änderungen

Analysiere zuerst den tatsächlichen aktuellen Quellcode.

Insbesondere:

- Benutzer- und Gruppenmodell
- Datenbankmigrationen
- `Principal`
- Grants und Permissions
- `visible_zones`
- `has_permission`
- `PERMISSION_AREAS`
- `web/navigation.py`
- alle HTML-Router
- `base.html`
- `base_plain.html`
- `zone_header.html`
- Startseite
- Schedule-Router und `schedule.js`
- Override- und Setpoint-Endpunkte
- Statistik
- Benutzer-/Gruppenverwaltung
- aktuelle Tests für Navigation und Rechte
- HTMX-/CSRF-Infrastruktur
- URL-Prefix-/Ingress-Behandlung

Verifiziere insbesondere, welches Recht aktuell zum Bearbeiten eines Zeitplans erforderlich ist.

Wenn Zeitplanänderungen derzeit nur über ein zu weitgehendes Recht wie `zone.manage` möglich sind, darf einem Mieter **nicht** einfach `zone.manage` gegeben werden, nur damit er seinen Zeitplan ändern kann.

Führe dann ein passendes separates, zonenspezifisches Schreibrecht für Zeitpläne ein, sofern im bestehenden Modell noch keines existiert, beispielsweise sinngemäß:

`schedule.write`

Der konkrete Name soll zur vorhandenen Permission-Namenskonvention passen.

---

# 3. Rollenmodell: Admin und Mieter

## 3.1 UI-Profil ergänzen

Führe ein explizites UI-Profil ein:

- `admin`
- `tenant`

Das UI-Profil bestimmt, **welche Weboberfläche** ein Benutzer erhält.

Es darf nicht die bestehende Permission-Prüfung ersetzen.

Bevorzugte Modellierung:

`Group.ui_profile`

Da thermoctl Benutzer derzeit einer Gruppe zuordnet, gehört das Oberflächenprofil sinnvollerweise zur Gruppe.

Prüfe das tatsächliche Datenmodell vor der Implementierung. Falls der reale Code von dieser Annahme abweicht, wähle eine entsprechend saubere zentrale Lösung.

Keine Prüfung auf Gruppennamen wie:

```python
if group.name == "Mieter":
```

oder

```python
if user.group == "Administratoren":
```

Das ist ausdrücklich nicht erwünscht.

Verwende ein typisiertes Feld beziehungsweise Enum.

Beispielkonzept:

```text
WebUiProfile.ADMIN
WebUiProfile.TENANT
```

## 3.2 Migration

Erstelle eine saubere Datenbankmigration.

Für bestehende Installationen gilt:

- existierende Gruppen werden zunächst `admin`
- dadurch darf ein Upgrade keine Benutzer überraschend in eine eingeschränkte Oberfläche verschieben
- der erste Benutzer aus `/setup` muss weiterhin Administrator sein
- frisch angelegte Administratorgruppen erhalten `admin`
- Gruppenverwaltung muss das UI-Profil anzeigen und ändern können

Optional kann für neue Installationen eine Mieter-Gruppenvorlage angeboten werden. Bestehende Gruppen dürfen durch die Migration aber nicht automatisch anhand ihres Namens umklassifiziert werden.

## 3.3 UI-Profil ist nicht gleich Permission

Ein Mieter darf nur Funktionen ausführen, für die seine Grants ausreichen.

Ein Administrator mit eingeschränkten Grants darf ebenfalls nur das ausführen, was seine Grants erlauben.

Das bedeutet:

```text
UI-Profil = Informationsarchitektur / Web-Erlebnis
Permissions = tatsächliche Berechtigung
```

Beides muss geprüft werden.

---

# 4. HTML-Routen nach UI-Profil trennen

Die Trennung darf nicht ausschließlich über versteckte Navigation erfolgen.

Admin-spezifische HTML-Seiten müssen zusätzlich sicherstellen, dass sie nur mit dem Admin-UI-Profil aufgerufen werden können.

Mieter dürfen nicht durch manuelles Eingeben von beispielsweise

`/settings`

`/control`

`/users`

`/groups`

`/interfaces`

`/relay-wear`

in die Administratoroberfläche gelangen.

Implementiere hierfür eine zentrale und testbare Dependency beziehungsweise Guard-Funktion.

Zum Beispiel sinngemäß:

```text
require_web_ui_profile(ADMIN)
require_web_ui_profile(TENANT)
```

Verwende beim Ablehnen den im Projekt bereits etablierten HTTP-Status beziehungsweise das vorhandene Denial-Verhalten.

Wichtig:

Auch hinter diesem UI-Profile-Guard müssen alle bestehenden Permission-Prüfungen bestehen bleiben.

REST/API-Verhalten darf durch die Einführung der UI-Profile nicht versehentlich verändert werden.

---

# 5. Navigation

Erweitere die bestehende zentrale Navigationsdefinition statt Navigation an vielen Stellen hart zu codieren.

Navigationseinträge sollen mindestens nach folgenden Faktoren gefiltert werden können:

- UI-Profil
- Permission
- gegebenenfalls sichtbare Zonen

Die bisherige Eigenschaft, dass Navigation nur Darstellung ist und die Endpunkte selbst ebenfalls prüfen, muss bestehen bleiben.

## Administrator

Desktop-Sidebar entsprechend der Admin-Demo:

### Hauptbereich
- Übersicht
- Zonen
- Geräte
- Betrieb

### Analyse
- Heizstatistik
- Relaisverschleiß
- Protokolle

### System
- Regelvorgaben
- Sollwert-Modi
- Zeitplan
- Schnittstellen
- Bediengeräte

### Zugänge
- Benutzer
- Gruppen
- API-Tokens
- Kiosk-Tokens

Persönliche Kontofunktionen wie Passkeys, Passwort, Sitzungen und Logout sind über das Benutzer-/Kontomenü erreichbar.

Auf kleinen Displays eine reduzierte Bottom-Navigation:

- Übersicht
- Zonen
- Geräte
- Mehr

## Mieter

Desktop:

- Zuhause
- Zeitplan
- Heizzeit
- Mehr

Mobil dieselben vier Bereiche als Bottom-Navigation.

Keine technische Sidebar.

---

# 6. Template-Architektur

Vermeide zwei vollständig unabhängige Kopien der gesamten Anwendung.

Baue einen gemeinsamen Kern und zwei App-Shells.

Eine sinnvolle Zielstruktur wäre beispielsweise:

```text
base_core.html
base_admin.html
base_tenant.html
base_plain.html
```

`base_plain.html` bleibt die separate Grundlage für den Kiosk.

Der gemeinsame Kern enthält unter anderem:

- `<head>`
- Farbschema
- gemeinsame Assets
- HTMX
- CSRF-Infrastruktur
- Ladeindikator
- Stale-Page-Infrastruktur
- gemeinsame Meta-Tags

Admin- und Mieter-Shell enthalten jeweils ihre eigene Navigation und Layoutstruktur.

Extrahiere wiederverwendbare Jinja-Komponenten beziehungsweise Includes, zum Beispiel für:

- Status-Chip
- Alert
- Temperaturanzeige
- Setpoint-Stepper
- Timeline
- Forecast
- Modal/Dialog
- Empty State
- Tabellenstatus
- Segment-Control

Admin- und Mieter-Zonenkarten dürfen unterschiedliche Komponenten sein, weil ihre Informationsdichte absichtlich unterschiedlich ist.

---

# 7. Keine Emojis

In der gesamten neuen WebUI dürfen **keine Emojis** verwendet werden.

Das gilt für:

- Navigation
- Buttons
- Hinweise
- Statusanzeigen
- Toasts
- Empty States
- Tabellen
- Dialoge

Verwende stattdessen:

- Text
- CSS-Statuspunkte
- CSS-Formen
- lokal eingebettete SVG-Icons

Keine externe Icon-CDN und keine Icon-Font-Abhängigkeit.

Status darf niemals ausschließlich durch Farbe oder ein Symbol vermittelt werden. Immer zusätzlich verständlichen Text anzeigen.

Beispiele:

```text
Sensor still
Verbunden
Heizt
Übersteuerung aktiv
MQTT gesperrt
```

---

# 8. Administrator-Dashboard

Die Admin-Startseite `/` orientiert sich an `thermoctl_admin_final.html`.

Sie beantwortet weiterhin primär:

> Tut das Haus gerade, was konfiguriert wurde?

Keine belanglosen KPI-Kacheln wie Benutzeranzahl oder Zonenanzahl hinzufügen.

## 8.1 Drei Betriebsriegel

Die drei wesentlichen Zustände müssen separat sichtbar bleiben:

1. Aktorausgabe / `control_armed`
2. MQTT-Ausgabe / `sending_allowed`
3. aktive bzw. Bereitschaftsinstanz

Diese dürfen nicht zu einem einzelnen „System OK“-Status verschmolzen werden.

Auch der Hinweis, dass Änderungen des MQTT-Riegels erst nach Prozessneustart wirken, muss dort erhalten bleiben, wo er relevant ist.

## 8.2 Zonen

Admin-Zonenkarten zeigen unter anderem:

- Anzeigename
- Sensor
- Isttemperatur
- aufgelösten Sollwert
- Begründung
- Heiz-/Regelzustand
- Tagesverlauf
- aktuellen Modus
- Setpoint-Stepper
- Übersteuerung
- Link zu Details

Thermostat und Übersteuerung müssen klar getrennt sein.

Der Stepper verändert weiterhin den Sollwert des aktuell laufenden Modus **dauerhaft**.

Die Oberfläche muss ausdrücklich anzeigen, welcher Modus geändert wird.

---

# 9. Mieter-Startseite

Die Mieter-Startseite `/` folgt `thermoctl_mieter_final.html`.

Keine technischen Anlageninformationen wie:

- MQTT
- Broker
- Aktorfreigabe
- Relais
- Schattenentscheidung
- PI-Regelung
- Hysterese
- Bridge-Konfiguration

Stattdessen verständliche Zusammenfassung, zum Beispiel:

```text
Heizung läuft normal
Alle Räume werden wie geplant geregelt.
```

Wenn ein Problem existiert, nur den für den Mieter relevanten Effekt erklären.

Beispiel:

```text
Bad: Messwert möglicherweise nicht aktuell

Seit 24 Minuten kam keine neue Temperatur.
Die Heizung regelt weiter, aber der angezeigte Wert kann veraltet sein.
```

Keine unnötigen technischen Diagnosedaten.

---

# 10. Mieter-Zonenkarte

Für jede sichtbare Zone anzeigen:

- Raumname
- aktueller Heizstatus
- Isttemperatur
- Zeitpunkt/Aktualität der Messung
- aktueller Modus
- Solltemperatur
- verständliche Sollwertbegründung
- Tagesverlauf
- nächste geplante Änderung
- normale Temperaturänderung
- temporäre Übersteuerung
- „Zur nächsten Schaltzeit springen“

Der Benutzer darf ausschließlich Zonen sehen, die `visible_zones` für seinen Principal zurückgibt.

Keine Zonennamen anderer Mieter dürfen über Seitentitel, Select-Optionen, Fehlermeldungen oder versteckte HTML-Daten durchsickern.

---

# 11. Temperatur-Stepper

Der normale Stepper:

```text
-   21,0 °C   +
```

ändert weiterhin in 0,5-K-Schritten den Sollwert des aktuell laufenden Modus.

Wichtig:

Die tatsächliche Berechnung geschieht serverseitig.

Keine Browserlogik wie:

```javascript
value += 0.5
value = Math.min(...)
```

als fachliche Wahrheit übernehmen.

Der Client sendet lediglich die gewünschte Aktion.

Der Server bestimmt anhand des aktuellen Zustands den neuen Wert und validiert alle Grenzen.

Dadurch bleiben auch schnelle aufeinanderfolgende Klicks korrekt.

---

# 12. Temporäre Übersteuerung

Mieteraktion:

`Für eine Weile wärmer`

Dialog mit:

- gewünschter Temperatur
- 30 Minuten
- 1 Stunde
- 2 Stunden
- bis zur nächsten Umschaltung

Danach gilt automatisch wieder der Zeitplan.

Wenn eine Übersteuerung aktiv ist:

- sichtbar kennzeichnen
- Endzeit anzeigen
- „Beenden“ anbieten
- erklären, dass danach wieder der normale Zeitplan gilt

Die bestehende Override-Domänenlogik wiederverwenden.

Keine parallele Override-Implementierung nur für Mieter erstellen.

---

# 13. „Zur nächsten Schaltzeit springen“

Diese Funktion ist ein wichtiger Teil der finalen Mieteroberfläche.

Beispiel:

Aktuell:

```text
Komfort
21,0 °C
Nächste Umschaltung: Nacht 18,0 °C um 23:00
```

Klick auf:

`Zur nächsten Schaltzeit springen`

Ergebnis:

```text
Nacht vorgezogen
18,0 °C bis 23:00
```

Umsetzung:

- nächsten relevanten Schaltpunkt serverseitig aus der vorhandenen Schedule-Domäne bestimmen
- den dort vorgesehenen Modus beziehungsweise Sollwert bestimmen
- eine Übersteuerung bis exakt zu diesem Schaltpunkt anlegen
- Wochenplan nicht verändern
- danach automatisch wieder normal weiterlaufen

Keine Berechnung des nächsten Schaltpunkts in JavaScript.

Wenn bereits eine andere Übersteuerung aktiv ist, darf nicht still eine zweite konkurrierende Aktion erzeugt werden. Zeige stattdessen den aktuellen Override-Zustand und biete dessen Beendigung beziehungsweise eine klar definierte Ersetzung an.

Nutze die bestehende Domain-Priorität und Forecast-Logik als einzige Wahrheit.

---

# 14. Mieter-Zeitplan

Baue eine mieterfreundliche Zeitplanansicht.

Navigation:

`Zeitplan`

Der Benutzer kann zwischen seinen sichtbaren Räumen wechseln.

Oben:

## Nächste 24 Stunden

Zeige den **tatsächlichen** zu erwartenden Verlauf inklusive:

- Betriebsart
- laufender Übersteuerung
- Zeitplan
- Fallback/Frostschutz

Diese Vorschau muss von derselben Domain-Funktion stammen wie die laufende Regelung.

Darunter:

## Deine Woche

Montag bis Sonntag als kompakte Timeline.

Der Mieter soll primär verstehen:

```text
Warm ab 06:30
Nacht ab 23:00
```

Die Oberfläche darf einfacher sein als der Admin-Schedule-Editor.

Ermögliche:

- einzelnen Tag bearbeiten
- Tag kopieren
- Werktage angleichen
- Zeitplan eines eigenen Raums auf einen anderen eigenen Raum übernehmen
- Rückgängig, sofern die bestehende Undo-Infrastruktur das unterstützt

Alle Mutationen müssen serverseitig autorisiert und validiert werden.

Falls für diese Funktionen noch kein separates Schedule-Schreibrecht existiert, führe eines ein.

---

# 15. Heizzeit für Mieter

Mieter erhalten eine vereinfachte Variante von `/statistics`.

Zeiträume:

- 7 Tage
- 30 Tage
- 90 Tage

Kein frei wählbarer Datumsbereich.

Nur sichtbare Zonen anzeigen.

Bezeichne die Werte ausdrücklich als:

`Heizzeit`

Nicht:

- Energieverbrauch
- Verbrauch
- Kosten
- kWh

Wenn die Anlage im Trockenlauf ist, muss ausdrücklich erklärt werden, dass die Werte zeigen, wann die Anlage **hätte geheizt**.

---

# 16. Mieter „Mehr“

Dieser Bereich enthält ausschließlich persönliche bzw. wohnungsbezogene Funktionen.

## Konto und Sicherheit

- Passkey
- Passwort ändern
- andere Sitzungen beenden
- Logout

Bestehende Funktionen wiederverwenden.

Keine API-Tokens im normalen Mieter-Menü.

## Hilfe und relevante Informationen

- Erklärung von Sollwert, Heizstatus, Zeitplan und Sensorwarnungen
- Erreichbarkeits-/Aktualitätsinformation in verständlicher Form

---

# 17. Abwesenheit

Die in der Mieter-Demo dargestellte Abwesenheitsfunktion ist als gewünschte neue Produktfunktion zu behandeln.

Ziel:

```text
Ich bin bis Samstag weg.
Alle meine Räume bis dahin sparsamer regeln.
```

Der Mieter wählt:

- Rückkehrdatum/-zeit
- Abwesenheitstemperatur

Die Aktion gilt nur für seine sichtbaren beziehungsweise bedienbaren Zonen.

Implementiere dies **nicht** als neue konkurrierende Regelengine.

Bevorzugt soll die bestehende Override-Domäne verwendet werden.

Falls erforderlich, ergänze eine serverseitige Bulk-Service-Funktion, die für alle berechtigten Zonen atomar beziehungsweise kontrolliert entsprechende Overrides bis zu einem gemeinsamen Endzeitpunkt anlegt.

Anforderungen:

- jedes einzelne Ziel muss `override.create` erlauben
- keine fremden Zonen
- Abwesenheit muss sichtbar sein
- vorzeitig beendbar
- Ende automatisch
- Fehler bei einzelnen Zonen dürfen nicht zu einem unklaren Teilzustand führen

Wenn das bestehende Override-Modell absolute Endzeitpunkte noch nicht unterstützt, erweitere zuerst sauber die Domain statt dies in der Web-Schicht zu simulieren.

---

# 18. Problem melden

Die Demo enthält „Problem melden“.

Implementiere keinen Fake-Button.

Prüfe zunächst die vorhandene Störungs-/Webhook-Infrastruktur.

Wenn sich diese sinnvoll erweitern lässt, implementiere einen echten Mieter-Problembericht.

Der Mieter wählt beispielsweise:

- Raum wird nicht warm
- Temperatur wirkt falsch
- Raum zu warm
- anderes Problem

Automatisch serverseitig ergänzbar:

- Benutzer
- erlaubte Zone
- Zeitpunkt
- letzter Messwert
- aktueller Sollwert
- aktueller Modus
- relevante Regelentscheidung

Keine geheimen Zugangsdaten, MQTT-Zugangsdaten oder Daten fremder Zonen mitsenden.

Der Bericht sollte über eine vorhandene, administrativ konfigurierte Meldekette versendet und im Audit nachvollziehbar sein.

Wenn dafür ein neues Recht benötigt wird, verwende ein explizites Schreibrecht. Eine reine `zone.read`-Berechtigung darf keine externen Meldungen auslösen.

Wenn keine belastbare Zustellmöglichkeit existiert, soll die Funktion nicht als scheinbar funktionierender No-op im Produktions-UI verbleiben.

---

# 19. Persönliche Störungsbenachrichtigungen

Die Demo zeigt eine Option für wichtige Störungen.

Prüfe kritisch, ob thermoctl aktuell überhaupt einen realen individuellen Zustellkanal für Mieter besitzt.

Falls kein solcher Kanal existiert:

- keine funktionslose Einstellung bauen
- stattdessen zunächst relevante Hinweise innerhalb der WebUI anzeigen
- fehlende individuelle Notification-Infrastruktur als klar dokumentierte Folgearbeit markieren

Falls bereits ein passender Zustellkanal existiert, darf eine persistente persönliche Präferenz eingeführt werden.

---

# 20. Admin-Seiten

Die bestehenden Admin-Funktionen müssen erhalten bleiben und in das neue Design überführt werden.

Dazu gehören mindestens:

- Startseite
- Zonen
- Zone anlegen/bearbeiten/löschen
- Zonenparameter
- Sollwerte
- Gerätezuordnung
- Zeitplan
- Geräte
- Anlagenbild
- Bediengeräte
- Betrieb
- Regelvorgaben
- Sollwert-Modi
- Schnittstellen
- Statistik
- Relaisverschleiß
- Audit
- Schaltprotokoll
- Benutzer
- Gruppen
- API-Tokens
- Kiosk-Tokens
- Passkeys

Kein Feature darf beim visuellen Umbau still verloren gehen.

---

# 21. Betrieb und Regelvorgaben getrennt lassen

`/control` und `/settings` bleiben getrennte Aufgaben.

`/control`:

> Was macht die Anlage gerade und können Befehle tatsächlich ausgegeben werden?

`/settings`:

> Nach welchen anlagenweiten Vorgaben soll geregelt werden?

Diese Seiten nicht wieder zusammenlegen.

---

# 22. Geräte

Die Geräteübersicht behält das Prinzip:

`Auffälliges zuerst`

Wichtige Informationen:

- Batterie
- Funkqualität
- Verfügbarkeit
- Stille
- letzte Aktivität
- Zone

Desktop darf eine kompakte Tabelle verwenden.

Nicht jedes Gerät in eine riesige Karte umwandeln.

Biete:

```text
Liste | Anlagenbild
```

als zusammengehörige Darstellungen an.

Die vorhandene Spezialbehandlung einzelner Gerätetypen muss erhalten bleiben.

---

# 23. Regelvorgaben

Strukturiere die Seite in semantische Gruppen statt als lange Formularwand:

- Regelverhalten
- Zeit & Daten
- Solarprognose
- Störungsmeldungen
- Webhook
- gegebenenfalls Erweitert

Zu jedem technischen Parameter eine kurze, sachliche Erklärung.

Die serverseitigen Werte und Grenzen sind maßgeblich.

HTML-`min`/`max` oder JavaScript dürfen keine zweite fachliche Wahrheit erzeugen.

---

# 24. Protokolle und Trockenlauf

Audit-Protokoll und Schaltprotokoll dürfen visuell enger zusammenrücken, ihre Semantik darf aber nicht verloren gehen.

Bei Trockenlauf muss weiterhin unübersehbar erklärt werden:

- Statistik: Anlage **hätte geheizt**
- Schaltprotokoll: Befehl wurde **unterdrückt**

Nicht nur über Farbe oder Tooltip kommunizieren.

---

# 25. Kiosk nicht mit Mieter verwechseln

Der bestehende Kiosk bleibt ein separater Zugangsweg mit:

- Kiosk-Token
- eigenem Principal
- `visible_zones`
- `has_permission`
- `base_plain.html`
- Cookie-basierter Tokenbehandlung
- bestehendem Refresh-Verhalten

Mieter-UI und Kiosk-UI sind zwei unterschiedliche Konzepte.

Kiosk nicht auf Benutzerlogin oder Mietergruppe umbauen.

---

# 26. Technische Rahmenbedingungen

Diese Regeln sind verbindlich.

## Kein Frontend-Buildsystem

Nicht einführen:

- npm
- Node
- Vite
- Webpack
- React
- Vue
- Svelte
- Tailwind-Build
- externe CDN-Abhängigkeiten

Weiterhin:

- serverseitiges Jinja
- lokales Bootstrap 5
- HTMX
- Vanilla JavaScript, wo wirklich erforderlich

## HTMX

`hx-boost="true"` bleibt berücksichtigt.

JavaScript darf nicht so eingebaut werden, dass es nach HTMX-Navigation mehrfach registriert wird.

Keine produktiven Skriptblöcke in ausgetauschten Body-Fragmenten, wenn sie dadurch erneut ausgeführt werden.

Bevorzugt:

- Skripte im `<head>`
- Event Delegation
- HTMX-Lifecycle-Events

## CSRF

Bestehenden Mechanismus vollständig erhalten.

Keine neuen Mutationsendpunkte ohne CSRF-Schutz.

## Ingress-Prefix

Alle URLs weiterhin über die bestehende Prefix-Infrastruktur erzeugen.

Keine hart codierten Root-URLs.

## OpenAPI

HTML-/Formularrouten bleiben `include_in_schema=False`.

REST-OpenAPI bleibt Vertrag der REST-Schnittstelle.

---

# 27. Live-Aktualisierung

Bestehende Live-Aktualisierung erhalten.

Insbesondere:

- Poll-Intervall aus Servereinstellung
- nicht im Hintergrund pollen, wenn das bestehende Verhalten dies pausiert
- `hx-preserve` für geöffnete Bereiche und begonnene Eingaben
- stille Polls lösen keinen globalen Ladebalken aus
- keine unnötige Poll-Frequenz

---

# 28. Stale Page

`HX-Stale-Page` muss weiterhin sichtbar behandelt werden.

Neues Design:

deutliches Banner im App-Shell mit:

```text
Diese Ansicht ist nicht mehr aktuell.
Einige Aktionen wurden deshalb nicht ausgeführt.

[Neu laden]
```

Kein automatisches Reload.

Nicht als kurzlebigen Toast ersetzen.

---

# 29. Farbschema

Dark Mode folgt weiterhin ausschließlich:

`prefers-color-scheme`

Keinen manuellen Dark-/Light-Schalter hinzufügen.

Die neuen Farben können sich an den beiden Demos orientieren.

Administrator:

- neutrale Oberfläche
- Blau als Primärfarbe
- Orange für Wärme/Heizen
- Grün für Erfolg
- Amber für Warnung
- Rot für Fehler

Mieter:

- ruhiger, wohnlicher Gesamteindruck
- gedämpftes Grün als Primärfarbe
- warme Farbe für Heizen
- dezente neutrale Flächen

Kontrast und Lesbarkeit haben Vorrang vor pixelgenauer Übernahme der Demo-Farbwerte.

---

# 30. Responsivität

Beide Oberflächen müssen von Smartphone bis Desktop funktionieren.

## Mieter

Mobile first.

- einspaltige Raumkarten
- Bottom Navigation
- große Temperaturen
- große Touch-Ziele
- wichtige Aktionen ohne Hover erreichbar
- mindestens ca. 44–48 px hohe primäre Touch-Flächen

## Administrator

Desktop-orientiert, aber mobil benutzbar.

Desktop:

- Sidebar
- kompakte Tabellen
- mehrspaltige Zonenkarten

Mobil:

- Sidebar ausblenden
- Bottom Navigation
- Tabellen horizontal scrollbar oder sinnvoll reduziert
- Karten einspaltig

---

# 31. Accessibility

Achte mindestens auf:

- semantische HTML-Struktur
- echte Buttons für Aktionen
- echte Links für Navigation
- sichtbare Focus-States
- Tastaturbedienbarkeit
- Dialog-Fokus
- verständliche Labels
- ARIA nur dort, wo semantisches HTML nicht reicht
- `prefers-reduced-motion`
- ausreichenden Farbkontrast
- Status nie nur über Farbe
- große Touch-Ziele

---

# 32. Demo-JavaScript nicht übernehmen

Die JavaScript-Funktionen aus den HTML-Demos dienen nur der Demonstration.

Insbesondere nicht produktiv übernehmen:

```text
lokales Temperatur-Clamping
lokales Ändern des Sollwerts
lokales Forecast-Berechnen
lokales Bestimmen des nächsten Schaltpunkts
lokales Speichern von Zeitplänen
Fake-Toasts als Ersatz für Serverantworten
```

Fachliche Zustände müssen vom Server kommen.

Der Browser zeigt an und löst Aktionen aus.

---

# 33. Empfohlene Umsetzungsreihenfolge

Arbeite in dieser Reihenfolge:

1. bestehenden Code und Tests analysieren
2. UI-Profil im Datenmodell ergänzen
3. Migration erstellen
4. Profile-Guard für HTML-Routen
5. Navigation um Profile erweitern
6. gemeinsamen Template-Kern erstellen
7. Admin-App-Shell bauen
8. bestehende Admin-Seiten schrittweise migrieren
9. Mieter-App-Shell bauen
10. Mieter-Startseite
11. Mieter-Schedule-Hub
12. Mieter-Statistik
13. persönlicher Konto-Bereich
14. „Zur nächsten Schaltzeit springen“
15. Abwesenheit
16. Problem melden, sofern real implementierbar
17. Kiosk-Regressionsprüfung
18. responsive und Accessibility-Überarbeitung
19. vollständige Tests
20. alten, nicht mehr verwendeten UI-Code entfernen

Nicht zuerst alte Templates löschen.

Migriere kontrolliert und entferne Altcode erst, wenn seine Funktionen nachweislich ersetzt sind.

---

# 34. Tests für Rollen und Sicherheit

Ergänze automatisierte Tests.

Mindestens folgende Fälle müssen abgedeckt sein:

## Migration

- bestehende Gruppe erhält `admin`
- Setup-Benutzer landet im Admin-Profil
- kein bestehender Grant geht verloren

## Navigation

- Admin sieht nur erlaubte Admin-Navigation
- Mieter sieht nur Mieter-Navigation
- fehlende Permissions entfernen weiterhin einzelne Funktionen

## HTML-Endpunkte

- Mieter kann Admin-only-Seiten nicht öffnen
- Admin kann Mieter-spezifische Daten nicht versehentlich anderer Benutzer sehen
- Profilprüfung ersetzt Permission-Prüfung nicht

## Zonenisolation

Ein Mieter mit Zugriff auf Zone A darf niemals:

- Zone B sehen
- Zone B im Zeitplan auswählen
- Zone B in Statistik sehen
- Zone B in Formularoptionen sehen
- Zone B durch manipulierte POST-Daten ändern
- Namen oder IDs von Zone B in Fehlermeldungen erhalten

## Setpoint

- 0,5-K-Schritt serverseitig
- zwei schnelle Aktionen ergeben zwei korrekte Schritte
- Grenzen serverseitig

## Override

- erstellen
- beenden
- bis nächsten Schaltpunkt
- Ablauf

## Zur nächsten Schaltzeit springen

- nächster Schaltpunkt kommt aus Domain
- korrekter Zielmodus
- korrekter Zielwert
- korrekte Endzeit
- Wochenplan unverändert
- aktive konkurrierende Übersteuerung korrekt behandelt

## Zeitplan

- lesen
- bearbeiten
- Berechtigung pro Zone
- kopieren
- übernehmen
- Undo, sofern vorhanden
- Forecast entspricht Domain

## Statistik

- nur sichtbare Zonen
- 7/30/90 Tage
- Trockenlauf korrekt beschriftet

## HTMX

- CSRF
- Stale-Page
- quiet poll
- Prefix
- keine doppelte Eventregistrierung

---

# 35. Bestehende Wächtertests erhalten und erweitern

Bestehende Tests, die Navigation und Endpoint-Rechte zusammenhalten, dürfen nicht entfernt oder aufgeweicht werden.

Erweitere sie um das neue UI-Profil.

Die zentrale Idee soll weiterhin sein:

```text
Navigation kann etwas verstecken.
Nur der Endpoint entscheidet, ob die Aktion erlaubt ist.
```

---

# 36. Keine Sicherheitsregression durch die Mieteroberfläche

Besonders prüfen:

- Zonennamen sind geschützt
- Anlagenweite Seiten dürfen keine fremden Zonennamen rendern
- Hidden Inputs enthalten keine fremden IDs
- Select-Optionen enthalten nur sichtbare Zonen
- Fehlertexte leaken keine fremden Objekte
- Bulk-Abwesenheit akzeptiert ausschließlich serverseitig bestimmte sichtbare/berechtigte Zonen
- Problemberichte enthalten ausschließlich erlaubte Daten
- Profile können nicht clientseitig umgeschaltet werden
- Mieter-Template wird anhand authentifizierter Serverdaten gewählt

Niemals:

```text
?role=admin
localStorage role
Cookie role
JavaScript role
```

als Quelle des UI-Profils verwenden.

---

# 37. UX-Texte

Administratoroberfläche darf technische Begriffe verwenden, wenn diese für Betrieb und Diagnose erforderlich sind.

Mieteroberfläche bevorzugt Alltagssprache.

Beispiele:

Admin:

```text
MQTT-Ausgabe freigegeben
Sensor still
Aktorfreigabe
Übersteuerung
```

Mieter:

```text
Heizung läuft normal
Messwert möglicherweise nicht aktuell
Für eine Weile wärmer
Als Nächstes
Normale Komforttemperatur
```

Keine verniedlichende Sprache.

Keine Emojis.

---

# 38. Definition of Done

Die Aufgabe ist erst abgeschlossen, wenn:

- Admin/Mieter-Profil im Datenmodell existiert
- Migration vorhanden ist
- Profile serverseitig bestimmt werden
- Admin- und Mieter-App-Shell existieren
- Login auf die richtige Oberfläche führt
- Admin-HTML-Routen gegen Tenant-Profil geschützt sind
- Permissions weiterhin überall geprüft werden
- Mieter ausschließlich sichtbare Zonen sieht
- Admin-Oberfläche die bisherige Funktionalität vollständig ersetzt
- Mieter-Startseite funktionsfähig ist
- Temperatur-Stepper real arbeitet
- Override real arbeitet
- „Zur nächsten Schaltzeit springen“ real arbeitet
- Mieter-Zeitplan real arbeitet
- Heizzeit real arbeitet
- Konto-/Passkey-Funktionen erreichbar sind
- Stale-Page funktioniert
- Polling funktioniert
- CSRF funktioniert
- URL-Prefix funktioniert
- Kiosk weiterhin funktioniert
- Dark Mode über Betriebssystem funktioniert
- Mobile Layouts funktionieren
- keine Emojis verwendet werden
- kein npm/Build/CDN eingeführt wurde
- keine Domain-Logik unnötig in JavaScript dupliziert wurde
- relevante Tests ergänzt wurden
- vollständige Testsuite erfolgreich läuft

---

# 39. Abschlussbericht

Wenn die Umsetzung abgeschlossen ist, liefere einen strukturierten Abschlussbericht mit:

1. geänderter Architektur
2. neuem Rollen-/UI-Profil-Modell
3. Datenbankmigration
4. neuen beziehungsweise geänderten Permissions
5. neuen Routen
6. migrierten Templates
7. neuen Komponenten
8. neuen JavaScript-Dateien beziehungsweise Änderungen
9. Tests
10. bewusst nicht implementierten Punkten und Begründung
11. verbleibenden Risiken
12. manuellen Prüfschritten

Führe alle automatisierbaren Tests selbst aus.

Falls Tests fehlschlagen, behebe die Ursache, solange sie Teil dieser Änderung ist.

Lasse keine Demo-Platzhalter, Fake-Buttons, Mock-Daten oder rein visuelle Funktionen in der produktiven Oberfläche zurück.

---

# Prioritäten bei Konflikten

Wenn Demo, vorhandener Code und diese Arbeitsanweisung an einer Stelle nicht exakt zusammenpassen, gilt folgende Priorität:

1. Sicherheit und bestehende Autorisierungsregeln
2. bestehende Domänenlogik und fachliche Semantik
3. diese Arbeitsanweisung
4. Informationsarchitektur der finalen HTML-Demos
5. pixelgenaue Optik der Demos

Die Demos sind Zielbild für UX und visuelle Gestaltung, aber niemals Grund, Sicherheits- oder Domänenlogik zu duplizieren oder zu umgehen.