# Teilprojekt 2 — Geräte-Anbindung im Schattenbetrieb

Stand: 2026-08-29. Konkretisiert Phase 2 der [Roadmap](../../roadmap.md). Bei Widerspruch
gilt der [Rahmenentwurf](2026-08-28-thermoctl-neubau-design.md).

## 1. Ziel und Grenze

**Ziel:** Belegen, dass Gerätedaten und Adressierung stimmen — mit echten Daten der Anlage,
ohne die Heizung anzufassen. Am Ende laufen Ist-Temperaturen aller Zonen fortlaufend ein,
und ein Schattenprotokoll zeigt für jeden Zyklus, **was geschaltet würde und warum**.

**Harte Grenze — Trockenlauf.** In dieser Phase wird gelesen und protokolliert, nie
geschaltet. Der Dienst veröffentlicht in Phase 2 **keine einzige** MQTT-Nachricht und ruft
**keine** schaltende Fremd-API auf. Das ist keine Absichtserklärung, sondern eine
abgesicherte Eigenschaft:

- Ein globaler Schalter `setting.control_armed` steht auf `false` und wird in dieser Phase
  nirgends auf `true` gesetzt. Er ist die Rückfallebene für Phase 4, nicht für jetzt.
- Jeder Aktor-Adapter prüft ihn als Erstes und protokolliert stattdessen, was er getan
  hätte.
- Der MQTT-Client verweigert `publish()` grundsätzlich, solange `control_armed` false ist —
  auch, wenn ein Aufrufer es verlangt. Ein Test hält das nach
  (`test_kein_publish_im_trockenlauf`).

Grundsatz 7 aus CLAUDE.md: Der Dienst steuert eine echte Wohnung. Die Absicherung gehört
in den Code, nicht in die Erinnerung des Umsetzenden.

## 2. Woher die Wahrheit über das Nutzlastformat kommt

`.superpowers/sdd/anlage-beispiele.json` enthält echte Daten der Anlage: 35 Gerätenamen,
10 Zustandsnachrichten im Original, 40 Topics des Altsystems. **Das Format wird gegen diese
Datei gebaut, nicht gegen Vermutungen** und nicht gegen die Bestandsaufnahme, die es aus
zweiter Hand beschreibt.

Die Datei wird als Testdatum nach `tests/daten/anlage-beispiele.json` kopiert, damit die
Testsuite nicht auf ein Verzeichnis außerhalb des Pakets zugreift.

Was die echten Daten zeigen, und was daraus folgt:

| Beobachtung | Folge für die Umsetzung |
|---|---|
| Felder sind je Gerätetyp völlig verschieden; kein Feld kommt überall vor | Auswertung feldweise und tolerant, nie „Nachricht hat Schema X" |
| `last_seen` ist ISO 8601 mit `Z` (`2026-08-29T06:43:58.479Z`) | Messzeitpunkt daraus, nicht die Empfangszeit; fehlt es, gilt die Empfangszeit |
| Werte können `null` sein (`effect_color`, `led_indication`, `motion_sensitivity`) | `null` heißt „kein Messwert", nicht 0 |
| `battery` ist mal `100`, mal `10.5`, mal `95.5` | Prozent als Dezimalzahl, keine Ganzzahl-Annahme |
| `voltage` ist `2900` (mV, Batteriegerät) **und** `230` (V, Netzsteckdose) | Aus `voltage` wird **nichts** abgeleitet — es wird nur roh abgelegt |
| Verschachtelte Objekte: `update`, `color` | Werden übersprungen, nicht ausgepackt |
| Sechs von zehn Geräten liefern `temperature`, auch Lampen­sensoren und ein Pflanzensensor | Nicht jedes Gerät mit `temperature` ist eine taugliche Raumquelle — die Zuordnung entscheidet der Mensch, nicht die Heuristik |
| `state` ist `"ON"`/`"OFF"` als Zeichenkette | Als Text ablegen, Auswertung an einer Stelle |
| In der Geräteliste stehen `bridge`, `linos_zimmer`, `wohnzimmer` | Kein Gerät: Brücke und Gruppen werden aussortiert |
| Gerätenamen enthalten Leerzeichen und Umlaute (`Über Küche`, `Bad Thermostat Heizköper`) | Namen sind Fremdschlüssel-Text, nie Bezeichner; UTF-8 durchgängig |

