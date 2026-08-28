# Stand

Letzte Aktualisierung: 2026-08-28

Diese Datei ist der Einstiegspunkt für jede neue Session. Sie wird **im selben Commit**
nachgezogen wie die Änderung, die sie beschreibt.

## Wo wir stehen

| | |
|---|---|
| Aktuelles Teilprojekt | **1 — Fundament** |
| Phase | Spezifikation geschrieben, Implementierungsplan steht aus |
| Code | **noch keine Zeile.** Auch kein Projektgerüst |
| Repository | lokal, noch kein GitHub-Remote |

## Zuletzt fertig

- Initialer Commit: Rahmenentwurf, Bestandsaufnahme, CLAUDE.md, `.gitignore`.
- Teilprojekt 1 ausbrainstormt, Entscheidungen zu Datenmodell, Auth- und Rechtemodell,
  Konfiguration, Logging, Container und CI getroffen.
- [TP1-Spezifikation](superpowers/specs/2026-08-28-teilprojekt-1-fundament-design.md)
  geschrieben.
- Arbeitsweise festgelegt und in CLAUDE.md verankert: Agent-Verteilung, Worktrees,
  kreuzweises Review, Commit-Disziplin, CI.
- `docs/technisches_konzept.md` als unverbindlich eingestuft; vier Punkte daraus übernommen.

## Als Nächstes

1. Implementierungsplan zu Teilprojekt 1 schreiben.
2. GitHub-Repository **privat** anlegen und Remote setzen (Konto `MagicalWig34653`).
3. Umsetzung nach Plan, in der Reihenfolge aus Abschnitt 9 der TP1-Spezifikation.

## Getroffene Entscheidungen, die nicht in der Spezifikation stehen

- Modellwahl für Agents: Sonnet für Claude, Standardmodell für Codex. **Opus nur nach
  ausdrücklicher Genehmigung des Nutzers.**
- Zielverteilung der Aufgaben rund 60 % Codex, Rest Claude Code.

## Offene Punkte

- **Datenübernahme** aus dem Altschema ist auf TP4 vertagt — offen bleibt, wie aus einem
  unregelmäßigen Stundenraster Schaltpunkte werden.
- **Alte MQTT-Topics:** ob sie übergangsweise zusätzlich bedient werden, entscheidet TP2.
- **`vm130-nginx`** (PHP-Oberfläche, kein Git) ist bis zum abgeschlossenen Cutover die
  Rückfallebene. Bis dahin wird dort **nichts** entfernt oder abgeschaltet.
