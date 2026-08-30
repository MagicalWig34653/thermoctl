from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_secret, neues_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.domain.authz import Forbidden, has_permission, principal_for_user


def token_ausstellen(
    session: Session, besitzer: User, name: str,
    permissions: list[tuple[str, int | None]], gueltig_bis: datetime | None,
) -> tuple[ApiToken, str]:
    """Issues a token. The plaintext appears exactly once — here.

    The scope must be a subset of the owner's own permissions. This is also checked
    on every request (see principal_for_token); here the error surfaces early and
    with a comprehensible message.
    """
    p = principal_for_user(session, besitzer)
    for code, zone_id in permissions:
        if not has_permission(p, code, zone_id):
            raise Forbidden(
                f"{besitzer.username} kann kein Token mit {code} ausstellen — "
                "das Recht fehlt ihm selbst."
            )

    plaintext, prefix, hash_value = neues_token()
    token = ApiToken(user_id=besitzer.id, name=name, prefix=prefix,
                     token_hash=hash_value, expires_at=gueltig_bis)
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
    teile = plaintext.split("_", 2)
    if len(teile) != 3 or teile[0] != "tctl":
        return None
    token = session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_secret(teile[2]))
    )
    if token is None or token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at <= utcnow():
        return None
    token.last_used_at = utcnow()
    return token


def revoke_token(session: Session, token: ApiToken) -> None:
    token.revoked_at = utcnow()
