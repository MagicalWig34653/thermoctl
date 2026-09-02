# Sicherheitsdurchsicht 2026-09-02

## Zusammenfassung

Die Sicherheitsriegel am eigentlichen Aktorversand sind vorhanden, aber mehrere vorgelagerte Wege können eine scharf geschaltete Anlage trotzdem kalt oder heiß machen. Das größte unmittelbar ausnutzbare Risiko ist der MQTT-Befehlspfad: Wer auf dem Broker in den thermoctl-Befehlsbaum veröffentlichen darf, handelt ohne thermoctl-Benutzer oder zonenbezogene Rechte mit Anlagenvollmacht. Daneben erlauben ein zonenübergreifender Fehler bei Bediengerätekanälen und die Wiederverwendung von Kiosk-Token als normale REST-/MCP-Token mehr Heizungssteuerung, als die Oberfläche und die alte Durchsicht behaupten. Ein reales Installationsgeheimnis wurde im aktuellen Repository-Baum nicht gefunden; zwei Geheimnispfade in Laufzeitprotokolle bleiben dennoch bestehen.

## Befunde

### Hoch — MQTT-Veröffentlichungsrecht ist unbeschränkte Heizungssteuerung — **neu**

**Beleg:** `thermoctl/config.py:27-40`, `thermoctl/app.py:408-454`, `thermoctl/app.py:548-554`, `thermoctl/integrations/mqtt/commands.py:62-131`, `.env.example:16-33`.

**Angriff und Wirkung:** Ein Angreifer, der den konfigurierten Broker erreicht und auf `thermoctl/zones/+/command/...` veröffentlichen darf, braucht weder Benutzerkonto noch API-Token. Er kann für jede bekannte Zonen-ID Betriebsart, aktuellen Sollwert, Modus-Sollwerte, Boost und Regelparameter setzen. Die Domänengrenzen verhindern 900 °C, erlauben aber −20 bis 35 °C; bei scharf geschalteter Anlage kann er damit Räume auskühlen oder dauerhaft aufheizen. Dieselbe Broker-Vertrauensgrenze gilt für gefälschte Zigbee2MQTT-Bediengerätenachrichten. Die Vorgaben sind ohne TLS und ohne Benutzer/Passwort; die Beispielkonfiguration behauptet zudem noch, es werde ausschließlich gelesen, obwohl der Empfänger inzwischen steuert.

**Empfehlung:** Eigene Broker-Zugangsdaten mit zwingender TLS-Verbindung und ACL dokumentieren und erzwingen: thermoctl darf nur eng definierte Zustände lesen/veröffentlichen, Home Assistant nur den Befehlsbaum beschreiben. Den HA-Befehlsempfang separat abschaltbar machen und zusätzlich kryptografisch oder durch einen getrennten, nur intern erreichbaren Broker-Principal absichern. Die veralteten Hinweise in `.env.example` vor einer Freigabe entfernen.

### Hoch — `device.manage` einer Zone kann eine fremde Zone steuern — **neu**

**Beleg:** `thermoctl/web/controller_views.py:69-92` prüft nur, ob das Bediengerät in irgendeiner verwaltbaren Zone hängt; die eingesandte `zone_id` wird nicht gegen den Principal geprüft. `thermoctl/domain/controller_channels.py:57-105` prüft nur die Existenz der Zielzone, und `thermoctl/domain/controller_channels.py:109-129` übernimmt den später gemeldeten Wert als Sollwert oder Betriebsart. Bei Tastenbelegungen wird es noch breiter: `thermoctl/domain/controller.py:171-181` ermittelt alle Zonen des Geräts und `thermoctl/domain/controller.py:184-247` führt die neue Belegung in allen aus. Der Beweis-Test `tests/test_security_review_2026_09_02.py:67-128` stellt mit `device.manage` nur für Zone A einen Kanal auf Zone B und schaltet B anschließend auf `off`.

**Angriff und Wirkung:** Ein Benutzer mit Geräteverwaltungsrecht in Zone A sendet beim Kanalformular die ID von Zone B. Danach genügt das Drehen des Bediengeräts beziehungsweise eine passende MQTT-Gerätenachricht, um Zone B auszuschalten oder ihren Sollwert zu verändern. Bei einem gemeinsam zugeordneten Bediengerät kann derselbe Benutzer eine Tastenbelegung ändern, die bei jedem Druck auch alle fremden Zonen betrifft. So kann ein bewusst zonenbeschränkter Nutzer andere Räume kalt oder heiß machen.

**Empfehlung:** Zielzone und Quellgerät serverseitig gegen die Rechte des Principals prüfen. Eine Tastenbelegung eines gemeinsam genutzten Bediengeräts nur erlauben, wenn `device.manage` für sämtliche betroffenen Zonen vorliegt, oder die Belegung pro Zone speichern.

### Hoch — Ein Kiosk-Token ist außerhalb des Kiosks ein wesentlich mächtigerer Bearer — **neu**

