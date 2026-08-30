from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_mode, create_settings, create_zone, source
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, ZoneSetpoint


def _csrf(client: TestClient) -> dict[str, str]:
    geheimnis = client.cookies[COOKIE_NAME]
    token = csrf_token(geheimnis, get_settings().secret_key.get_secret_value())
    return {"X-CSRF-Token": token}


def test_modusliste_braucht_mode_manage(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/modes").status_code == 403
    assert client_als([("mode.manage", None)]).get("/modes").status_code == 200


def test_modus_neu_formular_wird_angezeigt(client_als) -> None:
    response = client_als([("mode.manage", None)]).get("/modes/new")
    assert response.status_code == 200
    assert "Technischer Code" in response.text


def test_modus_wird_angelegt_und_auditiert(client_als, session: Session) -> None:
    source(session)
    client = client_als([("mode.manage", None)])
    response = client.post(
        "/modes",
        data={"code": "urlaub", "name": "Urlaub", "sort_order": "30"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    mode = session.scalar(select(SetpointMode).where(SetpointMode.code == "urlaub"))
    assert response.status_code == 303
    assert mode is not None and mode.name == "Urlaub" and mode.sort_order == 30
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "setpoint_mode", AuditEvent.action == "create"
        )
    ) is not None


def test_doppelter_code_kommt_mit_wert_ins_formular_zurueck(
    client_als, session: Session
) -> None:
    create_mode(session, "tag", "Tag")
    client = client_als([("mode.manage", None)])
    response = client.post(
        "/modes",
        data={"code": "tag", "name": "Mein Tag", "sort_order": "0"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "bereits vergeben" in response.text
    assert 'value="Mein Tag"' in response.text


def test_modus_bearbeiten_formular_zeigt_werte(client_als, session: Session) -> None:
    mode = create_mode(session, "nacht", "Nacht")
    response = client_als([("mode.manage", None)]).get(f"/modes/{mode.id}")
    assert response.status_code == 200
    assert 'value="nacht"' in response.text


def test_modus_wird_geaendert(client_als, session: Session) -> None:
    source(session)
    mode = create_mode(session, "nacht", "Nacht")
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode.id}",
        data={"code": "abend", "name": "Abend", "sort_order": "20"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (mode.code, mode.name, mode.sort_order) == ("abend", "Abend", 20)
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(mode.id), AuditEvent.action == "update"
        )
    ) is not None


def test_loeschformular_fuer_freien_modus(client_als, session: Session) -> None:
    mode = create_mode(session, "urlaub", "Urlaub")
    response = client_als([("mode.manage", None)]).get(f"/modes/{mode.id}/delete")
    assert response.status_code == 200
    assert "wirklich gelöscht" in response.text


def test_freier_modus_wird_geloescht(client_als, session: Session) -> None:
    source(session)
    mode = create_mode(session, "urlaub", "Urlaub")
    mode_id = mode.id
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode_id}/delete", headers=_csrf(client), follow_redirects=False
    )

    assert response.status_code == 303
    assert session.get(SetpointMode, mode_id) is None
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_id == str(mode_id), AuditEvent.action == "delete"
        )
    ) is not None


def test_eingebauter_modus_ist_mit_begruendung_nicht_loeschbar(
    client_als, session: Session
) -> None:
    mode = create_mode(session, "tag", "Tag")
    mode.is_builtin = True
    client = client_als([("mode.manage", None)])

    form = client.get(f"/modes/{mode.id}/delete")
    response = client.post(f"/modes/{mode.id}/delete", headers=_csrf(client))

    assert "weil die Anwendung sie benötigt" in form.text
    assert "weil die Anwendung sie benötigt" in response.text
    assert session.get(SetpointMode, mode.id) is mode


def test_frostschutzmodus_ist_mit_begruendung_nicht_loeschbar(
    client_als, session: Session
) -> None:
    settings = create_settings(session)
    client = client_als([("mode.manage", None)])
    response = client.get(f"/modes/{settings.frost_protection_mode_id}/delete")

    assert response.status_code == 200
    assert (
        "Der Frostschutzmodus kann nicht gelöscht werden — er ist die Rückfallebene, "
        "wenn ein Sensor ausfällt."
    ) in response.text


