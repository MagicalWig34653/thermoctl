# thermoctl

`thermoctl` ist das Fundament einer neuen Heizungssteuerung: Es stellt Datenmodell,
Domänenlogik, Anmeldung und Rechte, einen Einrichtungsassistenten, Verwaltungsseiten sowie
eine REST-Schnittstelle bereit. Teilprojekt 1 steuert noch keine Heizung.

## Weiterlesen

- **[Eine eigene Instanz betreiben](docs/self-hosting.md)** — Schritt für Schritt, mit
  Sicherung, Aktualisierung und dem, was bei TLS zu beachten ist.
- **[REST-Schnittstelle](docs/api.md)** — Endpunkte, Tokens, Rechte.
- **[MCP-Server](docs/mcp.md)** — derselbe Funktionsumfang für einen MCP-Client.
- **[Sicherheitsdurchsicht](docs/sicherheitsdurchsicht.md)** — was geprüft wurde und was offen ist.
- **[Roadmap](docs/roadmap.md)** und **[Stand](docs/STATUS.md)** — was da ist und was folgt.
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

Die Suite läuft gegen beide Datenbanken; die CI verlangt mindestens 97 % Abdeckung.

Beim ersten Start stehen die Datenbankmigrationen und anschließend der Dienststart an. Ist
noch kein Benutzer vorhanden, erscheint das einmalig verwendbare Einrichtungs-Token im
Container-Log. Damit wird die Einrichtung unter `/setup` abgeschlossen. Logs mit diesem
Token sind wie Zugangsdaten zu schützen.

## Noch nicht enthalten

Geräteanbindung und Schattenbetrieb, der eigentliche Regelkreis sowie eine vollständige
Pflegeoberfläche folgen in späteren Teilprojekten. Bis dahin ist `thermoctl` keine
betriebsfertige Heizungssteuerung.