**Beleg:** Der ausgestellte Rechtesatz ist in `thermoctl/domain/kiosk.py:30-34,63-73` zwar auf `zone.read`, `setpoint.write` und `override.create` begrenzt, aber `is_kiosk` ändert laut `thermoctl/auth/tokens.py:14-27` die Auswertung nicht. REST akzeptiert jedes auflösbare Token (`thermoctl/api/routes.py:95-111`), MCP ebenso (`thermoctl/mcp/server.py:53-57`). REST kann mit diesen Rechten sämtliche Modus-Sollwerte ersetzen (`thermoctl/api/routes.py:241-265`) und eine zeitlich unbegrenzte Übersteuerung anlegen (`thermoctl/api/routes.py:597-637`, `thermoctl/api/schemas.py:153-160`); MCP bietet dieselbe freie Übersteuerung (`thermoctl/mcp/server.py:277-301`). Der Beweis-Test `tests/test_security_review_2026_09_02.py:30-64` setzt mit einem Kiosk-Token über REST 35 °C ohne Endzeit.

**Angriff und Wirkung:** Wer das Wandtablet in der Hand hat, kopiert Token oder Lesezeichen und benutzt es als `Authorization: Bearer ...` beziehungsweise als MCP-Token. Bei einem Kiosk mit Bedienrecht kann er nicht nur um 0,5 K verstellen oder boosten, sondern alle Sollwerte der zugewiesenen Zonen verändern und eine unbefristete Übersteuerung von −20 bis 35 °C setzen. Eine bereits scharf geschaltete Anlage macht den Raum damit kalt oder heiß. Das Token kann die Anlage nicht scharfschalten und kommt mangels Rechte nicht an Benutzerverwaltung, Geräteanbindung, globale Einstellungen, Schalt-/Auditprotokoll oder Meross-E-Mail und -Zugangsdaten; Zeitpläne und Sollwerte der zugewiesenen Zonen kann es lesen.

**Empfehlung:** Kiosk-Token an REST und MCP grundsätzlich ablehnen oder eigene, semantisch enge Kiosk-Rechte und Endpunkte verwenden. `setpoint.write` und `override.create` sind für die behauptete Kiosk-Wirkung zu breit; eine serverseitige Schrittoperation und ein ausschließlich zeitplanbestimmter Boost wären die passende Grenze.

### Hoch — Unangemeldete Login-Anfragen können den Regelzyklus blockieren — **neu**

**Beleg:** `thermoctl/web/auth_views.py:33-37` hält einen unbegrenzten, pro Prozess und Benutzernamen wachsenden Zähler. Der asynchrone Login-Handler ruft in `thermoctl/web/auth_views.py:69-95` vor jeder Passwortprüfung synchron `time.sleep()` über `sleep()` aus `thermoctl/web/auth_views.py:48-51` auf; schon der erste Versuch wartet eine Sekunde. Regelzyklus und Webanfragen laufen als Tasks derselben Ereignisschleife (`thermoctl/app.py:262-359`, `thermoctl/app.py:548-558`).

**Angriff und Wirkung:** Ein unangemeldeter Angreifer schickt fortlaufend parallele Login-POSTs, wahlweise mit immer neuen Namen. Die synchronen Wartezeiten und Argon2-Prüfungen werden auf der Ereignisschleife seriell abgearbeitet; gleichzeitig wächst `FEHLVERSUCHE` ohne Grenze. Regel- und Publikationszyklen werden beliebig verspätet, sodass Aktoren in ihrem letzten Zustand bleiben: Die Wohnung kann weiterheizen oder auskühlen, obwohl die Regelung längst anders entscheiden müsste.

**Empfehlung:** Nie synchron in einem `async`-Handler schlafen. Eine begrenzte, ablaufende Rate-Limit-Struktur nach Quelladresse und Konto verwenden, Verzögerung asynchron ausführen und die Passwortprüfung in einen begrenzten Worker auslagern. Zusätzlich den Regelprozess vom öffentlich erreichbaren Webprozess trennen oder mit einem unabhängigen Watchdog absichern.

### Hoch — Netzwerkweite HTTP-Vorgabe gibt Anmeldungen und Sitzungen preis — **schon in der alten Durchsicht**

**Beleg:** `thermoctl/config.py:20-32` bindet standardmäßig an `0.0.0.0`, setzt `secure_cookies=False` und lässt MQTT ebenfalls ohne TLS beginnen. Sitzungs- und CSRF-Cookie übernehmen diese Vorgabe in `thermoctl/web/auth_views.py:125-135` und `thermoctl/web/passkey_views.py:89-105`. `thermoctl/app.py:573-594` warnt nur.

**Angriff und Wirkung:** Wer im gleichen Netz HTTP-Verkehr beobachten oder verändern kann, erlangt Passwort oder Sitzung eines berechtigten Benutzers und kann mit dessen Rechten Sollwerte, Zeitpläne, Zonen und gegebenenfalls die Scharfschaltung verändern. Die Compose-Beispielbindung auf Loopback reduziert das Risiko für diesen einen Installationsweg, die Anwendungsvorgabe selbst nicht.

**Empfehlung:** Standardmäßig nur an Loopback binden. Für nichtlokale Bindungen einen expliziten Unsicherheits-Opt-in verlangen und TLS am dokumentierten Reverse Proxy samt sicheren Cookies verbindlich machen.