Was die Beispiele **nicht** enthalten: eine Zustandsnachricht eines Heizkörperventils
(`local_temperature`, `current_heating_setpoint`) und eines Fensterkontakts (`contact`).
Beide Gerätetypen existieren in der Anlage laut Namensliste. Die Auswertung muss sie
deshalb aus der Gerätebeschreibung der Brücke (`bridge/devices`, Feld `definition.exposes`)
erkennen und darf sich nicht darauf stützen, das Feld schon einmal gesehen zu haben.

## 3. Aufbau

```
thermoctl/
  domain/
    beobachtung.py     Nutzlast → Beobachtungen. Rein, ohne Datenbank, ohne MQTT.
    geraeteklassen.py  Gerätebeschreibung → Fähigkeiten. Rein.
    regelung.py        Regelentscheidung samt Begründung. Rein.
    stoerung.py        Sensor-Timeout und Störungszustände. Rein.
  integrations/
    mqtt/
      client.py        Verbindung, TLS, Wiederverbindung, Abonnements.
      zigbee2mqtt.py   Topic-Zuschnitt: welches Topic bedeutet was.
    aktoren.py         Aktor-Schnittstelle und die beiden Adapter, im Trockenlauf.
  services/
    ingest.py          Beobachtung → Datenbank.
    schattenlauf.py    Der periodische Lauf, der das Schattenprotokoll schreibt.
    aufbewahrung.py    Alte Messwerte wegräumen.
  web/geraete_views.py Geräteübersicht, lesend.
```

Die vier Module unter `domain/` sind **rein**: keine Netzverbindung, keine Uhr, kein
FastAPI. Zeitpunkte werden hineingereicht. Das ist der Grund, warum die Regelentscheidung
erschöpfend testbar ist — und der Grund, warum Phase 4 sie unverändert übernehmen kann.
`tests/test_architektur.py` hält die Trennung nach.

## 4. Datenmodell

Eine einzige Alembic-Migration für die ganze Phase (Migrationen vertragen keine
Parallelität, siehe CLAUDE.md).

### `measurement` — Messwert-Historie

| Spalte | Typ | Anmerkung |
|---|---|---|
| `id` | Integer PK | |
| `device_id` | FK `device.id` ON DELETE CASCADE | |
| `capability_id` | FK `device_capability.id` | *was* gemessen wurde |
| `value_numeric` | `Numeric(12,3)` nullable | Zahlwerte |
| `value_text` | `String(32)` nullable | `"ON"`, `"OFF"`, `"online"` |
| `measured_at` | DateTime, indiziert | aus `last_seen`, sonst Empfangszeit |
| `received_at` | DateTime | wann der Dienst es sah |

CHECK: genau eine der beiden Wertspalten ist gesetzt. Index über
`(device_id, capability_id, measured_at)` — die Abfrage „letzter Wert je Gerät und
Fähigkeit" ist die einzige, die im Betrieb häufig läuft.

*Verworfen:* eine Spalte je physikalischer Größe (bricht bei jedem neuen Gerätetyp) und
eine JSON-Spalte (Grundsatz 3).

### `zone_state` — der aktuelle Zustand je Zone

`zone_id` PK/FK, `temperature_c Numeric(5,2)` nullable, `measured_at` nullable,
`sensor_status_id` FK auf die neue Nachschlagetabelle `sensor_status`, `window_open`
Boolean nullable, `updated_at`.

Abgeleitet und jederzeit neu berechenbar — aber abgelegt, weil die Übersichtsseite sonst
je Zone über die Historie aggregieren müsste, und weil Phase 4 den Wert braucht, den die
Entscheidung tatsächlich gesehen hat.

### `device_health` — Lebenszeichen je Gerät

`device_id` PK/FK, `last_payload_at`, `link_quality` Integer nullable, `battery_percent`
`Numeric(5,2)` nullable, `availability` `String(16)` nullable, `payload_count` Integer.

### `shadow_decision` — das Schattenprotokoll

| Spalte | Anmerkung |
|---|---|
| `id`, `decided_at` (indiziert) | |
| `zone_id` FK | |
| `temperature_c`, `setpoint_c` `Numeric(5,2)` nullable | was die Entscheidung sah |
| `setpoint_reason` `String(255)` | aus `aufgeloester_sollwert()` |
| `would_heat` Boolean | die Entscheidung |
| `previous_would_heat` Boolean nullable | um Wechsel zu erkennen |
| `outcome_code` `String(32)` | `heizen`, `aus`, `unveraendert`, `gesperrt_mindestdauer`, `fenster_offen`, `frostschutz_sensorausfall`, `keine_quelle` |
| `reason` `String(255)` | ein Satz in Klartext |

