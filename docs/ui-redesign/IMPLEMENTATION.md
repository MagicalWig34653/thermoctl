# thermoctl v0.9.0 – Arbeitsanweisung UI-Redesign

## Ziel

Für `v0.9.0` die bestehende WebUI anhand der Referenzen unter `/docs/ui-redesign/` vollständig überarbeiten.

Die dort liegenden finalen Admin- und Mieter-Demos definieren das UX-/UI-Zielbild. Die bestehende Anwendung und ihre Domänenlogik bleiben die fachliche Quelle der Wahrheit.

Zusätzlich muss erstmals eine saubere Trennung zwischen **Admin-UI** und **Mieter-UI** eingeführt werden.

Die Umsetzung erfolgt auf einer neuen Branch, bevorzugt `feature/v0.9.0-ui-redesign`. Falls das Repo eine andere Branch-Konvention vorgibt, diese verwenden.

## Arbeitsweise

Vor Änderungen Repo-Instruktionen, Subagent-Instruktionen und `/docs/ui-redesign/` vollständig lesen, `git status` prüfen, eine neue Branch anlegen und Baseline-Tests ausführen.

Nutze **superpowers** aktiv für Planung, Umsetzung, Debugging und Reviews. Nutze die im Repo definierten Subagents gezielt: **Sonnet 5 High** für Architektur/UX/Permission/Accessibility/Reviews und **Codex mit GPT-5.6 Sol High** für Implementierung, Migrationen, Tests, Security-Checks und Debugging. Repo-eigene Subagent-Anweisungen haben Vorrang. Opus bleibt für Orchestrierung, Architekturentscheidungen, Integration und finale Qualitätskontrolle verantwortlich.

## 1. UI-Profil Admin / Mieter

Führe ein explizites serverseitiges UI-Profil `admin` / `tenant` ein, bevorzugt auf Gruppenebene, sofern das zum vorhandenen Modell passt. Keine Logik über Gruppennamen wie `group.name == "Mieter"`.

UI-Profil und Berechtigungen bleiben getrennt:
- **UI-Profil:** Welche Oberfläche wird dargestellt?
- **Permissions/Grants:** Was darf der Principal tatsächlich lesen oder ändern?

Bestehende Endpoint-Permission-Prüfungen bleiben maßgeblich. Das UI-Profil darf niemals aus Query-Parametern, JavaScript, Local Storage oder clientseitig kontrollierbaren Cookies stammen.

### Migration

Bestehende Gruppen standardmäßig zu `admin` migrieren, damit Upgrades keinen Zugriff verlieren. `/setup` erzeugt weiterhin einen Admin. Gruppenverwaltung erhält eine UI-Profil-Auswahl. Keine Klassifizierung anhand von Gruppennamen. Tests ergänzen.

## 2. Route-Schutz

Admin-Seiten für Tenant-Profile serverseitig sperren, nicht nur in der Navigation ausblenden. Eine zentrale Guard-/Dependency-Lösung verwenden. Bestehende Permission-Prüfungen bleiben zusätzlich bestehen. REST/API-Semantik nicht unbeabsichtigt verändern.

## 3. Navigation

Bestehende zentrale Navigation erweitern und nach UI-Profil, Permission und ggf. sichtbaren Zonen filtern.

### Admin
- Übersicht
- Zonen
- Geräte
- Betrieb
- Heizstatistik
- Relaisverschleiß
- Protokolle
- Regelvorgaben
- Sollwert-Modi
- Zeitplan
- Schnittstellen
- Bediengeräte
- Benutzer
- Gruppen
- API-Tokens
- Kiosk-Tokens

### Mieter
- Zuhause
- Zeitplan
- Heizzeit
- Mehr

Mobil reduzierte Bottom-Navigation gemäß Demos.

## 4. Template-Architektur

Keine zwei komplett unabhängigen Anwendungen kopieren. Gemeinsamen Template-Kern plus getrennte App-Shells verwenden, z. B. `base_core.html`, `base_admin.html`, `base_tenant.html`, `base_plain.html`. Namen an Repo-Konventionen anpassen. Kiosk bleibt separat.

Gemeinsame Komponenten extrahieren, wo sinnvoll: Alerts, Statusanzeigen, Dialoge, Timeline, Forecast, Setpoint-Steuerung, Tabellenstatus, Empty States. Admin- und Mieter-Zonenkarten dürfen getrennt bleiben.