### Hoch — Das Einrichtungs-Token wird absichtlich unmaskiert protokolliert — **schon in der alten Durchsicht**

**Beleg:** `thermoctl/app.py:508-519` interpoliert das Klartext-Token ausdrücklich so in den Meldungstext, dass der Maskierungsfilter es nicht erfasst. `thermoctl/db/models/credential.py:69-77` kennt Verbrauch, aber keinen Ablaufzeitpunkt; `thermoctl/setup.py:38-47,50-64` akzeptiert jedes noch unverbrauchte Token. Der Maskierungsfilter erklärt in `thermoctl/logging.py:82-95`, dass Meldungstext nicht geschützt wird.

**Angriff und Wirkung:** Ein Leser lokaler, Container- oder zentral weitergeleiteter Logs nimmt vor der Ersteinrichtung das Token und richtet den ersten Administrator ein. Danach hat er vollständige Heizungs- und Benutzerverwaltung; das Token kann bis zum Verbrauch beliebig alt sein. Das verletzt den Projektgrundsatz „kein Geheimnis im Log“ ausdrücklich.

**Empfehlung:** Token über einen eigens geschützten lokalen Einrichtungsweg, eine Datei mit restriktiven Rechten oder Standardausgabe nur bei interaktiver CLI ausgeben, mit kurzer Ablaufzeit versehen und bei Neustart rotieren. Niemals in normale, weiterleitbare Anwendungslogs schreiben.

### Mittel — Meross bestätigt den Befehl, nicht den Zielzustand — **behoben**

**Beleg (Stand vor der Behebung):** Der MQTT-Empfänger paart Antworten nur über `messageId` (`thermoctl/integrations/meross_mqtt.py:152-200`). Danach akzeptiert `MerossSwitch` jedes Objekt mit `header.method == "SETACK"`; Signatur, Namespace, Geräte-ID, Kanal und bestätigter Zustand werden nicht geprüft (`thermoctl/integrations/actuators.py:276-292`). Ein als ausgeführt gemerkter Zustand wird in Folgezyklen übersprungen (`thermoctl/services/publishing.py:601-602,621-641`).

**Angriff und Wirkung:** Ein kompromittierter oder falsch antwortender Meross-Broker sieht die frisch veröffentlichte `messageId` und antwortet sofort mit einem passenden, inhaltlich leeren `SETACK`, ohne dass der Aktor geschaltet hat. thermoctl protokolliert Erfolg und versucht denselben Zustand nicht erneut; das Relais kann offen oder geschlossen bleiben und die Wohnung weiter aufheizen oder auskühlen. Ein unaufgefordertes altes `SETACK` oder eines für eine fremde `messageId` reicht dagegen nicht, und der Code veröffentlicht immer erst einen Befehl, bevor er auf dessen Antwort wartet.

**Behebung:** `thermoctl/integrations/meross_mqtt.py::_verify_answer_authenticity` verwirft eine Antwort mit passender `messageId`, aber falscher Signatur (`md5(messageId + key + timestamp)`, dieselbe Regel wie ausgehend) oder falschem `namespace`, bevor sie überhaupt zurückgegeben wird — ein Treffer hier zählt als „keine Antwort", nicht als Bestätigung. `thermoctl/integrations/actuators.py::MerossSwitch.switching` prüft zusätzlich, wo die Antwort ihren `togglex`-Zustand mitliefert, ob Kanal und `onoff` zum gesendeten Befehl passen; ein `SETACK` mit leerem Payload (das Verhalten der real getesteten Hardware) bleibt Erfolg, ein `SETACK` mit abweichendem Zustand wird als Fehlschlag gemeldet. Ein Fehlschlag ist `FAILED`, nicht `EXECUTED` — der bestehende Unterschied in `services/publishing.py` sorgt dafür, dass ein nicht verifizierbarer Erfolg im nächsten Zyklus erneut versucht wird, ohne dass dafür ein neuer Zustand eingeführt werden musste. Eine Geräte-ID trägt das Antwortprotokoll nicht mit; die Bindung an das richtige Gerät ergibt sich stattdessen daraus, dass jeder Befehl über eine frische, einmalige Antwort-Topic-Verbindung läuft (`MerossConnection.build`). Eine echte Bestätigung, dass das Relais physisch geschaltet hat, liefert nur ein zusätzliches `GET` — das wurde bewusst nicht gebaut, siehe `MerossSwitch`-Docstring. Tests: `tests/test_meross_mqtt.py` (Signatur/Namespace), `tests/test_actuators.py` (Zustandsabgleich).

### Mittel — Meross-HTTP-Antworten sind größenmäßig unbeschränkt; Cloud-Text gelangt unmaskiert ins Log — **neu**

**Beleg:** `thermoctl/integrations/meross.py:223-234` liest den gesamten Antwortkörper ohne Größenlimit in den Speicher und parst ihn anschließend. Eine beliebig große gültige Geräteliste wird in `thermoctl/services/meross_discovery.py:68-142` vollständig in Datenbankzeilen umgesetzt. Bei `apiStatus != 0` übernimmt `thermoctl/integrations/meross.py:120-127` das fremde Feld `info` wörtlich in die Ausnahme; `thermoctl/services/meross_discovery.py:167-169` und `thermoctl/services/meross_session.py:119-123` loggen es unter dem nicht maskierten Schlüssel `grund`, während `thermoctl/logging.py:33-103` nur anhand sensibler Schlüsselnamen maskiert.

