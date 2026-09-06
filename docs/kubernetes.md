# Der Aktiv-Bereitschafts-Verbund unter Kubernetes

**Für wen das hier gedacht ist.** `thermoctl` steuert eine Heizung in einer bewohnten
Wohnung, kein mehrmandantenfähiges Produkt. Ein eigener Kubernetes-Cluster nur dafür ist
für die meisten Betreiber der falsche Aufwand — wer keinen Cluster ohnehin schon für
anderes betreibt, ist bei `docker compose` (`docs/self-hosting.md`) oder, für den
Aktiv-Bereitschafts-Verbund allein, bei [Docker Swarm](docker-swarm.md) besser
aufgehoben: weniger bewegliche Teile für denselben Nutzen. Diese Anleitung ist für den
Fall gedacht, dass bereits ein Kubernetes-Cluster da ist — etwa für andere
Heimnetz-Dienste — und `thermoctl` dort mitlaufen soll, nicht als Empfehlung, für
`thermoctl` allein einen anzuschaffen.

Setzt [`docs/self-hosting.md`](self-hosting.md) voraus, insbesondere Abschnitt 6d zum
Aktiv-Bereitschafts-Verbund selbst. Die Beispielmanifeste liegen unter `k8s/`:

- `k8s/thermoctl-migrate-job.beispiel.yaml` — der einmalige Migrations-Job.
- `k8s/thermoctl-statefulset.beispiel.yaml` — der Dienst selbst: ein `StatefulSet` mit
  zwei Nachbildungen, ein zugehöriger „headless" Service für die stabilen Namen, und ein
  gewöhnlicher `Service` für den Zugriff von außen.

Beide Dateien wurden gegen das tatsächliche Kubernetes-Schema geprüft
(`kubeconform -strict`, siehe unten) — kein Feldname darin ist erfunden.

## Warum ein StatefulSet und kein Deployment

Ein `Deployment` vergibt Pod-Namen mit einem zufälligen Suffix (`thermoctl-7d9f8b-x2k4m`)
— nichts, worauf sich eine MQTT-Client-Kennung stabil stützen ließe. Ein `StatefulSet`
vergibt stattdessen durchnummerierte, über einen Neustart hinweg stabile Namen
(`thermoctl-0`, `thermoctl-1`) und braucht dafür einen „headless" Service
(`clusterIP: None`) als Voraussetzung — beides steht in
`k8s/thermoctl-statefulset.beispiel.yaml`. Genau diese Nummer ist es, die unten die
MQTT-Client-Kennung trägt.

Kein `volumeClaimTemplates`: Der Verbund setzt MariaDB voraus (nächster Abschnitt),
`thermoctl` selbst legt dabei keine eigenen Daten im Pod ab.

## Die Datenbank

**Der Verbund setzt MariaDB voraus**, aus demselben Grund wie unter Swarm
(`docs/docker-swarm.md`): SQLite ist eine einzelne Datei, zwei Nachbildungen können sie
nicht gemeinsam beschreiben, und ein Netzwerkdateisystem darunter wäre eine Quelle
stiller Schäden, keine Abhilfe. Für den Einzelbetrieb bleibt SQLite mit einem
beständigen Datenträger richtig (`docs/self-hosting.md`, Abschnitt 1).

Eine hochverfügbare MariaDB innerhalb desselben Clusters zu betreiben (etwa über einen
MariaDB-Operator) ist ein eigenes Thema, das diese Anleitung nicht behandelt — `
THERMOCTL_DATABASE_URL` zeigt auf eine erreichbare MariaDB, gleich ob sie im selben
Cluster oder außerhalb läuft. Beide Nachbildungen zeigen auf **dieselbe**.

## Die MQTT-Client-Kennung