## 5. Technische Grenzen

Beibehalten: Jinja, lokales Bootstrap, HTMX, Vanilla JS und bestehende Domain-/Service-Schicht.

Nicht einführen: npm, Node-Buildprozess, Vite, Webpack, React, Vue, Svelte, CDN-Abhängigkeiten oder Tailwind-Build.

`hx-boost="true"` berücksichtigen. Keine mehrfach registrierten Skripte nach Body-Swaps. Quiet Polls, Ladebalken, serverseitiges Poll-Intervall, Pause bei verstecktem Dokument, `hx-preserve` und stale-page handling erhalten.

Bestehenden CSRF-Mechanismus und URL-Prefix/Ingress-Helfer für alle neuen Routen/Aktionen verwenden. HTML/Form-Routen nicht in den REST-OpenAPI-Vertrag aufnehmen.

## 6. Keine zweite Domänenlogik im Browser

Die HTML-Demos sind UX-Referenzen, keine technische Vorlage. Nicht aus Demo-JavaScript übernehmen: Temperaturgrenzen, Setpoint-Berechnung, Forecast, nächste Schaltzeit, Override-Priorität oder Schedule-Berechnung. Der Browser löst Aktionen aus und zeigt Serverzustand; Domäne/Server bleiben die einzige fachliche Wahrheit.

## 7. Admin-UI

Die finale Admin-Demo unter `/docs/ui-redesign/` als Zielbild verwenden. Alle bestehenden Admin-Funktionen müssen erhalten bleiben: Dashboard, Zonen und Unterseiten, Geräte, Anlagenbild, Bediengeräte, Betrieb, Regelvorgaben, Sollwert-Modi, Zeitplan, Schnittstellen, Statistik, Relaisverschleiß, Audit, Schaltprotokoll, Benutzer, Gruppen, API-/Kiosk-Tokens, Passkeys, Login/Setup und Kiosk.

Dashboard ohne Vanity-KPIs. Die drei Betriebsriegel separat darstellen:
1. `control_armed`
2. MQTT `sending_allowed`
3. aktive / Standby-Führungsrolle

MQTT-Neustart-Hinweis erhalten. Zonenkarten zeigen Isttemperatur, aufgelösten Sollwert, Begründung, Sensorzustand, Regel-/Heizzustand, Tagesverlauf, aktuellen Modus, permanenten Setpoint-Stepper und Override. **Thermostat ≠ Override** muss klar bleiben.

## 8. Mieter-UI

Mieter sehen ausschließlich verständliche wohnungsbezogene Funktionen. Keine technischen Anlagenparameter wie MQTT, Broker, PI-Regelung, Hysterese, Relaisverschleiß, Gerätezuordnung, Schnittstellen, Benutzerverwaltung oder Audit-Interna.

Pro sichtbarer Zone anzeigen: Raumname, aktuelle Temperatur, Aktualität, aktueller Modus, Sollwert, verständliche Begründung, Heizstatus, Tagesverlauf, nächste geplante Änderung, permanente Temperaturänderung, temporäre Übersteuerung und „Zur nächsten Schaltzeit springen“.

Nur `visible_zones(principal)` rendern. Keine fremden Zonennamen oder IDs über HTML, Selects, Hidden Inputs, Fehlertexte, Statistik, Schedule-Kopierdialoge, Seitentitel oder Reports leaken.

## 9. Permanenter Setpoint

Der normale `± 0,5 K`-Stepper ändert den Sollwert des aktuell laufenden Modus dauerhaft. Keine Override-Semantik. Der Server berechnet den neuen Wert und validiert Grenzen. Schnelle Mehrfachklicks müssen serverseitig korrekt akkumulieren.

## 10. Temporäre Übersteuerung

Mieteraktion „Für eine Weile wärmer“. Mit bestehender Override-Domäne passende Dauern unterstützen, z. B. 30 Minuten, 1 Stunde, 2 Stunden und bis zur nächsten Schaltzeit. Aktive Übersteuerung sichtbar anzeigen und beendbar machen.

## 11. Zur nächsten Schaltzeit springen

Für v0.9.0 real implementieren: Die nächste reguläre Schedule-Phase wird sofort vorgezogen, aber nur bis zu dem Zeitpunkt, an dem sie regulär begonnen hätte. Der Wochenplan bleibt unverändert.

