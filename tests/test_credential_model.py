import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.helpers import CONSTRAINT_ERRORS, create_mode, create_user, ensure_permission, source
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, SetupToken
from thermoctl.db.models.operations import AuditEvent, Setting


def test_token_hash_is_unique(session: Session) -> None:
    user = create_user(session, "a")
    session.add(ApiToken(user_id=user.id, name="HA", prefix="ab12", token_hash="h1"))
    session.flush()
    session.add(ApiToken(user_id=user.id, name="Zweit", prefix="cd34", token_hash="h1"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_token_carries_its_own_permission_scope(session: Session) -> None:
    user = create_user(session, "b")
    token = ApiToken(user_id=user.id, name="HA", prefix="ef56", token_hash="h2")
    session.add(token)
    session.flush()
    session.add(ApiTokenPermission(api_token_id=token.id,
                                   permission_id=ensure_permission(session, "zone.read", True).id))
    session.flush()
    assert session.query(ApiTokenPermission).filter_by(api_token_id=token.id).count() == 1


def test_token_is_deleted_with_its_owner(session: Session) -> None:
    user = create_user(session, "c")
    session.add(ApiToken(user_id=user.id, name="X", prefix="gh78", token_hash="h3"))
    session.flush()
    session.delete(user)
    session.flush()
    assert session.query(ApiToken).filter_by(user_id=user.id).count() == 0


def test_there_is_exactly_one_settings_row(session: Session) -> None:
    mode = create_mode(session, "frostschutz", "Frostschutz")
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=mode.id))
    session.flush()
    session.add(Setting(id=2, timezone="Europe/Berlin", frost_protection_mode_id=mode.id))
    with pytest.raises(CONSTRAINT_ERRORS):
        session.flush()


def test_audit_records_actor_and_source(session: Session) -> None:
    user = create_user(session, "d")
    session.add(AuditEvent(occurred_at=utcnow(), source_id=source(session, "web").id,
                           actor_user_id=user.id, action="login",
                           object_type="user", object_id=str(user.id),
                           summary="Anmeldung erfolgreich"))
    session.flush()
    entry = session.query(AuditEvent).one()
    assert entry.actor_user_id == user.id
    assert entry.actor_token_id is None


def test_setup_token_is_consumed_only_once(session: Session) -> None:
    marker = SetupToken(token_hash="s1")
    session.add(marker)
    session.flush()
    assert marker.consumed_at is None
    marker.consumed_at = utcnow()
    session.flush()
    assert marker.consumed_at is not None
