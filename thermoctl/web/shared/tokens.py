"""Token-Rechte: Auswahl pruefen und ersetzen -- für /ui-api."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.zone import Zone
from thermoctl.domain.authz import has_permission
from thermoctl.domain.principal import Principal


def token_grants(
    session: Session,
    principal: Principal,
    selected: list[str],
) -> list[tuple[str, int | None]] | None:
    known = {row.code: row for row in session.scalars(select(Permission))}
    zone_ids = set(session.scalars(select(Zone.id)))
    grants: list[tuple[str, int | None]] = []
    for raw in dict.fromkeys(selected):
        code, separator, zone_text = raw.partition(":")
        zone_id: int | None = int(zone_text) if separator else None
        row = known.get(code)
        if (
            row is None
            or (zone_id is not None and (not row.is_zone_scoped or zone_id not in zone_ids))
            or not has_permission(principal, code, zone_id)
        ):
            return None
        grants.append((code, zone_id))
    return grants


def grant_label(code: str, zone_id: int | None) -> str:
    """Recht für das Protokoll: ``code`` bzw. ``code@zone-id``."""
    return code if zone_id is None else f"{code}@{zone_id}"


def replace_token_grants(
    session: Session,
    token: ApiToken,
    grants: list[tuple[str, int | None]],
    *,
    actor_id: int | None,
) -> None:
    """Ersetzt die Rechte eines Tokens und protokolliert alt/neu.

    Aus dem View herausgelöst, damit HTML-Weg und `/ui-api` dieselbe Schreibstelle
    benutzen. Die Prüfung, dass `grants` eine Teilmenge der Rechte des Handelnden ist,
    liegt vorher in `token_grants`.
    """
    ids = {row.code: row.id for row in session.scalars(select(Permission))}
    bisherige = list(
        session.scalars(
            select(ApiTokenPermission).where(ApiTokenPermission.api_token_id == token.id)
        )
    )
    codes = {value: key for key, value in ids.items()}
    alt = sorted(grant_label(codes[entry.permission_id], entry.zone_id) for entry in bisherige)
    for entry in bisherige:
        session.delete(entry)
    session.flush()
    for code, zone_id in grants:
        session.add(
            ApiTokenPermission(api_token_id=token.id, permission_id=ids[code], zone_id=zone_id)
        )
    neu = sorted(grant_label(code, zone_id) for code, zone_id in grants)
    audit.record(
        session,
        source="web",
        action="token.permissions_changed",
        object_type="api_token",
        object_id=str(token.id),
        summary=f"Rechte von Token '{token.name}' geändert",
        user_id=actor_id,
        detail=f"alt: {', '.join(alt) or '-'}; neu: {', '.join(neu) or '-'}",
    )
