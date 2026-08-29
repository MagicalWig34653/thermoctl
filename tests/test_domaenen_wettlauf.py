"""Die Fälle, die erst bei gleichzeitigen Anfragen auftreten.

Jede dieser Funktionen prüft erst und schreibt dann. Zwischen beidem kann eine zweite
Anfrage denselben Namen oder denselben Zeitpunkt belegen — die Vorprüfung sagt dann „frei",
und die Datenbankbedingung schlägt zu. Genau dafür steht hinter jeder Vorprüfung noch ein
`except IntegrityError`.

Diese Zweige sind über HTTP kaum zu treffen, weil die Vorprüfung fast immer greift. Der
Test stellt den Wettlauf her, indem er die Vorprüfung ins Leere laufen lässt — genau das,
was eine gleichzeitige Anfrage bewirkt.
"""

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import betriebsart, modus_anlegen, quelle
from tests.hilfen import zone_anlegen as zone_hilfe
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain import zonen as zonen_modul
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import Zeitplanfehler, zeitplanpunkt_anlegen
from thermoctl.domain.zonen import ZonennameVergeben, zone_aendern, zone_anlegen


@pytest.fixture(autouse=True)
def _quelle(session: Session) -> None:
    quelle(session, "web")


def _principal() -> Principal:
    return Principal(user_id=None, token_id=None, grants=frozenset())


def test_gleichzeitig_vergebener_zonenname_beim_anlegen(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = betriebsart(session, "auto")
    zone_anlegen(
        session, _principal(), name="besetzt", display_name="Besetzt",
        operating_mode_id=art.id, sort_order=0, temperature_source_device_id=None,
    )
    # Die Vorpruefung sagt 'frei' — wie bei einer zweiten Anfrage, die im selben Moment
    # denselben Namen anlegt.
    monkeypatch.setattr(zonen_modul, "_name_vergeben", lambda *a, **k: False)
    with pytest.raises(ZonennameVergeben):
        zone_anlegen(
            session, _principal(), name="besetzt", display_name="Zweiter",
            operating_mode_id=art.id, sort_order=0, temperature_source_device_id=None,
        )


def test_gleichzeitig_vergebener_zonenname_beim_aendern(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = betriebsart(session, "auto")
    zone_anlegen(
        session, _principal(), name="schon-da", display_name="Schon da",
        operating_mode_id=art.id, sort_order=0, temperature_source_device_id=None,
    )
    andere = zone_anlegen(
        session, _principal(), name="andere", display_name="Andere",
        operating_mode_id=art.id, sort_order=0, temperature_source_device_id=None,
    )
    monkeypatch.setattr(zonen_modul, "_name_vergeben", lambda *a, **k: False)
    with pytest.raises(ZonennameVergeben):
        zone_aendern(
            session, andere, _principal(), name="schon-da", display_name="Andere",
            operating_mode_id=art.id, sort_order=0, temperature_source_device_id=None,
        )
    assert andere.name == "andere"


def test_gleichzeitig_belegter_zeitpunkt(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = zone_anlegen_hilfe(session)
    modus = modus_anlegen(session, "wettlauf-tag", "Tag")
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=modus.id
        )
    )
    session.flush()
    import thermoctl.domain.schedule as schedule_modul

    # Die Belegtpruefung sagt 'frei' — wie bei einer zweiten Anfrage im selben Moment.
    monkeypatch.setattr(schedule_modul, "_zeitpunkt_belegt", lambda *a, **k: False)
    with pytest.raises(Zeitplanfehler):
        zeitplanpunkt_anlegen(
            session, zone, wochentag=1, minute=360, modus_id=modus.id,
            user_id=None, token_id=None,
        )


def zone_anlegen_hilfe(session: Session):  # type: ignore[no-untyped-def]
    return zone_hilfe(session, "wettlauf-zone")


@pytest.mark.parametrize(
    ("wochentag", "minute", "feld"),
    [(0, 360, "wochentag"), (8, 360, "wochentag"), (1, -1, "uhrzeit"), (1, 1440, "uhrzeit")],
)
def test_zeitplanpunkt_grenzen_gelten_auch_in_der_domaene(
    session: Session, wochentag: int, minute: int, feld: str
) -> None:
    """Die Ansicht prueft schon — die Domaene prueft trotzdem selbst.

    REST und MCP rufen dieselbe Funktion auf, und eine Regel, die nur im Adapter steht,
    gilt fuer die anderen nicht.
    """
    zone = zone_anlegen_hilfe(session)
    modus = modus_anlegen(session, f"grenze-{wochentag}-{minute}", "Tag")
    with pytest.raises(Zeitplanfehler) as fehler:
        zeitplanpunkt_anlegen(
            session, zone, wochentag=wochentag, minute=minute, modus_id=modus.id,
            user_id=None, token_id=None,
        )
    assert fehler.value.feld == feld


def test_unbekannter_modus_wird_in_der_domaene_abgewiesen(session: Session) -> None:
    zone = zone_anlegen_hilfe(session)
    with pytest.raises(Zeitplanfehler) as fehler:
        zeitplanpunkt_anlegen(
            session, zone, wochentag=1, minute=360, modus_id=999999,
            user_id=None, token_id=None,
        )
    assert fehler.value.feld == "modus"


@pytest.mark.parametrize("eingabe", ["kein Doppelpunkt", "aa:bb", "6:00:00", "", "25:00", "6:60"])
def test_unsinnige_uhrzeiten_werden_abgewiesen(eingabe: str) -> None:
    from thermoctl.domain.schedule import uhrzeit_in_minuten

    with pytest.raises(Zeitplanfehler):
        uhrzeit_in_minuten(eingabe)
