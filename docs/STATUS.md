# Stand

Letzte Aktualisierung: 2026-08-29

## Wo wir stehen

| Phase | Zustand |
|---|---|
| 1 — Fundament | abgeschlossen, veröffentlicht als `v0.1.0` |
| 1a — Nacharbeiten | abgeschlossen |
| 2 — Geräte-Anbindung im Schattenbetrieb | **gebaut**; der Nachweis über mehrere Tage braucht die Anlage |
| 3 — Konfigurations-Oberfläche | **abgeschlossen**, Abnahmekriterium nachgewiesen |
| 4 — Regelkreis und Cutover | Logik und Tests stehen, **nichts ist scharf** |
| 5 — Integrationen und Veröffentlichung | bis auf zwei Punkte erledigt |

`thermoctl` liest Sensoren, führt eine Messwert-Historie, erkennt ausgefallene Sensoren,
meldet Störungen, und schreibt für jede Zone auf, **was es schalten würde und warum** —
ohne je etwas zu schalten. Eine vollständige Anlage lässt sich über die Oberfläche
einrichten, ohne einen einzigen SQL-Befehl.

## Zahlen

Vom Controller selbst nachgeprüft, nicht aus Berichten übernommen:

| | |
|---|---|
| Tests | 617, grün unter SQLite **und** MariaDB |
| Testabdeckung | 99 %, Mindestschwelle 97 % in der CI |
| Ruff, mypy strict | ohne Befund, 73 Quelldateien |
| Migrationskette | linear, ein Kopf, vorwärts und rückwärts gegen beide Datenbanken |
| CI und Container | grün |

## Der Trockenlauf ist abgesichert, nicht zugesagt

- `setting.control_armed` steht auf `false` und wird nirgends gesetzt.
- Jeder Aktor prüft ihn als Erstes.
- Der MQTT-Client verweigert das Veröffentlichen zusätzlich, solange er nicht scharf gebaut
  wurde — auch wenn ein Aufrufer es ausdrücklich verlangt.
- Tests belegen beides **und** den Gegenbeweis: Ein scharf gebauter Client sendet wirklich.
  Ohne den belegte die Suite nur, dass nichts gesendet wird — auch dann, wenn das Senden
  gar nicht gebaut wäre.

## Was in diesem Lauf entstanden ist

**Phase 2** — Nutzlast-Auswertung gegen die echten Anlagendaten, Geräteerkennung aus
`bridge/devices` (erkennt Ventile und Fensterkontakte, ohne je eine Zustandsnachricht von
ihnen gesehen zu haben), MQTT-Client mit TLS und Wiederverbindung, Ingest samt
Messwert-Historie und Aufbewahrung, Störungserkennung, Fensterkontakte, Aktor-Adapter im
Trockenlauf hinter zwei Riegeln, Schattenprotokoll, Geräteübersicht.

**Die Regelentscheidung** ist aus Phase 4 vorgezogen — sonst hätte das Schattenprotokoll
nichts zu protokollieren. Hysterese, Mindestschaltdauer, Fensterpause, Frostschutz bei
Sensorausfall, als reine Funktion mit 33 Tests, darunter der Defekt des Altsystems
ausdrücklich vorgeführt.

**Phase 3** — Formularbausteine, Zonenverwaltung, Gerätezuordnung samt Tausch, Modi und
Sollwerte, Zeitplan-Editor mit Wochenansicht, Übersteuern, Regelparameter je Zone,
Benutzer-/Gruppen-/Tokenverwaltung, Audit-Ansicht, Übersichtsseite.

**Aus Phase 5 vorgezogen** — MCP-Server als dritter Adapter, API-Dokumentation,
Self-Hosting-Anleitung, Beispiel-Compose, Benachrichtigungen bei Störungen,
[Sicherheitsdurchsicht](sicherheitsdurchsicht.md), und die REST-Endpunkte, mit denen die
drei Adapter wieder auf demselben Stand sind.

**Aus Phase 4 vorgebaut, ohne etwas scharf zu schalten** — die Umwandlung des alten
Stundenrasters in Schaltpunkte (die Roadmap führte sie als ungeklärt; sie ist jetzt eine
reine Funktion mit einer Rückprobe über alle 168 Wochenstunden) und die lesende Grundlage
des Vergleichsbetriebs.

## Neun Fehler, die alle Tests und Reviews passiert hatten