def test_sollwertformular_zeigt_alle_modi_und_hilfetext(client_als, session: Session) -> None:
    zone = create_zone(session, "bad")
    create_mode(session, "tag", "Tag")
    create_mode(session, "nacht", "Nacht")
    response = client_als([("setpoint.write", zone.id)]).get(
        f"/zones/{zone.id}/setpoints"
    )

    assert response.status_code == 200
    assert "Tag (°C)" in response.text and "Nacht (°C)" in response.text
    assert "Leer lassen löscht den Sollwert" in response.text


def test_sollwerte_werden_als_decimal_gespeichert_und_auditiert(
    client_als, session: Session
) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"sollwert_{mode.id}": "21.5"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    setpoint = session.get(ZoneSetpoint, (zone.id, mode.id))
    assert response.status_code == 303
    assert setpoint is not None and setpoint.temperature_c == Decimal("21.5")
    assert session.scalar(
        select(AuditEvent).where(
            AuditEvent.object_type == "zone_setpoint",
            AuditEvent.object_id == str(zone.id),
        )
    ) is not None


def test_sollwert_ausserhalb_der_grenzen_wird_am_feld_abgewiesen(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"sollwert_{mode.id}": "36.0"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "zwischen -20,0 und 35,0 °C" in response.text
    assert 'value="36.0"' in response.text
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_sollwert_mit_zwei_nachkommastellen_wird_abgewiesen(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"sollwert_{mode.id}": "21.25"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "höchstens eine Nachkommastelle" in response.text
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_leeres_sollwertfeld_loescht_die_zeile(client_als, session: Session) -> None:
    source(session)
    zone = create_zone(session, "bad")
    mode = create_mode(session, "tag", "Tag")
    session.add(
        ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal("20.0"))
    )
    session.flush()
    client = client_als([("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/setpoints",
        data={f"sollwert_{mode.id}": ""},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.get(ZoneSetpoint, (zone.id, mode.id)) is None


def test_fremde_zone_ergibt_404(client_als, session: Session) -> None:
    eigene = create_zone(session, "bad")
    fremde = create_zone(session, "kueche")
    client = client_als([("setpoint.write", eigene.id)])

    assert client.get(f"/zones/{fremde.id}/setpoints").status_code == 404
    assert (
        client.post(
            f"/zones/{fremde.id}/setpoints", data={}, headers=_csrf(client)
        ).status_code
        == 404
    )


def test_verwendeter_modus_ist_nicht_loeschbar(client_als, session: Session) -> None:
    """Die dritte Loeschsperre: Ein Modus, auf den ein Zeitplan zeigt, verschwindet nicht.

    Ohne sie zerrisse das Loeschen den Zeitplan jeder Zone, die ihn benutzt — und zwar
    still, weil der Fremdschluessel erst beim naechsten Regelzyklus auffiele.
    """
    create_settings(session)
    source(session, "web")
    urlaub = create_mode(session, "urlaub", "Urlaub")
    zone = create_zone(session, "zone-mit-plan")
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=urlaub.id
        )
    )
    session.flush()

    client = client_als([("mode.manage", None)])
    response = client.get(f"/modes/{urlaub.id}/delete")
    assert response.status_code == 200
    assert "Zeitpläne oder historische" in response.text

    response = client.post(f"/modes/{urlaub.id}/delete", headers=_csrf(client))
    assert response.status_code == 200
    assert session.get(SetpointMode, urlaub.id) is not None


def test_unbekannter_modus_ergibt_404(client_als) -> None:
    client = client_als([("mode.manage", None)])
    assert client.get("/modes/999999").status_code == 404
    assert client.get("/modes/999999/delete").status_code == 404


def test_leere_und_zu_lange_moduswerte_bleiben_im_formular(
    client_als, session: Session
) -> None:
    source(session, "web")
    client = client_als([("mode.manage", None)])
    faelle = [
        ({"code": "  ", "name": "Name", "sort_order": "0"}, "Code darf nicht leer"),
        ({"code": "c" * 33, "name": "Name", "sort_order": "0"}, "höchstens 32"),
        ({"code": "gut", "name": "  ", "sort_order": "0"}, "Name darf nicht leer"),
        ({"code": "gut", "name": "n" * 65, "sort_order": "0"}, "höchstens 64"),
        ({"code": "gut", "name": "Name", "sort_order": "keine Zahl"}, "ganze Zahl"),
    ]
    for daten, expected in faelle:
        response = client.post("/modes", data=daten, headers=_csrf(client))
        assert response.status_code == 200, daten
        assert expected in response.text, daten
    assert session.scalar(select(SetpointMode).where(SetpointMode.code == "gut")) is None


def test_bestehender_sollwert_wird_geaendert(client_als, session: Session) -> None:
    """Der dritte Fall neben Anlegen und Loeschen — bisher ungeprueft."""
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-aendern", "Tag")
    zone = create_zone(session, "zone-sollwert-aendern")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    client.post(
        f"/zones/{zone.id}/setpoints", data={f"sollwert_{day.id}": "20.0"},
        headers=_csrf(client),
    )
    client.post(
        f"/zones/{zone.id}/setpoints", data={f"sollwert_{day.id}": "22.5"},
        headers=_csrf(client),
    )
    zeilen = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == day.id
        )
    ).all()
    assert len(zeilen) == 1
    assert zeilen[0].temperature_c == Decimal("22.5")


