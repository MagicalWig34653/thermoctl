from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_state,
    create_settings,
    create_shadow_decision,
    create_zone,
    create_zone_state,
    source,
    user_with_permissions,
    zone_with_schedule,
)
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.config import Settings
from thermoctl.db.models.device import DeviceCapabilityLink
from thermoctl.db.models.lookup import DeviceCapability
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.control import arm
from thermoctl.mcp import server


def _token(session: Session, name: str, permissions: list[tuple[str, int | None]]) -> str:
    nutzer = user_with_permissions(session, name, permissions)
    _objekt, plaintext = token_ausstellen(session, nutzer, name, permissions, None)
    return plaintext


def test_zonen_auflisten_beachtet_zoneneinschraenkung(session: Session) -> None:
    first = create_zone(session, "zone-eins")
    create_zone(session, "zone-zwei")
    plaintext = _token(session, "zonenleser", [("zone.read", first.id)])

    result = server.list_zones(session, plaintext)

    assert result == [
        {"name": first.name, "display_name": first.display_name, "operating_mode": "auto"}
    ]


def test_zonenzustand_liefert_messwert_und_sensorzustand(session: Session) -> None:
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


def test_sollwert_erklaeren_reicht_domaenenbegruendung_durch(session: Session) -> None:
    zone = zone_with_schedule(
        session, "sollwertzone", [(1, 8 * 60, "tag-sollwertzone", Decimal("21.0"))]
    )
    plaintext = _token(session, "sollwertleser", [("zone.read", zone.id)])
    now = datetime(2026, 8, 31, 9, 0)

    expected = server.resolved_setpoint(session, zone, now)
    result = server.explain_setpoint(session, plaintext, zone.id, now)

    assert result["reason"] == expected.grund
    assert result["temperature_c"] == str(expected.temperature_c)


def test_zeitplan_und_sollwerte_lesen_nennen_die_modi(session: Session) -> None:
    zone = zone_with_schedule(session, "lesezone", [(2, 390, "tag-lesezone", Decimal("20.5"))])
    plaintext = _token(session, "konfigurationsleser", [("zone.read", zone.id)])

    assert server.read_schedule(session, plaintext, zone.id) == [
        {"weekday": 2, "minute_of_day": 390, "mode": "Tag-lesezone"}
    ]
    assert {
        entry["mode"]: entry["temperature_c"]
        for entry in server.read_setpoints(session, plaintext, zone.id)
    }["Tag-lesezone"] == "20.5"


def test_geraete_auflisten_liefert_faehigkeiten_und_gesundheit(session: Session) -> None:
    device = create_device(session, "testgeraet")
    capability = DeviceCapability(code="temperature", label="Temperatur")
    session.add(capability)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    gesund = create_device_state(session, device)
    gesund.battery_percent = Decimal("87.50")
    plaintext = _token(session, "geraeteleser", [("device.read", None)])

    result = server.list_devices(session, plaintext)

    assert result[0]["capabilities"] == ["temperature"]
    assert result[0]["batterie_prozent"] == "87.50"


def test_geraete_auflisten_verweigert_fehlendes_recht(session: Session) -> None:
    create_device(session, "unsichtbares-geraet")
    plaintext = _token(session, "ohnegeraeterecht", [("zone.read", None)])

    with pytest.raises(Forbidden, match="device.read"):
        server.list_devices(session, plaintext)


def test_schattenentscheidungen_liefert_juengste_begruendung(session: Session) -> None:
    zone = create_zone(session, "schattenzone")
    entscheidung = create_shadow_decision(session, zone)
    plaintext = _token(session, "schattenleser", [("zone.read", zone.id)])

    result = server.shadow_decisions(session, plaintext, zone.id, 1)

    assert result == [
        {
            "moment": entscheidung.decided_at.isoformat(),
            "ist_c": None,
            "soll_c": None,
            "sollwert_begruendung": "Zeitplan",
            "would_heat": False,
            "outcome": "aus",
            "reason": "Sollwert ist erreicht.",
        }
    ]


