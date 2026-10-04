"""Human-facing text over the Notbetrieb state machine (Auftrag 8b).

Pure functions only -- the same style as `emergency_operation.py`,
`emergency_cycle.py` and `emergency_actuator_plan.py`: no database, no clock,
every bit of already-persisted state arrives as a plain argument. Three
callers share this module (Grundsatz 6 -- a rule, here a wording, lives once):
the plant/tenant/kiosk banners (`web/start_views.py`, `web/tenant_views.py`,
`web/kiosk_views.py`), the control page's detailed per-actuator breakdown
(`web/control_views.py`), and the REST/MCP `emergency-state` read (`services/
emergency_state.py`). All three must describe the exact same persisted state
the exact same way; a wording fixed in only one adapter is the kind of drift
Grundsatz 6 exists to rule out.

Für Bewohner verständlich formuliert (plan Auftrag 8): `zone_banner` nennt
weder "Sensor" noch "Fühler" noch "Ersatzquelle" im Fließtext -- nur das
Gerät, das jetzt die Heizung steuert, und was das für den Raum bedeutet.
`zone_banner`'s Text ist bewusst nicht alarmistisch (kein "Störung", kein
"Fehler", kein Ausrufezeichen): die Heizung tut in jeder Stufe etwas
Sinnvolles, es ist nur nicht mehr die normale Wandfühlerregelung.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from thermoctl.domain import emergency_actuator_plan, emergency_cycle, emergency_operation


@dataclass(frozen=True)
class ZoneEmergencyBanner:
    """One zone's banner for start/tenant/kiosk: a short headline plus one
    sentence of plain-language detail. Both strings are already final HTML
    text (escaped by the template like any other Jinja variable) -- nothing
    further to interpolate downstream."""

    stage: str
    headline: str
    detail: str


def _cycle_parts(
    on_seconds: int | None, off_seconds: int | None, cycle_source: str | None
) -> tuple[int, int, str]:
    """Minutes and the optional outdoor hint of a duty cycle.

    Kreuzreview von c1ae1c5: "Kennlinie"/"Festtakt" sind Betreiber-
    Fachbegriffe (sie stehen weiterhin in der Aktorentabelle der
    Betriebsseite, `control.html`) und gehören nicht in einen Text, den auch
    Bewohner/Kiosk zeigen. Die Außentemperaturabhängigkeit wird höchstens als
    "passend zur Außentemperatur" angedeutet, nie als Zahl oder Quellenname.
    """
    outdoor_text = (
        ", passend zur Außentemperatur" if cycle_source == emergency_cycle.SOURCE_CURVE else ""
    )
    return round((on_seconds or 0) / 60), round((off_seconds or 0) / 60), outdoor_text


def zone_banner(
    *,
    stage: str,
    actuator_kind: str | None,
    source_device_name: str | None,
    emergency_setpoint_c: Decimal | None,
    cycle_phase: str | None,
    on_seconds: int | None,
    off_seconds: int | None,
    cycle_source: str | None,
    recovery_sample_count: int,
    recovery_samples: int | None,
    switch_cycle_phase: str | None = None,
    switch_on_seconds: int | None = None,
    switch_off_seconds: int | None = None,
    switch_cycle_source: str | None = None,
) -> ZoneEmergencyBanner | None:
    """The one banner a zone shows while it is not in `STAGE_NORMAL`.

    `actuator_kind` (`emergency_actuator_plan.KIND_THERMOSTAT`/`KIND_SWITCH`,
    or `None` while the zone has no emergency-eligible actuator at all) picks
    between the two very different Notbetrieb texts plan Auftrag 8 asks for --
    a self-regulating thermostat is told to hold its own setpoint and left
    alone ("Thermostat regelt selbst"), a plain switch gets an actual duty
    cycle ("Fußboden taktet"). `None` falls back to the generic "Notbetrieb
    aktiv" wording -- a zone can reach `STAGE_NOTBETRIEB` with no armed
    actuator yet (e.g. right after activation, before the first cycle has run).
    """
    if stage == emergency_operation.STAGE_NORMAL:
        return None
    if stage == emergency_operation.STAGE_ERSATZQUELLE:
        name = source_device_name or "ein anderes Thermostat"
        return ZoneEmergencyBanner(
            stage=stage,
            headline=f"Ersatzquelle aktiv: {name}",
            detail=(
                f"Die übliche Temperaturmessung dieses Raums liefert gerade keinen "
                f"aktuellen Wert. {name} übernimmt sie, bis sie sich wieder meldet."
            ),
        )
    if stage == emergency_operation.STAGE_RUECKKEHRPRUEFUNG:
        samples = recovery_samples or 2
        subject = (
            f"Die Temperaturmessung über {source_device_name}"
            if source_device_name
            else "Die übliche Temperaturmessung"
        )
        return ZoneEmergencyBanner(
            stage=stage,
            headline=f"Rückkehrprüfung läuft ({recovery_sample_count}/{samples})",
            detail=(
                f"{subject} liefert wieder Werte. Sobald sich das eine Weile "
                "bestätigt, übernimmt die normale Regelung wieder."
            ),
        )
    # STAGE_NOTBETRIEB
    # Eine gemischte Zone (Thermostat UND Fußbodenschalter) tut beides
    # gleichzeitig: `actuator_kind` ist dann KIND_THERMOSTAT, und die
    # `switch_*`-Argumente tragen den Takt des Schalters dazu. Ohne sie (oder
    # solange der Schalter noch keinen Takt hat) bleibt es der reine
    # Thermostat-Text.
    if actuator_kind == emergency_actuator_plan.KIND_THERMOSTAT:
        setpoint = emergency_setpoint_c if emergency_setpoint_c is not None else Decimal("20")
        headline = f"Thermostat regelt selbst (Notsollwert {setpoint} °C)"
        detail = (
            "Kein Temperaturwert verfügbar. Das Thermostat hält selbst "
            f"{setpoint} °C, bis wieder ein Messwert da ist."
        )
        if switch_cycle_phase is not None:
            on_min, off_min, outdoor = _cycle_parts(
                switch_on_seconds, switch_off_seconds, switch_cycle_source
            )
            headline = f"Thermostat regelt selbst; Fußboden taktet {on_min}/{off_min} min"
            detail += (
                f" Der Fußboden taktet in festen Abständen ({on_min} Min. an, "
                f"{off_min} Min. aus{outdoor})."
            )
        return ZoneEmergencyBanner(stage=stage, headline=headline, detail=detail)
    if actuator_kind == emergency_actuator_plan.KIND_SWITCH and cycle_phase is not None:
        on_minutes, off_minutes, outdoor_text = _cycle_parts(
            on_seconds, off_seconds, cycle_source
        )
        return ZoneEmergencyBanner(
            stage=stage,
            headline=f"Notbetrieb: Fußboden taktet {on_minutes}/{off_minutes} min",
            detail=(
                "Der Raumfühler meldet gerade keinen Wert. Die Fußbodenheizung "
                f"läuft deshalb in festen Abständen ({on_minutes} Min. an, "
                f"{off_minutes} Min. aus{outdoor_text}), bis wieder ein "
                "Messwert da ist."
            ),
        )
    return ZoneEmergencyBanner(
        stage=stage,
        headline="Notbetrieb aktiv",
        detail=(
            "Kein Temperaturwert verfügbar. Die Heizung läuft nach einem "
            "festen Zeitplan, bis wieder ein Messwert da ist."
        ),
    )


# Human labels for the four stages -- used by the control page, which (unlike
# `zone_banner` above) is allowed the technical vocabulary (Betreiberseite,
# not Bewohneransicht).
STAGE_LABELS: dict[str, str] = {
    emergency_operation.STAGE_NORMAL: "Normal",
    emergency_operation.STAGE_ERSATZQUELLE: "Ersatzquelle",
    emergency_operation.STAGE_NOTBETRIEB: "Notbetrieb",
    emergency_operation.STAGE_RUECKKEHRPRUEFUNG: "Rückkehrprüfung",
}


def handover_status_text(
    attempted_at_known: bool, result: str | None
) -> str:
    """Betreibertext für eine Übergabe/Rückstellung je Thermostat (Auftrag 8b
    item 2): "versucht, Ergebnis unbekannt" ist ein eigener, sichtbarer Zustand
    -- nicht derselbe wie "nie versucht" und nicht derselbe wie "fehlgeschlagen"
    (siehe `docs/STATUS.md` Befund 2 zu Absturz zwischen Markierung und Versand).
    """
    if not attempted_at_known:
        return "nicht versucht"
    if result is None:
        return "versucht, Ergebnis unbekannt"
    if result == "executed":
        return "erfolgreich"
    if result == "failed":
        return "fehlgeschlagen"
    return result


_OUTDOOR_STATUS_TEXT = {
    "ok": "in Ordnung",
    "veraltet": "veraltet (Quelle meldet nicht mehr)",
    "keine_quelle": "keine Außenquelle eingerichtet",
}


def outdoor_status_text(status: str) -> str:
    """Klartext für den Außenwertstatus der Betriebsseite; REST/MCP behalten den
    Code. Ein unbekannter Code bleibt sichtbar statt zu verschwinden."""
    return _OUTDOOR_STATUS_TEXT.get(status, status)


def suggested_offset_text(mean_deviation_k: Decimal | None) -> str | None:
    """"vorgeschlagener Ausgleichswert ≈ x K" (Auftrag 8b item 2, Entscheidung
    R2) -- `None` when there is nothing to compare yet (empty/all-unusable
    history), never a guessed value (Grundsatz 1)."""
    if mean_deviation_k is None:
        return None
    sign = "+" if mean_deviation_k >= 0 else ""
    return f"vorgeschlagener Ausgleichswert ≈ {sign}{mean_deviation_k} K"
