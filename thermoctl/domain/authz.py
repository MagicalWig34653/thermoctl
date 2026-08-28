from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import (
    GroupPermission,
    User,
    UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.zone import Zone
from thermoctl.domain.principal import Principal


class Forbidden(Exception):
    """Die Handlung ist diesem Principal nicht erlaubt."""


def _benutzerrechte(session: Session, user: User) -> frozenset[tuple[str, int | None]]:
    if not user.is_active:
        return frozenset()
    zeilen = session.execute(
        select(Permission.code, GroupPermission.zone_id)
        .join(GroupPermission, GroupPermission.permission_id == Permission.id)
        .join(
            UserAccessGroup,
            UserAccessGroup.access_group_id == GroupPermission.access_group_id,
        )
        .where(UserAccessGroup.user_id == user.id)
    ).all()
    return frozenset((code, zone_id) for code, zone_id in zeilen)


def principal_fuer_benutzer(session: Session, user: User) -> Principal:
    return Principal(user_id=user.id, token_id=None, grants=_benutzerrechte(session, user))


def principal_fuer_token(session: Session, token: ApiToken) -> Principal:
    """Der Umfang eines Tokens ist stets die Schnittmenge mit den Rechten des Besitzers.

    Zur Laufzeit, nicht nur beim Ausstellen: verliert der Besitzer spaeter ein Recht,
    verliert das Token es ebenfalls.
    """
    jetzt = utcnow()
    if token.revoked_at is not None or (
        token.expires_at is not None and token.expires_at <= jetzt
    ):
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())

    besitzer = session.get(User, token.user_id)
    if besitzer is None:
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())
    vom_besitzer = _benutzerrechte(session, besitzer)

    zeilen = session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .where(ApiTokenPermission.api_token_id == token.id)
    ).all()
    vom_token = frozenset((code, zone_id) for code, zone_id in zeilen)

    wirksam = {
        (code, zone_id)
        for code, zone_id in vom_token
        if (code, zone_id) in vom_besitzer or (code, None) in vom_besitzer
    }
    return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset(wirksam))


def hat_recht(principal: Principal, code: str, zone_id: int | None = None) -> bool:
    """Ein anlagenweites Recht deckt jede Zone ab, ein zonenbezogenes nur die eigene.

    Umgekehrt gilt das ausdruecklich nicht: wer nur das Bad darf, darf nicht 'ueberall'.
    """
    if (code, None) in principal.grants:
        return True
    if zone_id is None:
        return False
    return (code, zone_id) in principal.grants


def require(principal: Principal, code: str, zone_id: int | None = None) -> None:
    if not hat_recht(principal, code, zone_id):
        # `is not None` und nicht `if zone_id`: eine Kennung 0 waere sonst als
        # "keine Zone" behandelt. Heute vergeben beide Datenbanken ab 1, aber die
        # Annahme steht nirgends geschrieben — und in einer Fehlermeldung, die eine
        # Rechtsverweigerung erklaert, ist eine fehlende Zonenangabe irrefuehrend.
        zusatz = f" fuer Zone {zone_id}" if zone_id is not None else ""
        raise Forbidden(f"Recht {code} fehlt{zusatz}")


def visible_zones(session: Session, principal: Principal, code: str) -> list[Zone]:
    """Die Zonen, auf die ein Principal mit diesem Recht sehen darf.

    Jede Liste und jede API-Antwort geht hier durch. Das ist die Stelle, an der
    zonenbezogene Rechte still lecken, wenn man sie irgendwo vergisst — deshalb liegt
    sie in der Domaenenlogik und nicht in den Adaptern.
    """
    if (code, None) in principal.grants:
        return list(session.scalars(select(Zone).order_by(Zone.sort_order, Zone.name)))
    erlaubt = {
        zone_id for vergebener_code, zone_id in principal.grants
        if vergebener_code == code and zone_id is not None
    }
    if not erlaubt:
        return []
    return list(
        session.scalars(
            select(Zone).where(Zone.id.in_(erlaubt)).order_by(Zone.sort_order, Zone.name)
        )
    )
