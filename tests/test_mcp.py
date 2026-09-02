from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    capability,
    create_device,
    create_device_command,
    create_device_state,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    integration,
    role,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.tokens import issue_token
from thermoctl.config import Settings
from thermoctl.db.models.device import Device, DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.control import arm, save_solar_location
from thermoctl.mcp import server


def _assign_switch_actuator(session: Session, zone: Zone) -> None:
    """The minimal assignment `pi_eligible()` accepts."""
    device = Device(
        integration_id=integration(session).id,
        external_id=f"{zone.name}-relais",
        display_name=f"{zone.name}-relais",
    )
    session.add(device)
    session.flush()
    session.add(
        DeviceCapabilityLink(device_id=device.id, capability_id=capability(session, "switch").id)
    )
    session.add(
        ZoneDevice(
            zone_id=zone.id,
            device_id=device.id,
            device_role_id=role(session, "actuator").id,
            self_regulating=False,
        )
    )
    session.flush()


def _token(session: Session, name: str, permissions: list[tuple[str, int | None]]) -> str:
    user_record = user_with_permissions(session, name, permissions)
    _objekt, plaintext = issue_token(session, user_record, name, permissions, None)
    return plaintext


def test_listing_zones_respects_the_zone_restriction(session: Session) -> None:
    first = create_zone(session, "zone-eins")
    create_zone(session, "zone-zwei")
    plaintext = _token(session, "zonenleser", [("zone.read", first.id)])

    result = server.list_zones(session, plaintext)

    assert result == [
        {
            "name": first.name,
            "display_name": first.display_name,
            "operating_mode": "auto",
            "solar_gain_factor": "0.00",
        }
    ]


def test_zone_state_returns_the_reading_and_the_sensor_state(session: Session) -> None:
    zone = create_zone(session, "zustandszone")
    state = create_zone_state(session, zone)
    state.temperature_c = Decimal("20.25")
    state.measured_at = datetime(2026, 8, 29, 7, 30)
    plaintext = _token(session, "zustandsleser", [("zone.read", zone.id)])

    result = server.zone_state(session, plaintext, zone.id)

    assert result == {
        "temperature_c": "20.25",
        "measured_at": "2026-08-29T07:30:00",
        "sensor_state": "ok",
    }


def test_explaining_the_setpoint_passes_the_domain_reason_through(session: Session) -> None:
    zone = zone_with_schedule(
        session, "sollwertzone", [(1, 8 * 60, "tag-sollwertzone", Decimal("21.0"))]
    )
    plaintext = _token(session, "sollwertleser", [("zone.read", zone.id)])
    now = datetime(2026, 8, 31, 9, 0)

    expected = server.resolved_setpoint(session, zone, now)
    result = server.explain_setpoint(session, plaintext, zone.id, now)

    assert result["reason"] == expected.reason
    assert result["temperature_c"] == str(expected.temperature_c)


def test_reading_schedule_and_setpoints_names_the_modes(session: Session) -> None:
    zone = zone_with_schedule(session, "lesezone", [(2, 390, "tag-lesezone", Decimal("20.5"))])
    plaintext = _token(session, "konfigurationsleser", [("zone.read", zone.id)])

    assert server.read_schedule(session, plaintext, zone.id) == [
        {"weekday": 2, "minute_of_day": 390, "mode": "Tag-lesezone"}
    ]
    assert {
        entry["mode"]: entry["temperature_c"]
        for entry in server.read_setpoints(session, plaintext, zone.id)
    }["Tag-lesezone"] == "20.5"


def test_listing_devices_returns_capabilities_and_health(session: Session) -> None:
    device = create_device(session, "testgeraet")
    capability = DeviceCapability(code="temperature", label="Temperatur")
    session.add(capability)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    healthy = create_device_state(session, device)
    healthy.battery_percent = Decimal("87.50")
    plaintext = _token(session, "geraeteleser", [("device.read", None)])

    result = server.list_devices(session, plaintext)

    assert result[0]["capabilities"] == ["temperature"]
    assert result[0]["batterie_prozent"] == "87.50"


def test_listing_devices_denies_a_missing_permission(session: Session) -> None:
    create_device(session, "unsichtbares-geraet")
    plaintext = _token(session, "ohnegeraeterecht", [("zone.read", None)])

    with pytest.raises(Forbidden, match="device.read"):
        server.list_devices(session, plaintext)


