from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_secret, new_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.domain.authz import Forbidden, has_permission, principal_for_user


def issue_token(
    session: Session, owner: User, name: str,
    permissions: list[tuple[str, int | None]], valid_until: datetime | None,
    *, is_kiosk: bool = False,
) -> tuple[ApiToken, str]:
    """Issues a token. The plaintext appears exactly once — here.

    The scope must be a subset of the owner's own permissions. This is also checked
    on every request (see principal_for_token); here the error surfaces early and
    with a comprehensible message.

    `is_kiosk` only tags the row so `/tokens` and `/kiosk-tokens` can each list their
    own kind without showing the other's — it changes nothing about how the token's
    scope is computed or checked.
    """
    p = principal_for_user(session, owner)
    for code, zone_id in permissions:
        if not has_permission(p, code, zone_id):
            raise Forbidden(
                f"{owner.username} kann kein Token mit {code} ausstellen — "
                "das Recht fehlt ihm selbst."
            )

    plaintext, prefix, hash_value = new_token()
    token = ApiToken(user_id=owner.id, name=name, prefix=prefix,
                     token_hash=hash_value, expires_at=valid_until, is_kiosk=is_kiosk)
    session.add(token)
    session.flush()

    alle = {code: pid for code, pid in session.execute(
        select(Permission.code, Permission.id)
    ).all()}
    for code, zone_id in permissions:
        session.add(ApiTokenPermission(api_token_id=token.id, permission_id=alle[code],
                                       zone_id=zone_id))
    session.flush()
    return token, plaintext


def resolve_token(session: Session, plaintext: str) -> ApiToken | None:
    parts = plaintext.split("_", 2)
    if len(parts) != 3 or parts[0] != "tctl":
        return None
    token = session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_secret(parts[2]))
    )
    if token is None or token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at <= utcnow():
        return None
    token.last_used_at = utcnow()
    return token


def revoke_token(session: Session, token: ApiToken) -> None:
    token.revoked_at = utcnow()
