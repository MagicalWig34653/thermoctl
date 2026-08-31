# Eine eigene Instanz betreiben

Diese Anleitung richtet sich an jemanden, der `thermoctl` selbst betreibt und das Projekt
nicht kennt. Sie setzt Docker voraus und dauert etwa zehn Minuten.

> **Stand der Dinge.** `thermoctl` regelt noch keine Heizung. Fundament, Anmeldung, Rechte,
> Verwaltung und REST-Schnittstelle stehen; die Geräte-Anbindung läuft im Schattenbetrieb,
> der Regelkreis kommt danach. Wer heute eine betriebsfertige Steuerung sucht, ist noch zu
> früh dran. Was jetzt schon geht, steht in der [Roadmap](roadmap.md).

## 1. Was Sie brauchen

- Docker mit Compose.
- Einen Rechner, der durchläuft — eine Heizungssteuerung, die nachts aus ist, ist keine.
- Später, für die Geräte-Anbindung: einen MQTT-Broker mit Zigbee2MQTT.

Eine Datenbank brauchen Sie **nicht** mitzubringen. Voreingestellt ist SQLite in einer
Datei; für eine Wohnung genügt das vollständig.

## 2. Einrichten

```bash
git clone <dieses Repository> thermoctl && cd thermoctl
cp docker/compose.beispiel.yml compose.yml
cp .env.example .env
```

Dann `.env` ausfüllen. Zwei Angaben sind Pflicht:

```dotenv
THERMOCTL_DATABASE_URL=sqlite:////data/thermoctl.db
THERMOCTL_SECRET_KEY=<hier den erzeugten Schlüssel einsetzen>
```

Den Schlüssel erzeugen Sie so — **nicht** ausdenken, nicht wiederverwenden:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Er unterschreibt Sitzungen und CSRF-Token. Wer ihn kennt, kann Sitzungen fälschen. Wird er
später geändert, sind alle Anmeldungen ungültig — das ist gewollt und der Weg, alle
Sitzungen auf einmal zu beenden.

> **Vier Schrägstriche in der SQLite-URL sind kein Tippfehler.** `sqlite:////data/…` ist ein
> absoluter Pfad und liegt im Volume. `sqlite:///data/…` mit dreien wäre relativ, landete im
> Container und wäre beim nächsten Start weg — mitsamt allen Benutzern.

## 3. Starten

```bash
docker compose up -d
docker compose logs -f thermoctl
```

Beim ersten Start laufen die Datenbankmigrationen, danach erscheint im Log eine Zeile:

```
Einrichtung erforderlich. Einmal-Token: <ein langer zufälliger Wert>
```

**Dieses Token ist ein Zugangsdatum.** Es ist der einzige Weg, den ersten Verwalter
anzulegen — ohne es gewinnt im ungünstigen Fall der Erste im Netz, der die
Einrichtungsseite findet. Es wird genau einmal erzeugt, gilt genau einmal, und ein
Neustart erzeugt kein zweites. Behandeln Sie das Log entsprechend.

Dann `http://127.0.0.1:8000/setup` im Browser öffnen, Token einsetzen, ersten Verwalter
anlegen. Danach ist `/setup` dauerhaft geschlossen, nicht nur ausgeblendet.

## 4. Ins Netz stellen — nur mit TLS

Das Beispiel-Compose bindet den Port bewusst nur an `127.0.0.1`. Wer den Dienst im Netz
erreichbar machen will, stellt einen Reverse-Proxy mit TLS davor und setzt in `.env`:

```dotenv
THERMOCTL_SECURE_COOKIES=true
```

Damit werden Sitzungscookies nur noch über HTTPS gesendet. Ohne TLS ist die Anmeldung im
Klartext unterwegs — im eigenen WLAN wie überall sonst.

Ein Beispiel für Caddy, das TLS von selbst besorgt:

```
heizung.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Mit nginx entsprechend `proxy_pass`, dazu `proxy_set_header X-Forwarded-Proto $scheme;`.

**Ins offene Internet gehört dieser Dienst nicht.** Er steuert eine Heizung in einer
bewohnten Wohnung. Ein VPN ins Heimnetz ist der richtige Weg; ein öffentlicher Name mit
Passwortanmeldung ist es nicht.

## 5. Sichern

Es gibt genau zwei Dinge zu sichern:

- **die Datenbank** — bei SQLite das Volume `thermoctl-data`, bei MariaDB ein `mysqldump`;
- **die `.env`** — ohne `THERMOCTL_SECRET_KEY` sind nach dem Zurückspielen alle Sitzungen
  ungültig, was verkraftbar ist; ohne die Datenbank ist alles weg.

```bash
docker compose stop thermoctl
docker run --rm -v thermoctl-data:/daten -v "$PWD":/sicherung alpine \
  tar czf /sicherung/thermoctl-$(date +%F).tar.gz -C /daten .