**Angriff und Wirkung:** Eine kompromittierte Cloud oder ein per Umgebungsvariable gewählter falscher API-Endpunkt antwortet mit HTTP 200 und einem riesigen JSON-Körper beziehungsweise einer riesigen Liste. Der Prozess kann Speicher und Datenbank füllen oder sterben; Aktoren verbleiben dann im letzten Zustand. Dieselbe Gegenstelle kann in `info` zuvor empfangene E-Mail, Passwort-Hash oder Meross-Token spiegeln, die daraufhin im Anwendungslog landen. Eine leere Geräteliste löscht dagegen nichts; fremde IDs werden zwar als neue, zunächst unzugeordnete Geräte angelegt, schalten aber nicht automatisch.

**Empfehlung:** Antwortgröße, Anzahl Geräte sowie Länge und Typ jedes Feldes vor Verarbeitung begrenzen. Fremde Fehlermeldungen nur als festen Statuscode oder hart gekürzt und geheimnisbereinigt protokollieren. Cloud-Geräte niemals allein aufgrund des gemeldeten Modellpräfixes als zuweisbaren Heizaktor einstufen.

### Mittel — Passwortwechsel beendet gestohlene Sitzungen nicht, ein „Alle abmelden“ fehlt — **neu**

**Beleg:** Sitzungen gelten standardmäßig 14 Tage (`thermoctl/auth/sessions.py:14-29`) und konfigurierbar bis zu einem Jahr (`thermoctl/domain/control.py:48-63`). `thermoctl/domain/administration.py:208-224` lässt bestehende Sitzungen beim Passwortwechsel ausdrücklich gültig und behauptet einen gesonderten Widerrufsweg; tatsächlich gibt es nur den Widerruf der aktuellen Sitzung beim Logout (`thermoctl/web/auth_views.py:139-160`, `thermoctl/auth/sessions.py:53-64`). Die Benutzeroberfläche weist nur auf das Fortbestehen hin (`thermoctl/web/templates/users.html:95-100`).

**Angriff und Wirkung:** Ein Angreifer stiehlt ein Sitzungscookie. Selbst nachdem Benutzer oder Administrator das Passwort als Reaktion ändern, kann er bis zum Ablauf mit allen bisherigen Rechten weitersteuern und die Wohnung kalt oder heiß machen. Deaktivieren des ganzen Kontos stoppt ihn, ist aber kein Ersatz für Sitzungswiderruf.

**Empfehlung:** Beim administrativen Passwort-Reset alle Sitzungen und optional Passkeys widerrufen; beim eigenen Wechsel eine klare Wahl anbieten. Eine sichtbare Sitzungsübersicht mit „diese“ und „alle Sitzungen beenden“ implementieren.

### Mittel — Das Schaltprotokoll wächst ohne Aufbewahrungsgrenze — **neu**

**Beleg:** `thermoctl/db/models/state.py:61-103` speichert Nutzlast und Fehler als unbeschränkten Text und bewahrt Zeilen absichtlich auch nach Geräte-/Zonenlöschung. `thermoctl/domain/device_commands.py:10-12` und `thermoctl/api/routes.py:518-524` dokumentieren die fehlende Aufbewahrung. Die tägliche Bereinigung in `thermoctl/services/retention.py:13-38` löscht ausschließlich Messwerte. Schreiben erfolgt in `thermoctl/services/device_commands.py:29-81`.

**Angriff und Wirkung:** Jeder neue Schaltzustand beziehungsweise neue Ergebniszustand erzeugt über die Lebensdauer weitere Zeilen. Ein Nutzer mit Steuerrechten oder ein Broker-Angreifer kann Zustände wiederholt ändern und das Wachstum beschleunigen. Füllt die Datenbank das Dateisystem, fallen Web- und Regeltransaktionen aus; Aktoren können im letzten heißen oder kalten Zustand stehen bleiben.

**Empfehlung:** Eigene, standardmäßig endliche Aufbewahrung mit mengen- und zeitbegrenzter Löschung in kleinen Blöcken einführen, freien Speicher überwachen und bei Knappheit sicher unscharf schalten. Das Protokoll optional in einen separaten Speicher schreiben, damit sein Wachstum die Regelzustandsdatenbank nicht blockiert.

### Niedrig — Webhook folgt beliebigen Zielen und nimmt `Authorization` über Hostwechsel mit — **behoben**

**Beleg (Stand vor der Behebung):** Ziel und Token stammen ausschließlich aus der Prozessumgebung (`thermoctl/config.py:64-65`); die Oberfläche zeigt sie nur lesend (`thermoctl/web/control_views.py:236-259`, `thermoctl/domain/interfaces.py:198-220`). `thermoctl/integrations/notification.py:14-31` übergab die beliebige URL samt Bearer-Header an `urllib.request.urlopen`, das Weiterleitungen folgt. Der Beweis-Test `tests/test_security_review_2026_09_02.py` zeigte für die verwendete Python-Version, dass ein 302 zu `127.0.0.1` in einen GET umgewandelt und `Authorization` beibehalten wird.