def test_nicht_numerischer_sollwert_bleibt_im_formular(client_als, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-keine-zahl", "Tag")
    zone = create_zone(session, "zone-keine-zahl")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/setpoints", data={f"sollwert_{day.id}": "warm"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "muss eine Zahl sein" in response.text
    assert session.scalar(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)) is None


def test_unendlicher_sollwert_wird_abgewiesen(client_als, session: Session) -> None:
    """`Decimal("nan")` und `Decimal("Infinity")` sind gueltige Dezimalzahlen.

    Ohne die Endlichkeitspruefung liefe ein solcher Wert bis in die Datenbank und von dort
    in die Regelentscheidung — jeder Vergleich mit NaN ist falsch, die Zone wuerde nie
    heizen und nie abschalten.
    """
    create_settings(session)
    source(session, "web")
    day = create_mode(session, "tag-unendlich", "Tag")
    zone = create_zone(session, "zone-unendlich")
    client = client_als([("setpoint.write", None), ("zone.read", None)])
    for value in ("nan", "Infinity"):
        response = client.post(
            f"/zones/{zone.id}/setpoints", data={f"sollwert_{day.id}": value},
            headers=_csrf(client),
        )
        assert response.status_code == 200, value
        assert "endliche Zahl" in response.text, value
    assert session.scalar(select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)) is None


def test_modus_aendern_mit_ungueltigem_wert_bleibt_im_formular(
    client_als, session: Session
) -> None:
    source(session, "web")
    mode = create_mode(session, "aenderbar", "Aenderbar")
    client = client_als([("mode.manage", None)])
    response = client.post(
        f"/modes/{mode.id}",
        data={"code": "  ", "name": "Neuer Name", "sort_order": "0"},
        headers=_csrf(client),
    )
    assert response.status_code == 200
    assert "Code darf nicht leer" in response.text
    assert mode.code == "aenderbar"


