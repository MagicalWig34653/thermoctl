# Der Aktiv-Bereitschafts-Verbund unter Docker Swarm

Diese Anleitung setzt [`docs/self-hosting.md`](self-hosting.md) voraus, insbesondere
Abschnitt 6d zum Aktiv-Bereitschafts-Verbund selbst. Hier geht es nur um das Zusätzliche,
das ein Orchestrierer braucht: eine eindeutige MQTT-Client-Kennung je Nachbildung, die
Migrationen beim Start, und was `/healthz` auf der Bereitschaft antwortet.

Wer den Verbund nicht braucht, bleibt bei `docker compose` — Abschnitt 1 dort erklärt,
wofür der einfache Betrieb reicht. Swarm lohnt sich, sobald zwei Instanzen wirklich auf
unterschiedlichen Rechnern laufen sollen; auf einem einzigen Rechner leistet Swarm
gegenüber `docker compose` mit zwei Diensten nichts zusätzlich.

## Was Sie brauchen

- Einen initialisierten Swarm (`docker swarm init`), mit **mindestens zwei Knoten** — ein
  Ein-Knoten-Swarm kann die beiden Nachbildungen nicht wie unten beschrieben trennen,
  siehe "Auf unterschiedliche Knoten verteilen".
- Eine erreichbare **MariaDB**, siehe nächster Abschnitt.
- Die beiden Beispieldateien aus `docker/`: `swarm.compose.beispiel.yml` (der Dienst) und
  `swarm.migrate.compose.beispiel.yml` (die Migration, eigene Datei, Begründung unten).

```bash
cp docker/swarm.compose.beispiel.yml compose.swarm.yml
cp docker/swarm.migrate.compose.beispiel.yml compose.swarm-migrate.yml
cp .env.example .env
```

`.env` wird wie im Einzelbetrieb ausgefüllt (`docs/self-hosting.md`, Abschnitt 2) — mit
einer Ausnahme: `THERMOCTL_MQTT_CLIENT_ID` und `THERMOCTL_INSTANCE_ID` bleiben leer, sie
kommen aus der Compose-Datei selbst, siehe unten.

## Die Datenbank

**Der Verbund setzt MariaDB voraus.** SQLite ist eine einzelne Datei; zwei Nachbildungen,
die dieselbe Datei gleichzeitig beschreiben, ist kein Betriebsmodus, den SQLite anbietet
— und ein Netzwerkdateisystem darunter, um die Datei doch zu teilen, ist eine bekannte
Quelle stiller Schäden, keine Abhilfe. Für den Einzelbetrieb bleibt SQLite mit einem
beständigen Datenträger die richtige Wahl (`docs/self-hosting.md`, Abschnitt 1) — das
ändert sich hier nicht, nur der Verbund selbst braucht mehr.

`compose.beispiel.yml` enthält bereits einen auskommentierten MariaDB-Dienst für den
Einzelbetrieb; im Verbund läuft MariaDB sinnvollerweise **außerhalb** dieses Stacks —
als eigener, ebenfalls unter Swarm verwalteter Dienst mit eigenem Volume, oder als
Datenbank, die ohnehin schon woanders läuft. Beide Instanzen zeigen auf **dieselbe**
`THERMOCTL_DATABASE_URL`.

## Die MQTT-Client-Kennung

**Jede Nachbildung braucht eine eigene Kennung**, sonst wirft der Broker beide im
Sekundentakt gegenseitig hinaus (`docs/self-hosting.md`, Abschnitt 7, das Symptom mit
`code:128 Unspecified error`). Swarm bietet dafür ein eigenes, kleines Templating an, das
in `hostname` **und** `environment` ausgewertet wird — geprüft mit einem echten
`docker stack deploy` gegen zwei Nachbildungen, nicht nur aus der Dokumentation
übernommen:

```yaml
environment:
  THERMOCTL_MQTT_CLIENT_ID: "thermoctl-{{.Task.Slot}}"
  THERMOCTL_INSTANCE_ID: "swarm-{{.Task.Slot}}"
hostname: "thermoctl-{{.Task.Slot}}"
```

`{{.Task.Slot}}` zählt 1..N innerhalb **dieses** Dienstes und bleibt für dieselbe
Aufgabe stabil — verliert ein Knoten seine Nachbildung und Swarm plant sie neu, behält
sie ihren Platz und damit dieselbe Kennung. Das ist genau das, was gebraucht wird: nicht
irgendeine eindeutige Zeichenfolge, sondern eine, die über einen Neustart derselben
Aufgabe hinweg gleich bleibt, damit ein Log über mehrere Neustarts hinweg lesbar bleibt.

Diese Auswertung geschieht **vor** dem Start des Containers, durch die Swarm-Engine
selbst — nicht durch eine Shell-Interpolation im Container. Das Beispiel oben stand
in keiner Docker-Dokumentationsseite explizit für `docker stack deploy` (nur für
`docker service create` auf der Kommandozeile); geprüft wurde es deshalb direkt: ein
Zwei-Nachbildungen-Stack mit genau dieser `environment:`-Angabe, `docker service
inspect` bzw. `docker inspect` auf die entstandenen Container zeigt
`THERMOCTL_MQTT_CLIENT_ID=thermoctl-1` bzw. `…-2` — kein Platzhalter, kein leerer Wert.

## Migrationen beim Start

