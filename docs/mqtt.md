# MQTT: was gelesen wird und was einmal gesendet werden soll

Zwei getrennte Dinge, die oft verwechselt werden:

- **Lesen** — was `thermoctl` heute tut: Sensordaten aus Zigbee2MQTT aufnehmen.
- **Senden** — was es tun *wird*: den eigenen Zustand veröffentlichen, damit Home Assistant
  die Zonen findet. **Das ist entworfen, aber abgeschaltet.** Solange der Regelkreis nicht
  scharf ist, veröffentlicht der Dienst nichts.

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

## 4. Was noch fehlt

Das Senden selbst. Es wartet auf Phase 4, aus einem Grund, der keine Formalie ist: Solange
der Dienst im Schattenbetrieb läuft, würde eine veröffentlichte Sollwertänderung aus Home
Assistant eine Erwartung wecken, die er nicht erfüllt — die Zone bekäme einen neuen
Sollwert und würde trotzdem nicht heizen.

Wenn es so weit ist, sind es zwei Schritte: die Funktionen aus
`thermoctl/integrations/mqtt/veroeffentlichung.py` an den Client hängen, und den
Last-Will-Eintrag beim Verbinden setzen. Beide sind bereits getestet.