Grundsatz 5: Wer Wochen später fragt, warum um 3 Uhr nicht geheizt wurde, muss es aus
dieser Zeile beantworten können, ohne den Code zu lesen.

### Erweiterungen an Bestehendem

- `setting`: `+ control_armed Boolean NOT NULL DEFAULT false`,
  `+ measurement_retention_days Integer NOT NULL DEFAULT 30`,
  `+ shadow_interval_seconds Integer NOT NULL DEFAULT 60`.
- Nachschlagetabelle `sensor_status`: `ok`, `veraltet`, `keine_quelle`.
- `device_capability` bekommt weitere Zeilen: `humidity`, `illuminance`, `occupancy`,
  `link_quality`, `power`, `energy`, `valve_position`, `setpoint`, `availability`.
  Die bestehenden fünf bleiben unverändert.
- `device`: `+ is_group Boolean NOT NULL DEFAULT false` (Zigbee2MQTT-Gruppen wie
  `wohnzimmer` erscheinen in derselben Liste, sind aber keine Geräte).

## 5. MQTT

### Konfiguration (Grundsatz 1 und 2)

Neu in `Settings`, alles aus der Umgebung, nichts im Repo:
`mqtt_enabled` (bool, Standard `false`), `mqtt_host`, `mqtt_port` (1883),
`mqtt_tls` (bool), `mqtt_username`, `mqtt_password` (`SecretStr`), `mqtt_client_id`,
`mqtt_base_topic` (Standard `zigbee2mqtt`), `mqtt_ca_cert` (Pfad, optional).

`mqtt_enabled` steht standardmäßig auf `false`: Die Testsuite und ein frisch gebauter
Container dürfen nicht versuchen, irgendwohin eine Verbindung aufzubauen.

### Abonnements

| Topic | Bedeutung |
|---|---|
| `<basis>/bridge/devices` (retained) | Geräteliste samt `definition.exposes` → Erkennung |
| `<basis>/bridge/state` | Brücke erreichbar |
| `<basis>/+` | Zustandsnachricht eines Geräts |
| `<basis>/+/availability` | Erreichbarkeit eines Geräts |

`<basis>/+` trifft auch `<basis>/bridge` und Gruppennamen; beides wird beim Zuschnitt
aussortiert, nicht beim Abonnieren. Ein Gerätename mit `/` würde `+` entgehen — in der
Anlage kommt keiner vor; der Fall wird protokolliert, nicht stillschweigend verschluckt.

**Nicht abonniert wird `heizung/#`**, das Altsystem. Begründung in
[offene-entscheidungen.md](../../offene-entscheidungen.md).

### Verbindung

Wiederverbindung mit wachsendem Abstand (1 s, verdoppelnd, höchstens 60 s), Endlosversuch —
ein Heizungsdienst, der nach fünf Fehlversuchen aufgibt, ist im Winter wertlos. Jeder
Verbindungsverlust und jede Wiederverbindung wird protokolliert, das Passwort nie
(`thermoctl/logging.py` maskiert ohnehin, der Client legt es zusätzlich nie in ein
`extra=`-Feld).

## 6. Regelentscheidung

Vorgezogen aus Phase 4, weil das Schattenprotokoll sonst nichts zu protokollieren hätte.
Sie wird gebaut und erschöpfend getestet — **scharf geschaltet wird sie nicht.**

```python
@dataclass(frozen=True)
class Lage:          # alles, was die Entscheidung sieht
    ist_c: Decimal | None
    soll_c: Decimal
    soll_grund: str
    betriebsart: str            # auto | manual | off
    heizt_gerade: bool
    seit_s: int | None          # wie lange der aktuelle Zustand schon gilt
    fenster_offen: bool
    fenster_zu_seit_s: int | None
    sensor_status: str          # ok | veraltet | keine_quelle
    parameter: Regelparameter

@dataclass(frozen=True)
class Entscheidung:
    heizen: bool
    grund_code: str
    grund: str
```

Rangfolge, von oben nach unten — die erste zutreffende Regel gewinnt:

1. **Sensorausfall** (`veraltet` oder `keine_quelle`): nicht heizen ist falsch (Wohnung
   kühlt aus), voll heizen ist falsch (Überhitzung ohne Rückmeldung). Es gilt der
   Frostschutz-Sollwert; ohne Ist-Wert heißt das: aus. Ausdrücklich protokolliert.
