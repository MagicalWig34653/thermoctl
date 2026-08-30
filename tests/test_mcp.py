from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import (
    benutzer_mit_rechten,
    einstellungen_anlegen,
    geraet_anlegen,
    geraetezustand_anlegen,
    quelle,
    schattenentscheidung_anlegen,
    zone_anlegen,
    zone_mit_zeitplan,
    zonenzustand_anlegen,
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
from thermoctl.domain.steuerung import scharf_schalten
from thermoctl.mcp import server


def _token(session: Session, name: str, rechte: list[tuple[str, int | None]]) -> str:
    nutzer = benutzer_mit_rechten(session, name, rechte)
    _objekt, klartext = token_ausstellen(session, nutzer, name, rechte, None)
    return klartext


def test_zonen_auflisten_beachtet_zoneneinschraenkung(session: Session) -> None:
    erste = zone_anlegen(session, "zone-eins")
    zone_anlegen(session, "zone-zwei")
    klartext = _token(session, "zonenleser", [("zone.read", erste.id)])

    ergebnis = server.zonen_auflisten(session, klartext)

    assert ergebnis == [
        {"name": erste.name, "anzeigename": erste.display_name, "betriebsart": "auto"}
    ]


def test_zonenzustand_liefert_messwert_und_sensorzustand(session: Session) -> None:
    zone = zone_anlegen(session, "zustandszone")
    zustand = zonenzustand_anlegen(session, zone)
    zustand.temperature_c = Decimal("20.25")
    zustand.measured_at = datetime(2026, 8, 29, 7, 30)
    klartext = _token(session, "zustandsleser", [("zone.read", zone.id)])

    ergebnis = server.zonenzustand(session, klartext, zone.id)

    assert ergebnis == {
        "temperatur_c": "20.25",
        "messzeitpunkt": "2026-08-29T07:30:00",
        "sensorzustand": "ok",
    }


def test_sollwert_erklaeren_reicht_domaenenbegruendung_durch(session: Session) -> None:
    zone = zone_mit_zeitplan(
        session, "sollwertzone", [(1, 8 * 60, "tag-sollwertzone", Decimal("21.0"))]
    )
    klartext = _token(session, "sollwertleser", [("zone.read", zone.id)])
    jetzt = datetime(2026, 8, 31, 9, 0)

    erwartet = server.aufgeloester_sollwert(session, zone, jetzt)
    ergebnis = server.sollwert_erklaeren(session, klartext, zone.id, jetzt)

    assert ergebnis["begruendung"] == erwartet.grund
    assert ergebnis["temperatur_c"] == str(erwartet.temperature_c)


def test_zeitplan_und_sollwerte_lesen_nennen_die_modi(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "lesezone", [(2, 390, "tag-lesezone", Decimal("20.5"))])
    klartext = _token(session, "konfigurationsleser", [("zone.read", zone.id)])

    assert server.zeitplan_lesen(session, klartext, zone.id) == [
        {"wochentag": 2, "minute_im_tag": 390, "modus": "Tag-lesezone"}
    ]
    assert {
        eintrag["modus"]: eintrag["temperatur_c"]
        for eintrag in server.sollwerte_lesen(session, klartext, zone.id)
    }["Tag-lesezone"] == "20.5"


def test_geraete_auflisten_liefert_faehigkeiten_und_gesundheit(session: Session) -> None:
    geraet = geraet_anlegen(session, "testgeraet")
    faehigkeit = DeviceCapability(code="temperature", label="Temperatur")
    session.add(faehigkeit)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=geraet.id, capability_id=faehigkeit.id))
    gesund = geraetezustand_anlegen(session, geraet)
    gesund.battery_percent = Decimal("87.50")
    klartext = _token(session, "geraeteleser", [("device.read", None)])

    ergebnis = server.geraete_auflisten(session, klartext)

    assert ergebnis[0]["faehigkeiten"] == ["temperature"]
    assert ergebnis[0]["batterie_prozent"] == "87.50"


def test_geraete_auflisten_verweigert_fehlendes_recht(session: Session) -> None:
    geraet_anlegen(session, "unsichtbares-geraet")
    klartext = _token(session, "ohnegeraeterecht", [("zone.read", None)])

    with pytest.raises(Forbidden, match="device.read"):
        server.geraete_auflisten(session, klartext)


