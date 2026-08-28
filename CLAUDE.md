# CLAUDE.md

Arbeitsanweisung für Claude Code in diesem Repository.

## Was das hier ist

`thermoctl` ist eine eigenständige, self-hostbare Heizungssteuerung: sensorbasierte
Raumregelung mit Zeitplänen, konfiguriert über eine Weboberfläche, ansprechbar zusätzlich
über REST-API und MCP-Server.

**Das Projekt ist ein Neubau, kein Refactoring.** Es ersetzt vier gewachsene Python-Skripte
und eine PHP-Oberfläche aus zwei anderen Projekten. Der Stand der Planung ist:

- [`docs/superpowers/specs/2026-08-28-thermoctl-neubau-design.md`](docs/superpowers/specs/2026-08-28-thermoctl-neubau-design.md)
  — Rahmenentwurf: Ziele, getroffene Entscheidungen samt Begründung, verworfene Alternativen,
  Zerlegung in fünf Teilprojekte.
- [`docs/bestandsaufnahme-altsystem.md`](docs/bestandsaufnahme-altsystem.md) — das
  abzulösende System: Services, vollständiges Ist-Schema, MQTT-Topic-Vertrag, bekannte Defekte.

**Beide Dokumente vor der ersten Änderung lesen.** Sie ersparen es, zwei fremde Projekte
erneut zu durchsuchen.

## Stand

Der Rahmen ist abgestimmt, **noch keine Zeile Implementierung**. Der nächste Schritt ist,
Teilprojekt 1 (Fundament) auszubrainstormen — Schwerpunkt Datenmodell und Auth-Modell —,
daraus eine Spezifikation zu schreiben und daraus einen Implementierungsplan. Nicht vorher
mit dem Bauen anfangen.

## Technischer Rahmen (entschieden, nicht neu verhandeln)

| | |
|---|---|
| Backend | Python, FastAPI |
| Persistenz | SQLAlchemy + Alembic |
| Datenbank | Nutzerwahl beim Setup: SQLite (Standard) oder MariaDB |
| Frontend | Jinja-Templates, HTMX, Bootstrap — server-gerendert, kein Build-Schritt, kein npm |
| Schnittstellen | Drei dünne Adapter über gemeinsamer Domänenlogik: HTMX-Views, REST-API, MCP-Server |
| Betrieb | Eigener Docker-Container |
| Home Assistant | Optionale Integration per MQTT, **keine** Voraussetzung |

Begründungen und verworfene Alternativen stehen im Rahmenentwurf. Wenn eine dieser
Entscheidungen im Weg steht, das ansprechen — nicht stillschweigend anders bauen.

## Grundsätze

1. **Nichts hart verdrahtet.** Keine Geräte-IDs, Raumnamen, Broker-Adressen oder Zugangsdaten
   im Quelltext. Alles kommt aus Konfiguration oder Datenbank. Das ist der Hauptgrund für
   den Neubau — nicht ein Detail.
2. **Keine Secrets im Repo.** Auch nicht als Fallback-Wert, auch nicht in Beispielen, auch
   nicht zu Debug-Zwecken in Logs. Das Repo soll veröffentlichbar sein. Zugangsdaten des
   Altsystems wurden bewusst nicht übernommen.
3. **Datenbankagnostisch.** Kein `ENUM`, kein `SET`, keine JSON-Spalten als Datenmodell,
   keine datenbankspezifischen Funktionen. Jede Schemaänderung als Alembic-Migration.
4. **Authentifizierung ist verpflichtend.** Im Altsystem war fehlende Auth eine akzeptierte
   Heimnetz-Eigenschaft. Hier ausdrücklich nicht mehr.
5. **Debuggbarkeit ist ein Ziel, kein Nebenprodukt.** Strukturiertes Logging, nachvollziehbare
   Regelentscheidungen (warum wurde geschaltet oder nicht), aussagekräftige Fehlermeldungen.
6. **Domänenlogik gehört nicht in Adapter.** Eine Regel wird einmal implementiert und von
   UI, API und MCP gleichermaßen benutzt.
7. **Sicherheitsrelevant, weil physisch.** Der Dienst steuert eine echte Heizung. Fehler in
   der Regellogik haben reale Folgen. Änderungen daran besonders sorgfältig prüfen.

## Beim Umstieg zu beachten

Der Wechsel läuft als Parallelbetrieb: `thermoctl` entscheidet erst im Schattenbetrieb ohne
zu schalten, wird gegen das Altsystem verglichen, und erst dann scharf geschaltet — mit dem
Altsystem als Rückfallebene. Solange das nicht abgeschlossen ist, darf nichts am Altsystem
abgeschaltet oder gelöscht werden.

Zwei Defekte des Altsystems ausdrücklich **nicht** übernehmen:
- Die Regelschleife dort hat **keine Hysterese** (`if ist < soll: an, sonst aus`) und schaltet
  am Sollwert in jedem Zyklus um. `thermoctl` braucht Hysterese und Mindestschaltdauer.
- Zeitpläne liegen dort als positionell interpretierter JSON-Blob mit acht Slots. Hier werden
  sie als echte Zeilen modelliert.
