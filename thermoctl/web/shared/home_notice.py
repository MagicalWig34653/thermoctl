"""Der für den Mieter relevante Effekt eines Problems, für die Startseite der Oberfläche."""

from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import Zone
from thermoctl.domain import emergency_display
from thermoctl.domain.emergency_display import ZoneEmergencyBanner


def home_notice(
    zones: list[Zone],
    states: dict[int, tuple[ZoneState, SensorStatus]],
    emergency_banners: dict[int, ZoneEmergencyBanner | None] | None = None,
) -> dict[str, object] | None:
    """Der für den Mieter relevante Effekt eines Problems -- nicht die Ursache.

    Erlaubt sind ein stiller/festhängender Sensor, ein offenes Fenster und
    (Auftrag 8b) ein laufender Notbetrieb, je einer sichtbaren Zone. Ausdrücklich
    nicht: MQTT, Broker, Brücke, ``control_armed``, Schattenentscheidung, PI,
    Hysterese, Verbundrolle, Relais, Gerätenamen -- keines davon sagt einem
    Mieter etwas über seine Wohnung.

    Notbetrieb zuerst geprüft, vor Sensor/Fenster: ohne diesen Vorrang würde
    "Heizung läuft normal" oben stehen, während ein Raum tatsächlich im
    Notbetrieb läuft -- genau die Art falscher, wörtlich widerlegbarer Aussage,
    die `tests/test_user_visible_effect_texts.py` verhindern soll (zwei
    frühere Fälle: fehlende Startseite, fehlendes Stylesheet).
    """
    if emergency_banners:
        for zone in zones:
            banner = emergency_banners.get(zone.id)
            if banner is not None:
                return {
                    "kind": "notbetrieb",
                    "zone_name": zone.display_name,
                    # Nicht `banner.headline` hinter "<Zone>: " hängen: dessen
                    # eigener Text beginnt im Notbetrieb-Fall selbst mit
                    # "Notbetrieb: …" -- zusammen ergäbe das einen doppelten
                    # Doppelpunkt ("Wohnzimmer: Notbetrieb: …", Kreuzreview von
                    # c1ae1c5). Die Vorlage setzt stattdessen "<Zone> –
                    # <Stufe>" als Titel; `banner.detail` bleibt die einzige
                    # Fließtextquelle.
                    "stage_label": emergency_display.STAGE_LABELS.get(
                        banner.stage, banner.stage
                    ),
                    "detail": banner.detail,
                }
    for zone in zones:
        entry = states.get(zone.id)
        if entry is None:
            continue
        zone_state, sensor_status = entry
        if sensor_status.code != "ok":
            return {
                "kind": "sensor",
                "zone_name": zone.display_name,
                "measured_at": zone_state.measured_at,
            }
    for zone in zones:
        entry = states.get(zone.id)
        if entry is None:
            continue
        zone_state, _sensor_status = entry
        if zone_state.window_open:
            return {"kind": "window", "zone_name": zone.display_name}
    return None
