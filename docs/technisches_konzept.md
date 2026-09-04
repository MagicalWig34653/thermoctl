# Technisches Konzept: Bedienung, Einrichtung und Gerätetypen

> **Status: unverbindlich.** Dieses Dokument stammt aus einem anderen Kontext und ist eine
> Ideensammlung, keine Vorgabe (Beschluss vom 2026-08-28). Es setzt Home Assistant als
> Einstiegspunkt voraus — der Rahmenentwurf hat sowohl HA als Voraussetzung als auch ein
> HA-Add-on ausdrücklich verworfen (siehe `CLAUDE.md`, „Technischer Rahmen"). **Bei
> Widerspruch gilt der technische Rahmen dort.** Vier Punkte wurden übernommen: Regelparameter
> je Zone, Fensterkontakte als Geräterolle, die Betriebsart Automatik/Manuell/Aus sowie
> Sensor-Timeout und Temperatur-Offset.

Dieses Dokument ersetzt die vorherige, stark implementierungslastige Fassung des technischen Konzepts. Es legt bewusst **keine** Programmiersprache, Frameworks, Datenbank oder Programmierschnittstellen fest – das bleibt der Umsetzung überlassen. Stattdessen beschreibt es, wie der Nutzer das System einrichtet und bedient, welche Gerätetypen unterstützt werden und wie diese den Raumobjekten aus dem [Konzept](Konzept) zugeordnet werden. Es ist die fachliche/funktionale Ergänzung zum bestehenden Konzept-Dokument.

---

## 1. Ausgangspunkt

Home Assistant, Mosquitto und Zigbee2MQTT sind bereits eingerichtet, laufen und sind miteinander verbunden. Die Zigbee-Geräte (Sensoren, Aktoren, Bediengeräte) sind bereits über Zigbee2MQTT mit dem Zigbee-Netzwerk gekoppelt ("gepairt") und dort mit einem Namen sichtbar. Der Heating Controller setzt darauf auf und muss dafür **kein** eigenes Zigbee-Pairing anbieten – das Pairing neuer Geräte bleibt Aufgabe von Zigbee2MQTT. Der Heating Controller übernimmt Geräte, die dort bereits bekannt sind.

Damit ergibt sich ein sauberer Zuständigkeitsschnitt aus Nutzersicht:

```text
Neues Zigbee-Gerät koppeln        → geschieht in Zigbee2MQTT (einmalig, pro Gerät)
Gerät einem Raum zuordnen         → geschieht im Heating Controller (Alltag)
Raum bedienen (Sollwert, Modus)   → geschieht im Heating Controller / am Bediengerät / in HA
```

---

## 2. Ersteinrichtung (Onboarding)

### 2.1 Erststart

Beim ersten Öffnen des Heating Controllers (über das Home-Assistant-Menü) findet der Nutzer noch keine Räume vor. Der Controller hat zu diesem Zeitpunkt bereits über Zigbee2MQTT die Liste aller bekannten Zigbee-Geräte eingelesen und automatisch klassifiziert (siehe Abschnitt 5). Der Einstieg ist entsprechend eine Übersicht der erkannten Geräte statt eines leeren Formulars:

```text
Willkommen beim Heating Controller

Es wurden 7 Zigbee-Geräte gefunden:

🌡  Aqara W100 Wohnzimmer         → Temperatursensor + Bediengerät
🌡  Aqara W100 Schlafzimmer       → Temperatursensor + Bediengerät
🌡  Aqara Temp-Sensor Küche       → Temperatursensor
🔌  Heizungsaktor Wohnzimmer 1    → Heizungsaktor
🔌  Heizungsaktor Wohnzimmer 2    → Heizungsaktor
🔌  Heizungsaktor Küche           → Heizungsaktor
🚪  Fensterkontakt Wohnzimmer     → Fensterkontakt

Diese Geräte kennt der Controller bereits nicht zugeordnete
Geräte werden weiterhin hier aufgelistet, sobald neue dazukommen.

[ Ersten Raum einrichten ]
```

Damit muss der Nutzer keine technischen Bezeichner nachschlagen – er sieht sofort, was bereits erkannt wurde, und beginnt direkt mit der sinnvollen nächsten Handlung.

### 2.2 Raum einrichten – Schritt für Schritt

Das Anlegen eines Raumes ist ein geführter, kurzer Ablauf mit maximal vier Schritten:

```text
Schritt 1/4 – Name
Wie soll der Raum heißen?
[ Wohnzimmer                          ]
                                        [ Weiter → ]

Schritt 2/4 – Temperaturerfassung
Womit soll die Temperatur gemessen werden?

○ Aqara W100 (misst UND zeigt an UND kann bedient werden)
    → Wohnzimmer W100 auswählen [ ▾ ]
○ Einfacher Temperatursensor (Bedienung nur über App/Home Assistant)
    → Sensor auswählen [ ▾ ]

                                        [ ← Zurück ]  [ Weiter → ]

Schritt 3/4 – Heizkreise
Welche Heizungsaktoren gehören zu diesem Raum?
(Mehrfachauswahl möglich, z.B. bei mehreren Heizkreisen im selben Raum)

☑ Heizungsaktor Wohnzimmer 1
☑ Heizungsaktor Wohnzimmer 2
☐ Heizungsaktor Küche

                                        [ ← Zurück ]  [ Weiter → ]

Schritt 4/4 – Fenster (optional)
Soll die Heizung bei offenem Fenster pausieren?

☑ Fensterkontakt Wohnzimmer

                                        [ ← Zurück ]  [ Raum erstellen ]
```

Nach dem Erstellen ist der Raum sofort aktiv, mit sinnvollen Standardwerten (siehe Abschnitt 2.4) und dem einfachen Zeitprogramm "immer 20 °C", bis der Nutzer ein eigenes Zeitprogramm hinterlegt. So ist der Raum von der ersten Minute an nutzbar, ohne dass der Nutzer sofort alle Details ausfüllen muss.

### 2.3 Reihenfolge bei mehreren Räumen

Der Assistent kann direkt im Anschluss an einen erstellten Raum den nächsten anbieten ("Noch ein Raum einrichten?"), bis alle erkannten, noch nicht zugeordneten Geräte verplant sind. Geräte, die keinem Raum zugeordnet sind, bleiben in einer eigenen Übersicht "Nicht zugeordnete Geräte" sichtbar, ohne dass dadurch etwas geschieht – nicht zugeordnete Aktoren und Sensoren werden vom Controller schlicht ignoriert.

### 2.4 Sinnvolle Standardwerte

Damit der Nutzer bei der Einrichtung nicht mit technischen Reglerparametern konfrontiert wird, gelten beim Anlegen automatisch Vorgaben, die er über die erweiterten Einstellungen (Abschnitt 4) jederzeit ändern kann:

```text
Solltemperatur           20 °C
Hysterese                0,3 K
Mindest-Einschaltdauer   5 min
Mindest-Ausschaltdauer   5 min
Sensor-Timeout           30 min
Zeitprogramm             durchgehend 20 °C
Betriebsart              Automatik
```

---

## 3. Bedienung im laufenden Betrieb

### 3.1 Bedienwege im Überblick

Ein Raum lässt sich – je nach Ausstattung – auf mehreren Wegen bedienen. Der Controller sorgt dafür, dass alle Wege denselben, konsistenten Zustand zeigen (kein Weg kennt einen "eigenen" Sollwert).

| Bedienweg | Wofür geeignet | Voraussetzung |
|---|---|---|
| Aqara W100 direkt am Gerät | Schnelle Sollwertänderung im Raum, ohne Handy | Raum ist mit W100 eingerichtet |
| Heating-Controller-Weboberfläche | Räume einrichten, Zeitprogramme, erweiterte Einstellungen, Geräteübersicht | immer verfügbar |
| Home-Assistant-Dashboard | Einbettung neben anderen Smart-Home-Funktionen, Sprachsteuerung (Alexa/Google/HomeKit), Automationen | immer verfügbar, sobald Raum angelegt ist |

### 3.2 Hauptansicht

```text
Heizung
──────────────────────────────────

Wohnzimmer
21,4°       22,0°       🔥 Heizt

Küche
21,8°       21,0°       ● Aus

Bad
22,1°       23,0°       🔥 Heizt

Schlafzimmer
20,3°       20,0°       ● Aus


                       + Raum hinzufügen
```

Ein Tippen/Klicken auf einen Raum öffnet die Detailansicht mit Verlauf der letzten Stunden, aktuellem Zeitprogramm-Eintrag und den Bedienelementen aus 3.3.

### 3.3 Sollwert und Betriebsart ändern

```text
Wohnzimmer

Solltemperatur         22,0 °C   [ − ]  [ + ]

Betriebsart
● Automatik     (folgt dem Zeitprogramm)
○ Manuell       (fester Sollwert, siehe unten)
○ Aus           (Heizung in diesem Raum deaktiviert)

Manueller Sollwert gilt
● bis zur nächsten Schaltzeit im Zeitprogramm
○ dauerhaft, bis ich es wieder ändere
```

Eine Sollwertänderung direkt am W100 wirkt sich unmittelbar auf diese Ansicht aus (und umgekehrt) – es gibt für den Nutzer nur einen Sollwert pro Raum, unabhängig davon, wo er ihn ändert.

### 3.4 Zeitprogramm einrichten

```text
Wohnzimmer – Zeitprogramm

Mo–Fr
  06:00 – 08:00     21 °C
  08:00 – 16:00     19 °C
  16:00 – 23:00     21 °C
  23:00 – 06:00     18 °C

Sa–So
  08:00 – 23:00     21 °C
  23:00 – 08:00     18 °C

[ + Zeitfenster hinzufügen ]     [ Von anderem Raum kopieren ]
```

Die Funktion "Von anderem Raum kopieren" erleichtert die Einrichtung, wenn mehrere Räume ein ähnliches Nutzungsmuster haben (z. B. alle Schlafräume).

### 3.5 Status und Fehler verstehen

Jeder Raum zeigt jederzeit einen für Laien verständlichen Grund für seinen aktuellen Zustand, nicht nur "an/aus":

```text
Wohnzimmer                     Küche                    Bad
21,7° → 22,0°                  21,8° → 21,0°             ⚠ Temperatursensor
🔥 Heizt                       ● Ziel erreicht            nicht erreichbar
                                                          Letzter Wert vor 37 Min.
                                                          Heizung sicherheitshalber aus


Schlafzimmer
🪟 Fenster offen
Heizung pausiert
```

Bei einem Fehlerzustand zeigt die Detailansicht zusätzlich eine kurze Erklärung und – wo sinnvoll – eine Handlungsempfehlung:

```text
⚠ Heizungsaktor "Heizungsaktor Wohnzimmer 1" antwortet nicht

Der Aktor hat den letzten Schaltbefehl nicht bestätigt.
Mögliche Ursachen: Aktor ohne Strom, außerhalb der Zigbee-Reichweite,
Batterie leer (falls batteriebetrieben).

Die übrigen Heizkreise in diesem Raum sind davon nicht betroffen.
```

Diese Meldungen erscheinen zusätzlich als Home-Assistant-Benachrichtigung, sofern der Nutzer das in Home Assistant so eingerichtet hat (Automationen sind nicht Teil des Heating Controllers selbst, siehe Konzept-Dokument – der Controller stellt dafür lediglich die passenden Zustände bereit).

---

## 4. Erweiterte Einstellungen

Für den Normalbetrieb nicht nötig, aber pro Raum einsehbar und änderbar für Nutzer, die feiner justieren möchten:

```text
Wohnzimmer – Erweiterte Einstellungen

Hysterese                  0,3 K      (wie stark darf die Temperatur um den
                                        Sollwert schwanken, bevor geschaltet wird)
Mindest-Einschaltdauer     5 min      (verhindert zu kurzes Takten)
Mindest-Ausschaltdauer     5 min
Sensor-Timeout             30 min     (ab wann ein fehlender Messwert als Fehler gilt)
Temperatur-Offset          0,0 K      (Korrektur, falls der Sensor systematisch
                                        zu warm/kalt misst)
Verzögerung "Fenster zu"   2 min      (wie lange nach dem Schließen gewartet wird,
                                        bevor die Heizung wieder anspringt)
```

Jede Einstellung hat einen erklärenden Hilfetext direkt daneben; es sind bewusst keine Fachbegriffe wie "P-Glied" oder "Totzone" nötig, um sie zu verstehen.

---

## 5. Gerätetypen

### 5.1 Übersicht

| Gerätetyp | Rolle im Raum | Pflicht? | Mehrfach pro Raum? | Typische Geräte |
|---|---|---|---|---|
| Temperatursensor | Liefert die Ist-Temperatur | Ja, genau einer | Nein | Aqara W100, Aqara-Temperatursensor, andere Zigbee-Temperatursensoren |
| Kombi-Bediengerät (Sensor + Thermostat) | Liefert Ist-Temperatur, zeigt Sollwert an, erlaubt lokale Bedienung | Nein, ersetzt bei Nutzung den einfachen Temperatursensor | Nein | Aqara W100 (im Thermostat-Modus) |
| Heizungsaktor | Schaltet den Heizkreis (Stellantrieb) | Ja, mindestens einer | Ja, beliebig viele | Zigbee-Schaltaktor + 230-V-Stellantrieb, Zigbee-Thermostatventil |
| Fensterkontakt | Pausiert die Heizung bei offenem Fenster/Tür | Nein | Ja, beliebig viele | Zigbee-Tür-/Fensterkontakt (Aqara, Sonoff u. a.) |

### 5.2 Temperatursensor

Liefert der Steuerung fortlaufend die Ist-Temperatur eines Raumes. Anforderung an das Gerät: Es muss über Zigbee2MQTT einen Temperaturwert bereitstellen. Ein Raum hat genau einen aktiven Temperatursensor – bei mehreren im selben Raum vorhandenen Sensoren wählt der Nutzer bei der Einrichtung, welcher maßgeblich ist.

### 5.3 Kombi-Bediengerät (z. B. Aqara W100)

Ein Sonderfall des Temperatursensors: Das Gerät liefert nicht nur die Ist-Temperatur, sondern zeigt zusätzlich den Sollwert an und lässt sich direkt am Gerät bedienen (Sollwert ändern, teils auch Betriebsart). Wird ein Raum mit einem solchen Gerät eingerichtet, ist automatisch auch die lokale Bedienung am Gerät selbst aktiv – ohne weitere Einrichtung. Der Controller hält Anzeige am Gerät und Sollwert im System dauerhaft synchron.

### 5.4 Heizungsaktor

Schaltet den eigentlichen Heizkreis – üblicherweise ein Zigbee-Schaltaktor, der einen klassischen 230-V-Stellantrieb an der Fußbodenheizungsverteilung ansteuert, alternativ ein Zigbee-Thermostatventil mit eigenem Schaltausgang. Ein Raum kann mehrere Heizungsaktoren haben (z. B. mehrere Heizkreise für einen großen Raum); sie werden von der Steuerung immer gemeinsam geschaltet – der Nutzer sieht und bedient sie als eine Einheit ("Wohnzimmer heizt"), nicht als Einzelaktoren.

Anforderung an das Gerät: schaltbar über Zigbee2MQTT (Ein/Aus) und meldet seinen aktuellen Schaltzustand zurück – letzteres ist Voraussetzung dafür, dass der Controller einen Schaltbefehl bestätigen und damit Aktorfehler erkennen kann.

### 5.5 Fensterkontakt

Optionaler Tür-/Fensterkontakt, der beim Öffnen die Regelentscheidung im zugeordneten Raum
pausieren soll. Ein Raum kann mehrere Fensterkontakte haben (z. B. mehrere Fenster plus
Terrassentür); es reicht, dass **eines** davon offen ist. Erst wenn **alle** wieder geschlossen
sind, soll die Regelentscheidung nach kurzer Verzögerung fortgesetzt werden.

### 5.6 Geräte, die (noch) nicht unterstützt werden

Zur Erwartungssteuerung: Luftfeuchtigkeitssensoren, Bewegungsmelder, Energiemessungen und ähnliche Zigbee-Geräte werden vom Controller zwar über Zigbee2MQTT erkannt, aber keiner der oben genannten Rollen zugeordnet und tauchen entsprechend nicht als zuordenbare Option auf. Das schließt eine spätere Erweiterung nicht aus, ist für Version 1 aber bewusst außen vor (vgl. Konzept-Dokument, Abschnitt „Umfang Version 1").

---

## 6. Geräte im laufenden Betrieb verwalten

### 6.1 Neues Gerät hinzufügen

```text
1. Neues Zigbee-Gerät in Zigbee2MQTT koppeln (wie gewohnt, außerhalb des
   Heating Controllers)
2. Heating Controller erkennt das Gerät automatisch und schlägt eine Rolle vor:

   1 neues Gerät gefunden

   Aqara Temperatursensor (neu)
   → Als Temperatursensor verwenden?
     [ Raum zuordnen ]   [ Später ]

3. Nutzer wählt einen bestehenden Raum oder legt einen neuen an
```

### 6.2 Gerät ersetzen (z. B. defekter Aktor)

```text
Wohnzimmer – Heizkreise

Heizungsaktor Wohnzimmer 1     ⚠ antwortet nicht
[ Ersetzen ▾ ]  →  neues, bereits gekoppeltes Gerät auswählen
                    alle Einstellungen des Raumes bleiben erhalten
```

Der Ersatz eines Gerätes ändert ausschließlich die technische Zuordnung; Sollwert, Zeitprogramm und erweiterte Einstellungen des Raumes bleiben unverändert erhalten.

### 6.3 Gerät entfernen / Raum löschen

```text
Raum löschen entfernt nur die Konfiguration im Heating Controller.
Die Zigbee-Geräte selbst bleiben in Zigbee2MQTT weiterhin gekoppelt
und stehen danach wieder als "nicht zugeordnet" zur Verfügung.
```

So bleibt eine Fehlbedienung risikoarm: Das Löschen eines Raumes entkoppelt keine Hardware und erfordert kein erneutes Zigbee-Pairing.

---

## 7. Zusammenfassung: Prinzipien für Bedienung und Einrichtung

1. **Einrichtung in Minuten, nicht in Stunden.** Geräteerkennung plus ein vierstufiger Assistent pro Raum, mit sinnvollen Standardwerten.
2. **Ein Sollwert pro Raum, überall gleich.** Egal ob am W100, im Webinterface oder in Home Assistant geändert – der Zustand ist immer konsistent.
3. **Verständliche Statusmeldungen statt Rohdaten.** Der Nutzer sieht "Fenster offen, Heizung pausiert", nicht "binary_sensor.window = on".
4. **Fehler mit Erklärung und Handlungsempfehlung.** Jeder Fehlerzustand sagt, was passiert ist und was der Nutzer tun kann.
5. **Geräte tauschen ohne Konfigurationsverlust.** Raumkonfiguration und Hardware-Zuordnung sind getrennt.
6. **Zigbee-Pairing bleibt bei Zigbee2MQTT.** Der Heating Controller übernimmt bereits gekoppelte Geräte und mischt sich nicht in die Zigbee-Netzwerkverwaltung ein.
