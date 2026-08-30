# MQTT: was gelesen, was gesendet und was entgegengenommen wird

Drei getrennte Dinge:

- **Lesen** — Sensordaten aus Zigbee2MQTT aufnehmen. Läuft, sobald MQTT eingeschaltet ist.
- **Senden** — den eigenen Zustand veröffentlichen und die Zonen bei Home Assistant
  anmelden. Läuft ebenfalls, sobald MQTT eingeschaltet ist — **auch im Trockenlauf.**
- **Entgegennehmen** — Sollwert und Betriebsart, die aus Home Assistant kommen. Auch das
  im Trockenlauf: Der Wunsch wird übernommen, nur bewegt sich kein Ventil.

Dass Senden und Entgegennehmen im Trockenlauf laufen, ist Absicht. Eine Zustandsmeldung
bewegt nichts, und eine Anbindung, die man erst nach dem Scharfschalten ausprobieren kann,
lässt sich genau dann nicht mehr gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre.

Gelogen wird dabei nicht: Solange die Regelung nicht scharf ist, trägt jede Zone in Home
Assistant ein `(Trockenlauf)` im Namen. Das steht an jeder Karte und verschwindet, sobald
wirklich geschaltet wird.

## 1. Lesen: Zigbee2MQTT

Konfiguriert wird über `.env` (siehe [self-hosting.md](self-hosting.md)). Abonniert werden
genau vier Topics:

| Topic | Wozu |
|---|---|
| `<basis>/bridge/devices` | Geräteliste samt Fähigkeiten — daraus entsteht die Geräteerkennung |
| `<basis>/bridge/state` | Ist die Brücke erreichbar? Ein Wechsel löst eine Meldung aus |
| `<basis>/+` | Zustandsnachricht eines Geräts (Temperatur, Feuchte, Batterie, …) |
| `<basis>/+/availability` | Erreichbarkeit eines einzelnen Geräts |

`<basis>` ist `THERMOCTL_MQTT_BASE_TOPIC`, standardmäßig `zigbee2mqtt`.

**Nicht abonniert wird `heizung/#`** — die Topics des Altsystems. Der Grund steht in
[offene-entscheidungen.md](offene-entscheidungen.md): Der Vergleichsbetrieb gehört zu
Phase 4 und bekommt dort sein eigenes Datenmodell.

Ein Gerätename mit Schrägstrich entginge dem `+`-Platzhalter. In der Anlage kommt keiner
vor; der Fall wird protokolliert statt stillschweigend verschluckt.

## 2. Senden: die eigene Struktur

Entworfen und geprüft, **noch nicht angeschlossen**. Sie behebt die drei Eigenheiten, die
die [Bestandsaufnahme](bestandsaufnahme-altsystem.md) am gewachsenen Altsystem festhält.

```
thermoctl/verfuegbarkeit                      online | offline  (Last Will)
thermoctl/zonen/<id>/zustand/ist_temperatur
thermoctl/zonen/<id>/zustand/sollwert
thermoctl/zonen/<id>/zustand/betriebsart
thermoctl/zonen/<id>/zustand/sensorzustand
thermoctl/zonen/<id>/zustand/wuerde_heizen
thermoctl/zonen/<id>/befehl/sollwert
thermoctl/zonen/<id>/befehl/betriebsart
```

Drei Entscheidungen darin, jede mit Grund:

- **Kein `/get`-Suffix an Zustands-Topics.** Das Altsystem hängt es an; es liest sich wie
  eine Aufforderung, ist aber eine Aussage.
- **`zustand` und `befehl` in getrennten Teilbäumen.** Ein Abonnent, der den Zustandsbaum
  hört, bekommt nie seine eigenen Befehle zurück — der klassische Weg in eine
  Rückkopplung.
- **Im Topic steht die Kennung der Zone, nicht ihr Name.** Namen dürfen Leerzeichen und
  Umlaute enthalten (`Über Küche`) und sich ändern; ein Topic, das sich beim Umbenennen
  ändert, verliert alle Abonnenten. Der Name steht in der Discovery-Nutzlast, wo er
  hingehört.

Das Präfix `thermoctl` ist einstellbar, damit zwei Instanzen an einem Broker möglich sind.