**Angriff und Wirkung:** Entgegen der Annahme im Auftrag kann ein Benutzer mit `setting.manage` die URL nicht setzen. Kontrolliert oder kompromittiert jemand aber den vom Betreiber konfigurierten Webhook, kann er thermoctl auf interne HTTP-Adressen umleiten und dort blind GET-Anfragen auslösen; der Webhook-Bearer landet zusätzlich beim internen Ziel. Eine unmittelbare Heizwirkung ist im untersuchten Code nicht belegt, interne Dienste können jedoch erreicht werden.

**Behebung:** `thermoctl/integrations/notification.py::_NoRedirectHandler` ersetzt den Standard-`HTTPRedirectHandler`; jede Weiterleitung (3xx) wird abgelehnt statt in eine neue Anfrage übersetzt, `Authorization` verlässt den ursprünglichen Ursprung damit nie. `https` wird bewusst **nicht** erzwungen — ein Webhook auf eine Adresse im eigenen Heimnetz per `http` bleibt ein gültiger Fall, und die eigentliche Lücke war die Weiterleitung, nicht das Schema. Tests: `tests/test_notification.py` (Handler ist verdrahtet, Weiterleitung wird abgelehnt), `tests/test_security_review_2026_09_02.py::test_offen_webhook_redirect_nimmt_authorization_an_internes_ziel_mit` (umgeschrieben, beweist jetzt die Korrektur statt der Lücke).

### Niedrig — Die CSRF-Ausnahme ermöglicht Abmelde- und Anmelde-CSRF, aber keine Heizhandlung — **neu**

**Beleg:** Nur `/login` und `/logout` sind Wiederherstellungspfade (`thermoctl/auth/dependencies.py:69-129`). Bei einem ungültigen Token führt der Handler die Route nicht aus, löscht aber die Browsercookies (`thermoctl/app.py:650-685`); ohne mitgesendetes Sitzungscookie entfällt die CSRF-Prüfung vollständig (`thermoctl/auth/dependencies.py:104-121`). `tests/test_csrf.py:45-62,80-101,149-198` belegt sowohl den Router-weiten Schutz aller übrigen schreibenden Routen als auch die Ausnahme.

**Angriff und Wirkung:** Eine fremde Seite kann einen Besucher ausloggen, indem sie dessen Browser zu einem POST auf `/logout` navigiert; die Datenbanksitzung wird dabei nicht widerrufen, das Browsercookie aber gelöscht. Sie kann außerdem mit bekannten gültigen Zugangsdaten eine Login-CSRF auslösen und den Browser in dieses Konto setzen. Kein Pfad der Ausnahme schaltet, konfiguriert oder löscht etwas; alle solchen UI-Routen bleiben hinter CSRF- und Rechteprüfung. Die körperliche Wirkung ist daher höchstens indirekte Nichtbedienbarkeit, nicht eine fremde Heizentscheidung.

**Empfehlung:** Für Login und Logout zusätzlich `Origin` beziehungsweise `Sec-Fetch-Site` prüfen und eine eigene GET-Wiederherstellungsseite anbieten, die nur lokale Cookies verwirft. Die aktuelle Ausnahme nicht auf weitere Pfade ausdehnen.

### Niedrig — Die Bediengeräteseite verrät alle Gerätenamen — **neu**

**Beleg:** `thermoctl/web/controller_views.py:41-60` filtert Bediengeräte nach sichtbaren Zonen, lädt für `devices` aber den vollständigen Gerätebestand. `thermoctl/web/templates/controllers.html:42-59` gibt diese Liste als Quellgeräte und Gerätepool aus. Schon eine beliebige gültige Sitzung erreicht `GET /controllers` (`thermoctl/web/controller_views.py:64-66`); ein ausdrückliches `device.read` am Endpunkt fehlt.

**Angriff und Wirkung:** Ein angemeldeter Nutzer ohne Geräte-Leserecht erfährt Namen sämtlicher Geräte und damit möglicherweise Raum-, Bewohner- oder Integrationsbezüge. Meross-Zugangsdaten werden nicht ausgegeben und eine direkte Heizwirkung entsteht nicht; die Information erleichtert jedoch das Erraten von MQTT-Zielen und der Anlagenstruktur.

**Empfehlung:** Den Endpunkt mindestens mit `device.read` schützen und Quellgeräte auf den wirksamen Zonenumfang beschränken. Für Schreibkanäle auch `source_device_id` serverseitig autorisieren.

## Die drei Fragen aus dem Plan

### 1. Reicht das Rechtemodell für den Kiosk-Token?

