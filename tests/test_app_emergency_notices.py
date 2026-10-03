"""`app.py`'s Notbetrieb notification wiring (Auftrag 8b):

* `_sensor_notices` must not also report a zone whose Notbetrieb is enabled --
  that zone's sensor-fault lifecycle is reported exclusively by
  `_emergency_notices` (no "keine Doppelmeldung" violation).
* `_emergency_notices` drives entirely off `sensor_failure_episode.
  notification_state`, sends exactly one notice per transition, and never a
  second one after the state has already moved on (process-restart safety --
  the state transition IS the "already handled" marker, there is nothing else
  to check).
* `REASON_DEAKTIVIERT` never produces an Entwarnung, but still closes the loop
  (no `notification_state` stuck at `"gemeldet"` forever, no late Entwarnung
  on a future, unrelated recovery).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, sensor_status_of, source
from thermoctl import app as app_modul
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.sensor_failure import SensorFailureEpisode, ZoneSensorFailureState
from thermoctl.db.models.state import ZoneState
from thermoctl.domain import emergency_operation

NOW = datetime(2026, 10, 2, 12, 0, 0)


def _episode(
    session: Session,
    zone,
    *,
    notification_state: str = "offen",
    ended_at: datetime | None = None,
    ended_reason_code: str | None = None,
    stage: str = emergency_operation.STAGE_NOTBETRIEB,
) -> SensorFailureEpisode:
    episode = SensorFailureEpisode(
        zone_id=zone.id,
        zone_name=zone.display_name,
        started_at=NOW,
        ended_at=ended_at,
        ended_reason_code=ended_reason_code,
        trigger_kind=emergency_operation.TRIGGER_ALLE_QUELLEN,
        profile_version=1,
        notification_state=notification_state,
        fixed_on_seconds=600,
        fixed_off_seconds=1200,
        recovery_seconds=60,
        recovery_samples=2,
        warm_restart_hysteresis_k=Decimal("1"),
        emergency_setpoint_c=Decimal("20"),
        sensor_timeout_seconds=1800,
    )
    session.add(episode)
    session.flush()
    session.add(
        ZoneSensorFailureState(
            zone_id=zone.id,
            episode_id=episode.id if ended_at is None else None,
            stage=stage if ended_at is None else emergency_operation.STAGE_NORMAL,
        )
    )
    session.flush()
    return episode


def test_sensor_notices_skip_a_zone_with_sensor_failure_enabled(session: Session) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    plain_zone = create_zone(session, "Ohne")
    nb_zone = create_zone(session, "Mit")
    nb_zone.sensor_failure_enabled = True
    session.add(
        ZoneState(
            zone_id=plain_zone.id,
            temperature_c=Decimal("19"),
            measured_at=NOW,
            sensor_status_id=sensor_status_of(session, "ok").id,
            updated_at=NOW,
        )
    )
    session.add(
        ZoneState(
            zone_id=nb_zone.id,
            temperature_c=Decimal("19"),
            measured_at=NOW,
            sensor_status_id=sensor_status_of(session, "ok").id,
            updated_at=NOW,
        )
    )
    session.flush()
    previous = app_modul._sensor_states(session)
    for zone in (plain_zone, nb_zone):
        state = session.get(ZoneState, zone.id)
        assert state is not None
        state.sensor_status_id = sensor_status_of(session, "veraltet").id

    notices = app_modul._sensor_notices(session, previous, setting_row)

    assert len(notices) == 1
    assert notices[0].key == f"sensor:{plain_zone.id}"


def test_emergency_notices_entered_marks_melden_laeuft_and_plans_one_notice(
    session: Session,
) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "Flur")
    episode = _episode(session, zone)

    notices = app_modul._emergency_notices(session, NOW, setting_row)

    assert len(notices) == 1
    assert notices[0].notice.key == f"notbetrieb:{episode.id}"
    assert notices[0].notice.severity == "stoerung"
    # The Zwischenzustand: the final `gemeldet` is set only after the send
    # (`tests/test_app_emergency_retry.py`).
    assert episode.notification_state == "melden_laeuft"
    assert notices[0].final_state == "gemeldet"
    assert notices[0].is_retry is False


def test_emergency_notices_resolved_plans_one_entwarnung(session: Session) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "Flur")
    episode = _episode(
        session,
        zone,
        notification_state="gemeldet",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_RUECKKEHR_ABGESCHLOSSEN,
    )

    notices = app_modul._emergency_notices(session, NOW, setting_row)

    assert len(notices) == 1
    assert notices[0].notice.severity == "entwarnung"
    assert episode.notification_state == "entwarnung_laeuft"
    assert notices[0].final_state == "entwarnung_gesendet"


def test_emergency_notices_deactivation_sends_no_entwarnung_but_closes_the_episode(
    session: Session,
) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "Flur")
    episode = _episode(
        session,
        zone,
        notification_state="gemeldet",
        ended_at=NOW,
        ended_reason_code=emergency_operation.REASON_DEAKTIVIERT,
    )

    notices = app_modul._emergency_notices(session, NOW, setting_row)

    assert notices == []
    assert episode.notification_state == "entwarnung_gesendet"

    # A later, unrelated cycle must never retroactively send an Entwarnung.
    again = app_modul._emergency_notices(session, NOW, setting_row)
    assert again == []


def test_emergency_entered_notice_gets_an_audit_entry(session: Session) -> None:
    setting_row = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "Flur")
    episode = _episode(session, zone)

    app_modul._emergency_notices(session, NOW, setting_row)

    entry = session.scalar(select(AuditEvent))
    assert entry is not None
    assert entry.object_id == f"notbetrieb:{episode.id}"
