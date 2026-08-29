# MCP-Server

Der MCP-Server stellt Zonen, Geraete, Zeitplaene, Sollwerte, Sollwertbegruedungen und
Schattenentscheidungen bereit. Er kann Uebersteuerungen anlegen und aufheben, schaltet aber
keine Geraete.

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

## Werkzeuge fuer die Heizkonfiguration

`zeitplan_lesen(zone_id)` liefert die Schaltpunkte mit Wochentag, Minute im Tag und
Modusnamen. `sollwerte_lesen(zone_id)` liefert die gesetzte Temperatur je Modus. Beide
benötigen `zone.read`; eine nicht sichtbare Zone wird wie eine unbekannte behandelt.

Schreibende Werkzeuge für diese Konfiguration gibt es bewusst nicht. Für den täglichen
Bedarf genügt `uebersteuern`, und ein Assistent soll Zeitpläne oder Regelparameter nicht
ungefragt verändern können.
