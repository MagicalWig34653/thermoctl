"""Befehlsprotokoll: Seitengroesse und Beschriftungen -- geteilt von Alt-View und /ui-api."""
from thermoctl.domain.device_commands import SETBACK_OUTCOME_PREFIX
from thermoctl.domain.solar_setback_log import KIND_BEGIN, KIND_CHANGE, KIND_END

ENTRIES_PER_PAGE = 50


SETBACK_OUTCOME_LABELS = {
    SETBACK_OUTCOME_PREFIX + KIND_BEGIN: "Absenkung beginnt",
    SETBACK_OUTCOME_PREFIX + KIND_CHANGE: "Absenkung geändert",
    SETBACK_OUTCOME_PREFIX + KIND_END: "Absenkung beendet",
}
