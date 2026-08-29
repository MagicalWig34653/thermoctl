from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal

import pytest
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
from thermoctl.db.models.override import ZoneOverride
from thermoctl.domain.authz import Forbidden
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
