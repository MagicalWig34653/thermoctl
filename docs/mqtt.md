# MQTT: was gelesen, was gesendet und was entgegengenommen wird

Drei getrennte Dinge:

- **Lesen** — Sensordaten aus Zigbee2MQTT aufnehmen. Läuft, sobald MQTT eingeschaltet ist.
- **Senden** — den eigenen Zustand veröffentlichen und die Zonen bei Home Assistant
  anmelden. Läuft ebenfalls, sobald MQTT eingeschaltet ist — **auch im Trockenlauf.**
- **Entgegennehmen** — Sollwert, Betriebsart, Boost, die Solltemperatur je Modus und die
  Regelparameter, die aus Home Assistant kommen. Auch das im Trockenlauf: Der Wunsch wird
  übernommen, nur bewegt sich kein Ventil.

Dass Senden und Entgegennehmen im Trockenlauf laufen, ist Absicht. Eine Zustandsmeldung
bewegt nichts, und eine Anbindung, die man erst nach dem Scharfschalten ausprobieren kann,
lässt sich genau dann nicht mehr gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre.

Gelogen wird dabei nicht: Ob wirklich geschaltet wird, sagt eine eigene Entität für den
ganzen Dienst — der `binary_sensor` **Regelung scharf**.

Bis August 2026 stand der Trockenlauf stattdessen als `(Trockenlauf)` im *Namen* jeder
Zone. Das war gut sichtbar und genau deshalb falsch: Home Assistant leitet die
Entitätskennung beim ersten Auftauchen aus dem Namen ab, und eine Zone, die zuerst im
Trockenlauf erschien, hieß danach für immer `climate.thermoctl_zone_1_trockenlauf` — auch
scharf geschaltet. Die Kennung bleibt jetzt über den ganzen Umstieg dieselbe; die
Discovery-Nutzlast ist im Trockenlauf und scharf **Byte für Byte gleich**.

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

### Bediengeräte: Tastendrücke

Ein Bediengerät an der Wand — etwa ein Aqara W100 — schickt seine Tastendrücke als Feld
`action` in derselben Zustandsnachricht. Der Dienst legt jeden davon als Messwert ab und
führt aus, was für diese Taste **belegt** ist.

**Die Belegung steht in der Datenbank, nicht im Quelltext.** Wie ein Gerät seine Tasten
nennt, entscheidet Zigbee2MQTT je Modell: der eine schickt `single_plus`, der nächste
`button_1_single`, der übernächste `up_open`. Eine Tabelle dieser Namen im Code wäre genau
die harte Verdrahtung, gegen die dieses Projekt gebaut ist (Grundsatz 1) — und für jedes
Gerät falsch, das noch nicht darin steht.

Statt zu raten, wird zugehört: Unter *Zone → Geräte → Tastenbelegung* steht, welche
Aktionen dieses Gerät **wirklich** geschickt hat. Wer ein neues Modell anschließt, drückt
einmal jede Taste, lädt die Seite neu und ordnet zu, was er sieht. Ein Datenblatt braucht
dafür niemand.

Belegbar sind fünf Dinge — bewusst wenige, weil man an einem Knopf im Vorbeigehen nicht
sieht, was man tut: **Wärmer**, **Kälter** (Schrittweite je Taste einstellbar, Standard
0,5 K), **nächste Schaltung vorziehen**, **Betriebsart Aus**, **Betriebsart Automatik**.
Wärmer und kälter verstellen dabei den Sollwert des Modus, der gerade gilt — dasselbe wie
der Thermostat in Home Assistant. Als Übersteuerung wäre der Wert nach dem nächsten
Schaltpunkt weg, und der Raum kühlte ohne Zutun wieder aus.

**Derselbe Tastendruck wirkt nur einmal.** Eine behaltene Nachricht wird bei jeder
Neuverbindung erneut zugestellt; ohne diesen Schutz löste ein Wackelkontakt in der
Netzverbindung denselben Druck immer wieder aus, und ein Boost, den niemand gedrückt hat,
fiele erst auf, wenn es im Raum zu warm ist. Verglichen wird der Messzeitpunkt gegen den
zuletzt verarbeiteten.