`docker/entrypoint.sh` führt `alembic upgrade head` unbedingt aus, bevor der Dienst
startet. Unter Swarm ist das nicht harmlos: `docker stack deploy` bringt alle Dienste
einer Datei **gleichzeitig** hoch, ohne Rücksicht auf `depends_on` — das Feld wirkt nur
bei `docker compose up`, nicht im Swarm-Modus. Zwei gleichzeitig startende Nachbildungen
würden also beide gleichzeitig `alembic upgrade head` gegen dieselbe MariaDB ausführen.
Die Migrationen selbst sichern das nicht ab (kein Sperr-Mechanismus in
`migrations/env.py`); MySQL/MariaDB committen DDL zudem implizit, sodass eine
Transaktion allein nicht schützt. Im günstigen Fall bricht die zweite Nachbildung mit
einem Fehler wie „Table already exists" ab und startet neu; ein wirklich schädlicher
Ausgang ist nicht ausgeschlossen. **Das ist ein offener Punkt am Code selbst** (der
Entrypoint müsste die Migration gegen genau diesen Fall absichern, z. B. über eine
Datenbank-Sperre) — diese Anleitung umschifft ihn nur für den Orchestrierer-Betrieb,
löst ihn nicht.

Die Umgehung hier: Migration und Dienst sind **zwei getrennte Compose-Dateien**, unter
demselben Stack-Namen, nacheinander ausgerollt.

```bash
docker stack deploy -c compose.swarm-migrate.yml thermoctl
docker service ps thermoctl_migrieren   # warten, bis CURRENT STATE "Complete" zeigt
docker stack deploy -c compose.swarm.yml thermoctl
```

Der Migrations-Dienst läuft mit `deploy.mode: replicated-job` (Docker Engine 20.10+,
geprüft: der Dienst wechselt nach Exitcode 0 selbst in den Zustand `Complete` und wird
nicht neu gestartet — anders als ein gewöhnlicher Dienst, dessen `restart_policy` ihn
sonst endlos neu anliefe). Bei einer Aktualisierung: `docker service rm
thermoctl_migrieren`, dann die beiden Befehle oben erneut mit dem neuen Abbild.

## Zustandsprüfungen

`/healthz` meldet, ob der Prozess läuft und die Datenbank erreichbar ist — **nicht**, ob
diese Nachbildung gerade führt (`thermoctl/app.py`, Endpunkt `/healthz`). Eine
Bereitschaft antwortet dort also ebenso mit `{"status": "ok", …}` wie die aktive
Instanz. Das ist richtig so und braucht keine gesonderte Behandlung: Ein Orchestrierer,
der die Bereitschaft dafür für krank hielte und neu starten würde, machte den Verbund
kaputt — genau das tut Swarms `HEALTHCHECK` hier nicht, weil er dieselbe, im Abbild
eingebaute Prüfung wie im Einzelbetrieb verwendet (`docker/Dockerfile`,
`docker/compose.beispiel.yml`). Das Beispiel oben übernimmt sie unverändert.

## Auf unterschiedliche Knoten verteilen

```yaml
deploy:
  replicas: 2
  placement:
    max_replicas_per_node: 1
```

Ohne diese Angabe kann der Scheduler beide Nachbildungen auf denselben Knoten legen —
ein einzelner Rechnerausfall träfe dann beide zugleich, und der ganze Sinn des Verbunds
wäre dahin. **Auf einem Swarm mit nur einem Knoten bleibt die zweite Nachbildung mit
dieser Angabe dauerhaft `Pending`** (`no suitable node (max replicas per node limit
exceeded)`, selbst beobachtet) — für einen Testaufbau auf einem Rechner die Zeile
weglassen, für den echten Betrieb mindestens zwei Knoten vorsehen.

## Wieviele Nachbildungen?

Genau zwei. Mehr bringen nichts — je Regelzyklus entscheidet ohnehin nur eine einzige
Instanz, die zweite steht bereit (`docs/self-hosting.md`, Abschnitt 6d). Eine dritte
Nachbildung wäre eine zweite Bereitschaft, die nie etwas täte.

## Zugangsdaten

Keine in der Compose-Datei. `.env` bleibt der Weg für `THERMOCTL_DATABASE_URL`,
`THERMOCTL_SECRET_KEY`, MQTT-Zugang — wie im Einzelbetrieb. Wer sie nicht als Datei auf
jedem Knoten verteilen will, hält sie stattdessen als
[Docker Secret](https://docs.docker.com/engine/swarm/secrets/) (`docker secret create`)
und mountet sie in den Container; die Anwendung selbst liest ausschließlich Umgebungs-
variablen, keine Datei unter einem festen Pfad, ein Secret muesste also über
`entrypoint`/eine kleine Startzeile in eine Umgebungsvariable übersetzt werden. Diese
Anleitung zeigt das nicht als Beispieldatei, um keine Konfiguration vorzugeben, die den
falschen Eindruck erweckt, sie sei Pflicht — `.env` per `env_file:` bleibt der einfachere
und hier gezeigte Weg.

## Erreichbarkeit

Wie im Einzelbetrieb: ein Reverse-Proxy mit TLS davor, siehe `docs/self-hosting.md`,
Abschnitt 4. Der Proxy zeigt auf den Swarm-Ingress-Port des Dienstes (oder, bei
`endpoint_mode: vip`, die vom Swarm vergebene virtuelle Adresse) — welche der beiden
Nachbildungen eine einzelne Anfrage beantwortet, ist dafür unerheblich, siehe
"Zustandsprüfungen" oben.
