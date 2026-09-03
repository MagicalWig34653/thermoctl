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

Seit 0.5.0 gibt es zusätzlich eine **PI-Regelung als Beta**, je Zone einschaltbar und aus
als Vorgabe. Sie ersetzt für eine eingeschaltete Zone die Hysterese durch einen
Proportional-Integral-Regler mit zeitproportionalem Ausgang und gilt nur für gewöhnliche
Schaltaktoren — selbstregelnde Ventile und Geräte mit `thermostat`-Fähigkeit sind
ausgeschlossen, weil dort zwei Regler auf derselben Regelstrecke säßen. Der Preis steht am
Schalter: PI schaltet deutlich häufiger und verkürzt die Lebensdauer eines Schaltaktors.
Wie oft eine Zone tatsächlich schaltet, zeigt die Seite **Relaisverschleiß** je Gerät und
Tag mit Jahreshochrechnung — auch ohne PI nützlich.

Die Ausgabe hat drei klar getrennte Stufen: Im **Trockenlauf** werden Regelentscheidungen
nur protokolliert. **Scharf ohne Neustart** ändert den gespeicherten ersten Riegel, der beim
Start gebaute MQTT-Riegel bleibt aber zu — es wird weiterhin nichts gesendet. Erst **scharf
und neu gestartet** öffnet auch diesen zweiten Riegel und sendet wirklich: Sollwerte an
selbstregelnde Thermostatventile ebenso wie Ein/Aus-Befehle an gewöhnliche Aktoren
(Zigbee2MQTT-Schalter, Zigbee-Thermostatventile ohne eigene Regelung, Meross-Steckdosen).

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

### Browsertests

Eine zweite, unabhängige Suite unter `browser_tests/` prüft im echten Browser (Playwright),
was ein HTTP-Test nicht sehen kann: geladenes CSS, Fehler in der Browserkonsole, den
Zeitplan-Editor, den PI-Schalter, das Kiosk-Dashboard. Sie startet einen echten Server gegen
eine eigene, frische SQLite-Datenbank und läuft **nicht** in der gewöhnlichen Suite und
**nicht** in der CI — ausschließlich lokal, auf Wunsch, mit beliebig vielen Tests.

Einmalig einzurichten:

```bash
.venv/bin/pip install -e ".[browser-tests]"
.venv/bin/python -m playwright install chromium
```

Aufruf (das Verzeichnis muss auf der Kommandozeile stehen — ohne es sucht pytest, wie unten
in `browser_tests/pytest.ini` erklärt, im gesamten Arbeitsverzeichnis statt nur hier):

```bash
.venv/bin/pytest -c browser_tests/pytest.ini browser_tests
```

## Noch nicht enthalten

Der Regelkreis ist gebaut und erschöpfend getestet, und alle vier Aktorwege sind mit ihm
verdrahtet: Zigbee2MQTT-Schalter, Zigbee-Thermostatventile ohne eigene Regelung,
Meross-Steckdosen und selbstregelnde Ventile. Sein gespeicherter erster Riegel lässt sich
scharf schalten; wirklich gesendet wird aber erst nach einem anschließenden Neustart, der
den zweiten, beim Prozessstart gebauten Riegel öffnet — das gilt für Sollwerte an
selbstregelnde Thermostatventile genauso wie für Ein/Aus-Befehle an gewöhnliche Aktoren.
Der Trockenlauf bleibt die Vorgabe, und ein mehrtägiger Vergleichsbetrieb gegen das
Altsystem wurde auf Wunsch des Projektinhabers übersprungen: Dieser Code läuft als Erstes
an einer echten Heizung.

Die **PI-Regelung ist ausdrücklich Beta**: keine bestätigte Relaisfreigabe, kein
versprochener Verbrauchsvorteil, je Zone aus als Vorgabe und jederzeit rückgängig. Ihr
Nutzen ist einer — ein Zweipunktregler pendelt um den Sollwert, der Integralanteil
beseitigt genau das. Weniger Verbrauch oder schnelleres Aufheizen folgt daraus nicht.

Die Home-Assistant-Anbindung ist dagegen angeschlossen: Sie meldet die Zonen an,
veröffentlicht ihren Zustand und nimmt Sollwert und Betriebsart entgegen. Die Ausgabe eines
Sollwerts an ein selbstregelndes Thermostat folgt den drei Stufen oben.

Der Stand im Einzelnen: [docs/STATUS.md](docs/STATUS.md) und [docs/roadmap.md](docs/roadmap.md).