def test_schattenentscheidungen_liefert_juengste_begruendung(session: Session) -> None:
    zone = zone_anlegen(session, "schattenzone")
    entscheidung = schattenentscheidung_anlegen(session, zone)
    klartext = _token(session, "schattenleser", [("zone.read", zone.id)])

    ergebnis = server.schattenentscheidungen(session, klartext, zone.id, 1)

    assert ergebnis == [
        {
            "zeitpunkt": entscheidung.decided_at.isoformat(),
            "ist_c": None,
            "soll_c": None,
            "sollwert_begruendung": "Zeitplan",
            "wuerde_heizen": False,
            "ergebnis": "aus",
            "begruendung": "Sollwert ist erreicht.",
        }
    ]


def test_uebersteuern_ruft_domaenenmutation_mit_tokenbezug_auf(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "api")
    zone = zone_anlegen(session, "uebersteuerungszone")
    klartext = _token(
        session,
        "uebersteuerer",
        [("zone.read", zone.id), ("override.create", zone.id)],
    )

    ergebnis = server.uebersteuern(session, klartext, zone.id, Decimal("22.0"))

    eintrag = session.query(ZoneOverride).one()
    assert ergebnis["temperatur_c"] == "22.0"
    assert eintrag.created_by_token_id is not None


def test_uebersteuerung_aufheben_beendet_historieneintrag(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "api")
    zone = zone_anlegen(session, "aufhebungszone")
    klartext = _token(
        session,
        "aufheber",
        [
            ("zone.read", zone.id),
            ("override.create", zone.id),
            ("override.cancel", zone.id),
        ],
    )
    server.uebersteuern(session, klartext, zone.id, Decimal("19.0"))

    ergebnis = server.uebersteuerung_aufheben(session, klartext, zone.id)

    assert ergebnis["aufgehoben"] is True
    assert session.query(ZoneOverride).one().cancelled_at is not None


def test_start_ohne_mcp_token_wird_verweigert(monkeypatch: pytest.MonkeyPatch) -> None:
    einstellungen = Settings(
        _env_file=None, database_url="sqlite://", secret_key="x" * 32, mcp_token=None
    )
    monkeypatch.setattr(server, "get_settings", lambda: einstellungen)

    with pytest.raises(SystemExit, match="THERMOCTL_MCP_TOKEN fehlt"):
        server.main()


def test_fehlendes_mcp_paket_wird_verstaendlich_gemeldet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ohne_mcp(name: str) -> object:
        raise ModuleNotFoundError("kein mcp", name=name)

    monkeypatch.setattr(server, "import_module", ohne_mcp)

    with pytest.raises(RuntimeError, match=r"thermoctl\[mcp\]"):
        server._mcp_server_klasse()


