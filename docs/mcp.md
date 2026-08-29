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

Eine nicht sichtbare Zone wird wie eine unbekannte behandelt — die Antwort verrät nicht,
dass es sie gibt.

**Der eigentliche Gewinn sind `sollwert_erklaeren` und `schattenentscheidungen`.** Sie
beantworten „warum ist es hier kalt?" in einem Aufruf, statt Ist-Wert, Zeitplan und
Regelentscheidung von Hand zusammenzusuchen.

## Was es bewusst nicht gibt

**Kein Werkzeug schaltet etwas.** Der Trockenlauf gilt hier wie überall.

Schreibende Werkzeuge für die Konfiguration — Zonen, Zeitpläne, Sollwerte, Regelparameter —
gibt es ebenfalls nicht. Für den täglichen Bedarf genügt `uebersteuern`, und je weniger ein
Assistent ungefragt ändern kann, desto besser. Wer eine Zone umbaut, tut das in der
Oberfläche oder über die REST-Schnittstelle, wo eine Person daneben sitzt.

Die Grenzen für Übersteuerungen (5 bis 35 °C, eine Nachkommastelle) prüft die Domäne, nicht
der Adapter. Ein Werkzeug, das `temperatur_c=99` schickt, bekommt einen Fehler — und zwar
denselben wie die Oberfläche.
