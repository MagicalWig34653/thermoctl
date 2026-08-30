# MCP-Server

Der dritte Adapter über derselben Domänenlogik, neben der Weboberfläche und der
[REST-Schnittstelle](api.md). Er stellt Zonen, Geräte, Zeitpläne, Sollwerte samt
Begründung und die Schattenentscheidungen bereit und kann übersteuern — **geschaltet wird
nichts.**

Installation mit der optionalen Abhaengigkeit:

```console
pip install 'thermoctl[mcp]'
```

Ein MCP-Client kann den Server beispielsweise so ueber stdio starten:

```json
{
  "mcpServers": {
    "thermoctl": {
      "command": "/pfad/zu/thermoctl-mcp",
      "env": {
        "THERMOCTL_DATABASE_URL": "sqlite:////pfad/zur/thermoctl.db",
        "THERMOCTL_SECRET_KEY": "<mindestens-32-zeichen-langes-geheimnis>",
        "THERMOCTL_MCP_TOKEN": "<ausgestelltes-api-token>"
      }
    }
  }
}
```

Das Token muss die Rechte der verwendeten Werkzeuge tragen. Ohne
`THERMOCTL_MCP_TOKEN` verweigert der Server den Start. Token und Datenbankadresse im
Beispiel sind ausschliesslich Platzhalter.

## Die Werkzeuge

Neun Stück, alle über dieselbe Domänenlogik wie Oberfläche und REST-Schnittstelle. Jedes
prüft dasselbe Recht wie der entsprechende REST-Endpunkt.

| Werkzeug | Recht | Was es liefert |
|---|---|---|
| `zonen_auflisten()` | `zone.read` | Name, Anzeigename und Betriebsart je sichtbarer Zone |
| `zonenzustand(zone_id)` | `zone.read` | Ist-Temperatur, Messzeitpunkt, Sensorzustand |
| `sollwert_erklaeren(zone_id)` | `zone.read` | Sollwert **und Begründung**, aus derselben Funktion, die auch regelt |
| `zeitplan_lesen(zone_id)` | `zone.read` | Schaltpunkte mit Wochentag, Minute im Tag und Modusnamen |
| `sollwerte_lesen(zone_id)` | `zone.read` | die gesetzte Temperatur je Modus |
| `geraete_auflisten()` | `device.read` | Anbindung, Fähigkeiten, letzte Nachricht, Batterie |
| `schattenentscheidungen(zone_id, anzahl=10)` | `zone.read` | die jüngsten Entscheidungen samt Grund |
| `uebersteuern(zone_id, temperatur_c, endet_am)` | `override.create` | legt eine Übersteuerung an |
| `uebersteuerung_aufheben(zone_id)` | `override.cancel` | beendet die laufende Übersteuerung |
| `steuerung_lesen()` | `zone.read` | Betriebszustand und globale Vorgaben |
| `trockenlauf_erzwingen(begruendung)` | `control.arm` | nimmt die Regelung in den Trockenlauf zurück |
| `zeitplanpunkt_verschieben(zone_id, punkt_id, wochentag, minute)` | `schedule.manage` | setzt einen Punkt auf einen anderen Zeitpunkt |

Eine nicht sichtbare Zone wird wie eine unbekannte behandelt — die Antwort verrät nicht,
dass es sie gibt.

**Der eigentliche Gewinn sind `sollwert_erklaeren` und `schattenentscheidungen`.** Sie
beantworten „warum ist es hier kalt?" in einem Aufruf, statt Ist-Wert, Zeitplan und
Regelentscheidung von Hand zusammenzusuchen.

## Was es bewusst nicht gibt

**Kein Werkzeug schaltet die Anlage scharf.** `trockenlauf_erzwingen` gibt es, die
Gegenrichtung nicht — obwohl die Oberfläche und die REST-Schnittstelle sie können.

Der Grund: Die Domäne verlangt beim Scharfschalten eine Begründung, und für ein
Sprachmodell ist das keine Hürde, sondern genau die Sorte Text, die es mühelos erzeugt. Die
Sperre wäre hier eine Formalie statt einer Entscheidung. Zurück in den Trockenlauf ist
dagegen immer die sichere Richtung und soll jedem offenstehen, der die Anlage bedienen darf.
Wer scharf schalten will, tut das dort, wo ein Mensch am Knopf steht. Nachzulesen in
[offene-entscheidungen.md](offene-entscheidungen.md).

Schreibende Werkzeuge für die Konfiguration — Zonen, Sollwerte, Regelparameter — gibt es
ebenfalls nicht. Je weniger ein Assistent ungefragt ändern kann, desto besser. Wer eine
Zone umbaut, tut das in der Oberfläche oder über die REST-Schnittstelle, wo eine Person
daneben sitzt.

Beim Zeitplan gibt es eine Ausnahme: `zeitplanpunkt_verschieben` ändert einen **schon
vorhandenen** Punkt, legt aber keinen an und löscht keinen. „Verschieb die Morgenheizung
im Bad eine halbe Stunde nach hinten" ist eine alltägliche Bitte, und der Umfang des
Schadens ist auf einen Zeitpunkt begrenzt.

Die Grenzen für Übersteuerungen (1 bis 35 °C, eine Nachkommastelle) prüft die Domäne, nicht
der Adapter. Ein Werkzeug, das `temperatur_c=99` schickt, bekommt einen Fehler — und zwar
denselben wie die Oberfläche.