def test_shadow_decisions_returns_the_most_recent_reason(session: Session) -> None:
    zone = create_zone(session, "schattenzone")
    decision = create_shadow_decision(session, zone)
    plaintext = _token(session, "schattenleser", [("zone.read", zone.id)])

    result = server.shadow_decisions(session, plaintext, zone.id, 1)

    assert result == [
        {
            "moment": decision.decided_at.isoformat(),
            "ist_c": None,
            "soll_c": None,
            "setpoint_reason": "Zeitplan",
            "would_heat": False,
            "outcome": "aus",
            "reason": "Sollwert ist erreicht.",
        }
    ]


def test_device_commands_reports_an_explicit_utc_offset(session: Session) -> None:
    """Same requirement as the REST endpoint -- see CLAUDE.md and `docs/mcp.md`."""
    zone = create_zone(session, "schaltzone")
    geraet = create_device(session, "schaltgeraet")
    create_device_command(session, zone, geraet, at=datetime(2026, 8, 29, 8, 0))
    plaintext = _token(session, "schaltleser", [("audit.read", None)])

    result = server.device_commands(session, plaintext)

    assert result == [
        {
            "sent_at": "2026-08-29T08:00:00+00:00",
            "source": "system",
            "zone": "schaltzone",
            "device": "schaltgeraet",
            "command": "setpoint",
            "payload": '{"occupied_heating_setpoint": 21.0}',
            "outcome": "executed",
            "error": None,
            "reason": "Zeitplan",
        }
    ]


def test_device_commands_denies_a_missing_permission(session: Session) -> None:
    zone = create_zone(session, "unbefugtzone")
    geraet = create_device(session, "unbefugtgeraet")
    create_device_command(session, zone, geraet)
    plaintext = _token(session, "unbefugt", [("zone.read", None)])

    with pytest.raises(Forbidden, match="audit.read"):
        server.device_commands(session, plaintext)


def test_device_commands_filters_by_zone_and_outcome(session: Session) -> None:
    zone_a = create_zone(session, "zone-a")
    zone_b = create_zone(session, "zone-b")
    device_a = create_device(session, "geraet-a")
    device_b = create_device(session, "geraet-b")
    create_device_command(session, zone_a, device_a, outcome_code="executed")
    create_device_command(session, zone_b, device_b, outcome_code="failed")
    plaintext = _token(session, "filterleser", [("audit.read", None)])

    result = server.device_commands(session, plaintext, zone="zone-a")
    assert [entry["device"] for entry in result] == ["geraet-a"]

    result = server.device_commands(session, plaintext, outcome="failed")
    assert [entry["device"] for entry in result] == ["geraet-b"]


def test_device_commands_refuses_a_nonsensical_limit(session: Session) -> None:
    zone = create_zone(session, "grenzzone")
    geraet = create_device(session, "grenzgeraet")
    create_device_command(session, zone, geraet)
    plaintext = _token(session, "grenzleser", [("audit.read", None)])

    with pytest.raises(ValueError):
        server.device_commands(session, plaintext, limit=0)


def test_overriding_calls_the_domain_mutation_with_the_token_attached(session: Session) -> None:
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "uebersteuerungszone")
    plaintext = _token(
        session,
        "uebersteuerer",
        [("zone.read", zone.id), ("override.create", zone.id)],
    )

    result = server.override_zone(session, plaintext, zone.id, Decimal("22.0"))

    entry = session.query(ZoneOverride).one()
    assert result["temperature_c"] == "22.0"
    assert entry.created_by_token_id is not None


def test_cancelling_an_override_ends_the_history_entry(session: Session) -> None:
    create_settings(session)
    source(session, "api")
    zone = create_zone(session, "aufhebungszone")
    plaintext = _token(
        session,
        "aufheber",
        [
            ("zone.read", zone.id),
            ("override.create", zone.id),
            ("override.cancel", zone.id),
        ],
    )
    server.override_zone(session, plaintext, zone.id, Decimal("19.0"))

    result = server.cancel_override(session, plaintext, zone.id)

    assert result["cancelled"] is True
    assert session.query(ZoneOverride).one().cancelled_at is not None


def test_starting_without_an_mcp_token_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key="x" * 32, mcp_token=None
    )
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="THERMOCTL_MCP_TOKEN fehlt"):
        server.main()


