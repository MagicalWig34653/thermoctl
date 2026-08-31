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
| `list_zones()` | `zone.read` | Name, Anzeigename, Betriebsart und Sonnenprofil je sichtbarer Zone |
| `zone_state(zone_id)` | `zone.read` | Ist-Temperatur, Messzeitpunkt, Sensorzustand |
| `explain_setpoint(zone_id)` | `zone.read` | Sollwert **und Begründung**, aus derselben Funktion, die auch regelt |
| `read_schedule(zone_id)` | `zone.read` | Schaltpunkte mit Wochentag, Minute im Tag und Modusnamen |
| `read_setpoints(zone_id)` | `zone.read` | die gesetzte Temperatur je Modus |
| `list_devices()` | `device.read` | Anbindung, Fähigkeiten, letzte Nachricht, Batterie |
| `shadow_decisions(zone_id, count=10)` | `zone.read` | die jüngsten Entscheidungen samt Grund |
| `override(zone_id, temperature_c, ends_at)` | `override.create` | legt eine Übersteuerung an |
| `cancel_override(zone_id)` | `override.cancel` | beendet die laufende Übersteuerung |
| `boost(zone_id)` | `override.create` | zieht die nächste Schaltung vor |
| `read_control_parameters(zone_id)` | `zone.read` | wirksame Regelparameter **samt ihrer Grenzen** |
| `set_control_parameters(zone_id, name, value)` | `zone.manage` | setzt einen Parameter, lässt die übrigen |
| `read_control()` | `zone.read` | Betriebszustand, globale Vorgaben und die Sonnenabsenkung (an/aus, Standort, Vorlauf, Obergrenze) |
| `force_dry_run(reason)` | `control.arm` | nimmt die Regelung in den Trockenlauf zurück |
| `move_schedule_point(zone_id, point_id, weekday, minute)` | `schedule.manage` | setzt einen Punkt auf einen anderen Zeitpunkt |

Eine nicht sichtbare Zone wird wie eine unbekannte behandelt — die Antwort verrät nicht,
dass es sie gibt.

**`boost` ist für ein Sprachmodell die verlässlichere Form von „mach es hier wärmer".** Es
muss weder eine Temperatur noch eine Dauer raten, und nach dem Schaltpunkt räumt sich der
Eingriff selbst weg. `override` bleibt daneben für den Fall, dass jemand eine bestimmte
Temperatur nennt.

**`read_control_parameters` liefert die Grenzen mit.** Ohne sie wäre jeder Schreibversuch ein
Versuch: „0,05 Kelvin Hysterese" sieht für ein Modell so plausibel aus wie „0,5".
`set_control_parameters` verlangt `zone.manage` und nicht `override.create` — ein
Regelparameter wirkt dauerhaft und auf jede künftige Entscheidung, eine Übersteuerung nur
bis zum nächsten Schaltpunkt.

**Der eigentliche Gewinn sind `explain_setpoint` und `shadow_decisions`.** Sie
beantworten „warum ist es hier kalt?" in einem Aufruf, statt Ist-Wert, Zeitplan und
Regelentscheidung von Hand zusammenzusuchen.

## Was es bewusst nicht gibt

**Kein Werkzeug schaltet die Anlage scharf.** `force_dry_run` gibt es, die
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

Beim Zeitplan gibt es eine Ausnahme: `move_schedule_point` ändert einen **schon
vorhandenen** Punkt, legt aber keinen an und löscht keinen. „Verschieb die Morgenheizung
im Bad eine halbe Stunde nach hinten" ist eine alltägliche Bitte, und der Umfang des
Schadens ist auf einen Zeitpunkt begrenzt.

Die Grenzen für Übersteuerungen (−20 bis 35 °C, eine Nachkommastelle) prüft die Domäne, nicht
der Adapter. Ein Werkzeug, das `temperature_c=99` schickt, bekommt einen Fehler — und zwar
denselben wie die Oberfläche.