docker compose start thermoctl
```

Der Dienst wird dafür angehalten: Eine SQLite-Datei, die währenddessen geschrieben wird,
ergibt eine Sicherung, die erst beim Zurückspielen als unbrauchbar auffällt.

## 6. Aktualisieren

```bash
docker compose pull
docker compose up -d
```

Die Migrationen laufen beim Start von selbst. **Vorher einen Blick in
[CHANGELOG.md](../CHANGELOG.md) werfen** — dort steht je Version, was beim Umstieg zu tun
ist; zu 0.2.0 gehört etwa eine umbenannte Variable in der `.env`. Der Weg zurück ist die
vorherige Marke:

```bash
docker compose down
# in compose.yml die Marke festnageln, etwa :0.1.0 statt :latest
docker compose up -d
```

**Vor einem Versionssprung sichern.** Eine Migration kann Spalten umbauen; zurück geht es
dann nur über die Sicherung. Die Marke `latest` entsteht ausschließlich aus einer
Veröffentlichung, nie aus einem Zwischenstand — trotzdem ist eine feste Marke die ruhigere
Wahl, wenn der Dienst wirklich heizt.

## 7. Wenn etwas nicht geht

| Symptom | Ursache und Abhilfe |
|---|---|
| Container startet, `/healthz` antwortet nicht | Log ansehen: `docker compose logs thermoctl`. Meist fehlt `THERMOCTL_DATABASE_URL` oder der Schlüssel ist kürzer als 32 Zeichen. |
| `Die Datenbank hat kein Schema` | Die Migrationen liefen nicht. Der Entrypoint erledigt sie; wer den Dienst ohne ihn startet (etwa `uvicorn` von Hand), führt `alembic upgrade head` selbst aus. |
| `Das Datenbankschema steht auf …, der Code erwartet …` | Dieselbe Ursache nach einem Update: `alembic upgrade head` nachholen. Der Dienst startet dann nicht mehr halb und scheitert später an einer fehlenden Spalte. |
| Nach dem Neustart sind alle Benutzer weg | Die SQLite-URL ist relativ. Abschnitt 2, die Sache mit den vier Schrägstrichen. |
| Kein Einrichtungs-Token im Log | Es gibt bereits einen Benutzer — dann ist die Einrichtung abgeschlossen und `/setup` zu Recht geschlossen. |
| Anmeldung wirft die Sitzung sofort weg | `THERMOCTL_SECURE_COOKIES=true` ohne TLS davor. Entweder TLS einrichten oder die Einstellung zurücknehmen. |

Das Log ist strukturiert (`THERMOCTL_LOG_FORMAT=json`, für Menschen `text`) und maskiert
Geheimnisse. Jede Antwort trägt eine `X-Request-ID`, die in jeder zugehörigen Logzeile
wieder auftaucht — damit lässt sich ein einzelner Aufruf durch das ganze Log verfolgen.

## 7a. Ein Wandtablet

Unter `/kiosk` gibt es eine eigene, große Ansicht für ein Tablet an der Wand: je Zone
Ist-Temperatur, Sollwert und Betriebsart, und — wenn erlaubt — zwei Knöpfe für den
Sollwert und einer für den Boost.

**Sie ist nicht öffentlich.** Ein Dashboard ohne Anmeldung widerspräche dem Grundsatz, dass
Authentifizierung Pflicht ist. Stattdessen wird unter *Einstellungen → Kiosk-Tokens* ein
Token ausgestellt:

1. Namen vergeben, damit man das Gerät später wiedererkennt.
2. Die Zonen ankreuzen, die das Tablet sehen darf.
3. „Auch bedienen" nur setzen, wenn am Tablet wirklich verstellt werden soll — sonst zeigt
   es nur an.
4. Optional eine Gültigkeit in Tagen. Ein befristetes Token ist die sicherere Wahl.

Die Adresse erscheint **einmal** im Klartext, in der Form `/kiosk/<token>`. Diese Adresse
als Lesezeichen auf dem Tablet ablegen; danach lebt das Token nur noch in einem Cookie, und
die Adresszeile zeigt das nackte `/kiosk`. Gespeichert wird nur ein Hash — geht die Adresse
verloren, stellt man ein neues Token aus und widerruft das alte.

Was ein Kiosk-Token **nicht** kann: Einstellungen, Benutzer, Geräte, Protokoll, andere
Zonen, Scharfschalten. Es antwortet dort mit `401`.

Zwei Dinge, die man wissen sollte:

- **Wer das Tablet in der Hand hat, kann das Token auslesen.** Es steht im Cookie des
  Browsers. Das ist der Preis eines Lesezeichens statt einer Anmeldung; abgefedert wird es
  durch den engen Rechtesatz und dadurch, dass sich das Token jederzeit widerrufen lässt.
- **Im Trockenlauf bewegt auch das Tablet nichts.** Ein dort verstellter Sollwert ändert
  die Entscheidung, aber solange nicht scharf geschaltet ist, bewegt er kein Ventil.

## 8. Passkeys

Ein Passkey ersetzt das Passwort: Der geheime Teil verlässt das Gerät nie, und er lässt
sich nicht auf einer nachgemachten Seite eingeben — er gilt nur für den Hostnamen, für den
er angelegt wurde. Das ist der Unterschied, auf den es ankommt; ein Passwort können Sie
verraten, einen Passkey nicht.

```dotenv
THERMOCTL_PASSKEY_RP_ID=heizung.example.org
```

Mehr braucht es nicht. Ohne diese Angabe sind Passkeys abgeschaltet, und die Anmeldeseite
bietet sie gar nicht erst an — statt eine Schaltfläche zu zeigen, die nichts tun kann.

Drei Dinge, die dabei oft übersehen werden:

- **Die Angabe ist der nackte Hostname**, ohne `https://` und ohne Port. Sie wird
  absichtlich nicht aus der Anfrage abgeleitet: Die `Host`-Kopfzeile setzt der Aufrufer,
  und eine Relying-Party-ID unter seiner Kontrolle hebt genau den Schutz auf, um den es
  geht.
