# Änderungen

Alle nennenswerten Änderungen an `thermoctl`, neueste zuerst. Das Format folgt lose
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen
[semantischer Versionierung](https://semver.org/lang/de/).

Der Stand im Einzelnen — was gebaut ist, was an der echten Anlage noch aussteht und warum
etwas so entschieden wurde — steht in [docs/STATUS.md](docs/STATUS.md).

---

## 0.2.2 — 2026-08-31

Eine Fassung, die vor allem geradezieht, was 0.2.0 behauptet hat: Meross ist jetzt
wirklich angebunden statt halb, die Sonnenabsenkung lässt sich wieder einschalten, und
zwei Fehler, die dem Bedienenden als Zufall erschienen (willkürliches Abgemeldetwerden,
eine Seite, aus der es keinen Ausweg gab), sind gefunden und behoben. Dazu ein neues
Stück Funktion: der Ventilschutz.

**Der Trockenlauf ist die Vorgabe und wird von zwei Riegeln gehalten** — geschaltet wird
nichts, solange niemand scharf schaltet. Das *kann* man aber: `/control/arm` hat ein
eigenes Recht (`control.arm`), und danach bewegen sich echte Ventile. Bei selbstregelnden
Thermostaten genügt dafür schon das Veröffentlichen des Sollwerts. Frühere Fassungen
dieses Abschnitts behaupteten pauschal „geschaltet wird weiterhin nichts"; das stimmte so
nicht und ist hier berichtigt.

### Zu beachten beim Umstieg

- **Eine Migration** läuft beim Start von selbst (Ventilschutz je Zone samt der
  Betriebszeitstempel dazu). Der Weg von 0.2.0 aufwärts ist gegen SQLite und MariaDB
  durchgespielt.
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

- **Ventilschutz je Zone.** Nach einer einstellbaren Zeit ohne reguläres Heizen kann das
  Ventil für eine einstellbare Dauer bewegt werden, damit es über den Sommer nicht
  festsitzt. Die Funktion ist standardmäßig aus und steht in der Regelkette hinter allen
  bisherigen Entscheidungen; Konfiguration gibt es in Oberfläche, REST und MCP.

- **Meross-Anbindung, beide Hälften.** Bisher gab es nur einen Schaltadapter und kein
  Gerät, auf das er gepasst hätte — Geräte entstanden ausschliesslich aus der
  Zigbee2MQTT-Liste, eine Meross-Steckdose konnte in der Anlage gar nicht auftauchen.
  Jetzt gleicht der Schattenzyklus die Geräteliste des Kontos stündlich ab und legt
  gefundene Steckdosen an. Die Zuordnung hängt an der `uuid`, nicht am Namen: Wer in der
  Meross-App umbenennt, verliert seine Zuordnung nicht. Gelöscht wird nie — ein Gerät,
  das die Wolke gerade nicht nennt, ist meist offline.

### Behoben

- **Reguläres Heizen verliert nach einem Ventilschutzlauf nicht mehr seine Hysterese.**
  Sobald die normale Regelung das Heizen übernimmt, wird der Schutzmarker beendet. Ein
  folgender Messwert innerhalb der Hysterese hält die Heizung damit wie vorgesehen an;
  reine Schutzläufe behalten den Marker bis zu ihrem regulären Ende.

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