def test_die_sollwertgrenze_steht_nur_an_einer_stelle() -> None:
    """Die Grenze hat schon einmal an drei Stellen verschieden dagestanden.

    Damals prueften die Oberflaeche von Hand ohne Nachkommastellen, die
    REST-Schnittstelle ueber ihr Schema und der MCP-Server gar nicht. Sie liegt seither
    in der Domaene -- aber eine abgeschriebene Zahl schleicht sich leicht zurueck:
    beim Umstellen von 5 auf 1 Grad stand sie noch einmal in `alltag_views.py`, in der
    Discovery-Nutzlast und im Markup des Formulars.

    Der Test sucht darum nach nackten Grenzwerten ausserhalb der Domaene. Er ist grob --
    eine 5 in einer Zeile ueber Hysterese meint etwas anderes -- deshalb sucht er nur
    das Muster, in dem eine Temperaturgrenze auftritt.

    **Auch in den Vorlagen.** Seine erste Fassung sah nur Python-Dateien und uebersah
    `sollwerte.html`, wo `min="5"` und `max="35"` als Zeichenketten standen. Aufgefallen
    ist das erst beim naechsten Verschieben der Grenze -- also genau dann, wenn der
    Waechter es haette verhindern sollen.
    """
    import re
    from pathlib import Path

    wurzel = Path(__file__).resolve().parent.parent / "thermoctl"
    match = []

    # Vorlagen: Eine Seite, die Temperaturen erfragt, darf keine nackte Grenze
    # enthalten -- weder als `min="5"` noch als Argument `"35"` an `zahlenfeld`.
    # Erkannt wird eine solche Seite am Gradzeichen.
    # Zwei Klassen: Die Obergrenze und die neue Untergrenze sind als Zahl eindeutig --
    # eine `35` oder `-20` in Anfuehrungszeichen ist hier nie etwas anderes. Die alten
    # Untergrenzen `5` und `1` stehen dagegen auch fuer Minuten oder Sortierung; sie
    # zaehlen nur, wenn die Zeile selbst von Temperatur spricht. Genau daran ist die
    # erste Fassung gescheitert: Sie meldete das Minutenfeld der Uebersteuerung.
    eindeutig = {"35", "35.0", "-20", "-20.0"}
    mehrdeutig = {"5", "5.0", "1", "1.0"}
    zahl_in_anfuehrung = re.compile(r"""["'](-?\d{1,2}(?:\.\d)?)["']""")
    for datei in sorted(wurzel.parent.rglob("web/templates/*.html")):
        text = datei.read_text(encoding="utf-8")
        if "°C" not in text:
            continue
        for nummer, zeile in enumerate(text.splitlines(), 1):
            if "temperatur" in zeile.lower():
                continue  # verweist auf die durchgereichten Konstanten
            gefunden = set(zahl_in_anfuehrung.findall(zeile))
            above_temperature = "°C" in zeile or "sollwert" in zeile.lower()
            if gefunden & eindeutig or (above_temperature and gefunden & mehrdeutig):
                match.append(f"{datei.name}:{nummer}: {zeile.strip()}")

    # Python: eine Zahl an einer Stelle, an der eine Temperaturgrenze steht. Der
    # Zusammenhang steckt oft in der Zeile davor (`temperature_c: Decimal = Field(`
    # umbricht), deshalb ein kleines Fenster.
    grenzstelle = re.compile(
        r"""(?:ge=|le=|min_temp["']?\s*:\s*|max_temp["']?\s*:\s*)"""
        r"""(?:Decimal\(["'])?-?\d+(?:\.\d+)?"""
    )
    for datei in sorted(wurzel.rglob("*.py")):
        if datei.name == "modes.py":
            continue  # dort gehoert sie hin
        zeilen = datei.read_text(encoding="utf-8").splitlines()
        for nummer, zeile in enumerate(zeilen, 1):
            if "MINIMUM_TEMPERATURE_C" in zeile or "MAXIMUM_TEMPERATURE_C" in zeile:
                continue  # verweist auf die Konstanten
            if not grenzstelle.search(zeile):
                continue
            # Eng auf das Sollwertfeld: Ein blosses "temp" im Umfeld traf auch
            # `sensor_timeout_seconds` neben `temperature_offset_k` -- beides
            # Temperaturnahes mit ganz anderen Grenzen.
            umfeld = " ".join(zeilen[max(0, nummer - 3) : nummer + 1])
            if "temperature_c" in umfeld or "min_temp" in umfeld or "max_temp" in umfeld:
                match.append(f"{datei.relative_to(wurzel)}:{nummer}: {zeile.strip()}")

    assert not match, "Sollwertgrenze ausserhalb der Domaene:\n" + "\n".join(match)


def test_die_meldung_nennt_die_geltende_grenze() -> None:
    """Sie wird aus den Konstanten gebaut, nicht abgeschrieben -- sonst nennt sie nach
    dem naechsten Verschieben eine Zahl, die nicht mehr gilt."""
    import pytest as _pytest

    from thermoctl.domain.modes import (
        MAXIMUM_TEMPERATURE_C,
        MINIMUM_TEMPERATURE_C,
        DomainError,
        check_temperature,
    )

    with _pytest.raises(DomainError) as errors:
        check_temperature(MINIMUM_TEMPERATURE_C - Decimal("0.1"))
    notice = errors.value.notice
    assert f"{MINIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
    assert f"{MAXIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