Was **nicht** gebaut ist: das Display eines Bediengeräts mit Werten aus thermoctl zu
speisen. Der W100 kann eine externe Temperatur anzeigen, aber unter welchem Schlüssel
Zigbee2MQTT sie entgegennimmt, ist ohne das Gerät in der Hand nicht zu verifizieren — und
eine geratene Nutzlast wäre eine Zusage, die niemand geprüft hat.

## 2. Senden: die eigene Struktur

Angeschlossen. Sie behebt die drei Eigenheiten, die die
[Bestandsaufnahme](bestandsaufnahme-altsystem.md) am gewachsenen Altsystem festhält.

```
thermoctl/verfuegbarkeit                          online | offline  (Last Will)
thermoctl/zustand/scharf                          true | false
thermoctl/zonen/<id>/zustand/ist_temperatur
thermoctl/zonen/<id>/zustand/sollwert
thermoctl/zonen/<id>/zustand/betriebsart
thermoctl/zonen/<id>/zustand/sensorzustand
thermoctl/zonen/<id>/zustand/wuerde_heizen
thermoctl/zonen/<id>/zustand/letzte_schaltung     ISO-8601 mit Zeitzone
thermoctl/zonen/<id>/zustand/naechste_schaltung   ISO-8601 mit Zeitzone
thermoctl/zonen/<id>/zustand/modus/<modus_id>     Solltemperatur dieses Modus
thermoctl/zonen/<id>/zustand/parameter/<name>     wirksamer Regelparameter
thermoctl/zonen/<id>/befehl/sollwert
thermoctl/zonen/<id>/befehl/betriebsart
thermoctl/zonen/<id>/befehl/boost                 zieht die nächste Schaltung vor
thermoctl/zonen/<id>/befehl/modus/<modus_id>
thermoctl/zonen/<id>/befehl/parameter/<name>
```

**Alles Bleibende geht mit dem retain-Flag hinaus** — Anmeldungen wie Zustände. Ohne das
steht in Home Assistant nach jedem Neustart eine leere Karte, bis dieser Dienst das nächste
Mal sendet. `retain` gehört dabei an das Senden und **nicht** in die Discovery-Nutzlast:
Der gleichnamige Schlüssel dort heißt in Home Assistant „sende *Befehle* mit retain-Flag",
und ein behaltener Befehl würde bei jeder Neuverbindung erneut zugestellt und erneut
ausgeführt.

**Abonniert werden zwei Muster**, `.../befehl/+` und `.../befehl/+/+`: `+` trifft in MQTT
genau eine Ebene, nie null und nie zwei. Mit nur dem ersten käme `befehl/modus/3` nie an.

Was ein Befehl bewirkt, steht in `domain/fernbedienung.py` — dieselben Funktionen, die auch
die Oberfläche benutzt, mit denselben Grenzen:

- **Sollwert** verstellt die Solltemperatur des Modus, der gerade gilt — nicht „jetzt
  gerade". Als Übersteuerung wäre der Wert nach dem nächsten Schaltpunkt wieder weg, und
  der Regler spränge scheinbar von selbst zurück. Läuft eine Übersteuerung mit fester
  Temperatur, gibt es keinen Modus, den man verstellen könnte — dann wird sie selbst
  gesetzt. Steht die Zone auf *Aus*, gilt der Frostschutz, und der Regler verstellt
  folgerichtig dessen Sollwert: Verstellt wird immer der Wert, der angezeigt wird.
- **Boost** zieht die nächste Schaltung vor. Was als Nächstes ohnehin käme, gilt ab
  sofort — und genau bis zu dem Zeitpunkt, an dem es planmäßig gekommen wäre. Danach läuft
  der Plan weiter, als wäre nichts gewesen. Ein Boost auf einen festen Wert müsste dagegen
  raten, wie warm und wie lange.
- **Regelparameter** werden als Zonenabweichung festgeschrieben. Eine `number`-Entität kann
  nicht leer sein, es gibt dort also kein „erbt vom globalen Standard"; wer die Vererbung
  zurückwill, leert das Feld in der Oberfläche.

Je Zone entsteht in Home Assistant ein eigenes Gerät (`via_device` auf den Dienst) mit
Thermostat, Boost-Knopf, zwei Zeitstempeln, je Modus einer Solltemperatur und je
Regelparameter einer Zahleneingabe.

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

Grenzen: −20 bis 35 °C, Schrittweite 0,5 K. Sie stehen in der Discovery-Nutzlast **und** in
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
