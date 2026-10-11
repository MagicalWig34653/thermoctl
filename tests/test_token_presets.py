"""Presets für Token-Rechte (Read-only / Read-write) und das Recht `control.disarm`."""

import pytest

from thermoctl.db.models.lookup import PERMISSIONS
from thermoctl.domain.token_presets import (
    PRESETS,
    READ_ONLY,
    READ_WRITE,
    preset_permissions,
)

ALL_CODES = {code for code, _description, _zone_scoped in PERMISSIONS}


def test_read_only_holds_exactly_the_three_read_permissions() -> None:
    assert READ_ONLY == {"zone.read", "device.read", "audit.read"}


def test_read_write_is_read_only_plus_the_writing_mcp_permissions() -> None:
    assert READ_ONLY < READ_WRITE
    assert READ_WRITE - READ_ONLY == {
        "override.create", "override.cancel", "vacation.manage", "zone.manage",
        "schedule.manage", "setting.manage", "control.disarm",
    }


def test_the_preset_table_names_both_presets() -> None:
    assert PRESETS == {"read_only": READ_ONLY, "read_write": READ_WRITE}


@pytest.mark.parametrize(
    "forbidden",
    ["control.arm", "user.manage", "group.manage", "token.manage", "token.self",
     "device.manage"],
)
def test_dangerous_permissions_are_in_no_preset(forbidden: str) -> None:
    """Scharfschalten, Benutzer-/Gruppen-/Token-Verwaltung und Gerätezuordnung bleiben
    Einzelhaken: ein Preset ist ein bequemer Ausgangspunkt, kein Freibrief."""
    for name, codes in PRESETS.items():
        assert forbidden not in codes, f"{forbidden} steckt im Preset {name}"


def test_every_preset_permission_exists_as_a_real_permission() -> None:
    for name, codes in PRESETS.items():
        assert codes <= ALL_CODES, f"{name}: unbekannt {sorted(codes - ALL_CODES)}"


def test_preset_permissions_returns_plant_wide_grants_sorted() -> None:
    grants = preset_permissions("read_only")
    assert grants == [("audit.read", None), ("device.read", None), ("zone.read", None)]
    assert {code for code, _ in preset_permissions("read_write")} == READ_WRITE
    assert all(zone_id is None for _code, zone_id in preset_permissions("read_write"))


def test_an_unknown_preset_raises_key_error() -> None:
    with pytest.raises(KeyError):
        preset_permissions("admin")


def test_control_disarm_is_a_plant_wide_permission_in_the_list() -> None:
    entry = [p for p in PERMISSIONS if p[0] == "control.disarm"]
    assert len(entry) == 1
    assert entry[0][2] is False
    # Neue Rechte stehen am Ende der Liste (siehe Kommentar in lookup.py).
    assert PERMISSIONS[-1][0] == "control.disarm"


def test_control_disarm_sits_in_the_plant_operation_area() -> None:
    from thermoctl.domain.authz import PERMISSION_AREAS

    areas = {name: codes for name, _hint, codes in PERMISSION_AREAS}
    assert "control.disarm" in areas["Betrieb der Anlage"]
