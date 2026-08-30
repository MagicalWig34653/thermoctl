from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.state import ShadowDecision


def _grundlage(session: Session):
    source(session, "web")
    source(session, "api")
    create_settings(session)
    return create_zone(session, "wohnzimmer")


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def test_the_parameter_page_shows_inherited_values_and_empty_restores_inheritance(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    zone.hysteresis_k = Decimal("0.70")
    client = client_als([("zone.manage", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/parameters",
        data={
            "hysteresis_k": "",
            "min_on_seconds": "",
            "min_off_seconds": "",
            "sensor_timeout_seconds": "",
            "temperature_offset_k": "",
            "window_resume_delay_seconds": "",
        },
        headers=_csrf(client),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert zone.hysteresis_k is None
    assert "Derzeit 0.30 K aus dem globalen Standard" in response.text


def test_a_negative_hysteresis_is_refused_but_the_offset_is_saved(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", zone.id)])
    pfad = f"/zones/{zone.id}/parameters"

    errors = client.post(pfad, data={"hysteresis_k": "-0.2"}, headers=_csrf(client))
    assert errors.status_code == 200
    assert "darf nicht negativ" in errors.text
    assert zone.hysteresis_k is None

    response = client.post(
        pfad,
        data={"temperature_offset_k": "-1.25"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert zone.temperature_offset_k == Decimal("-1.25")


def test_parameters_of_a_foreign_zone_yield_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als([("zone.manage", eigene.id)])
    assert client.get(f"/zones/{fremde.id}/parameters").status_code == 404
    assert (
        client.post(f"/zones/{fremde.id}/parameters", data={}, headers=_csrf(client)).status_code
        == 404
    )


def test_an_override_from_the_interface_uses_the_same_data_model_as_rest(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.read", zone.id), ("override.create", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21.5", "end": "dauerhaft"},
        headers=_csrf(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None
    # REST gibt genau diese persistierte ZoneOverride-Zeile zurück; die Webansicht
    # ruft dieselbe Domänenfunktion auf und erzeugt keinen zweiten UI-Datentyp.
    assert (entry.temperature_c, entry.ends_at, entry.cancelled_at) == (
        Decimal("21.5"),
        None,
        None,
    )


def test_showing_and_cancelling_an_override(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als(
        [("zone.read", zone.id), ("override.create", zone.id), ("override.cancel", zone.id)]
    )
    client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "22", "end": "dauerhaft"},
        headers=_csrf(client),
    )
    page = client.get("/")
    # Komma, nicht Punkt: Die Oberflaeche ist deutsch, und "22.0 °C" liest sich hier
    # wie ein Tippfehler. Eingabefelder behalten den Punkt -- ein <input type="number">
    # verwirft einen Wert mit Komma still.
    assert "Übersteuerung auf 22,0 °C" in page.text
    # Frueher stand hier zusaetzlich der feste Satz "manuell gewaehlte feste Temperatur".
    # Die Begruendung kommt jetzt aus der Domaene selbst -- derselbe Text, den auch das
    # Schattenprotokoll und die REST-Antwort tragen, statt einer zweiten Formulierung
    # allein fuer diese Seite.
    assert "Uebersteuerung (feste Temperatur)" in page.text

    response = client.post(
        f"/zones/{zone.id}/override/cancel",
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.cancelled_at is not None


def test_an_override_on_a_foreign_zone_yields_404(session: Session, client_als) -> None:
    eigene = _grundlage(session)
    fremde = create_zone(session, "fremd")
    client = client_als(
        [("zone.read", eigene.id), ("override.create", eigene.id), ("override.cancel", eigene.id)]
    )
    assert (
        client.post(
            f"/zones/{fremde.id}/override",
            data={"temperature_c": "20", "end": "dauerhaft"},
            headers=_csrf(client),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/zones/{fremde.id}/override/cancel", headers=_csrf(client)
        ).status_code
        == 404
    )


def test_the_overview_explains_a_missing_reading_and_shows_the_decision(
    session: Session, client_als
) -> None:
    zone = _grundlage(session)
    session.add(
        ShadowDecision(
            decided_at=datetime(2026, 8, 29, 7),
            zone_id=zone.id,
            temperature_c=None,
            setpoint_c=Decimal("16.0"),
            setpoint_reason="Frostschutz",
            would_heat=False,
            previous_would_heat=None,
            outcome_code="sensor_missing",
            reason="Kein verwertbarer Messwert",
        )
    )
    session.flush()
    client = client_als([("zone.read", zone.id)])

    response = client.get("/")

    assert response.status_code == 200
    assert "kein Messwert" in response.text
    assert "None" not in response.text
    assert ">0 °C" not in response.text
    assert "Kein verwertbarer Messwert" in response.text
    # Die Betriebsart steht nur da, wenn sie vom Regelfall abweicht: "Automatik" unter
    # jeder Zone waere Rauschen, "Aus" dagegen der Grund, warum es dort kalt bleibt.
    # Siehe test_betriebsart_steht_nur_da_wenn_sie_abweicht.
    assert "Betriebsart" not in response.text


def test_an_override_until_the_next_switch(session: Session, client_als) -> None:
    """Das Ende wird beim Anlegen ausgerechnet und abgelegt, nicht als Regel gemerkt —
    eine spaetere Zeitplanaenderung verschiebt eine laufende Uebersteuerung nicht."""
    from tests.helpers import create_mode
    from thermoctl.db.models.schedule import SchedulePoint

    zone = _grundlage(session)
    mode = create_mode(session, "tag-uebersteuerung", "Tag")
    session.add(
        SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id)
    )
    session.flush()
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21,5", "end": "naechste_schaltung"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is not None


def test_an_override_for_a_duration(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "20.0", "end": "dauer", "duration_minutes": "120"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is not None


def test_an_override_without_a_schedule_lasts_indefinitely(session: Session, client_als) -> None:
    """Ohne Schaltpunkt gibt es keine naechste Schaltung — dann gilt sie, bis jemand sie
    aufhebt. Stillschweigend gar nichts zu tun waere die schlechtere Antwort."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "20.0", "end": "naechste_schaltung"},
        headers=_csrf(client),
    )
    entry = session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id))
    assert entry is not None and entry.ends_at is None


def test_nonsensical_overrides_are_refused(session: Session, client_als) -> None:
    """Eine Heizung, die eine unsinnige Eingabe zurechtbiegt, ist schlimmer als eine, die
    widerspricht."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    for daten in (
        {"temperature_c": "warm", "end": "dauerhaft"},
        # Die Untergrenze liegt bei -20 Grad: Ein Sollwert im Minusbereich heisst
        # "hier wird nicht geheizt". Darunter liegt kein Wunsch mehr, sondern ein
        # Tippfehler.
        {"temperature_c": "-30", "end": "dauerhaft"},
        {"temperature_c": "50", "end": "dauerhaft"},
        {"temperature_c": "20", "end": "dauer", "duration_minutes": "0"},
        {"temperature_c": "20", "end": "dauer", "duration_minutes": "keine Zahl"},
        {"temperature_c": "20", "end": "irgendwas"},
    ):
        response = client.post(
            f"/zones/{zone.id}/override", data=daten,
            headers=_csrf(client), follow_redirects=False,
        )
        assert response.status_code == 303, daten
        assert "uebersteuerungsfehler" in (response.headers.get("location") or ""), daten
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None


def test_nonsensical_control_parameters_stay_in_the_form(session: Session, client_als) -> None:
    zone = _grundlage(session)
    client = client_als([("zone.manage", None), ("zone.read", None)])
    for feld, value in (("hysteresis_k", "keine Zahl"), ("min_on_seconds", "-5")):
        response = client.post(
            f"/zones/{zone.id}/parameters", data={feld: value}, headers=_csrf(client)
        )
        assert response.status_code == 200, feld
    assert zone.hysteresis_k is None and zone.min_on_seconds is None


def test_an_override_with_two_decimal_places_is_refused(
    session: Session, client_als
) -> None:
    """Die Oberflaeche prueft nicht mehr selbst — sie faengt nur noch ab, was die Domaene
    sagt. Vorher liess sie zwei Nachkommastellen durch, die REST-Schnittstelle nicht."""
    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "21,55", "end": "dauerhaft"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "uebersteuerungsfehler" in (response.headers.get("location") or "")
    assert session.scalar(select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)) is None


def test_the_operating_mode_is_shown_only_when_it_deviates(session: Session, client_als) -> None:
    """Gegenprobe zur Zeile oben. Eine Zone auf "Aus" sieht sonst aus wie jede andere --
    und genau sie ist der Grund, warum ein Raum kalt bleibt."""
    from thermoctl.db.models.lookup import OperatingMode

    zone = _grundlage(session)
    aus = OperatingMode(code="off", label="Aus")
    session.add(aus)
    session.flush()
    zone.operating_mode_id = aus.id
    session.flush()

    response = client_als([("zone.read", zone.id)]).get("/")
    assert "Betriebsart: Aus" in response.text


# --- Thermostat auf der Startseite -----------------------------------------


def _zone_with_mode(session: Session, temperature: str = "21.0"):
    """Eine Zone, deren geltender Sollwert aus einem Zeitplanmodus kommt."""
    from tests.helpers import create_mode
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    zone = _grundlage(session)
    mode = create_mode(session, "thermostat-tag", "Tag")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id, setpoint_mode_id=mode.id, temperature_c=Decimal(temperature)
        )
    )
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=0, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    return zone, mode


def test_the_thermostat_raises_the_setpoint_of_the_current_mode(
    session: Session, client_als
) -> None:
    """Nicht eine Uebersteuerung: Der Klick aendert den hinterlegten Sollwert des Modus
    dauerhaft. Deshalb steht auf der Seite auch daneben, welcher Modus gemeint ist."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])

    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    zeile = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == mode.id
        )
    ).one()
    assert zeile.temperature_c == Decimal("21.5")


def test_two_clicks_are_two_steps(session: Session, client_als) -> None:
    """Die Stufe wird auf den aktuellen Wert gerechnet, nicht auf den, den die Seite
    beim Rendern kannte -- sonst waere der zweite Klick wirkungslos."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    for _ in range(2):
        client.post(
            f"/zones/{zone.id}/thermostat",
            data={"mode_id": str(mode.id), "direction": "runter"},
            headers=_csrf(client),
        )
    zeile = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert zeile.temperature_c == Decimal("20.0")


def test_the_thermostat_stops_at_the_limit(session: Session, client_als) -> None:
    """35 Grad ist das Ende des Weges, kein Fehlerzustand -- die Domaenengrenze gilt,
    und die Seite zeigt danach den unveraenderten Wert mit einem Hinweis."""
    from thermoctl.db.models.zone import ZoneSetpoint

    zone, mode = _zone_with_mode(session, "35.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "thermostatfehler" in response.headers["location"]
    zeile = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert zeile.temperature_c == Decimal("35.0")


def test_thermostat_braucht_setpoint_write(session: Session, client_als) -> None:
    zone, mode = _zone_with_mode(session)
    client = client_als([("zone.read", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "hoch"},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_without_setpoint_write_no_thermostat_is_on_the_page(
    session: Session, client_als
) -> None:
    """Gegenprobe zur Anzeige: Wer nicht verstellen darf, sieht den Sollwert, aber keine
    Stufentasten."""
    zone, _mode = _zone_with_mode(session)
    read_only = client_als([("zone.read", zone.id)]).get("/")
    assert "tc-stufe" not in read_only.text

    darf = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)]).get("/")
    assert "tc-stufe" in darf.text


def test_the_thermostat_works_even_without_a_stored_setpoint(
    session: Session, client_als
) -> None:
    """Der Zustand einer frisch eingerichteten Anlage: keine Sollwerte gepflegt, kein
    Zeitplan. Die Seite zeigt dann den Frostschutz-Notnagel von 16 Grad -- und das
    Thermostat suchte eine Zeile, die es nicht gibt, und antwortete mit 404. Auf der
    Seite sah es aus, als passiere beim Druecken nichts.
    """
    from sqlalchemy import select

    from thermoctl.db.base import utcnow
    from thermoctl.db.models.zone import ZoneSetpoint
    from thermoctl.domain.schedule import resolved_setpoint

    zone = _grundlage(session)
    session.query(ZoneSetpoint).filter_by(zone_id=zone.id).delete()
    session.flush()
    angezeigt = resolved_setpoint(session, zone, utcnow())
    assert angezeigt.mode_id is not None

    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(angezeigt.mode_id), "direction": "hoch"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303

    zeile = session.scalars(
        select(ZoneSetpoint).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == angezeigt.mode_id,
        )
    ).one()
    assert zeile.temperature_c == angezeigt.temperature_c + Decimal("0.5")


def test_the_thermostat_for_a_foreign_mode_stays_a_404(
    session: Session, client_als
) -> None:
    """Gegenprobe: Der Notnagel gilt nur fuer den Modus, den die Seite gerade anzeigt.
    Ein beliebiger anderer bekommt weiter eine klare Absage statt eines aus dem Nichts
    erfundenen Sollwerts."""
    from tests.helpers import create_mode

    zone = _grundlage(session)
    fremder = create_mode(session, "nie-benutzt")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    response = client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(fremder.id), "direction": "hoch"},
        headers=_csrf(client),
    )
    assert response.status_code == 404


def test_overriding_into_the_negative_range(session: Session, client_als) -> None:
    """"Untersteuern" heisst: ein Sollwert unter null.

    Mit 1 Grad heizt die Anlage immer noch, sobald es kaelter wird. Wer eine Garage oder
    einen Schuppen nur ueberwachen und nicht temperieren will, braucht einen Wert, den
    die Raumtemperatur nie unterschreitet.
    """
    from sqlalchemy import select

    from thermoctl.db.models.override import ZoneOverride

    zone = _grundlage(session)
    client = client_als([("override.create", None), ("zone.read", None)])
    response = client.post(
        f"/zones/{zone.id}/override",
        data={"temperature_c": "-5", "end": "dauerhaft"},
        headers=_csrf(client),
        follow_redirects=False,
    )
    assert response.status_code == 303
    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert entry.temperature_c == Decimal("-5.0")


def test_the_thermostat_goes_below_zero(session: Session, client_als) -> None:
    """Gegenprobe von der anderen Seite: Auch die Stufentasten duerfen unter null."""
    from sqlalchemy import select

    from thermoctl.db.models.zone import ZoneSetpoint

    # Der Ausgangswert wird direkt gesetzt, nicht ueber `sollwerte_aendern`: Das schriebe
    # einen Audit-Eintrag, und dessen Fremdschluessel auf den Benutzer haelt unter
    # MariaDB wirklich -- unter SQLite fiel eine erfundene Kennung nicht auf.
    zone, mode = _zone_with_mode(session, "0.0")
    client = client_als([("zone.read", zone.id), ("setpoint.write", zone.id)])
    client.post(
        f"/zones/{zone.id}/thermostat",
        data={"mode_id": str(mode.id), "direction": "runter"},
        headers=_csrf(client),
    )
    zeile = session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.setpoint_mode_id == mode.id)
    ).one()
    assert zeile.temperature_c == Decimal("-0.5")
