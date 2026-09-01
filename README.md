# thermoctl

`thermoctl` ist eine selbst gehostete Heizungssteuerung: sensorbasierte Raumregelung mit
Zeitplänen, konfiguriert über eine Weboberfläche, ansprechbar zusätzlich über eine
REST-Schnittstelle und einen MCP-Server.

Was heute läuft: Sensoren werden über Zigbee2MQTT eingelesen, Messwerte fortgeschrieben,
ausgefallene Sensoren erkannt und gemeldet. Räume, Geräte, Sollwerte und Zeitpläne lassen
sich vollständig über die Oberfläche pflegen — Geräte per Ziehen und Ablegen. Für jede Zone
wird protokolliert, **was geschaltet würde und warum**.

Dazu: Bediengeräte und Zigbee-Heizkörperthermostate frei konfigurierbar (ein Thermostat
kann das Ventil auch selbst regeln und bekommt dann nur Soll- und Ist-Temperatur), eine
optionale Absenkung, wenn die Sonnenprognose sie erlaubt, ein Dashboard für ein Wandtablet
hinter einem widerrufbaren Kiosk-Token, und die Home-Assistant-Anbindung über
MQTT-Discovery.

Die Ausgabe hat drei klar getrennte Stufen: Im **Trockenlauf** werden Regelentscheidungen
nur protokolliert. **Scharf ohne Neustart** ändert den gespeicherten ersten Riegel, der beim
Start gebaute MQTT-Riegel bleibt aber zu. Erst **scharf und neu gestartet** können Sollwerte
an selbstregelnde Thermostatventile gesendet werden. Ein/Aus-Entscheidungen erreichen in
keiner Stufe einen Aktor.

## Weiterlesen

- **[Den Schattenbetrieb in Gang setzen](docs/inbetriebnahme-schattenbetrieb.md)** — der
  nächste Schritt an der echten Anlage.
- **[Eine eigene Instanz betreiben](docs/self-hosting.md)** — Schritt für Schritt, mit
  Sicherung, Aktualisierung und dem, was bei TLS zu beachten ist.
- **[REST-Schnittstelle](docs/api.md)** — Endpunkte, Tokens, Rechte.
- **[MCP-Server](docs/mcp.md)** — derselbe Funktionsumfang für einen MCP-Client.
- **[MQTT](docs/mqtt.md)** — was gelesen wird, und die entworfene eigene Topic-Struktur.
- **[Sicherheitsdurchsicht](docs/sicherheitsdurchsicht.md)** — was geprüft wurde und was offen ist.
- **[Roadmap](docs/roadmap.md)** und **[Stand](docs/STATUS.md)** — was da ist und was folgt.
- **[Änderungen](CHANGELOG.md)** — was sich je Version geändert hat, samt dem, was beim
  Umstieg zu beachten ist.
- **[Beispiel-Compose](docker/compose.beispiel.yml)** — kopieren, `.env` ausfüllen, starten.

## Start mit Docker

Vorausgesetzt werden Docker und eine erreichbare SQLite- oder MariaDB-Datenbank. Zuerst
eine lokale `.env` mit mindestens diesen Pflichtangaben anlegen:

- `THERMOCTL_DATABASE_URL`: SQLAlchemy-Verbindungs-URL zur Datenbank
- `THERMOCTL_SECRET_KEY`: zufälliger Schlüssel mit mindestens 32 Zeichen; erzeugen mit
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`

Optional sind `THERMOCTL_BIND_HOST`, `THERMOCTL_BIND_PORT`, `THERMOCTL_LOG_LEVEL`,
`THERMOCTL_LOG_FORMAT` und `THERMOCTL_SECURE_COOKIES`. Die Erläuterungen und Vorgabewerte
stehen in `.env.example`. Bei Betrieb hinter TLS muss `THERMOCTL_SECURE_COOKIES` aktiviert
werden.

```bash
docker build -f docker/Dockerfile -t thermoctl .
docker run --rm --env-file .env -p 8000:8000 -v thermoctl-data:/data thermoctl
```

Für die Datenbank im Container muss `THERMOCTL_DATABASE_URL` auf einen Pfad im Volume
zeigen, etwa `sqlite:////data/thermoctl.db` — ein relativer Pfad landet sonst im Container
und ist beim nächsten Start verschwunden.

## Örtlich starten, ohne Container

```bash
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m alembic upgrade head
.venv/bin/python -c "from thermoctl.cli import main; main()"
```

Der Konsolenbefehl `.venv/bin/thermoctl` funktioniert bei einer editierbaren Installation
nicht zuverlässig: Sein Modulpfad beginnt im `.venv/bin`, und die Datei, die das Paket dort
auffindbar macht, wird unter macOS als versteckt markiert und beim Start übersprungen. Der
Aufruf über `python -c` nimmt stattdessen das Projektverzeichnis in den Modulpfad. Im
Container tritt das nicht auf, dort ist das Paket regulär installiert.

## Tests

```bash
.venv/bin/pytest                                   # SQLite, mit Abdeckungsbericht
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://… .venv/bin/pytest    # gegen MariaDB
```

Die Suite läuft gegen beide Datenbanken; die CI verlangt 100 % Abdeckung.

Beim ersten Start stehen die Datenbankmigrationen und anschließend der Dienststart an. Ist
noch kein Benutzer vorhanden, erscheint das einmalig verwendbare Einrichtungs-Token im
Container-Log. Damit wird die Einrichtung unter `/setup` abgeschlossen. Logs mit diesem
Token sind wie Zugangsdaten zu schützen.

## Noch nicht enthalten

Der Regelkreis ist gebaut und erschöpfend getestet. Sein gespeicherter erster Riegel lässt
sich scharf schalten; Sollwerte an selbstregelnde Thermostatventile werden jedoch erst nach
einem anschließenden Neustart freigegeben. Ein/Aus-Entscheidungen werden nur protokolliert,
denn ein Aktor ist dafür noch nicht mit dem Regelkreis verdrahtet. Bis zum abgeschlossenen
Vergleichsbetrieb ist `thermoctl` keine betriebsfertige Heizungssteuerung.

Die Home-Assistant-Anbindung ist dagegen angeschlossen: Sie meldet die Zonen an,
veröffentlicht ihren Zustand und nimmt Sollwert und Betriebsart entgegen. Die Ausgabe eines
Sollwerts an ein selbstregelndes Thermostat folgt den drei Stufen oben.

Der Stand im Einzelnen: [docs/STATUS.md](docs/STATUS.md) und [docs/roadmap.md](docs/roadmap.md).