**Nein.** Die Rechteausstellung ist zonenbezogen und schmal, die Bedeutung der Rechte ist für ein physisch zugängliches Token aber zu breit. Ohne Bedienhaken kann das Token nur Zonen, Zustände, Zeitpläne und Sollwerte seiner zugewiesenen Zonen lesen. Mit Bedienhaken kann es über die Kioskseite Sollwerte schrittweise ändern und boosten, über REST/MCP aber zusätzlich alle Modus-Sollwerte frei ersetzen und eine beliebige Übersteuerung von −20 bis 35 °C ohne Ende setzen (`thermoctl/domain/kiosk.py:30-34`, `thermoctl/auth/tokens.py:25-27`, `thermoctl/api/routes.py:251-265,597-637`, `thermoctl/mcp/server.py:277-301`). Benutzerverwaltung, Gruppen, Geräteanbindung, globale Einstellungen, Schalt-/Auditprotokoll, Scharfschaltung und Meross-Konfigurationsdaten bleiben mangels Recht unerreichbar.

Ein Ablauf ist optional: Das Formular lässt leer ausdrücklich „unbegrenzt“ zu (`thermoctl/web/kiosk_admin_views.py:80-120`, `thermoctl/web/templates/kiosk_tokens.html:89-96`). Das Cookie gilt höchstens ein Jahr, ein unbegrenztes zugrunde liegendes Token aber weiter (`thermoctl/web/kiosk_views.py:51-61,113-123`). Ein Inhaber von `token.manage` kann es widerrufen (`thermoctl/web/kiosk_admin_views.py:127-138`), und Auflösung sowie effektive Rechte berücksichtigen Widerruf, Ablauf und spätere Rechteverluste des Besitzers (`thermoctl/auth/tokens.py:53-65`, `thermoctl/domain/authz.py:39-68`).

Der lokale Uvicorn-Zugriffslogger schwärzt `/kiosk/{token}` (`thermoctl/logging.py:130-160,236-238`), vorgelagerte Reverse-Proxy- oder Netzwerklogs kann der Code aber nicht schützen. Der Einstieg antwortet sofort mit 303 auf `/kiosk`, und die Kioskseite lädt nur gleichursprüngliche Ressourcen (`thermoctl/web/kiosk_views.py:95-124`, `thermoctl/web/templates/kiosk.html:1-48`); für den normalen Ablauf ist daher keine ausgehende Referer-Offenlegung belegt. Der ursprüngliche Token-Link bleibt jedoch das beabsichtigte Lesezeichen und kann beim Eintippen/Öffnen in Browser-Historie, Synchronisation oder Autovervollständigung landen; der Servercode löscht diese clientseitigen Spuren nicht.

### 2. Was geschieht bei falschen Meross-Antworten mit HTTP 200?

Die Antwort ist fallabhängig:

- Eine leere Geräteliste gilt als Erfolg, löscht aber bestehende Geräte und Zuordnungen nicht (`thermoctl/integrations/meross.py:171-207`, `thermoctl/services/meross_discovery.py:68-142`; Gegenbeweis in `tests/test_meross.py:492-506`).
- Fremde, formal gültige Geräte-IDs werden als neue Geräte angelegt; ein `mss...`-Modell erhält die Schaltfähigkeit (`thermoctl/services/meross_discovery.py:81-119`). Ohne anschließende privilegierte Zuordnung wird dadurch kein Aktor geschaltet. Metadaten eines bereits bekannten UUID-Eintrags werden aktualisiert.
- Ein nicht funktionierendes Token oder ein fehlender Broker führt beim Listenabruf beziehungsweise Schaltversuch zu Fehlern; andere Integrationen laufen weiter (`thermoctl/services/meross_session.py:80-132`, `thermoctl/services/publishing.py:624-640`).
- Eine riesige Antwort ist nicht begrenzt und kann Speicher oder Datenbank erschöpfen (`thermoctl/integrations/meross.py:223-234`, `thermoctl/services/meross_discovery.py:81-142`). Das kann den Dienst beenden und Aktoren im letzten Zustand lassen.
- Ein altes oder völlig unaufgefordertes `SETACK` wird wegen der frischen `messageId` ignoriert. Ein Broker kann aber nach dem veröffentlichten Befehl ein inhaltlich falsches `SETACK` mit dieser ID liefern; Methode und ID genügen für „ausgeführt“ (`thermoctl/integrations/meross_mqtt.py:152-200`, `thermoctl/integrations/actuators.py:276-292`). Ein `SETACK` für einen Befehl, der nie veröffentlicht wurde, führt daher nicht zufällig zum Schalten; die gefährliche Variante ist die falsche Bestätigung eines tatsächlich gesendeten, aber körperlich nicht ausgeführten Befehls.
- Cloud-Felder werden typgeprüft, aber nicht größenbegrenzt. Das Feld `info` kann unmaskiert ins Log gespiegelt werden (`thermoctl/integrations/meross.py:120-127`, `thermoctl/services/meross_discovery.py:167-169`). Der Login sendet E-Mail und MD5 des Passworts, die Geräteliste den Meross-Token (`thermoctl/integrations/meross.py:131-181`); die MQTT-Verbindung erhält nur vom Cloud-Login abgeleitete Nutzer-/Schlüsseldaten und nutzt TLS (`thermoctl/integrations/meross_mqtt.py:68-89,159-168`).

