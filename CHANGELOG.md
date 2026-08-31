# Änderungen

Alle nennenswerten Änderungen an `thermoctl`, neueste zuerst. Das Format folgt lose
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen
[semantischer Versionierung](https://semver.org/lang/de/).

Der Stand im Einzelnen — was gebaut ist, was an der echten Anlage noch aussteht und warum
etwas so entschieden wurde — steht in [docs/STATUS.md](docs/STATUS.md).

---

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