def test_uebersteuern_ruft_domaenenmutation_mit_tokenbezug_auf(session: Session) -> None:
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


def test_uebersteuerung_aufheben_beendet_historieneintrag(session: Session) -> None:
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


def test_start_ohne_mcp_token_wird_verweigert(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None, database_url="sqlite://", secret_key="x" * 32, mcp_token=None
    )
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="THERMOCTL_MCP_TOKEN fehlt"):
        server.main()


def test_fehlendes_mcp_paket_wird_verstaendlich_gemeldet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ohne_mcp(name: str) -> object:
        raise ModuleNotFoundError("kein mcp", name=name)

    monkeypatch.setattr(server, "import_module", ohne_mcp)

    with pytest.raises(RuntimeError, match=r"thermoctl\[mcp\]"):
        server._mcp_server_class()


def test_registrierte_mcp_werkzeuge_rufen_die_adapterfunktionen_auf(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die registrierten Werkzeuge tragen echte Schemas und sind nicht nur Namen."""
    zone = zone_with_schedule(
        session, "registrierungszone", [(1, 0, "tag-registrierung", Decimal("20.0"))]
    )
    source(session, "api")
    create_zone_state(session, zone)
    create_shadow_decision(session, zone)
    create_device(session, "registrierungsgeraet")
    permissions = [
        ("zone.read", None),
        ("device.read", None),
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
        "override",
        "cancel_override",
        "boost",
        "read_control_parameters",
        "set_control_parameters",
        "read_control",
        "force_dry_run",
        "move_schedule_point",
    }
    assert tools["list_zones"]()  # type: ignore[operator]
    assert tools["zone_state"](zone.id)  # type: ignore[operator]
    assert tools["explain_setpoint"](zone.id)  # type: ignore[operator]
    assert tools["read_schedule"](zone.id)  # type: ignore[operator]
    assert tools["read_setpoints"](zone.id)  # type: ignore[operator]
    assert tools["list_devices"]()  # type: ignore[operator]
    assert tools["shadow_decisions"](zone.id, 1)  # type: ignore[operator]
    assert tools["override"](zone.id, Decimal("21.0"))  # type: ignore[operator]
    assert tools["cancel_override"](zone.id)  # type: ignore[operator]
    assert tools["boost"](zone.id)  # type: ignore[operator]
    assert tools["read_control_parameters"](zone.id)  # type: ignore[operator]
    assert tools["set_control_parameters"](  # type: ignore[operator]
        zone.id, "hysteresis_k", Decimal("0.4")
    )
    assert tools["read_control"]()  # type: ignore[operator]
    # `trockenlauf_erzwingen` meldet `geaendert: False`, wenn schon Trockenlauf herrscht --
    # deshalb auf den Schluessel pruefen und nicht auf Wahrheit des Ergebnisses.
    assert "armed" in tools["force_dry_run"]()  # type: ignore[operator]
    point = session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)).first()
    assert point is not None
    assert tools["move_schedule_point"](  # type: ignore[operator]
        zone.id, point.id, 4, 480
    )


def test_unbekanntes_token_wird_abgewiesen(session: Session) -> None:
    """Der Adapter darf keine Hintertuer an der Anmeldung vorbei sein."""
    with pytest.raises(PermissionError):
        server.list_zones(session, "tctl_00000000_gibtesnicht")


def test_fremde_zone_ist_nicht_auffindbar(session: Session) -> None:
    """Nicht 'verboten', sondern 'gibt es nicht' — sonst verraet die Antwort, welche
    Zonen existieren. Der REST-Adapter haelt es genauso."""
    eigene = create_zone(session, "eigene-zone")
    fremde = create_zone(session, "fremde-zone")
    plaintext = _token(session, "eingeschraenkt", [("zone.read", eigene.id)])

    with pytest.raises(LookupError):
        server.zone_state(session, plaintext, fremde.id)


def test_ohne_zonenrecht_gibt_es_keine_leere_liste_sondern_eine_verweigerung(
    session: Session,
) -> None:
    """Eine leere Liste waere die falsche Antwort: Sie sieht aus wie 'keine Zonen
    vorhanden' und verdeckt, dass schlicht das Recht fehlt."""
    create_zone(session, "zone-ohne-zugriff")
    plaintext = _token(session, "rechtelos", [("token.self", None)])

    with pytest.raises(Forbidden):
        server.list_zones(session, plaintext)


def test_zonenzustand_ohne_messung_meldet_leere_werte(session: Session) -> None:
    """Eine frisch angelegte Zone hat noch keinen Zustand — das ist kein Fehler."""
    zone = create_zone(session, "zone-ohne-zustand")
    plaintext = _token(session, "leser-ohne-zustand", [("zone.read", None)])

    assert server.zone_state(session, plaintext, zone.id) == {
        "temperature_c": None,
        "measured_at": None,
        "sensor_state": None,
    }


@pytest.mark.parametrize("count", [0, -1, 101])
def test_schattenentscheidungen_weist_unsinnige_anzahl_ab(session: Session, count: int) -> None:
    """Ohne Obergrenze koennte ein Aufruf die ganze Historie ziehen."""
    zone = create_zone(session, f"zone-anzahl-{count}")
    plaintext = _token(session, f"leser-anzahl-{count}", [("zone.read", None)])

    with pytest.raises(ValueError):
        server.shadow_decisions(session, plaintext, zone.id, count)


def test_uebersteuern_weist_unsinnige_temperatur_ab(session: Session) -> None:
    """Der MCP-Server pruefte die Temperatur bis zum Abschlussreview gar nicht.

    Er ist der Adapter, der am ehesten unbeaufsichtigt aufgerufen wird — von einem
    Werkzeug, nicht von einem Menschen, der den Wert noch einmal ansieht. Ein
    `temperature_c=99` waere dort angekommen und flosse in Teilprojekt 4 ungefiltert in
    die scharfe Regelentscheidung. Die Grenze liegt jetzt in der Domaene und gilt fuer
    alle drei Adapter.
    """
    from decimal import Decimal

    from thermoctl.domain.modes import DomainError

    zone = create_zone(session, "zone-mcp-grenze")
    create_settings(session)
    source(session, "mcp")
    plaintext = _token(session, "uebersteuerer", [("override.create", None), ("zone.read", None)])

    # -5 ist seit der Absenkung auf -20 ein gueltiger Sollwert: "hier wird nicht
    # geheizt". Unbrauchbar bleibt, was darunter liegt.
    for value in (Decimal("99"), Decimal("-30"), Decimal("21.55")):
        with pytest.raises(DomainError):
            server.override_zone(session, plaintext, zone.id, value, None)


# --- Steuerung ueber MCP ---------------------------------------------------


def test_steuerung_lesen_zeigt_den_betriebszustand(session: Session) -> None:
    create_settings(session)
    plaintext = _token(session, "leser", [("zone.read", None)])
    response = server.read_control(session, plaintext)
    assert response["armed"] is False
    assert response["timezone"]


def test_steuerung_lesen_braucht_zone_read(session: Session) -> None:
    create_settings(session)
    plaintext = _token(session, "rechtlos", [("device.read", None)])
    with pytest.raises(Forbidden):
        server.read_control(session, plaintext)


def test_trockenlauf_erzwingen_nimmt_die_anlage_zurueck(session: Session) -> None:
    create_settings(session)
    source(session, "mcp")
    source(session, "web")
    arm(session, True, reason="von Hand", user_id=None)
    plaintext = _token(session, "notaus", [("zone.read", None), ("control.arm", None)])

    response = server.force_dry_run(session, plaintext, "Assistent nimmt zurück")
    assert response == {"armed": False, "changed": True}
    assert session.get(Setting, 1).control_armed is False


def test_mcp_kann_nicht_scharf_schalten(session: Session) -> None:
    """Bewusste Asymmetrie zu REST und Oberflaeche, dokumentiert in
    docs/offene-entscheidungen.md: Der MCP-Server spricht fuer ein Sprachmodell, und die
    Begruendung, die die Domaene beim Scharfschalten verlangt, ist fuer ein Modell keine
    Huerde. Es gibt hier deshalb kein Werkzeug in diese Richtung -- dieser Test haelt das
    fest, damit es niemand aus Symmetriegefuehl nachtraegt."""
    assert not [
        name
        for name in dir(server)
        if "armed" in name.lower() and name != "arm"
    ]
    # `scharf_schalten` ist die importierte Domaenenfunktion, kein Werkzeug: Sie wird
    # ausschliesslich mit `False` aufgerufen.
    quelltext = (
        Path(server.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    )
    assert quelltext.count("arm(") == 1
    assert "        False," in quelltext


def test_zeitplanpunkt_verschieben_ueber_mcp(session: Session) -> None:
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
    # `zone_mit_zeitplan` legt selbst die Einstellungszeile an und vertraegt deshalb
    # nur einen Aufruf je Test; die zweite Zone bekommt ihren Punkt von Hand.
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


def test_boost_zieht_die_naechste_schaltung_vor(session: Session) -> None:
    """Fuer ein Sprachmodell die verlaessliche Form von „mach es hier waermer".

    Es muss weder eine Temperatur noch eine Dauer raten, und nach dem Schaltpunkt
    raeumt sich der Eingriff selbst weg.
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


def test_boost_braucht_das_recht_zu_uebersteuern(session: Session) -> None:
    """Gegenprobe: Lesen allein reicht nicht, obwohl der Aufruf kein Argument traegt."""
    zone = zone_with_schedule(session, "boostsperre", [(1, 0, "tag-sperre", Decimal("21.0"))])
    plaintext = _token(session, "nurleser", [("zone.read", zone.id)])

    with pytest.raises(Forbidden):
        server.boost(session, plaintext, zone.id)


def test_regelparameter_lesen_liefert_die_grenzen_mit(session: Session) -> None:
    """Ohne sie waere jeder Schreibversuch ein Versuch.

    „0,05 Kelvin Hysterese" sieht fuer ein Sprachmodell so plausibel aus wie „0,5" --
    die Grenzen gehoeren deshalb in dieselbe Antwort und nicht in die Dokumentation.
    """
    zone = zone_with_schedule(session, "parameterzone", [(1, 0, "tag-p", Decimal("21.0"))])
    plaintext = _token(session, "parameterleser", [("zone.read", zone.id)])

    result = server.read_control_parameters(session, plaintext, zone.id)

    parameter = {p["name"]: p for p in result["parameter"]}  # type: ignore[union-attr]
    assert parameter["hysteresis_k"]["minimum"] == "0.1"
    assert parameter["hysteresis_k"]["maximum"] == "5.0"
    # Und ob der Wert dieser Zone gehoert oder vom globalen Standard kommt.
    assert parameter["hysteresis_k"]["own_value"] is False


def test_regelparameter_setzen_laesst_die_uebrigen_geerbt(session: Session) -> None:
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
    assert zone.min_on_seconds is None, "ein geerbter Wert wurde festgeschrieben"


def test_regelparameter_setzen_braucht_zone_manage(session: Session) -> None:
    """`zone.manage`, nicht `override.create`.

    Ein Regelparameter wirkt dauerhaft und auf jede kuenftige Entscheidung, eine
    Uebersteuerung nur bis zum naechsten Schaltpunkt.
    """
    zone = zone_with_schedule(session, "setzsperre", [(1, 0, "tag-ss", Decimal("21.0"))])
    plaintext = _token(
        session, "uebersteuerer", [("zone.read", zone.id), ("override.create", zone.id)]
    )

    with pytest.raises(Forbidden):
        server.set_control_parameters(
            session, plaintext, zone.id, "hysteresis_k", Decimal("0.4")
        )
