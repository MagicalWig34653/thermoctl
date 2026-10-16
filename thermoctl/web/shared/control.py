"""Betriebsseite: Anzeigetexte und Sperrfristen -- geteilt von den /ui-api-Endpunkten."""
from datetime import timedelta

from thermoctl.domain.pi_control import (
    RESET_REASON_ARMING,
    RESET_REASON_CONTEXT_CHANGE,
    RESET_REASON_FROST,
    RESET_REASON_INVALID_STATE,
    RESET_REASON_SENSOR_FAILURE,
    RESET_REASON_TIME_GAP,
    RESET_REASON_VALVE_PROTECTION,
    RESET_REASON_WINDOW_OPEN,
)
from thermoctl.services.shadow_run import PI_FALLBACK_INELIGIBLE

# Readable text for `shadow_decision.controller_fallback_reason` (specification
# section 6: "einschließlich Rückfallgrund" has to be legible on the operating
# page, not just as a code). Kept here, not in the domain: this is a display-only
# translation, the same reasoning as `domain.control.LABELS`.
PI_FALLBACK_LABELS: dict[str, str] = {
    PI_FALLBACK_INELIGIBLE: "Die Zone erfüllt die PI-Voraussetzungen nicht (mehr).",
    RESET_REASON_WINDOW_OPEN: "Ein Fenster ist offen.",
    RESET_REASON_FROST: "Frostschutz ist wirksam.",
    RESET_REASON_SENSOR_FAILURE: "Der Sensor gilt als ausgefallen.",
    RESET_REASON_VALVE_PROTECTION: "Ein Ventilschutzlauf ist aktiv.",
    RESET_REASON_CONTEXT_CHANGE: "Der Sollwertkontext hat gerade gewechselt.",
    RESET_REASON_TIME_GAP: "Eine Zeitlücke seit der letzten Auswertung.",
    RESET_REASON_ARMING: "Die Anlage wurde gerade scharf geschaltet.",
    RESET_REASON_INVALID_STATE: "Der PI-Zustand war ungültig oder fehlte.",
}


# Below this, a repeated test button does nothing for a while rather than firing a
# second outbound call: whoever is troubleshooting a webhook can fix an address and
# try again in a moment, but nothing here should let an unattended tab hammer an
# external target. Enforced on the same row the result is shown from, not a separate
# store -- there is exactly one webhook target for the whole plant, so a plant-wide
# cooldown on the same field the operator is already looking at is enough.
TEST_COOLDOWN = timedelta(seconds=10)
