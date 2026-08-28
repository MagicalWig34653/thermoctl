# Stand

Letzte Aktualisierung: 2026-08-28

Diese Datei ist der Einstiegspunkt für jede neue Session. Sie wird **im selben Commit**
nachgezogen wie die Änderung, die sie beschreibt.

## Wo wir stehen

| | |
|---|---|
| Aktuelles Teilprojekt | **1 — Fundament** |
| Phase | Spezifikation und Implementierungsplan fertig, Umsetzung steht aus |
| Code | **noch keine Zeile.** Auch kein Projektgerüst |
| Repository | `MagicalWig34653/thermoctl`, **privat**, Remote gesetzt |

## Zuletzt fertig

- Initialer Commit: Rahmenentwurf, Bestandsaufnahme, CLAUDE.md, `.gitignore`.
- Teilprojekt 1 ausbrainstormt, Entscheidungen zu Datenmodell, Auth- und Rechtemodell,
  Konfiguration, Logging, Container und CI getroffen.
- [TP1-Spezifikation](superpowers/specs/2026-08-28-teilprojekt-1-fundament-design.md)
  geschrieben.
- Arbeitsweise festgelegt und in CLAUDE.md verankert: Agent-Verteilung, Worktrees,
  kreuzweises Review, Commit-Disziplin, CI.
- `docs/technisches_konzept.md` als unverbindlich eingestuft; vier Punkte daraus übernommen.
- [Implementierungsplan](superpowers/plans/2026-08-28-teilprojekt-1-fundament.md) mit
  22 Aufgaben geschrieben, auf Codex und Claude-Agents verteilt.
- GitHub-Repository privat angelegt, Remote gesetzt.

## Als Nächstes

1. Umsetzung von Teilprojekt 1 nach Plan, Aufgabe 1 bis 22 der Reihe nach.
   Aufgaben 8–11 und 16–17 sind untereinander unabhängig und können parallel laufen.
2. Nach Aufgabe 22: Tag `v0.1.0` setzen — das erzeugt das erste `latest`-Image.

## Getroffene Entscheidungen, die nicht in der Spezifikation stehen

- Modellwahl für Agents: Sonnet für Claude, Standardmodell für Codex. **Opus nur nach
  ausdrücklicher Genehmigung des Nutzers.**
- Zielverteilung der Aufgaben rund 60 % Codex, Rest Claude Code. Im TP1-Plan konkret:
  14 von 22 Aufgaben an Codex, 8 an Claude (Logging-Maskierung, Datenbankgrundlage,
  Identität und Rechte, Anmeldung, Einrichtungsassistent).
- Docker: `latest` entsteht **nur** aus einem Git-Tag. Ein Push auf `main` erzeugt lediglich
  ein Testimage mit der Marke `sha-<kurzer Commit>`.

## Offene Punkte

- **Datenübernahme** aus dem Altschema ist auf TP4 vertagt — offen bleibt, wie aus einem
  unregelmäßigen Stundenraster Schaltpunkte werden.
- **Alte MQTT-Topics:** ob sie übergangsweise zusätzlich bedient werden, entscheidet TP2.
- **`vm130-nginx`** (PHP-Oberfläche, kein Git) ist bis zum abgeschlossenen Cutover die
  Rückfallebene. Bis dahin wird dort **nichts** entfernt oder abgeschaltet.