Serverseitig:
1. nächsten Schaltpunkt über vorhandene Schedule-Domäne bestimmen;
2. nächsten Modus/Sollwert bestimmen;
3. Override bis exakt zu diesem Zeitpunkt anlegen;
4. bestehende konkurrierende Overrides klar behandeln;
5. Zustand vom Server neu rendern.

Tests ergänzen.

## 12. Mieter-Zeitplan

Einfachere Oberfläche als Admin, aber dieselben echten Schedule-Daten. Oben 24h-Forecast aus derselben Domain-Logik wie Runtime. Darunter Wochenansicht mit Tag bearbeiten, Tag kopieren, Werktage angleichen, von eigener sichtbarer Zone übernehmen und Undo, sofern bestehend.

Wenn aktuell nur ein zu mächtiges Recht wie `zone.manage` zum Bearbeiten existiert, ein eigenes zonenspezifisches Schedule-Schreibrecht einführen, passend zur vorhandenen Permission-Namenskonvention. Mietern niemals breite Admin-Rechte nur wegen Schedule-Editing geben.

## 13. Heizzeit

Mieter erhalten eine reduzierte Statistik mit 7/30/90 Tagen und nur sichtbaren Zonen. Bezeichnung `Heizzeit`, nicht Energie, Verbrauch, Kosten oder kWh. Trockenlauf ausdrücklich als „hätte geheizt“ kennzeichnen.

## 14. Mieter „Mehr“

Nur persönliche Funktionen: Passkey, Passwort, andere Sitzungen beenden, Logout und verständliche Hilfe/Systeminformation. Keine Admin- oder API-Token-Funktionen in der normalen Mieter-Navigation.

## 15. Abwesenheit

Als gewünschte v0.9.0-Funktion implementieren, sofern sauber mit der vorhandenen Domain möglich. Mieter wählt Rückkehrdatum/-zeit und reduzierte Temperatur. Nur eigene berechtigte Zonen. Bestehende Override-Domäne wiederverwenden; keine zweite Regelengine. Bei mehreren Zonen serverseitigen Bulk-/Application-Service verwenden. Autorisierung jeder Zone prüfen, keine unklaren Teilzustände, Abwesenheit sichtbar, beendbar und automatisch auslaufend.

## 16. Problem melden / Benachrichtigungen

Keine Fake-Funktionen bauen. Bestehende Webhook-/Notification-/Audit-Infrastruktur prüfen. `Problem melden` nur real implementieren, wenn ein sauberer Zustellweg vorhanden ist. Keine fremden Daten, Secrets oder Credentials mitsenden. Externe Aktion benötigt passende Schreibberechtigung. Falls keine belastbare Infrastruktur vorhanden ist, Funktion nicht als No-op ausliefern und im Abschlussbericht als verschoben dokumentieren. Dasselbe gilt für persönliche Tenant-Benachrichtigungsschalter.

## 17. Betrieb und Settings getrennt

`/control` und `/settings` nicht zusammenführen. `/control` zeigt aktuellen Betriebsweg, Entscheidungen und Freigaben; `/settings` anlagenweite Regel- und Meldungsvorgaben.

## 18. Geräte und Protokolle

Geräte: Auffälliges zuerst, Liste und Anlagenbild erhalten. Desktop darf kompakte Tabellen verwenden.

Protokolle: Audit und Schaltprotokoll dürfen visuell zusammenrücken, Semantik bleibt getrennt. Trockenlauf immer explizit als „hätte geheizt“ bzw. „unterdrückt“ kennzeichnen.

## 19. Kiosk

Kiosk nicht mit Mieter-UI vermischen. Kiosk-Token, eigener Principal, `visible_zones`, `has_permission`, Cookie-/Redirect-Verhalten, `base_plain.html`, Kiosk-CSRF, 20-Sekunden-Refresh und fehlender normaler Ladebalken erhalten. Regressionstests durchführen.

## 20. Design / Accessibility

Finale HTML-Demos als visuelle Referenz verwenden. Kein pixelgenauer Zwang zulasten von Architektur, Accessibility oder Semantik.

Theme nur über `prefers-color-scheme`, kein manueller Toggle.

Keine Emojis oder emojiartigen Unicode-Piktogramme in der neuen WebUI. Stattdessen Text, CSS-Punkte/Formen oder lokale SVGs. Status nie nur über Farbe vermitteln.