def test_registrierte_mcp_werkzeuge_rufen_die_adapterfunktionen_auf(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die registrierten Werkzeuge tragen echte Schemas und sind nicht nur Namen."""
    zone = zone_mit_zeitplan(
        session, "registrierungszone", [(1, 0, "tag-registrierung", Decimal("20.0"))]
    )
    quelle(session, "api")
    zonenzustand_anlegen(session, zone)
    schattenentscheidung_anlegen(session, zone)
    geraet_anlegen(session, "registrierungsgeraet")
    rechte = [
        ("zone.read", None),
        ("device.read", None),
        ("override.create", zone.id),
        ("override.cancel", zone.id),
        ("schedule.manage", zone.id),
        ("zone.manage", zone.id),
        ("control.arm", None),
    ]
    klartext = _token(session, "registrierter-nutzer", rechte)

    werkzeuge: dict[str, object] = {}

    class TestServer:
        def tool(self, name: str | None = None):  # type: ignore[no-untyped-def]
            def dekorator(funktion):  # type: ignore[no-untyped-def]
                assert name is not None
                werkzeuge[name] = funktion
                return funktion

            return dekorator

        def run(self, transport: str = "stdio", **kwargs: object) -> None:
            raise AssertionError("Der Test startet keinen Transport")

    monkeypatch.setattr(server, "session_scope", lambda _factory: nullcontext(session))
    server._werkzeuge_registrieren(TestServer(), object(), klartext)  # type: ignore[arg-type]

    assert werkzeuge.keys() == {
        "zonen_auflisten",
        "zonenzustand",
        "sollwert_erklaeren",
        "zeitplan_lesen",
        "sollwerte_lesen",
        "geraete_auflisten",
        "schattenentscheidungen",
        "uebersteuern",
        "uebersteuerung_aufheben",
        "boost",
        "regelparameter_lesen",
        "regelparameter_setzen",
        "steuerung_lesen",
        "trockenlauf_erzwingen",
        "zeitplanpunkt_verschieben",
    }
    assert werkzeuge["zonen_auflisten"]()  # type: ignore[operator]
    assert werkzeuge["zonenzustand"](zone.id)  # type: ignore[operator]
    assert werkzeuge["sollwert_erklaeren"](zone.id)  # type: ignore[operator]
    assert werkzeuge["zeitplan_lesen"](zone.id)  # type: ignore[operator]
    assert werkzeuge["sollwerte_lesen"](zone.id)  # type: ignore[operator]
    assert werkzeuge["geraete_auflisten"]()  # type: ignore[operator]
    assert werkzeuge["schattenentscheidungen"](zone.id, 1)  # type: ignore[operator]
    assert werkzeuge["uebersteuern"](zone.id, Decimal("21.0"))  # type: ignore[operator]
    assert werkzeuge["uebersteuerung_aufheben"](zone.id)  # type: ignore[operator]
    assert werkzeuge["boost"](zone.id)  # type: ignore[operator]
    assert werkzeuge["regelparameter_lesen"](zone.id)  # type: ignore[operator]
    assert werkzeuge["regelparameter_setzen"](  # type: ignore[operator]
        zone.id, "hysteresis_k", Decimal("0.4")
    )
    assert werkzeuge["steuerung_lesen"]()  # type: ignore[operator]
    # `trockenlauf_erzwingen` meldet `geaendert: False`, wenn schon Trockenlauf herrscht --
    # deshalb auf den Schluessel pruefen und nicht auf Wahrheit des Ergebnisses.
    assert "scharf" in werkzeuge["trockenlauf_erzwingen"]()  # type: ignore[operator]
    punkt = session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)).first()
    assert punkt is not None
    assert werkzeuge["zeitplanpunkt_verschieben"](  # type: ignore[operator]
        zone.id, punkt.id, 4, 480
    )


def test_unbekanntes_token_wird_abgewiesen(session: Session) -> None:
    """Der Adapter darf keine Hintertuer an der Anmeldung vorbei sein."""
    with pytest.raises(PermissionError):
        server.zonen_auflisten(session, "tctl_00000000_gibtesnicht")


def test_fremde_zone_ist_nicht_auffindbar(session: Session) -> None:
    """Nicht 'verboten', sondern 'gibt es nicht' — sonst verraet die Antwort, welche
    Zonen existieren. Der REST-Adapter haelt es genauso."""
    eigene = zone_anlegen(session, "eigene-zone")
    fremde = zone_anlegen(session, "fremde-zone")
    klartext = _token(session, "eingeschraenkt", [("zone.read", eigene.id)])

    with pytest.raises(LookupError):
        server.zonenzustand(session, klartext, fremde.id)


def test_ohne_zonenrecht_gibt_es_keine_leere_liste_sondern_eine_verweigerung(
    session: Session,
) -> None:
    """Eine leere Liste waere die falsche Antwort: Sie sieht aus wie 'keine Zonen
    vorhanden' und verdeckt, dass schlicht das Recht fehlt."""
    zone_anlegen(session, "zone-ohne-zugriff")
    klartext = _token(session, "rechtelos", [("token.self", None)])

    with pytest.raises(Forbidden):
        server.zonen_auflisten(session, klartext)


def test_zonenzustand_ohne_messung_meldet_leere_werte(session: Session) -> None:
    """Eine frisch angelegte Zone hat noch keinen Zustand — das ist kein Fehler."""
    zone = zone_anlegen(session, "zone-ohne-zustand")
    klartext = _token(session, "leser-ohne-zustand", [("zone.read", None)])

    assert server.zonenzustand(session, klartext, zone.id) == {
        "temperatur_c": None,
        "messzeitpunkt": None,
        "sensorzustand": None,
    }


@pytest.mark.parametrize("anzahl", [0, -1, 101])
def test_schattenentscheidungen_weist_unsinnige_anzahl_ab(session: Session, anzahl: int) -> None:
    """Ohne Obergrenze koennte ein Aufruf die ganze Historie ziehen."""
    zone = zone_anlegen(session, f"zone-anzahl-{anzahl}")
    klartext = _token(session, f"leser-anzahl-{anzahl}", [("zone.read", None)])

    with pytest.raises(ValueError):
        server.schattenentscheidungen(session, klartext, zone.id, anzahl)


def test_uebersteuern_weist_unsinnige_temperatur_ab(session: Session) -> None:
    """Der MCP-Server pruefte die Temperatur bis zum Abschlussreview gar nicht.

    Er ist der Adapter, der am ehesten unbeaufsichtigt aufgerufen wird — von einem
    Werkzeug, nicht von einem Menschen, der den Wert noch einmal ansieht. Ein
    `temperature_c=99` waere dort angekommen und flosse in Teilprojekt 4 ungefiltert in
    die scharfe Regelentscheidung. Die Grenze liegt jetzt in der Domaene und gilt fuer
    alle drei Adapter.
    """
    from decimal import Decimal

    from thermoctl.domain.modi import Domaenenfehler

    zone = zone_anlegen(session, "zone-mcp-grenze")
    einstellungen_anlegen(session)
    quelle(session, "mcp")
    klartext = _token(session, "uebersteuerer", [("override.create", None), ("zone.read", None)])

    # -5 ist seit der Absenkung auf -20 ein gueltiger Sollwert: "hier wird nicht
    # geheizt". Unbrauchbar bleibt, was darunter liegt.
    for wert in (Decimal("99"), Decimal("-30"), Decimal("21.55")):
        with pytest.raises(Domaenenfehler):
            server.uebersteuern(session, klartext, zone.id, wert, None)


# --- Steuerung ueber MCP ---------------------------------------------------


def test_steuerung_lesen_zeigt_den_betriebszustand(session: Session) -> None:
    einstellungen_anlegen(session)
    klartext = _token(session, "leser", [("zone.read", None)])
    antwort = server.steuerung_lesen(session, klartext)
    assert antwort["scharf"] is False
    assert antwort["zeitzone"]


def test_steuerung_lesen_braucht_zone_read(session: Session) -> None:
    einstellungen_anlegen(session)
    klartext = _token(session, "rechtlos", [("device.read", None)])
    with pytest.raises(Forbidden):
        server.steuerung_lesen(session, klartext)


def test_trockenlauf_erzwingen_nimmt_die_anlage_zurueck(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "mcp")
    quelle(session, "web")
    scharf_schalten(session, True, begruendung="von Hand", user_id=None)
    klartext = _token(session, "notaus", [("zone.read", None), ("control.arm", None)])

    antwort = server.trockenlauf_erzwingen(session, klartext, "Assistent nimmt zurück")
    assert antwort == {"scharf": False, "geaendert": True}
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
        if "scharf" in name.lower() and name != "scharf_schalten"
    ]
    # `scharf_schalten` ist die importierte Domaenenfunktion, kein Werkzeug: Sie wird
    # ausschliesslich mit `False` aufgerufen.
    quelltext = (
        Path(server.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    )
    assert quelltext.count("scharf_schalten(") == 1
    assert "        False," in quelltext


def test_zeitplanpunkt_verschieben_ueber_mcp(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "mcp-zeitplan", [(1, 360, "tag-mcp", Decimal("21.0"))])
    quelle(session, "web")
    punkt = session.scalars(
        select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)
    ).one()
    klartext = _token(
        session, "planer", [("zone.read", None), ("schedule.manage", zone.id)]
    )
    antwort = server.zeitplanpunkt_verschieben(session, klartext, zone.id, punkt.id, 5, 450)
    assert antwort["wochentag"] == 5
    assert antwort["minute"] == 450
    session.refresh(punkt)
    assert punkt.id == antwort["punkt_id"]


