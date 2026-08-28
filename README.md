# thermoctl

`thermoctl` ist das Fundament einer neuen Heizungssteuerung: Es stellt Datenmodell,
Domänenlogik, Anmeldung und Rechte, einen Einrichtungsassistenten, Verwaltungsseiten sowie
eine REST-Schnittstelle bereit. Teilprojekt 1 steuert noch keine Heizung.

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

Beim ersten Start stehen die Datenbankmigrationen und anschließend der Dienststart an. Ist
noch kein Benutzer vorhanden, erscheint das einmalig verwendbare Einrichtungs-Token im
Container-Log. Damit wird die Einrichtung unter `/setup` abgeschlossen. Logs mit diesem
Token sind wie Zugangsdaten zu schützen.

## Noch nicht enthalten

Geräteanbindung und Schattenbetrieb, der eigentliche Regelkreis sowie eine vollständige
Pflegeoberfläche folgen in späteren Teilprojekten. Bis dahin ist `thermoctl` keine
betriebsfertige Heizungssteuerung.