### 3. Wie weit reicht die CSRF-Lockerung und ist der Tausch richtig?

Sie reicht im Code wirklich nur über die zwei Pfade `/login` und `/logout`; die Ausnahme löscht bei einem ungültigen Nachweis Cookies, führt die angeforderte Route aber nicht aus (`thermoctl/auth/dependencies.py:74-129`, `thermoctl/app.py:650-685`). Sämtliche anderen schreibenden UI-Routen hängen weiterhin an der Router-Abhängigkeit, und REST nimmt keine Sitzungscookies an (`tests/test_csrf.py:24-62`). Eine fremde Seite erreicht darüber keine Schaltung, Konfiguration oder Löschung.

Der Tausch ist für die körperliche Sicherheit vertretbar, aber nicht ideal: Abmelde-CSRF ist eine begrenzte Verfügbarkeitsstörung, Login-CSRF eine vermeidbare Kontoverwechslung. Eine Wiederherstellungsseite plus Herkunftsprüfung würde die gemeldete Aussperrung lösen, ohne fremden Ursprüngen diese beiden Zustandswechsel zu erlauben.

## Nicht belegt, aber angesehen

- **Keine realen Secrets im aktuellen Baum:** Geprüft wurden typische private Schlüssel, Cloud-/Git-Schlüssel, Datenbank-URLs mit Passwort, `.env` sowie Meross-/MQTT-/Webhook-/MCP-Bezüge. Funde waren Platzhalter, Testwerte oder der öffentlich bekannte Meross-Protokollwert `APP_SECRET` (`thermoctl/integrations/meross.py:30-37`). Eine `.env` ist nicht eingecheckt. Die Git-Historie wurde nicht geprüft.
- **Schaltprotokoll-Nutzlast heute ohne Zugangsdaten:** Alle aktuellen Aufrufer speichern nur Sollwert-/Temperaturfelder, `state`, `heating` oder Meross-`togglex` (`thermoctl/services/publishing.py:318-339,386-408,580-619,687-743`). Es gibt keine Exportfunktion. Rohes `payload` und `error` werden allerdings ohne zentrale Feld-Whitelist gespeichert und mit `audit.read` über Web, REST und MCP vollständig ausgegeben (`thermoctl/web/device_commands_views.py:36-98`, `thermoctl/api/routes.py:508-550`, `thermoctl/mcp/server.py:237-274`). Für heutige Aufrufer ist kein Meross-/MQTT-Geheimnis im Payload belegt.
- **Normale schaltende Adapter prüfen Rechte:** HTMX-Sollwert, Override und Parameter wählen Zonen über das jeweils passende Recht (`thermoctl/web/daily_views.py:59-63,132-184,239-273`); REST verlangt zonenbezogene Rechte und für Scharfschaltung eigens `control.arm` (`thermoctl/api/routes.py:251-265,362-448,597-710`); MCP prüft Override, Parameter, Zeitplan und Trockenlauf (`thermoctl/mcp/server.py:277-324,368-382,410-460`). MCP kann nicht scharfschalten. Außer Controller- und MQTT-Pfad wurde kein Weg gefunden, mit einem nur angemeldeten oder zonenfremden Principal zu steuern.
- **Absurde Werte:** Sollwerte müssen endlich sein, höchstens eine Nachkommastelle haben und zwischen −20 und 35 °C liegen (`thermoctl/domain/modes.py:180-196`). Regelparameter besitzen obere und untere Grenzen (`thermoctl/domain/control.py:44-63`, `thermoctl/domain/zone_settings.py:93-112`). Negative Übersteuerungsdauern werden abgewiesen; sehr große positive Dauern können in `timedelta` einen 500-Fehler auslösen (`thermoctl/web/daily_views.py:193-207`, `thermoctl/api/routes.py:614-615`), eine dauerhafte Änderung oder Prozessblockade wurde daraus nicht belegt.
- **Fremde Zonen- und Punkt-IDs:** REST-, HTMX- und MCP-Zeitplanoperationen koppeln Punkt und Zone und antworten sonst 404, beispielsweise `thermoctl/api/routes.py:763-790`, `thermoctl/web/schedule_views.py:244-294,474-492` und `thermoctl/mcp/server.py:438-460`. Ein Wochenplan kann aufgrund Minute/Wochentag und Eindeutigkeitsbedingung höchstens 10.080 Punkte enthalten (`thermoctl/db/models/schedule.py:16-30`). Eine Mengenquote gibt es nicht; dass 10.000 Punkte den Dienst hängen lassen, wurde nicht belegt.
- **Logout und Passkeys:** Ein regulärer Logout widerruft die serverseitige Sitzung (`thermoctl/web/auth_views.py:139-160`). Passkey-Challenges sind 32 zufällige Bytes, zwei Minuten gültig und werden beim Einlösen auch im Fehlerfall gelöscht (`thermoctl/domain/passkey.py:57-124`); RP-ID, Ursprung und Benutzerverifikation werden geprüft (`thermoctl/domain/passkey.py:165-185,210-224`), ebenso der Signaturzähler und der aktive Nutzer (`thermoctl/domain/passkey.py:228-289`).
- **Benachrichtigungsinhalt:** Webhook und Home Assistant erhalten nur Schlüssel, Schwere, Titel und Text (`thermoctl/integrations/notification.py:14-31`, `thermoctl/services/publishing.py:169-200`). Bei Sensorstörung nennt der Text Zonenname und Frostschutz-Sollwert (`thermoctl/domain/fault_notice.py:15-51`), keine Zugangsdaten. Der Auftrag nennt dies „Frostschutz-Auslösung“; tatsächlich wird der Übergang in Sensorstörung beziehungsweise Entwarnung gemeldet, nicht jede Frostschutzentscheidung.
- **Kiosk-Referer:** Im normalen Kiosk-Dokument gibt es keine fremden Ressourcen oder Links; ein tatsächlicher Referer-Abfluss wurde deshalb nicht reproduziert. Für vorgeschaltete Proxys und konkrete Browser-Historien gilt diese Aussage nicht.
- **Cloud-Ausfall und langsame Antwort:** Meross-Geräteabgleich läuft außerhalb der Regeltransaktion (`thermoctl/app.py:214-243`), Webhooks in einem Thread und als nicht erwarteter Task (`thermoctl/integrations/notification.py:34-50`, `thermoctl/app.py:339-357`). Der Geräteabgleich blockiert daher den Regelzyklus nicht. Meross-Schaltverbindungen werden jedoch je Befehl sequenziell aufgebaut; eine Gesamtlaufzeitgrenze für den ganzen Publikationszyklus wurde nicht gefunden, nur 20 Sekunden für die Antwort (`thermoctl/integrations/meross_mqtt.py:139-178`). Ein dauerhaftes Hängen über die Bibliothekstimeouts hinaus wurde nicht belegt.

