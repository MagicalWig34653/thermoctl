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

from tests.helpers import create_mode, operating_mode, source
from tests.helpers import create_zone as zone_hilfe
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.domain import zones as zone_modul
from thermoctl.domain.principal import Principal
from thermoctl.domain.schedule import ScheduleError, create_schedule_point
from thermoctl.domain.zones import ZonennameVergeben, create_zone, update_zone


@pytest.fixture(autouse=True)
def _source(session: Session) -> None:
    source(session, "web")


def _principal() -> Principal:
    return Principal(user_id=None, token_id=None, grants=frozenset())


def test_gleichzeitig_vergebener_zonenname_beim_anlegen(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = operating_mode(session, "auto")
    create_zone(
        session, _principal(), name="besetzt", display_name="Besetzt",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    # Die Vorpruefung sagt 'frei' — wie bei einer zweiten Anfrage, die im selben Moment
    # denselben Namen anlegt.
    monkeypatch.setattr(zone_modul, "_name_taken", lambda *a, **k: False)
    with pytest.raises(ZonennameVergeben):
        create_zone(
            session, _principal(), name="besetzt", display_name="Zweiter",
            operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
        )


def test_gleichzeitig_vergebener_zonenname_beim_aendern(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = operating_mode(session, "auto")
    create_zone(
        session, _principal(), name="schon-da", display_name="Schon da",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    andere = create_zone(
        session, _principal(), name="andere", display_name="Andere",
        operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
    )
    monkeypatch.setattr(zone_modul, "_name_taken", lambda *a, **k: False)
    with pytest.raises(ZonennameVergeben):
        update_zone(
            session, andere, _principal(), name="schon-da", display_name="Andere",
            operating_mode_id=kind.id, sort_order=0, temperature_source_device_id=None,
        )
    assert andere.name == "andere"


def test_gleichzeitig_belegter_zeitpunkt(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = zone_create_hilfe(session)
    mode = create_mode(session, "wettlauf-tag", "Tag")
    session.add(
        SchedulePoint(
            zone_id=zone.id, weekday=1, minute_of_day=360, setpoint_mode_id=mode.id
        )
    )
    session.flush()
    import thermoctl.domain.schedule as schedule_modul

    # Die Belegtpruefung sagt 'frei' — wie bei einer zweiten Anfrage im selben Moment.
    monkeypatch.setattr(schedule_modul, "_moment_taken", lambda *a, **k: False)
    with pytest.raises(ScheduleError):
        create_schedule_point(
            session, zone, weekday=1, minute=360, mode_id=mode.id,
            user_id=None, token_id=None,
        )


def zone_create_hilfe(session: Session):  # type: ignore[no-untyped-def]
    return zone_hilfe(session, "wettlauf-zone")


@pytest.mark.parametrize(
    ("weekday", "minute", "feld"),
    [(0, 360, "weekday"), (8, 360, "weekday"), (1, -1, "time_of_day"), (1, 1440, "time_of_day")],
)
def test_zeitplanpunkt_grenzen_gelten_auch_in_der_domaene(
    session: Session, weekday: int, minute: int, feld: str
) -> None:
    """Die Ansicht prueft schon — die Domaene prueft trotzdem selbst.

    REST und MCP rufen dieselbe Funktion auf, und eine Regel, die nur im Adapter steht,
    gilt fuer die anderen nicht.
    """
    zone = zone_create_hilfe(session)
    mode = create_mode(session, f"grenze-{weekday}-{minute}", "Tag")
    with pytest.raises(ScheduleError) as errors:
        create_schedule_point(
            session, zone, weekday=weekday, minute=minute, mode_id=mode.id,
            user_id=None, token_id=None,
        )
    assert errors.value.feld == feld


def test_unbekannter_modus_wird_in_der_domaene_abgewiesen(session: Session) -> None:
    zone = zone_create_hilfe(session)
    with pytest.raises(ScheduleError) as errors:
        create_schedule_point(
            session, zone, weekday=1, minute=360, mode_id=999999,
            user_id=None, token_id=None,
        )
    assert errors.value.feld == "mode_id"


@pytest.mark.parametrize("eingabe", ["kein Doppelpunkt", "aa:bb", "6:00:00", "", "25:00", "6:60"])
def test_unsinnige_uhrzeiten_werden_abgewiesen(eingabe: str) -> None:
    from thermoctl.domain.schedule import time_of_day_in_minutes

    with pytest.raises(ScheduleError):
        time_of_day_in_minutes(eingabe)
