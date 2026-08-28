import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.hilfen import CONSTRAINT_FEHLER, benutzer_anlegen, berechtigung, modus_anlegen, quelle
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, SetupToken
from thermoctl.db.models.operations import AuditEvent, Setting


def test_token_hash_ist_eindeutig(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "a")
    session.add(ApiToken(user_id=nutzer.id, name="HA", prefix="ab12", token_hash="h1"))
    session.flush()
    session.add(ApiToken(user_id=nutzer.id, name="Zweit", prefix="cd34", token_hash="h1"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_token_traegt_eigenen_rechteumfang(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "b")
    token = ApiToken(user_id=nutzer.id, name="HA", prefix="ef56", token_hash="h2")
    session.add(token)
    session.flush()
    session.add(ApiTokenPermission(api_token_id=token.id,
                                   permission_id=berechtigung(session, "zone.read", True).id))
    session.flush()
    assert session.query(ApiTokenPermission).filter_by(api_token_id=token.id).count() == 1


def test_token_wird_mit_seinem_besitzer_geloescht(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "c")
    session.add(ApiToken(user_id=nutzer.id, name="X", prefix="gh78", token_hash="h3"))
    session.flush()
    session.delete(nutzer)
    session.flush()
    assert session.query(ApiToken).filter_by(user_id=nutzer.id).count() == 0


def test_es_gibt_genau_eine_einstellungszeile(session: Session) -> None:
    modus = modus_anlegen(session, "frostschutz", "Frostschutz")
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=modus.id))
    session.flush()
    session.add(Setting(id=2, timezone="Europe/Berlin", frost_protection_mode_id=modus.id))
    with pytest.raises(CONSTRAINT_FEHLER):
        session.flush()


def test_audit_haelt_urheber_und_quelle_fest(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "d")
    session.add(AuditEvent(occurred_at=utcnow(), source_id=quelle(session, "web").id,
                           actor_user_id=nutzer.id, action="login",
                           object_type="user", object_id=str(nutzer.id),
                           summary="Anmeldung erfolgreich"))
    session.flush()
    eintrag = session.query(AuditEvent).one()
    assert eintrag.actor_user_id == nutzer.id
    assert eintrag.actor_token_id is None


def test_setup_token_wird_nur_einmal_verbraucht(session: Session) -> None:
    marke = SetupToken(token_hash="s1")
    session.add(marke)
    session.flush()
    assert marke.consumed_at is None
    marke.consumed_at = utcnow()
    session.flush()
    assert marke.consumed_at is not None