## Beweis-Tests

Neu angelegt wurde `tests/test_security_review_2026_09_02.py` mit drei absichtlich das heutige Verhalten beweisenden Tests:

1. Kiosk-Token als REST-Bearer für eine unbefristete 35-°C-Übersteuerung.
2. `device.manage` nur für Zone A richtet einen Bediengerätekanal auf Zone B und setzt B auf `off`.
3. Der von Webhooks verwendete Standard-Redirect übernimmt `Authorization` zu einem internen Ziel.

Gezielter Lauf: `3 passed`; `ruff check tests/test_security_review_2026_09_02.py`: `All checks passed!`.

## Prüflauf

Alle vier vorgegebenen Befehle wurden aus dem Worktree mit dem angegebenen Interpreter ausgeführt. Die Ergebnisse und wörtlichen Schlusszeilen sind:

- `ruff check .`: Exit 0; Schlusszeile `All checks passed!`
- `mypy thermoctl`: Exit 0; Schlusszeile `Success: no issues found in 107 source files`
- SQLite: ausgegebenes `exit=0`; `grep -c "^FAILED" /tmp/sich_sqlite.log` ergab `0`; wörtliche pytest-Schlusszeile `1619 passed, 8 warnings in 64.02s (0:01:04)`.
- MariaDB: ausgegebenes `exit=0`; `grep -c "^FAILED" /tmp/sich_maria.log` ergab `0`; wörtliche pytest-Schlusszeile `1618 passed, 1 skipped, 10 warnings in 165.73s (0:02:45)`.

Beim ersten vollständigen SQLite-Zwischenlauf schlug ausschließlich die projektspezifische Prüfsperre für ungeprüfte körperliche Wirkungsaussagen an: `exit=1`, `grep -c "^FAILED"` ergab `1`, Schlusszeile `1 failed, 1618 passed, 10 warnings in 28.83s`. Die zwölf im Fehler einzeln genannten Sätze dieser neuen Durchsicht wurden daraufhin in `tests/approved_physical_vocabulary.json` als geprüft registriert; der oben dokumentierte vollständige Wiederholungslauf ist sauber. Diese Änderung betrifft nur die Review-Prüfdaten, nicht Produktionscode.

## Was diese Durchsicht nicht abdeckt

- Git-Historie und bereits entfernte Geheimnisse.
- Schwachstellendatenbanken und eine aktuelle CVE-/Supply-Chain-Prüfung der Python-, JavaScript-, Container- und Betriebssystemabhängigkeiten.
- Reale ACLs, TLS-Terminierung, Reverse-Proxy-Logs, DNS, Firewall, WLAN und Rechte des produktiven MQTT-Brokers.
- Verhalten und Sicherheitszusagen der echten Meross-Cloud sowie ein Test gegen reale Aktoren; es wurde bewusst kein körperlicher Schaltversuch durchgeführt.
- Empirische Browserprüfung von Verlauf, Synchronisation, Autovervollständigung, Referrer und Drittanbieter-Cookie-Regeln auf dem tatsächlich eingesetzten Tablet.
- Last-, Fuzzing- und Langzeittests; die Aussagen zu Ressourcenerschöpfung folgen den unbegrenzten Codepfaden, nicht einer Messung bis zum Prozessabbruch.
- Das Verzeichnis `form-backend_bvk` wurde auftragsgemäß nicht verändert und inhaltlich nicht geprüft.

SICHERHEIT FERTIG