Mieter mobile-first, Admin desktop-produktiv und mobil vollständig benutzbar. Semantisches HTML, echte Links/Buttons, Tastaturbedienung, sichtbare Focus States, Dialog-Fokus, ausreichender Kontrast, `prefers-reduced-motion`, sinnvolle Touch-Ziele und keine Hover-only-Funktionen.

## 21. Tests

Mindestens ergänzen:
- UI-Profil/Migration
- Navigation je Profil und Permission
- Tenant kann Admin-HTML-Routen nicht öffnen
- vollständige Zonenisolation
- serverseitiges 0,5-K-Stepping inkl. schnelle Mehrfachaktionen
- Override erstellen/beenden/zeitlich begrenzen
- Jump-next aus Domain, korrekter Wert/Modus/Endzeit, Schedule unverändert
- Schedule read/write/copy/adopt/undo/forecast
- Statistik nur sichtbare Zonen, 7/30/90, Dry-run
- HTMX: CSRF, stale page, quiet poll, URL-Prefix, keine doppelte Eventregistrierung
- Kiosk-Regression

Bestehende Wächtertests für Navigation/Permissions nicht entfernen, sondern erweitern.

## 22. Security Review

Vor Abschluss unabhängigen Subagent-Review durchführen auf Tenant-Zugriff auf Admin-Routen, Zone-Leaks, fremde IDs, Bulk-Aktionen, CSRF, Write-Permissions, UI-Profil-vs-Permission, Schedule Copy/Adopt, Statistik-/Interface-Leaks und Reports/Notifications. Findings vor Abschluss beheben.

## 23. Vorgehensreihenfolge

1. Repo + Instruktionen lesen
2. `/docs/ui-redesign/` analysieren
3. Git-Status prüfen
4. neue Branch erstellen
5. Baseline-Tests
6. Architektur-/Permission-Audit
7. UI-Profil + Migration
8. Route Guards
9. Navigation
10. gemeinsame Template-Basis
11. Admin-Shell + Admin-Seiten migrieren
12. Tenant-Shell + Home
13. Tenant Schedule
14. Tenant Statistik
15. Tenant Account
16. Jump-next
17. Abwesenheit
18. optionale echte Problem-Reporting-Funktion
19. Responsive/Accessibility
20. Kiosk-Regression
21. Security Review
22. vollständige Tests
23. veralteten UI-Code entfernen
24. v0.9.0-Dokumentation/Versionsmetadaten aktualisieren

Nicht zuerst alte Templates löschen. Keine fachfremden Refactorings.

## 24. Definition of Done

Abgeschlossen erst wenn:
- Redesign-Branch existiert
- `admin` / `tenant` UI-Profil + sichere Migration existieren
- serverseitige Profilwahl und Route Guards funktionieren
- Permissions weiterhin autoritativ sind
- Zonenisolation vollständig funktioniert
- Admin-UI bestehende Funktionen ersetzt
- Tenant-UI echte Daten verwendet
- Setpoint, Override, Jump-next, Tenant-Schedule und Heizzeit funktionieren
- Account/Passkeys erreichbar sind
- Kiosk unverändert funktioniert
- stale-page, quiet-poll, loading, CSRF und URL-Prefix funktionieren
- responsive und Accessibility geprüft
- keine Emojis vorhanden
- kein npm/CDN/Buildsystem hinzugefügt
- keine Domainlogik unnötig im Browser dupliziert
- neue Tests vorhanden
- vollständige Testsuite erfolgreich bzw. ausschließlich dokumentierte vorbestehende Fehler
- v0.9.0-Dokumentation aktualisiert
- keine Demo-Daten, Fake-Buttons oder No-op-Funktionen im Produktiv-UI verbleiben

## Prioritäten bei Konflikten

1. Sicherheit / Autorisierung
2. bestehende Domain-Semantik
3. bestehende Betriebsanforderungen
4. diese Arbeitsanweisung
5. Informationsarchitektur der finalen Demos
6. pixelgenaue Gestaltung

## Abschlussbericht

Am Ende kurz dokumentieren: Branch, Architekturänderungen, UI-Profil + Migration, Permission-Änderungen, neue/geänderte Routen, Admin- und Tenant-Funktionen, Domain-/Service-Änderungen, HTMX/JS-Änderungen, Tests + Ergebnisse, Security-Review, bewusst verschobene Punkte, wichtige geänderte Dateien und verbleibende manuelle v0.9.0-Prüfschritte.
