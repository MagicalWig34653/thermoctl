from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_geheimnis, neues_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.domain.authz import Forbidden, hat_recht, principal_fuer_benutzer


def token_ausstellen(
    session: Session, besitzer: User, name: str,
    rechte: list[tuple[str, int | None]], gueltig_bis: datetime | None,
) -> tuple[ApiToken, str]:
    """Stellt ein Token aus. Der Klartext erscheint genau einmal — hier.

    Der Umfang muss eine Teilmenge der Rechte des Besitzers sein. Geprueft wird das
    zusaetzlich bei jeder Anfrage (siehe principal_fuer_token); hier faellt der Fehler
    frueh und mit einer verstaendlichen Meldung auf.
    """
    p = principal_fuer_benutzer(session, besitzer)
    for code, zone_id in rechte:
        if not hat_recht(p, code, zone_id):
            raise Forbidden(
                f"{besitzer.username} kann kein Token mit {code} ausstellen — "
                "das Recht fehlt ihm selbst."
            )

    klartext, prefix, hash_wert = neues_token()
    token = ApiToken(user_id=besitzer.id, name=name, prefix=prefix,
                     token_hash=hash_wert, expires_at=gueltig_bis)
    session.add(token)
    session.flush()

    alle = {code: pid for code, pid in session.execute(
        select(Permission.code, Permission.id)
    ).all()}
    for code, zone_id in rechte:
        session.add(ApiTokenPermission(api_token_id=token.id, permission_id=alle[code],
                                       zone_id=zone_id))
    session.flush()
    return token, klartext


def token_aufloesen(session: Session, klartext: str) -> ApiToken | None:
    teile = klartext.split("_", 2)
    if len(teile) != 3 or teile[0] != "tctl":
        return None
    token = session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_geheimnis(teile[2]))
    )
    if token is None or token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at <= utcnow():
        return None
    token.last_used_at = utcnow()
    return token


def token_widerrufen(session: Session, token: ApiToken) -> None:
    token.revoked_at = utcnow()