## 3. Home-Assistant-Discovery

Je Zone eine Nachricht auf `homeassistant/climate/thermoctl_zone_<id>/config`, die auf die
Topics oben zeigt. Alle Zonen erscheinen als ein Gerät `thermoctl` mit je einer
Climate-Entität.

**Die Abmeldung gehört dazu**: Eine gelöschte Zone bekommt eine leere Nutzlast auf
demselben Config-Topic. Ohne sie bleibt sie in Home Assistant als Leiche stehen — der Teil,
den man beim ersten Bauen vergisst.

## 4. Was Home Assistant bekommt

**Eine Climate-Entität je Zone — nicht je Gerät.** Alle Zonen erscheinen als ein einziges
Gerät namens `thermoctl` mit je einem Thermostat darunter. Die einzelnen Ventile, Sensoren
und Fensterkontakte meldet `thermoctl` *nicht* an: Die stehen in Home Assistant ohnehin
schon, wenn es an demselben Broker hängt — Zigbee2MQTT meldet sie selbst an. Zweimal
dasselbe Gerät anzumelden brächte nur zwei Einträge, die sich widersprechen können.

Je Zone trägt der Thermostat:

| In Home Assistant | Kommt von / geht nach |
|---|---|
| Ist-Temperatur | `<präfix>/zonen/<id>/zustand/ist_temperatur` |
| Soll-Temperatur (lesen und **setzen**) | `.../zustand/sollwert`, `.../befehl/sollwert` |
| Betriebsart `auto`/`heat`/`off` (lesen und **setzen**) | `.../zustand/betriebsart`, `.../befehl/betriebsart` |
| Anzeige „heizt / bereit" | `.../zustand/wuerde_heizen` |
| Erreichbarkeit | `<präfix>/verfuegbarkeit` |

Grenzen: 1 bis 35 °C, Schrittweite 0,5 K. Sie stehen in der Discovery-Nutzlast **und** in
der Domäne — Home Assistant zeigt sie an, abgewiesen wird an derselben Stelle wie ein
Klick in der Oberfläche.

Was ein eingehender Befehl bewirkt:

- **Soll-Temperatur** → eine Übersteuerung mit fester Temperatur, wie ein „Übersteuern"
  auf der Startseite. Nicht der hinterlegte Sollwert des Modus: Wer am Thermostat dreht,
  meint „jetzt", nicht „ab jetzt immer".
- **Betriebsart** → die Betriebsart der Zone (`auto`, `manual`, `off`).

Beides läuft über dieselben Domänenfunktionen wie Oberfläche, REST und MCP und steht mit
der Quelle `system` im Audit-Protokoll — niemand hat sich dafür angemeldet, und das soll
dort auch so dastehen.

## 5. Die beiden Riegel — und was sie *nicht* sperren

Sie gelten dem **Schalten**, nicht dem Melden:

1. **Beim Bau des Clients**, aus `setting.control_armed` gelesen — einmal, beim Start.
2. **Bei jedem Schaltbefehl**, ebenfalls aus `setting.control_armed`
   (`integrations/aktoren.py`).

Der zweite wirkt sofort, der erste erst nach einem Neustart. Wer die Anlage im laufenden
Betrieb scharf schaltet, hat bis dahin einen Zustand, in dem scharf entschieden und
trotzdem kein Ventil bewegt wird; die Betriebsseite sagt das ausdrücklich.

Zustandsmeldungen und die Home-Assistant-Anmeldung gehen an beiden vorbei — sie bewegen
nichts. Der Parameter am Client heißt deshalb `schaltet` und beschreibt, was eine Nachricht
**bewirkt**, nicht wie dringend ein Aufrufer sie meint.

## 6. Abgemeldet wird nur, was es nicht mehr gibt

Eine gelöschte Zone bekommt die leere Nutzlast auf ihrem Config-Topic. Der Wechsel zurück
in den Trockenlauf meldet dagegen **nicht** ab — er benennt nur um. Abmelden und
Neuanmelden bei jedem Umschalten ließe die Entität in Home Assistant kurz verschwinden, und
Verlaufsdaten und Automatisierungen liefen dort ins Leere.