def test_verschieben_eines_fremden_punktes_scheitert(session: Session) -> None:
    # `zone_mit_zeitplan` legt selbst die Einstellungszeile an und vertraegt deshalb
    # nur einen Aufruf je Test; die zweite Zone bekommt ihren Punkt von Hand.
    zone = zone_mit_zeitplan(session, "eigen", [(1, 360, "tag-eigen", Decimal("21.0"))])
    fremde = zone_anlegen(session, "fremd")
    fremder_punkt = SchedulePoint(
        zone_id=fremde.id,
        weekday=1,
        minute_of_day=360,
        setpoint_mode_id=session.scalars(select(SetpointMode)).first().id,
    )
    session.add(fremder_punkt)
    session.flush()
    klartext = _token(
        session, "planer2", [("zone.read", zone.id), ("schedule.manage", zone.id)]
    )
    with pytest.raises(ValueError, match="nicht gefunden"):
        server.zeitplanpunkt_verschieben(
            session, klartext, zone.id, fremder_punkt.id, 5, 450
        )


def test_boost_zieht_die_naechste_schaltung_vor(session: Session) -> None:
    """Fuer ein Sprachmodell die verlaessliche Form von „mach es hier waermer".

    Es muss weder eine Temperatur noch eine Dauer raten, und nach dem Schaltpunkt
    raeumt sich der Eingriff selbst weg.
    """
    zone = zone_mit_zeitplan(
        session,
        "boostzone",
        [(1, 0, "tag-boost", Decimal("21.0")), (1, 1320, "nacht-boost", Decimal("18.0"))],
    )
    quelle(session, "mcp")
    klartext = _token(
        session, "boostnutzer", [("zone.read", zone.id), ("override.create", zone.id)]
    )

    ergebnis = server.boost(session, klartext, zone.id)

    assert ergebnis["zone"] == zone.name
    assert ergebnis["modus"] in ("tag-boost", "nacht-boost")
    assert ergebnis["gilt_bis"] is not None
    assert Decimal(str(ergebnis["temperatur_c"])) in (Decimal("21.0"), Decimal("18.0"))


