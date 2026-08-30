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
    """This action is not permitted for this principal."""


def _user_permissions(session: Session, user: User) -> frozenset[tuple[str, int | None]]:
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


def principal_for_user(session: Session, user: User) -> Principal:
    return Principal(user_id=user.id, token_id=None, grants=_user_permissions(session, user))


def principal_for_token(session: Session, token: ApiToken) -> Principal:
    """A token's scope is always the intersection with the owner's permissions.

    At runtime, not just at issuance: if the owner later loses a permission, the
    token loses it too.
    """
    now = utcnow()
    if token.revoked_at is not None or (
        token.expires_at is not None and token.expires_at <= now
    ):
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())

    besitzer = session.get(User, token.user_id)
    if besitzer is None:
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())
    vom_besitzer = _user_permissions(session, besitzer)

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


# The permissions, sorted by area the way a human looks for them -- not alphabetically
# by code. The interface used to show a flat list of sixteen entries of the form
# "zone.read - see zones and their state"; anyone setting up a group had to read
# through all sixteen and assign them one by one.
#
# The grouping lives here and not in the template: a new permission should stand out
# as long as nobody has assigned it to anything yet. `test_authz.py` checks that every
# permission from PERMISSIONS appears exactly once.
PERMISSION_AREAS: list[tuple[str, str, list[str]]] = [
    (
        "Sehen und bedienen",
        "Was im Alltag gebraucht wird.",
        ["zone.read", "setpoint.write", "override.create", "override.cancel"],
    ),
    (
        "Zonen und Zeitplaene",
        "Die Anlage umbauen statt sie zu bedienen.",
        ["zone.manage", "schedule.manage", "mode.manage"],
    ),
    (
        "Geraete",
        "Sensoren, Ventile und ihre Zuordnung zu Zonen.",
        ["device.read", "device.manage"],
    ),
    (
        "Betrieb der Anlage",
        "Globale Vorgaben -- und der Riegel, hinter dem eine echte Heizung haengt.",
        ["setting.manage", "control.arm"],
    ),
    (
        "Benutzer und Zugang",
        "Wer sich anmelden darf und wer was sehen kann.",
        ["user.manage", "group.manage", "token.self", "token.manage", "audit.read"],
    ),
]


def has_permission(principal: Principal, code: str, zone_id: int | None = None) -> bool:
    """A plant-wide permission covers every zone, a zone-scoped one only its own.

    The reverse is explicitly not true: whoever is allowed only the bathroom is not
    allowed 'everywhere'.
    """
    if (code, None) in principal.grants:
        return True
    if zone_id is None:
        return False
    return (code, zone_id) in principal.grants


def require(principal: Principal, code: str, zone_id: int | None = None) -> None:
    if not has_permission(principal, code, zone_id):
        # `is not None` and not `if zone_id`: an id of 0 would otherwise be treated as
        # "no zone". Today both databases assign ids starting at 1, but that assumption
        # is not written down anywhere — and in an error message explaining a denied
        # permission, a missing zone reference would be misleading.
        zusatz = f" fuer Zone {zone_id}" if zone_id is not None else ""
        raise Forbidden(f"Recht {code} fehlt{zusatz}")


def visible_zones(session: Session, principal: Principal, code: str) -> list[Zone]:
    """The zones a principal with this permission is allowed to see.

    Every listing and every API response goes through here. This is the spot where
    zone-scoped permissions silently leak if someone forgets to check them anywhere —
    which is why it lives in the domain logic and not in the adapters.
    """
    if (code, None) in principal.grants:
        return list(session.scalars(select(Zone).order_by(Zone.sort_order, Zone.name)))
    allowed = {
        zone_id for vergebener_code, zone_id in principal.grants
        if vergebener_code == code and zone_id is not None
    }
    if not allowed:
        return []
    return list(
        session.scalars(
            select(Zone).where(Zone.id.in_(allowed)).order_by(Zone.sort_order, Zone.name)
        )
    )
