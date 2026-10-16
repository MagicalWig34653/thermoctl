
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import token_with_permissions, user_with_permissions
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.config import get_settings
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission


def _csrf(client):
    return {
        "X-CSRF-Token": csrf_token(
            client.cookies[COOKIE_NAME], get_settings().secret_key.get_secret_value()
        )
    }








def test_foreign_token_edit_is_404(client_als, session: Session) -> None:
    owner = user_with_permissions(session, "other", [("token.self", None)])
    token = token_with_permissions(session, owner, [])
    c = client_als([("token.self", None)])
    assert c.post(f"/tokens/{token.id}/permissions", headers=_csrf(c)).status_code == 404




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