def test_boost_braucht_das_recht_zu_uebersteuern(session: Session) -> None:
    """Gegenprobe: Lesen allein reicht nicht, obwohl der Aufruf kein Argument traegt."""
    zone = zone_mit_zeitplan(session, "boostsperre", [(1, 0, "tag-sperre", Decimal("21.0"))])
    klartext = _token(session, "nurleser", [("zone.read", zone.id)])

    with pytest.raises(Forbidden):
        server.boost(session, klartext, zone.id)


def test_regelparameter_lesen_liefert_die_grenzen_mit(session: Session) -> None:
    """Ohne sie waere jeder Schreibversuch ein Versuch.

    „0,05 Kelvin Hysterese" sieht fuer ein Sprachmodell so plausibel aus wie „0,5" --
    die Grenzen gehoeren deshalb in dieselbe Antwort und nicht in die Dokumentation.
    """
    zone = zone_mit_zeitplan(session, "parameterzone", [(1, 0, "tag-p", Decimal("21.0"))])
    klartext = _token(session, "parameterleser", [("zone.read", zone.id)])

    ergebnis = server.regelparameter_lesen(session, klartext, zone.id)

    parameter = {p["name"]: p for p in ergebnis["parameter"]}  # type: ignore[union-attr]
    assert parameter["hysteresis_k"]["minimum"] == "0.1"
    assert parameter["hysteresis_k"]["maximum"] == "5.0"
    # Und ob der Wert dieser Zone gehoert oder vom globalen Standard kommt.
    assert parameter["hysteresis_k"]["eigener_wert"] is False


def test_regelparameter_setzen_laesst_die_uebrigen_geerbt(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "setzzone", [(1, 0, "tag-s", Decimal("21.0"))])
    quelle(session, "mcp")
    klartext = _token(
        session, "parameterschreiber", [("zone.read", zone.id), ("zone.manage", zone.id)]
    )

    ergebnis = server.regelparameter_setzen(
        session, klartext, zone.id, "hysteresis_k", Decimal("0.4")
    )

    assert ergebnis["wert"] == "0.4"
    assert zone.hysteresis_k == Decimal("0.4")
    assert zone.min_on_seconds is None, "ein geerbter Wert wurde festgeschrieben"


def test_regelparameter_setzen_braucht_zone_manage(session: Session) -> None:
    """`zone.manage`, nicht `override.create`.

    Ein Regelparameter wirkt dauerhaft und auf jede kuenftige Entscheidung, eine
    Uebersteuerung nur bis zum naechsten Schaltpunkt.
    """
    zone = zone_mit_zeitplan(session, "setzsperre", [(1, 0, "tag-ss", Decimal("21.0"))])
    klartext = _token(
        session, "uebersteuerer", [("zone.read", zone.id), ("override.create", zone.id)]
    )

    with pytest.raises(Forbidden):
        server.regelparameter_setzen(
            session, klartext, zone.id, "hysteresis_k", Decimal("0.4")
        )