**Jede Nachbildung braucht eine eigene**, sonst wirft der Broker beide im Sekundentakt
gegenseitig hinaus (`docs/self-hosting.md`, Abschnitt 7). Kubernetes bietet dafür die
[Downward API](https://kubernetes.io/docs/tasks/inject-data-application/environment-variable-expose-pod-information/)
an: Der eigene Pod-Name lässt sich als Umgebungsvariable in den Container spiegeln, und
bei einem `StatefulSet` ist dieser Name stabil und durchnummeriert (`thermoctl-0`,
`thermoctl-1`) — anders als bei einem `Deployment`, siehe oben.

```yaml
env:
  - name: POD_NAME
    valueFrom:
      fieldRef:
        fieldPath: metadata.name
  - name: THERMOCTL_MQTT_CLIENT_ID
    value: "$(POD_NAME)"
  - name: THERMOCTL_INSTANCE_ID
    value: "$(POD_NAME)"
```

Die `$(POD_NAME)`-Ersetzung wertet Kubernetes selbst beim Erzeugen des Containers aus
(dokumentiertes Verhalten, keine Shell-Interpolation im Container) — vorausgesetzt, die
referenzierte Variable steht in der Liste **weiter oben**, wie hier. Ergebnis: die eine
Nachbildung bekommt `THERMOCTL_MQTT_CLIENT_ID=thermoctl-0`, die andere
`THERMOCTL_MQTT_CLIENT_ID=thermoctl-1` — stabil über einen Neustart desselben Platzes
hinweg, weil das `StatefulSet` denselben Pod-Namen wiedervergibt.

## Migrationen beim Start

`docker/entrypoint.sh` führt `alembic upgrade head` unbedingt aus, bevor der Dienst
startet. Unter Kubernetes ist das für sich genommen aus demselben Grund wie unter
Swarm nicht harmlos: Ein `StatefulSet` mit zwei Nachbildungen startet grundsätzlich
der Reihe nach (`thermoctl-0` vor `thermoctl-1`, das ordnungsgemäße Standardverhalten
eines `StatefulSet`) — das schützt vor zwei **gleichzeitig neu startenden**
Migrationen bei einem ganz neuen Ausrollen, aber **nicht** bei einem Rolling Update:
Kubernetes ersetzt dabei jede Nachbildung einzeln, aber die alte Nachbildung läuft
dabei weiter, bis die neue bereit ist — für eine kurze Zeitspanne liefe eine alte und
eine neue Fassung von `alembic upgrade head` nebeneinander, wenn beide beim Start
migrierten. MySQL/MariaDB committen DDL zudem implizit, eine Transaktion allein
schützt also nicht.

**Das ist inzwischen abgesichert:** `migrations/env.py` nimmt vor jedem
Migrationslauf eine Datenbank-Sperre (`GET_LOCK()` unter MariaDB, eine Dateisperre
unter SQLite) — dieselbe Absicherung wie unter Swarm (`docs/docker-swarm.md`,
Abschnitt „Migrationen beim Start"), weil sie im Alembic-Aufruf selbst sitzt und
nicht im Entrypoint. Zwei gleichzeitig migrierende Nachbildungen laufen dadurch
nacheinander statt gegeneinander: Eine bekommt die Sperre und migriert, die andere
wartet (mit einer Obergrenze, `THERMOCTL_MIGRATION_LOCK_TIMEOUT_SECONDS`, Vorgabe 60
Sekunden), findet die Datenbank danach bereits auf dem neuesten Stand vor und startet
ganz normal weiter. Stirbt die migrierende Nachbildung mitten im Lauf, löst sich ihre
Sperre mit der Verbindung von selbst.

Ein einfaches `kubectl apply` auf das `StatefulSet` reicht also aus:

```bash
kubectl apply -f k8s/thermoctl-statefulset.beispiel.yaml
```

`k8s/thermoctl-migrate-job.beispiel.yaml` (ein vorgeschalteter, einmaliger
Migrations-`Job`, mit `kubectl wait` abgewartet) ist damit **nicht mehr nötig, um die
Datenbank vor gleichzeitiger Migration zu schützen** — das leistet die Sperre jetzt
selbst. Die Datei bleibt für alle, denen es lieber ist, den Migrationsschritt als
eigenen, sichtbaren Vorgang vor dem Ausrollen des `StatefulSet` zu sehen — ein `Job`
garantiert für sich allein keine Reihenfolge gegenüber einem gleichzeitig
angewendeten `StatefulSet`, das übernimmt hier die Reihenfolge der
`kubectl`-Aufrufe:

```bash
kubectl apply -f k8s/thermoctl-migrate-job.beispiel.yaml
kubectl wait --for=condition=complete --timeout=120s job/thermoctl-migrieren
kubectl apply -f k8s/thermoctl-statefulset.beispiel.yaml
```

Bei einer Aktualisierung: `kubectl delete job thermoctl-migrieren` (Jobs lassen sich
nicht überschreiben), die drei Befehle oben erneut mit dem neuen Abbild.

## Zustandsprüfungen

`/healthz` meldet, ob der Prozess läuft und die Datenbank erreichbar ist — **nicht**, ob
diese Nachbildung gerade führt (`thermoctl/app.py`, Endpunkt `/healthz`). Eine
Bereitschaft antwortet dort ebenso mit `{"status": "ok", …}` wie die aktive Instanz.
Das ist richtig so: `livenessProbe` und `readinessProbe` gegen `/healthz` sind deshalb
für beide Nachbildungen unverändert die richtige Prüfung — ein Orchestrierer, der die
Bereitschaft dafür für krank hielte und neu starten würde, machte den Verbund kaputt,
und genau das vermeidet diese Prüfung, weil sie nichts über die Führungsrolle aussagt.
`k8s/thermoctl-statefulset.beispiel.yaml` setzt beide Sonden auf denselben Pfad.

Eine eigene Sonde, die nur die aktive Instanz als „bereit" meldet, wäre möglich (der
Anspruch steht in der Datenbank, `cluster.is_leader` liest ihn), ist hier aber bewusst
nicht gebaut: Eine `readinessProbe`, die die Bereitschaft aus dem `Service` herausnähme,
würde nur bewirken, dass der Reverse-Proxy sie nicht mehr erreicht — Ansehen und
Konfigurieren funktionieren auf beiden Nachbildungen gleich (`docs/self-hosting.md`,
Abschnitt 6d), eine Bereitschaft aus dem Zugriff zu nehmen wäre also ein Verlust ohne
Gewinn.

## Auf unterschiedliche Knoten verteilen

`k8s/thermoctl-statefulset.beispiel.yaml` enthält eine
`podAntiAffinity` mit `requiredDuringSchedulingIgnoredDuringExecution` auf
`kubernetes.io/hostname` — ohne sie könnte der Scheduler beide Nachbildungen auf
denselben Knoten legen, und ein einzelner Knotenausfall träfe dann beide zugleich.
**Auf einem Ein-Knoten-Cluster (z. B. einem lokalen Testaufbau) bleibt die zweite
Nachbildung mit dieser Angabe dauerhaft `Pending`** — dieselbe Falle wie unter Swarm
(`docs/docker-swarm.md`); für einen Testaufbau die `affinity` entfernen, für den echten
Betrieb mindestens zwei Knoten vorsehen.

## Wieviele Nachbildungen?

Genau zwei (`spec.replicas: 2`). Mehr bringen nichts — je Regelzyklus entscheidet
ohnehin nur eine einzige Instanz, die zweite steht bereit (`docs/self-hosting.md`,
Abschnitt 6d).

## Zugangsdaten

Keine in den Manifesten. `THERMOCTL_DATABASE_URL`, `THERMOCTL_SECRET_KEY` und der
MQTT-Zugang kommen aus einem [`Secret`](https://kubernetes.io/docs/concepts/configuration/secret/),
das diese Anleitung nicht als Datei anlegt — Zugangsdaten gehören nicht ins Repository,
auch nicht mit Platzhaltern, die versehentlich echt werden könnten. Anlegen:

```bash
kubectl create secret generic thermoctl-geheimnisse \
  --from-literal=THERMOCTL_DATABASE_URL='mysql+pymysql://thermoctl:<passwort>@mariadb:3306/thermoctl' \
  --from-literal=THERMOCTL_SECRET_KEY='<erzeugter Schlüssel, siehe docs/self-hosting.md Abschnitt 2>' \
  --from-literal=THERMOCTL_MQTT_ENABLED=true \
  --from-literal=THERMOCTL_MQTT_HOST='<broker>' \
  --from-literal=THERMOCTL_MQTT_USERNAME='<eigener Zugang>' \
  --from-literal=THERMOCTL_MQTT_PASSWORD='<passwort>'
```

Beide Manifeste (Migrations-Job und `StatefulSet`) beziehen es über `envFrom.secretRef`
— jede in `.env.example` aufgeführte Einstellung lässt sich auf demselben Weg ergänzen.

## Erreichbarkeit

Wie im Einzelbetrieb: ein Reverse-Proxy mit TLS davor, siehe `docs/self-hosting.md`,
Abschnitt 4 — üblicherweise ein `Ingress`-Objekt vor dem `Service` `thermoctl` aus
`k8s/thermoctl-statefulset.beispiel.yaml`. Welche der beiden Nachbildungen eine
einzelne Anfrage beantwortet, ist dafür unerheblich, siehe „Zustandsprüfungen" oben.

## Geprüft, nicht nur behauptet

```bash
kubeconform -summary -strict k8s/*.yaml
```

meldet alle vier Objekte (Job, zwei Services, StatefulSet) als gültig gegen das
offizielle Kubernetes-Schema. Die Downward-API-Ersetzung (`$(POD_NAME)`) und die
Reihenfolge, in der ein `StatefulSet` seine Nachbildungen startet, sind dokumentiertes
Kubernetes-Verhalten (siehe die verlinkte Kubernetes-Dokumentation oben); ein eigener
Cluster stand für diese Anleitung nicht zur Verfügung, um sie zusätzlich im Betrieb zu
beobachten — anders als beim Docker-Swarm-Pendant (`docs/docker-swarm.md`), das gegen
einen echten, lokal initialisierten Swarm geprüft wurde.
