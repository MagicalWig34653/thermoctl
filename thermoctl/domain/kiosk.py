"""Kiosk tokens: a narrow, revocable credential for a tablet on the wall.

A "public" dashboard would violate principle 4 (authentication is mandatory) -- the
old system's unauthenticated heating control on the home network was an accepted
property there, not here. Instead, a kiosk token is an ordinary `ApiToken` (see
`thermoctl/auth/tokens.py`), scoped through the very same `ApiTokenPermission` /
`Principal` machinery every other token and every group already uses -- nothing here
bypasses `thermoctl/domain/authz.py`.

Its scope is deliberately narrow and is composed from exactly two facts an admin
chooses: which zones it may see, and whether it may also operate them. "Operate" means
what `thermoctl/domain/remote_control.py` offers a dial from outside: nudging the
setpoint of the mode currently in effect, and boosting the next schedule point
forward. Both are covered by permissions that already exist -- `setpoint.write` and
`override.create` -- so a kiosk token that may operate a zone can do exactly what a
Home Assistant thermostat card can do there, no more.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.tokens import issue_token
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.domain.authz import Forbidden

# The only permissions a kiosk token may ever carry. Nothing outside this pair plus
# `zone.read` -- in particular nothing that would reach settings, users, devices, the
# audit log, or arming, no matter which permissions the issuing admin happens to hold.
KIOSK_VIEW_PERMISSION = "zone.read"
KIOSK_CONTROL_PERMISSIONS = ("setpoint.write", "override.create")


class KioskError(Exception):
    """A kiosk token cannot be issued as requested — not a fault of the service."""


def issue_kiosk_token(
    session: Session,
    owner: User,
    name: str,
    zone_ids: list[int],
    *,
    control_allowed: bool,
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    """Issues a kiosk token scoped to exactly the given zones.

    Delegates to `issue_token`, which already enforces that a token cannot carry
    more than its issuer holds, and re-checks that on every request via
    `principal_for_token` -- an admin who later loses a permission takes it away from
    every kiosk token they issued, too. This function adds nothing on top of that
    except composing the narrow (code, zone_id) list and tagging the row as a kiosk
    token.
    """
    if not zone_ids:
        raise KioskError(
            "Ohne Zone gäbe es nichts zu zeigen — bitte mindestens eine Zone wählen."
        )
    permissions: list[tuple[str, int | None]] = [
        (KIOSK_VIEW_PERMISSION, zone_id) for zone_id in zone_ids
    ]
    if control_allowed:
        permissions += [
            (code, zone_id) for code in KIOSK_CONTROL_PERMISSIONS for zone_id in zone_ids
        ]
    try:
        return issue_token(
            session, owner, name, permissions, expires_at, is_kiosk=True
        )
    except Forbidden as exc:
        # `owner` is always the admin issuing it, so this only fires if they
        # themselves lack `zone.read`/`setpoint.write`/`override.create` for one of
        # the chosen zones -- surfaced as a kiosk-specific message instead of the
        # generic one from `issue_token`.
        raise KioskError(str(exc)) from exc


def kiosk_scope(session: Session, token: ApiToken) -> tuple[list[int], bool]:
    """The zones a kiosk token was issued for, and whether it may operate them.

    Reads the token's *own* `ApiTokenPermission` rows -- not the effective, owner-
    intersected scope `principal_for_token` computes. The admin page lists what the
    token was configured to do; a permission the issuing admin has since lost is a
    fact about the admin, not about the token, and showing it as gone here would make
    the list lie about what was actually set up.
    """
    rows = session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .where(ApiTokenPermission.api_token_id == token.id)
    ).all()
    zone_ids = sorted({zone_id for code, zone_id in rows if zone_id is not None})
    control_allowed = any(code in KIOSK_CONTROL_PERMISSIONS for code, _ in rows)
    return zone_ids, control_allowed
