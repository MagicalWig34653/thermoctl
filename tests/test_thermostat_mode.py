"""Die Entscheidungstabelle der Thermostat-Betriebsarten (`domain/thermostat_mode.py`)."""

import pytest

from thermoctl.domain.thermostat_mode import (
    MODE_DESCRIPTIONS,
    MODE_LABELS,
    MODES,
    switches,
    validate_mode,
    writes_actual,
    writes_setpoint,
)


@pytest.mark.parametrize(
    ("mode", "setpoint", "actual", "switching"),
    [
        ("controlled", False, False, True),
        ("self_regulating", True, True, False),
        ("reading_only", False, True, False),
    ],
)
def test_each_mode_decides_what_thermoctl_writes(
    mode: str, setpoint: bool, actual: bool, switching: bool
) -> None:
    assert (writes_setpoint(mode), writes_actual(mode), switches(mode)) == (
        setpoint,
        actual,
        switching,
    )


def test_only_reading_only_writes_an_actual_without_a_setpoint() -> None:
    assert [m for m in MODES if writes_actual(m) and not writes_setpoint(m)] == ["reading_only"]


def test_every_mode_has_a_label_and_a_description() -> None:
    assert set(MODE_LABELS) == set(MODES) == set(MODE_DESCRIPTIONS)


def test_validate_mode_rejects_unknown_codes() -> None:
    assert validate_mode("reading_only") == "reading_only"
    with pytest.raises(ValueError, match="Betriebsart"):
        validate_mode("an")


# --- Aufrufer: eine Betriebsart, ein Verhalten an jeder Stelle ------------------

from sqlalchemy.orm import Session  # noqa: E402

from tests.helpers import capability, create_device, create_zone, role  # noqa: E402
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice  # noqa: E402
from thermoctl.domain.switch_commands import switch_commands, thermostat_commands  # noqa: E402
from thermoctl.services.shadow_run import zone_actuator_assignments  # noqa: E402


def _valve(session: Session, name: str, mode: str, *, capability_code: str = "thermostat"):  # type: ignore[no-untyped-def]
    zone = create_zone(session, name)
    device = create_device(session, f"{name}-ventil")
    session.add(
        DeviceCapabilityLink(
            device_id=device.id, capability_id=capability(session, capability_code).id
        )
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            thermostat_mode=mode,
        )
    )
    session.flush()
    return zone, device


def test_a_reading_only_valve_is_never_switched(session: Session) -> None:
    zone, _device = _valve(session, "nurist-ein-aus", "reading_only")

    assert thermostat_commands(session, zone) == []
    assert switch_commands(session, zone) == []


def test_a_controlled_valve_is_still_switched(session: Session) -> None:
    zone, device = _valve(session, "geschaltet", "controlled")

    assert [c.device.id for c in thermostat_commands(session, zone)] == [device.id]


def test_a_reading_only_valve_gets_no_emergency_handover_entry(session: Session) -> None:
    """Notbetrieb-Übergabe schreibt `operating_mode` und einen Sollwert. Beides ist für
    ein `reading_only`-Ventil ausgeschlossen -- es taucht im Aktorplan nicht auf."""
    zone, _ = _valve(session, "nurist-notbetrieb", "reading_only")
    other, device = _valve(session, "regelt-notbetrieb", "self_regulating")

    assert zone_actuator_assignments(session, zone) == []
    assert [d.id for _zd, d, _kind in zone_actuator_assignments(session, other)] == [device.id]
