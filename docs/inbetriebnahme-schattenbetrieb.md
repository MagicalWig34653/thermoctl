# Den Schattenbetrieb in Gang setzen

Diese Anleitung ist für den Projektinhaber. Sie beschreibt den einen Schritt, den nur er
tun kann und der Teilprojekt 2 wirklich abschließt: die Anlage anschließen und mehrere Tage
laufen lassen.

**Die Regelung bleibt dabei unscharf.** Sie liest, schreibt mit und protokolliert ihre
Entscheidungen. Es werden keine Sollwerte an Ventile gesendet; Ein/Aus-Entscheidungen
erreichen keinen Aktor. Das Altsystem bleibt unangetastet und regelt weiter.

## 1. Zugangsdaten eintragen

In `.env` — die Datei ist gitignored und bleibt es:

```dotenv
THERMOCTL_MQTT_ENABLED=true
THERMOCTL_MQTT_HOST=<Adresse des Brokers>
THERMOCTL_MQTT_PORT=8883
THERMOCTL_MQTT_TLS=true
THERMOCTL_MQTT_USERNAME=<Benutzer>
THERMOCTL_MQTT_PASSWORD=<Passwort>
```

`THERMOCTL_MQTT_ENABLED` steht standardmäßig auf `false`. Erst dieser Schalter startet den
Empfang und die Regelschleife im Schattenbetrieb.

## 2. Starten und beim ersten Mal zusehen

```bash
docker compose up -d && docker compose logs -f thermoctl
```

Was in den ersten Minuten zu sehen sein sollte:

- `MQTT-Verbindung hergestellt` mit Host und Port. Kommt stattdessen wiederholt
  `MQTT-Verbindung verloren`, stimmt etwas an Adresse, Port, TLS oder Zugangsdaten nicht —
  der Abstand zwischen den Versuchen verdoppelt sich bis auf eine Minute.
- Nach kurzer Zeit erscheinen unter **`/geraete`** die Geräte aus Zigbee2MQTT. Sie werden
  von selbst angelegt; zugeordnet werden sie von Hand.

## 3. Zonen einrichten und zuordnen

Über die Oberfläche, ohne SQL:

1. **`/zonen`** — je Raum eine Zone anlegen.
2. **`/zonen/<id>/geraete`** — die Messquelle wählen (der Temperatursensor des Raums), dazu
   Aktoren und, falls vorhanden, Fensterkontakte.
3. **`/modi`** und **`/zonen/<id>/sollwerte`** — je Modus eine Temperatur.
4. **`/zonen/<id>/zeitplan`** — Schaltpunkte eintragen. Ein Plan lässt sich von einer
   anderen Zone übernehmen.
5. **`/zonen/<id>/parameter`** — nur, wenn eine Zone vom globalen Standard abweichen soll.
   Leere Felder erben; die Seite zeigt, was gerade gilt.

## 4. Was danach zu beobachten ist

Auf der **Startseite** steht je Zone: Ist-Wert und wie alt er ist, Sollwert **mit
Begründung**, Sensorzustand, und die letzte Schattenentscheidung.

Über mehrere Tage sind drei Dinge zu prüfen — sie sind das Abnahmekriterium von
Teilprojekt 2:

1. **Laufen für jede Zone plausible Ist-Temperaturen ein?** Ein Wert, der stundenlang
   gleich bleibt, ist verdächtig; die Störungserkennung meldet ihn ab dem eingestellten
   Timeout als `veraltet`.
2. **Sind die Schattenentscheidungen nachvollziehbar?** Jede trägt ihren Grund. Wenn eine
   Zone nachts heizen würde, obwohl der Zeitplan Nacht sagt, stimmt etwas an der Zuordnung
   oder am Sollwert — nicht an der Regel.
3. **Decken sie sich mit dem, was das Altsystem tut?** Der maschinelle Vergleich kommt in
   Phase 4; fürs Erste genügt der gelegentliche Blick.

## 5. Wenn eine Störung gemeldet wird

Optional lässt sich ein Webhook eintragen (`THERMOCTL_NOTIFY_WEBHOOK`, siehe
[self-hosting.md](self-hosting.md)). Gemeldet wird der **Wechsel** in eine Störung und die
Entwarnung — nicht der Zustand, sonst käme die Meldung in jedem Zyklus.

## 6. Was danach ansteht

Erst wenn diese Beobachtung überzeugt, lohnt Phase 4. Sie beginnt nicht mit dem
Scharfschalten, sondern mit dem Vergleichsbetrieb: die eigenen Entscheidungen gegen die des
Altsystems stellen und die Abweichungen ansehen. Die lesende Grundlage dafür steht bereits;
was fehlt, ist die Ablage der Altwerte — und die sollte erst entworfen werden, wenn klar
ist, wie lange und in welcher Auflösung verglichen wird.

**Bis der Cutover abgeschlossen ist, bleibt `vm130-nginx` unverändert die Rückfallebene.**