2. **Betriebsart `off`**: Frostschutz-Sollwert, sonst normale Regel. „Aus" heißt
   Frostschutz, nicht stromlos.
3. **Fenster offen**: aus.
4. **Wiederanlaufverzögerung**: Fenster ist zu, aber noch keine
   `window_resume_delay_seconds` — noch aus.
5. **Mindestschaltdauer**: der aktuelle Zustand gilt kürzer als `min_on_seconds`
   beziehungsweise `min_off_seconds` — Zustand bleibt, egal was die Hysterese sagt.
6. **Hysterese:** heizt gerade nicht und `ist < soll - h` → an. Heizt gerade und
   `ist > soll + h` → aus. Sonst unverändert.

Punkt 6 ist der Defekt des Altsystems in einer Zeile: dort steht `if ist < soll: an, sonst
aus` — am Sollwert schaltet das Ventil in jedem Zyklus um. Ein Test führt genau diesen
Fall vor und belegt, dass `thermoctl` es nicht tut.

Der `temperature_offset_k` der Zone wird vor der Regel auf den Ist-Wert gerechnet: die
Kalibrierung des Sensors ist eine Eigenschaft der Messung, keine der Regel.

## 7. Störungserkennung

Eine Zone gilt als `veraltet`, wenn ihr jüngster Temperatur-Messwert älter ist als
`sensor_timeout_seconds` (Zone, sonst globaler Standard). Ohne zugeordnete Messquelle gilt
`keine_quelle`. Beides ist ein Zustand, kein Logeintrag, der untergeht — die Übersicht
zeigt ihn, das Schattenprotokoll begründet damit.

Ausbleibende Messwerte sind der wahrscheinlichste reale Fehler (leere Batterie, Gerät
außer Reichweite). Im Altsystem war ein alter Wert von einem frischen nicht zu
unterscheiden.

## 8. Aktoren im Trockenlauf

Eine gemeinsame Schnittstelle, zwei Adapter:

```python
class Aktor(Protocol):
    def beschreibung(self) -> str: ...
    async def schalten(self, ein: bool) -> Schaltergebnis: ...
```

- **Zigbee-Ventil** über `<basis>/<name>/set`.
- **Meross-Schalter** über die Cloud-API.

Beide sind vollständig gebaut — Adressbildung, Nutzlast, Fehlerbehandlung — und geben im
Trockenlauf `Schaltergebnis(ausgefuehrt=False, …)` zurück, samt der Nachricht, die sie
gesendet hätten. Genau das steht dann im Protokoll und ist bei der Abnahme prüfbar.

Der Meross-Adapter bringt **keine neue Abhängigkeit** ins Projekt: Er spricht die
HTTP-Schnittstelle selbst an und ist ohne hinterlegte Zugangsdaten schlicht „nicht
konfiguriert". Begründung in [offene-entscheidungen.md](../../offene-entscheidungen.md).

## 9. Oberfläche und API

- **`/geraete`** — lesend: Name, Anbindung, Fähigkeiten, letzte Nachricht, Batterie,
  Funkgüte, Zuordnung. Sichtbar mit `device.read`.
- Die **Startseite** bekommt je Zone Ist, Soll, Sensorzustand und die letzte
  Schattenentscheidung.
- **`GET /api/v1/devices`** und **`GET /api/v1/zones/{id}/state`**, beide lesend.

Nach jedem sichtbaren Teilschritt wird die Anwendung wirklich gestartet und die Seite
geöffnet. Zweimal ist genau dort ein grundlegender Fehler durchgerutscht.

## 10. Fertig, wenn

- Ist-Temperaturen aller zugeordneten Zonen laufen fortlaufend ein und stehen in
  `zone_state`.
- Die Geräteerkennung findet die Geräte der echten Anlage und klassifiziert sie; Brücke
  und Gruppen sind aussortiert.
- Ein ausbleibender Messwert wird als Störung sichtbar, nicht als alter Wert.
- Das Schattenprotokoll zeigt je Zone und Zyklus eine nachvollziehbare Entscheidung.
- **Nichts wurde geschaltet** — belegt durch Tests und durch ein leeres Schaltprotokoll.
- Beide Datenbanken grün, Abdeckung über der Schwelle, Ruff und mypy ohne Befund.

Was diese Phase **nicht** abschließen kann: der Nachweis über mehrere Tage echten Betriebs.
Der braucht Laufzeit an der Anlage und gehört dem Projektinhaber.