- **WebAuthn verlangt HTTPS.** Die einzige Ausnahme ist `localhost`. Wer den Dienst also
  ohne TLS im Netz betreibt, bekommt keine Passkeys — und sollte ihn ohnehin nicht so
  betreiben (Abschnitt 4).
- **Weicht die Adresse vom Hostnamen ab** — etwa bei der Entwicklung auf
  `http://localhost:8000` —, muss `THERMOCTL_PASSKEY_ORIGIN` genau diese Adresse tragen.

Hinterlegt werden Passkeys nach der Anmeldung unter **`/passkeys`**, ein Gerät je Eintrag.
Legen Sie mindestens zwei an, wenn Sie sich darauf verlassen wollen: Ein verlorenes Telefon
ist sonst ein verlorener Zugang — das Passwort bleibt zwar bestehen, aber genau das wollten
Sie ja loswerden.

## 9. Die Schnittstelle ausprobieren

Unter `/docs` liegt eine Swagger-Oberfläche: jeder Weg der REST-Schnittstelle zum
Anklicken, mit **Authorize** oben rechts für das API-Token. Sie kommt vollständig aus dem
Dienst selbst und funktioniert deshalb auch ohne Internetzugang.

## 10. Benachrichtigungen

Fällt ein Sensor aus oder ist die Zigbee2MQTT-Brücke nicht mehr erreichbar, schreibt der
Dienst eine Warnung ins Log. Steht in `.env` zusätzlich eine Webhook-Adresse, geht dieselbe
Meldung als JSON dorthin:

```dotenv
THERMOCTL_NOTIFY_WEBHOOK=https://…/hooks/heizung
THERMOCTL_NOTIFY_WEBHOOK_TOKEN=…      # optional, als Authorization: Bearer
```

**Gemeldet wird der Wechsel, nicht der Zustand.** Eine Störung, die drei Tage besteht,
ergibt eine Meldung und später eine Entwarnung — nicht eine je Regelzyklus. Das ist
Absicht: Wer stündlich dieselbe Meldung bekommt, schaltet sie ab und verpasst die nächste
echte.

Antwortet der Webhook nicht, wird das protokolliert und sonst nichts. Die Regelung läuft
weiter — eine Heizungssteuerung, die stehenbleibt, weil ein Webhook hängt, ist schlimmer
als eine, die eine Meldung verliert.

## 11. Was der Dienst über Sie nach außen gibt

Nichts. `thermoctl` ruft von sich aus keinen fremden Dienst auf, sendet keine Telemetrie
und braucht kein Konto bei irgendwem. Ausgehende Verbindungen entstehen erst, wenn Sie
einen MQTT-Broker, einen Webhook oder — später — die Meross-Cloud für Ihre Steckdosen
eintragen. Alle drei sind Ihre Entscheidung und stehen in Ihrer `.env`.
