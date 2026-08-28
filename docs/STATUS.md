# Stand

Letzte Aktualisierung: 2026-08-28

## Wo wir stehen

Teilprojekt 1 — Fundament ist abgeschlossen. Datenmodell, Domänenlogik, Anmeldung und
Rechte, Einrichtungsassistent, Verwaltung, REST-Adapter, Container, CI und die Tests der
Architekturgrenzen sind umgesetzt.

`thermoctl` steuert noch keine Heizung. Teilprojekt 2 — Geräte-Anbindung im
Schattenbetrieb — ist als Nächstes vorgesehen und erhält einen eigenen Zyklus aus
Brainstorming, Spezifikation und Plan.

## Offen

- Geräte anbinden und zunächst im Schattenbetrieb beobachten.
- Den eigentlichen Regelkreis implementieren.
- Die Pflegeoberfläche vervollständigen.
- Die Datenübernahme aus dem Altschema planen; insbesondere ist die Umwandlung des
  unregelmäßigen Stundenrasters in Schaltpunkte ungeklärt.
- In Teilprojekt 2 entscheiden, ob die alten MQTT-Topics übergangsweise zusätzlich bedient
  werden.
- `vm130-nginx` bis zum abgeschlossenen Cutover unverändert als Rückfallebene erhalten.

Tag, Veröffentlichung und Push bleiben eine gesonderte Freigabeentscheidung des
Projektinhabers.
