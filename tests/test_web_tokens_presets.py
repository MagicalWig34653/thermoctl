from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, token_with_permissions, user_with_permissions
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.oauth import register_client


def _csrf(client):
    return {
        "X-CSRF-Token": csrf_token(
            client.cookies[COOKIE_NAME], get_settings().secret_key.get_secret_value()
        )
    }


def test_preset_selects_owned_permissions(client_als) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    body = c.get("/tokens?preset=read_only").text
    assert 'value="zone.read" checked' in body
    assert 'value="audit.read" checked' not in body


def test_preset_issue_uses_only_owned_rights(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None), ("device.read", None)])
    response = c.post(
        "/tokens",
        data={"name": "Vorauswahl", "preset": "read_only"},
        headers=_csrf(c),
    )
    assert response.status_code == 200
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Vorauswahl"))
    assert token is not None
    assert (
        len(
            session.scalars(
                select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
            ).all()
        )
        == 2
    )


def test_edit_permissions_does_not_issue_plaintext(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None), ("device.read", None)])
    c.post("/tokens", data={"name": "Bearbeiten", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Bearbeiten"))
    assert token is not None
    response = c.post(
        f"/tokens/{token.id}/permissions",
        data={"permission": "device.read"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "tctl_" not in response.text
    assert (
        len(
            session.scalars(
                select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
            ).all()
        )
        == 1
    )


def test_foreign_token_edit_is_404(client_als, session: Session) -> None:
    owner = user_with_permissions(session, "other", [("token.self", None)])
    token = token_with_permissions(session, owner, [])
    c = client_als([("token.self", None)])
    assert c.post(f"/tokens/{token.id}/permissions", headers=_csrf(c)).status_code == 404


def test_edit_requires_csrf(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    c.post("/tokens", data={"name": "Geschützt", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Geschützt"))
    assert token is not None
    assert (
        c.post(f"/tokens/{token.id}/permissions", data={"permission": "zone.read"}).status_code
        == 403
    )


def test_kiosk_token_is_not_edited_here(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    web_session = resolve_session(session, c.cookies[COOKIE_NAME])
    assert web_session is not None
    owner = session.get(User, web_session.user_id)
    assert owner is not None
    token = token_with_permissions(session, owner, [])
    token.is_kiosk = True
    assert c.post(f"/tokens/{token.id}/permissions", headers=_csrf(c)).status_code == 404
    assert token.name not in c.get("/tokens").text


def test_edit_rejects_unowned_permission_without_changing_token(
    client_als, session: Session
) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    c.post("/tokens", data={"name": "Eng", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Eng"))
    assert token is not None
    response = c.post(
        f"/tokens/{token.id}/permissions",
        data={"permission": "zone.manage"},
        headers=_csrf(c),
    )
    assert response.status_code == 403
    assert (
        len(
            session.scalars(
                select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
            ).all()
        )
        == 1
    )


def test_token_list_shows_oauth_source_and_client(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    web_session = resolve_session(session, c.cookies[COOKIE_NAME])
    assert web_session is not None
    owner = session.get(User, web_session.user_id)
    assert owner is not None
    token = token_with_permissions(session, owner, [])
    oauth_client = register_client(session, "Anwendung", ["https://client.example/return"])
    token.source = "oauth"
    token.oauth_client_id = oauth_client.id
    body = c.get("/tokens").text
    assert "oauth" in body
    assert "Anwendung" in body


def test_revoking_an_oauth_token_also_blocks_its_refresh_family(
    client_als, session: Session
) -> None:
    from thermoctl.db.models.oauth import OAuthRefreshToken
    from thermoctl.domain.oauth import REFRESH_TTL

    c = client_als([("token.self", None)])
    web_session = resolve_session(session, c.cookies[COOKIE_NAME])
    assert web_session is not None
    owner = session.get(User, web_session.user_id)
    assert owner is not None
    token = token_with_permissions(session, owner, [])
    token.source = "oauth"
    oauth_client = register_client(session, "Anwendung", ["https://client.example/return"])
    token.oauth_client_id = oauth_client.id
    row = OAuthRefreshToken(
        token_hash="h" * 64,
        family="f" * 32,
        client_id=oauth_client.id,
        user_id=owner.id,
        api_token_id=token.id,
        expires_at=utcnow() + REFRESH_TTL,
    )
    session.add(row)
    session.flush()
    response = c.post(f"/tokens/{token.id}/revoke", headers=_csrf(c))
    assert response.status_code in (200, 303)
    assert token.revoked_at is not None
    assert row.revoked_at is not None  # Refresh-Familie gesperrt


def test_zone_scoped_permission_can_be_selected_without_widening(
    client_als, session: Session
) -> None:
    zone = create_zone(session, "Büro")
    c = client_als([("token.self", None), ("zone.read", zone.id)])
    body = c.get("/tokens").text
    assert f'value="zone.read:{zone.id}"' in body
    response = c.post(
        "/tokens",
        data={"name": "Büro", "permission": f"zone.read:{zone.id}"},
        headers=_csrf(c),
    )
    assert response.status_code == 200
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Büro"))
    assert token is not None
    rows = session.scalars(
        select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].zone_id == zone.id


def _permission_codes(session: Session, token: ApiToken) -> set[str]:
    session.expire_all()
    return {
        row.code
        for row in session.execute(
            select(Permission.code)
            .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
            .where(ApiTokenPermission.api_token_id == token.id)
        ).all()
    }


def test_permission_change_is_audited_with_old_and_new_rights(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None), ("device.read", None)])
    c.post("/tokens", data={"name": "Protokoll", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Protokoll"))
    assert token is not None
    response = c.post(
        f"/tokens/{token.id}/permissions",
        data={"permission": "device.read"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 303
    event = session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "token.permissions_changed",
            AuditEvent.object_id == str(token.id),
        )
    )
    assert event is not None
    assert event.object_type == "api_token"
    assert event.actor_user_id == token.user_id
    assert event.detail is not None
    assert "alt: zone.read" in event.detail
    assert "neu: device.read" in event.detail


def test_revoked_token_permissions_are_not_edited(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None), ("device.read", None)])
    c.post("/tokens", data={"name": "Widerrufen", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Widerrufen"))
    assert token is not None
    token.revoked_at = utcnow()
    session.flush()
    response = c.post(
        f"/tokens/{token.id}/permissions",
        data={"permission": "device.read"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert _permission_codes(session, token) == {"zone.read"}


def test_expired_token_permissions_are_not_edited(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None), ("device.read", None)])
    c.post("/tokens", data={"name": "Abgelaufen", "code": "zone.read"}, headers=_csrf(c))
    token = session.scalar(select(ApiToken).where(ApiToken.name == "Abgelaufen"))
    assert token is not None
    token.expires_at = utcnow() - timedelta(minutes=1)
    session.flush()
    response = c.post(
        f"/tokens/{token.id}/permissions",
        data={"permission": "device.read"},
        headers=_csrf(c),
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert _permission_codes(session, token) == {"zone.read"}


def test_malformed_zone_in_permission_value_is_refused(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    response = c.post(
        "/tokens",
        data={"name": "Kaputt", "permission": "zone.read:abc", "selection_submitted": "1"},
        headers=_csrf(c),
    )
    assert response.status_code == 200
    assert "kann kein Token mit diesem Recht ausstellen" in response.text
    assert session.scalar(select(ApiToken).where(ApiToken.name == "Kaputt")) is None


def test_issue_refusal_by_the_domain_is_shown_as_hint(
    client_als, session: Session, monkeypatch
) -> None:
    """Zwischen Vorprüfung und Ausstellen können sich die Rechte ändern; die Domäne lehnt
    dann ab, und die Seite zeigt den Satz statt eines Serverfehlers."""
    from thermoctl.domain.authz import Forbidden
    from thermoctl.web import admin_views

    def refuse(*args, **kwargs):
        raise Forbidden("Recht fehlt inzwischen")

    monkeypatch.setattr(admin_views, "issue_token", refuse)
    c = client_als([("token.self", None), ("zone.read", None)])
    response = c.post("/tokens", data={"name": "Rennen", "code": "zone.read"}, headers=_csrf(c))
    assert response.status_code == 200
    assert "Recht fehlt inzwischen" in response.text
