# Regelmodule

## Domäne

| Modul in `thermoctl/domain` | Aufgabe und Aufrufer |
| --- | --- |
| `plant_settings.py` | Freigabe der Anlage, Trockenlauf und globale Regelvorgaben für UI, REST und MCP. |
| `control_decision.py` | Berechnet im Regelzyklus aus einer gegebenen Situation die Entscheidung samt Begründung. |
| `pi_control.py` | Berechnet im Regelzyklus den PI-Tastgrad und dessen Begrenzung bei offenem Fenster. |
| `pi_feedforward.py` | Berechnet aus Außentemperatur und Sollwert den Vorsteueranteil für den PI-Regler im Regelzyklus. |
| `pi_intervention.py` | Prüft und protokolliert manuelle Änderungen des I-Anteils aus UI, REST und MCP und merkt sie für den nächsten Regelzyklus vor. |
| `pi_view.py` | Liefert I-Anteil und letzten Reset für die Lesesichten und filtert PI-Daten nach Zugriffsprofil. |
| `wall_controller.py` | Ordnet Wandtasten Aktionen zu und führt sie beim Nachrichteneingang aus. |
| `wall_controller_channels.py` | Konfiguriert den Werteaustausch mit Wandbediengeräten und prüft deren Kanäle für Empfang und Ausgabe. |
| `sensor_failure_policy.py` | Prüft und speichert die Notbetriebskonfiguration und ermittelt die wirksamen Vorgaben für den Regelzyklus. |
| `emergency_operation.py` | Bestimmt im Regelzyklus die Notbetriebsstufe je Raum und zählt gültige Messungen für die Rückkehr. |
| `emergency_cycle.py` | Berechnet die Ein- und Ausphasen im Notbetrieb aus Festtakt oder Außentemperaturkurve. |
| `emergency_actuator_plan.py` | Leitet im Regelzyklus aus der Vorrangtabelle den Sendeplan je Aktorzuordnung ab. |
| `emergency_display.py` | Formuliert den gespeicherten Notbetriebszustand für UI, Kiosk und Meldungen. |

## Dienste

| Modul in `thermoctl/services` | Aufgabe |
| --- | --- |
| `shadow_run.py` | Liest je Raum den Zustand, ruft die Regelmodule auf und speichert die Entscheidung. |
| `publishing.py` | Veröffentlicht den Anlagenzustand und übermittelt bei aktiver Freigabe die Befehle aus der Regelentscheidung. |

**Wo suche ich was?** Für die Begründung einer Entscheidung bei `control_decision.decide()` beginnen. Für die Eingangsdaten und PI-Verarbeitung `shadow_run.py` ansehen, für den Versand `publishing.py`. Die Tastenbelegung und ihre Ausführung stehen in `wall_controller.py`.

## Frühere Namen

| Alter Dateiname | Aktueller Dateiname |
| --- | --- |
| control.py | `plant_settings.py` |
| control_loop.py | `control_decision.py` |
| controller.py | `wall_controller.py` |
| controller_channels.py | `wall_controller_channels.py` |

Ältere datierte Dokumente behalten die damaligen Namen. Die mit `logging.getLogger(__name__)` gebildeten Logger-Namen lauten jetzt `thermoctl.domain.wall_controller` und `thermoctl.domain.wall_controller_channels`.