Alle in diesem Lauf gefunden:

1. **Zwei Wächtertests prüften nichts.** Seit FastAPI 0.141 verschachtelt `include_router()`
   die Routen; beide Wächter fanden nur noch `/healthz` und waren grün, weil sie leer
   liefen.
2. **Der Testlauf hing an einer zufällig vorhandenen `.env`.** Örtlich grün, in der CI wäre
   der nächste Lauf rot geworden.
3. **Die Startseite baute eine eigene Vorlagen-Umgebung mit relativem Pfad.** Im Container
   liegt das Paket in `site-packages` und das Arbeitsverzeichnis ist `/app` — dort hätte
   genau die Seite gefehlt, auf die Anmeldung und Navigation zeigen.
4. **Ein zu kurzes Passwort hinterließ eine halb angelegte Einrichtung**, weil die Prüfung
   erst nach den ersten Schreibzugriffen kam.
5. **Der Downgrade der Phase-2-Migration scheiterte nur unter MariaDB** — dort wird der
   Index für einen Fremdschlüssel gebraucht.
6. **Die Frostschutz-Sperre griff in keiner echten Anlage.** Der Einrichtungsassistent legt
   den Frostschutzmodus als eingebauten Modus an, und die allgemeine Sperre kam zuerst —
   also bekam genau der wichtigste Modus die nichtssagende Meldung. Der zugehörige Test war
   grün, weil seine Fixture einen Zustand herstellte, den es in keiner Instanz gibt.
7. **`shadow_decision.zone_id` hatte als einziger Zonenbezug keine Kaskade**, wodurch sich
   eine Zone nicht mehr löschen ließ, sobald ein Schattenlauf für sie gelaufen war.

8. **Die Sollwertgrenze für Übersteuerungen galt nicht überall.** Sie stand dreimal
   verschieden da — und der MCP-Server prüfte gar nicht. Übersteuerungen sind genau die
   Eingabe, die in Phase 4 ungefiltert in die scharfe Regelentscheidung fließt. Die
   Prüfung liegt jetzt in der Domäne.
9. **Die Fehlerklassen waren eingefrorene Dataclasses.** Python hängt einer Ausnahme beim
   Werfen ihren Traceback an, und eine eingefrorene Dataclass verweigert das — die
   Ausnahme kam als `FrozenInstanceError` an, sobald sie tief genug durchgereicht wurde.

Dazu vier Korrekturen aus der Gegenlesung in der Hauptsession: Frostschutz statt Abschalten
bei Sensorausfall, ein MQTT-Wiederverbindungsabstand, der monoton wuchs, eine doppelt
implementierte Zeitberechnung in zwei Adaptern, und ein Meldungsversand, der den Zyklustakt
verzögert hätte.

## Offen

**Was nur der Projektinhaber kann:**

- **Phase 2 wirklich abschließen.** Die Anlage muss über mehrere Tage laufen:
  `THERMOCTL_MQTT_ENABLED=true` samt Zugangsdaten in `.env`, dann Geduld. Erst dann steht
  fest, dass plausible Ist-Temperaturen aller Zonen einlaufen und das Schattenprotokoll
  nachvollziehbare Entscheidungen zeigt.
- **Die Frostschutz-Entscheidung bestätigen**, bevor Phase 4 scharf schaltet — siehe
  [offene-entscheidungen.md](offene-entscheidungen.md). Sie hat körperliche Folgen.
- **Meross-Zugangsdaten hinterlegen.** Der Adapter ist gebaut, sein Nutzlastaufbau ist eine
  begründete Annahme und nie gegen ein echtes Konto gelaufen.
- **Das Repository öffentlich schalten** — ausdrücklich seine Entscheidung.

**Was als Nächstes ansteht:**

- Phase 4: Vergleichsbetrieb entwerfen (Dauer, Auflösung, Bericht), dann das Schema dafür
  bauen — nicht umgekehrt. Datenübernahme aus dem Altschema. Scharfschalten hinter einem
  Schalter, jederzeit umkehrbar.
- Phase 5, Rest: Home-Assistant-Discovery veröffentlichen (der Entwurf steht, das Senden
  wartet auf Phase 4), altes Topic-Schema abkündigen.
- **`vm130-nginx` bleibt bis zum abgeschlossenen Cutover unverändert die Rückfallebene.**