def test_a_missing_mcp_package_is_reported_understandably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ohne_mcp(name: str) -> object:
        raise ModuleNotFoundError("kein mcp", name=name)

    monkeypatch.setattr(server, "import_module", ohne_mcp)

    with pytest.raises(RuntimeError, match=r"thermoctl\[mcp\]"):
        server._mcp_server_class()


def test_the_registered_mcp_tools_have_descriptions_and_call_the_adapter_functions(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered wrappers carry client descriptions, schemas, and behavior."""
    zone = zone_with_schedule(
        session, "registrierungszone", [(1, 0, "tag-registrierung", Decimal("20.0"))]
    )
    source(session, "api")
    create_zone_state(session, zone)
    create_shadow_decision(session, zone)
    geraet = create_device(session, "registrierungsgeraet")
    create_device_command(session, zone, geraet)
    permissions = [
        ("zone.read", None),
        ("device.read", None),
        ("audit.read", None),
        ("override.create", zone.id),
        ("override.cancel", zone.id),
        ("schedule.manage", zone.id),
        ("zone.manage", zone.id),
        ("control.arm", None),
    ]
    plaintext = _token(session, "registrierter-nutzer", permissions)

    tools: dict[str, object] = {}

    class TestServer:
        def tool(self, name: str | None = None):  # type: ignore[no-untyped-def]
            def dekorator(function):  # type: ignore[no-untyped-def]
                assert name is not None
                tools[name] = function
                return function

            return dekorator

        def run(self, transport: str = "stdio", **kwargs: object) -> None:
            raise AssertionError("Der Test startet keinen Transport")

    monkeypatch.setattr(server, "session_scope", lambda _factory: nullcontext(session))
    server._register_tools(TestServer(), object(), plaintext)  # type: ignore[arg-type]

    assert tools.keys() == {
        "list_zones",
        "zone_state",
        "explain_setpoint",
        "read_schedule",
        "read_setpoints",
        "list_devices",
        "shadow_decisions",
        "device_commands",
        "override",
        "cancel_override",
        "boost",
        "read_control_parameters",
        "set_control_parameters",
        "read_control",
        "force_dry_run",
        "move_schedule_point",
    }
    descriptions = {
        name: getattr(tool, "__doc__", None) for name, tool in tools.items()
    }
    assert all(description and description.strip() for description in descriptions.values())
    assert "MQTT-latch observability" in descriptions["read_control"]  # type: ignore[operator]
    assert tools["list_zones"]()  # type: ignore[operator]
    assert tools["zone_state"](zone.id)  # type: ignore[operator]
    assert tools["explain_setpoint"](zone.id)  # type: ignore[operator]
    assert tools["read_schedule"](zone.id)  # type: ignore[operator]
    assert tools["read_setpoints"](zone.id)  # type: ignore[operator]
    assert tools["list_devices"]()  # type: ignore[operator]
    assert tools["shadow_decisions"](zone.id, 1)  # type: ignore[operator]
    assert tools["device_commands"]()  # type: ignore[operator]
    assert tools["override"](zone.id, Decimal("21.0"))  # type: ignore[operator]
    assert tools["cancel_override"](zone.id)  # type: ignore[operator]
    assert tools["boost"](zone.id)  # type: ignore[operator]
    assert tools["read_control_parameters"](zone.id)  # type: ignore[operator]
    assert tools["set_control_parameters"](  # type: ignore[operator]
        zone.id, "hysteresis_k", Decimal("0.4")
    )
    assert tools["read_control"]()  # type: ignore[operator]
    # `trockenlauf_erzwingen` reports `geaendert: False` when dry run already applies --
    # so check for the key, not for the truthiness of the result.
    assert "armed" in tools["force_dry_run"]()  # type: ignore[operator]
    point = session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)).first()
    assert point is not None
    assert tools["move_schedule_point"](  # type: ignore[operator]
        zone.id, point.id, 4, 480
    )


def test_an_unknown_token_is_refused(session: Session) -> None:
    """The adapter must not be a backdoor around authentication."""
    with pytest.raises(PermissionError):
        server.list_zones(session, "tctl_00000000_gibtesnicht")


def test_a_foreign_zone_cannot_be_found(session: Session) -> None:
    """Not 'forbidden', but 'does not exist' -- otherwise the response would reveal
    which zones exist. The REST adapter behaves the same way."""
    eigene = create_zone(session, "eigene-zone")
    fremde = create_zone(session, "fremde-zone")
    plaintext = _token(session, "eingeschraenkt", [("zone.read", eigene.id)])

    with pytest.raises(LookupError):
        server.zone_state(session, plaintext, fremde.id)


def test_without_the_zone_permission_there_is_a_denial_not_an_empty_list(
    session: Session,
) -> None:
    """An empty list would be the wrong answer: it looks like 'no zones
    present' and hides that the permission is simply missing."""
    create_zone(session, "zone-ohne-zugriff")
    plaintext = _token(session, "rechtelos", [("token.self", None)])

    with pytest.raises(Forbidden):
        server.list_zones(session, plaintext)


def test_zone_state_without_a_measurement_reports_empty_values(session: Session) -> None:
    """A freshly created zone has no state yet -- that is not an error."""
    zone = create_zone(session, "zone-ohne-zustand")
    plaintext = _token(session, "leser-ohne-zustand", [("zone.read", None)])

    assert server.zone_state(session, plaintext, zone.id) == {
        "temperature_c": None,
        "measured_at": None,
        "sensor_state": None,
    }


@pytest.mark.parametrize("count", [0, -1, 101])
def test_shadow_decisions_refuses_a_nonsensical_count(session: Session, count: int) -> None:
    """Without an upper limit, a single call could pull the entire history."""
    zone = create_zone(session, f"zone-anzahl-{count}")
    plaintext = _token(session, f"leser-anzahl-{count}", [("zone.read", None)])

    with pytest.raises(ValueError):
        server.shadow_decisions(session, plaintext, zone.id, count)


def test_overriding_refuses_a_nonsensical_temperature(session: Session) -> None:
    """The MCP server did not check the temperature at all until the closing review.

    It is the adapter most likely to be called unattended -- by a tool, not by a
    human who looks the value over once more. A `temperature_c=99` would have gone
    through and flowed unfiltered into the live control decision in subproject 4.
    The limit now lives in the domain and applies to all three adapters.
    """
    from decimal import Decimal

    from thermoctl.domain.modes import DomainError

    zone = create_zone(session, "zone-mcp-grenze")
    create_settings(session)
    source(session, "mcp")
    plaintext = _token(session, "uebersteuerer", [("override.create", None), ("zone.read", None)])

    # -5 has been a valid setpoint since the lower limit was dropped to -20: "no
    # heating here". What lies below that stays unusable.
    for value in (Decimal("99"), Decimal("-30"), Decimal("21.55")):
        with pytest.raises(DomainError):
            server.override_zone(session, plaintext, zone.id, value, None)


# --- Control via MCP --------------------------------------------------------


def test_reading_control_shows_the_operating_state(session: Session) -> None:
    create_settings(session)
    plaintext = _token(session, "leser", [("zone.read", None)])
    response = server.read_control(session, plaintext)
    assert response["armed"] is False
    assert response["mqtt_startup_latch_state"] == "unknown_from_mcp_process"
    assert response["timezone"]


def test_steuerung_lesen_braucht_zone_read(session: Session) -> None:
    create_settings(session)
    plaintext = _token(session, "rechtlos", [("device.read", None)])
    with pytest.raises(Forbidden):
        server.read_control(session, plaintext)


def test_forcing_dry_run_takes_the_installation_back(session: Session) -> None:
    create_settings(session)
    source(session, "mcp")
    source(session, "web")
    arm(session, True, reason="von Hand", user_id=None)
    plaintext = _token(session, "notaus", [("zone.read", None), ("control.arm", None)])

    response = server.force_dry_run(session, plaintext, "Assistent nimmt zurück")
    assert response == {"armed": False, "changed": True}
    assert session.get(Setting, 1).control_armed is False


def test_mcp_cannot_arm_the_control(session: Session) -> None:
    """A deliberate asymmetry to REST and the UI, documented in
    docs/offene-entscheidungen.md: the MCP server speaks for a language model, and
    the justification the domain requires for arming is no hurdle at all for a
    model. There is therefore no tool in this direction here -- this test records
    that so nobody adds one later out of a sense of symmetry."""
    assert not [
        name
        for name in dir(server)
        if "armed" in name.lower() and name != "arm"
    ]
    # `scharf_schalten` is the imported domain function, not a tool: it is called
    # exclusively with `False`.
    quelltext = (
        Path(server.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    )
    assert quelltext.count("arm(") == 1
    assert "        False," in quelltext


def test_moving_a_schedule_point_through_mcp(session: Session) -> None:
    zone = zone_with_schedule(session, "mcp-zeitplan", [(1, 360, "tag-mcp", Decimal("21.0"))])
    source(session, "web")
    point = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
    ).one()
    plaintext = _token(
        session, "planer", [("zone.read", None), ("schedule.manage", zone.id)]
    )
    response = server.move_schedule_point(session, plaintext, zone.id, point.id, 5, 450)
    assert response["weekday"] == 5
    assert response["minute"] == 450
    session.refresh(point)
    assert point.id == response["point_id"]


def test_verschieben_eines_fremden_punktes_scheitert(session: Session) -> None:
    # `zone_mit_zeitplan` creates the settings row itself and therefore tolerates
    # only one call per test; the second zone gets its point set up by hand.
    zone = zone_with_schedule(session, "eigen", [(1, 360, "tag-eigen", Decimal("21.0"))])
    fremde = create_zone(session, "fremd")
    foreign_point = SchedulePoint(
        zone_id=fremde.id,
        weekday=1,
        minute_of_day=360,
        setpoint_mode_id=session.scalars(select(SetpointMode)).first().id,
    )
    session.add(foreign_point)
    session.flush()
    plaintext = _token(
        session, "planer2", [("zone.read", zone.id), ("schedule.manage", zone.id)]
    )
    with pytest.raises(ValueError, match="nicht gefunden"):
        server.move_schedule_point(
            session, plaintext, zone.id, foreign_point.id, 5, 450
        )


def test_boost_brings_the_next_switch_forward(session: Session) -> None:
    """The reliable form of "make it warmer here" for a language model.

    It has to guess neither a temperature nor a duration, and after the schedule
    point the intervention cleans itself up.
    """
    zone = zone_with_schedule(
        session,
        "boostzone",
        [(1, 0, "tag-boost", Decimal("21.0")), (1, 1320, "nacht-boost", Decimal("18.0"))],
    )
    source(session, "mcp")
    plaintext = _token(
        session, "boostnutzer", [("zone.read", zone.id), ("override.create", zone.id)]
    )

    result = server.boost(session, plaintext, zone.id)

    assert result["zone"] == zone.name
    assert result["mode"] in ("tag-boost", "nacht-boost")
    assert result["valid_until"] is not None
    assert Decimal(str(result["temperature_c"])) in (Decimal("21.0"), Decimal("18.0"))


def test_boost_needs_the_permission_to_override(session: Session) -> None:
    """Counter-check: reading alone is not enough, even though the call takes no argument."""
    zone = zone_with_schedule(session, "boostsperre", [(1, 0, "tag-sperre", Decimal("21.0"))])
    plaintext = _token(session, "nurleser", [("zone.read", zone.id)])

    with pytest.raises(Forbidden):
        server.boost(session, plaintext, zone.id)


def test_reading_control_parameters_returns_the_limits_as_well(session: Session) -> None:
    """Without them, every write attempt would just be a guess.

    "0.05 Kelvin hysteresis" looks just as plausible to a language model as "0.5" --
    the limits therefore belong in the same response, not in the documentation.
    """
    zone = zone_with_schedule(session, "parameterzone", [(1, 0, "tag-p", Decimal("21.0"))])
    plaintext = _token(session, "parameterleser", [("zone.read", zone.id)])

    result = server.read_control_parameters(session, plaintext, zone.id)

    parameter = {p["name"]: p for p in result["parameter"]}  # type: ignore[union-attr]
    assert parameter["hysteresis_k"]["minimum"] == "0.1"
    assert parameter["hysteresis_k"]["maximum"] == "5.0"
    # And whether the value belongs to this zone or comes from the global default.
    assert parameter["hysteresis_k"]["own_value"] is False
    assert parameter["valve_protection_interval_days"]["value"] == "30"
    assert parameter["valve_protection_duration_minutes"]["value"] == "10"


def test_setting_a_control_parameter_leaves_the_others_inherited(session: Session) -> None:
    zone = zone_with_schedule(session, "setzzone", [(1, 0, "tag-s", Decimal("21.0"))])
    source(session, "mcp")
    plaintext = _token(
        session, "parameterschreiber", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    result = server.set_control_parameters(
        session, plaintext, zone.id, "hysteresis_k", Decimal("0.4")
    )

    assert result["value"] == "0.4"
    assert zone.hysteresis_k == Decimal("0.4")
    assert zone.min_on_seconds is None, "an inherited value was pinned down"


def test_mcp_can_switch_pi_on_and_off_for_an_eligible_zone(session: Session) -> None:
    """PI (Beta) is not a separate MCP tool -- it goes through the same
    `set_control_parameters` tool as every other control parameter (specification
    section 6, principle 6: implemented once, used by every adapter alike)."""
    zone = zone_with_schedule(session, "pi-mcp-zone", [(1, 0, "tag-pi", Decimal("21.0"))])
    _assign_switch_actuator(session, zone)
    source(session, "mcp")
    plaintext = _token(
        session, "pi-schreiber", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    result = server.set_control_parameters(
        session, plaintext, zone.id, "pi_enabled", Decimal(1)
    )

    assert result["value"] == "1"
    assert zone.pi_enabled is True

    server.set_control_parameters(session, plaintext, zone.id, "pi_enabled", Decimal(0))
    assert zone.pi_enabled is False


def test_mcp_refuses_pi_for_an_ineligible_zone(session: Session) -> None:
    """No switch actuator assigned -- rejected before anything is switched on."""
    zone = zone_with_schedule(session, "pi-ungeeignet-mcp", [(1, 0, "tag-u", Decimal("21.0"))])
    source(session, "mcp")
    plaintext = _token(
        session, "pi-schreiber-2", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    with pytest.raises(ValueError, match="PI-Regelung"):
        server.set_control_parameters(session, plaintext, zone.id, "pi_enabled", Decimal(1))
    assert zone.pi_enabled is False


def test_reading_control_parameters_lists_pi_with_its_bounds(session: Session) -> None:
    """The Beta parameters are read the same way as every other one -- limits and
    current value included, so a language model does not have to guess them."""
    zone = zone_with_schedule(session, "pi-lesen", [(1, 0, "tag-l", Decimal("21.0"))])
    plaintext = _token(session, "pi-leser", [("zone.read", zone.id)])

    result = server.read_control_parameters(session, plaintext, zone.id)

    parameter = {p["name"]: p for p in result["parameter"]}  # type: ignore[union-attr]
    assert parameter["pi_gain_per_k"]["minimum"] == "0.05"
    assert parameter["pi_gain_per_k"]["maximum"] == "0.50"
    assert parameter["pi_enabled"]["value"] == "False"


def test_regelparameter_setzen_braucht_zone_manage(session: Session) -> None:
    """`zone.manage`, not `override.create`.

    A control parameter acts permanently and on every future decision, an override
    only until the next schedule point.
    """
    zone = zone_with_schedule(session, "setzsperre", [(1, 0, "tag-ss", Decimal("21.0"))])
    plaintext = _token(
        session, "uebersteuerer", [("zone.read", zone.id), ("override.create", zone.id)]
    )

    with pytest.raises(Forbidden):
        server.set_control_parameters(
            session, plaintext, zone.id, "hysteresis_k", Decimal("0.4")
        )


def test_read_control_reports_whether_the_solar_setback_is_switched_on(
    session: Session,
) -> None:
    """Without this, an assistant could see the setback's cap and lookahead but never
    learn whether it applies at all -- and would explain a zone heating less than its
    schedule says by guessing.

    The coordinates come back as text like every other number here: through JSON a
    Decimal would arrive as a float, and a coordinate that shifts in its last digit
    points somewhere else.
    """
    create_settings(session)
    source(session, "web")
    plaintext = _token(session, "sonnenleser", [("zone.read", None)])

    off = server.read_control(session, plaintext)
    assert off["solar_forecast_enabled"] is False
    assert off["solar_forecast_latitude"] is None

    save_solar_location(
        session,
        enabled=True,
        latitude_text="52.520",
        longitude_text="13.405",
        user_id=None,
    )
    on = server.read_control(session, plaintext)
    assert on["solar_forecast_enabled"] is True
    assert on["solar_forecast_latitude"] == "52.520"
    assert isinstance(on["solar_forecast_longitude"], str)
