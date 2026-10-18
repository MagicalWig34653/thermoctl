"""Personen, Zugänge und Konto für die Svelte-Oberfläche (`/ui-api/access`, `/ui-api/account`).

Dünner Adapter (Grundsatz 6) über denselben Domänen- und Servicefunktionen wie die
HTML-Views (`admin_views`, `kiosk_admin_views`, `account_views`, `passkey_views`):
`create_user`, `set_user_active`, `set_user_group`, `set_password`, `create_group`,
`delete_group`, `set_group_ui_profile`, `set_group_permissions`, `issue_token`,
`issue_kiosk_token`, `revoke_token`, `revoke_all_sessions`, `remove_passkey`. Hier steht
keine Fachregel; geprüft wird nur, was die Views auch prüfen, plus die Eingabeform.

Sicherheitsregeln, die in jedem Endpunkt gelten:

* Nur die Cookie-Sitzung (`current_principal`): Bearer-Token und Kiosk-Cookie führen
  zu 401, ein Token kann hier nichts verwalten.
* Reihenfolge: erst Recht (`forbidden`; das Gruppenprofil sperrt nichts), dann Existenz (404).
  Das Konto (`/ui-api/account`) gehört beiden
  Oberflächen und braucht kein Recht.
* Der Klartext eines Tokens steht genau einmal in der Antwort auf das Ausstellen (mit
  `Cache-Control: no-store`) und nirgends sonst -- nicht in Listen, nicht im Protokoll,
  nicht in Fehlerantworten.
* Fehler sind `{"detail": ..., "code": ...}` (optional `"field"`); eine Transaktion, in
  der ein Fehler gemeldet wird, ist zurückgerollt (Ausnahme läuft durch `get_session`).
"""

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from datetime import datetime, timedelta
from typing import Annotated

from anyio import to_thread
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.auth.passwords import PasswordTooShort, verify_password
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session, revoke_all_sessions
from thermoctl.auth.tokens import issue_token
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, Session_
from thermoctl.db.models.dashboard import DashboardTemplate
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.oauth import OAuthClient
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.passkey import UserPasskey
from thermoctl.db.models.zone import Zone
from thermoctl.domain.administration import (
    AdministrationError,
    create_group,
    create_user,
    delete_group,
    is_sole_holder,
    revoke_token,
    set_group_permissions,
    set_group_ui_profile,
    set_password,
    set_user_active,
    set_user_group,
)
from thermoctl.domain.authz import (
    PERMISSION_AREAS,
    Forbidden,
    has_permission,
    principal_for_token,
)
from thermoctl.domain.dashboard_templates import apply_template
from thermoctl.domain.kiosk import KioskError, issue_kiosk_token, kiosk_scope
from thermoctl.domain.oauth import revoke_access_token as revoke_oauth_access_token
from thermoctl.domain.passkey import PasskeyError, remove_passkey
from thermoctl.domain.principal import Principal
from thermoctl.domain.token_presets import PRESETS
from thermoctl.domain.ui_profile import WebUiProfile, parse_profile
from thermoctl.web.shared.login import record_failure, reset_failures, throttle
from thermoctl.web.shared.tokens import replace_token_grants, token_grants
from thermoctl.web.ui_api_rooms import RoomApiError, _iso, _RoomsRoute

router = APIRouter(
    prefix="/ui-api",
    dependencies=[Depends(csrf_protection)],
    include_in_schema=False,
    route_class=_RoomsRoute,
)

_MAX_VALID_DAYS = 3650
_MAX_PASSWORD = 1024


# -- Hilfen -----------------------------------------------------------------------------


def _need(principal: Principal, code: str) -> None:
    if not has_permission(principal, code):
        raise RoomApiError(403, "Dafür fehlt die Berechtigung.", "forbidden")


def _admin(principal: Principal, code: str) -> None:
    """Nur das Recht entscheidet (das Gruppenprofil sperrt nichts)."""
    _need(principal, code)


def _uid(principal: Principal) -> int:
    if principal.user_id is None:  # pragma: no cover - Cookie-Sitzung hat immer einen Benutzer
        raise RoomApiError(403, "Nur für angemeldete Benutzer.", "forbidden")
    return principal.user_id


def _owner(session: Session, principal: Principal) -> User:
    owner = session.get(User, _uid(principal))
    if owner is None:  # pragma: no cover - `current_principal` hat den Benutzer geladen
        raise RoomApiError(403, "Nur für angemeldete Benutzer.", "forbidden")
    return owner


def _not_self(principal: Principal, user: User, was: str) -> None:
    """Wer Benutzer verwaltet, verwaltet andere: am eigenen Konto ändert man das hier nicht.

    Sonst ließe sich über die Verwaltung das eigene Konto umgruppieren (und so Rechte
    verschaffen) oder sperren. Das eigene Passwort ändert `PUT /account/password`.
    """
    if user.id == principal.user_id:
        raise RoomApiError(
            409, f"Das eigene Konto lässt sich hier nicht {was}; das macht eine andere Person.",
            "own_account",
        )


def _within_own_rights(
    session: Session, principal: Principal, wanted: AbstractSet[tuple[str, int | None]]
) -> None:
    """Niemand vergibt oder übernimmt mehr Rechte, als er selbst hat.

    Sonst wäre `user.manage` ein Weg zur Identitätsübernahme (Passwort eines stärkeren
    Kontos setzen, in eine stärkere Gruppe wechseln) oder `group.manage` ein Weg, einer
    Gruppe Rechte zu geben, die man selbst nicht hat. Ausnahme: Wer als einzige aktive
    Person `user.manage` hält, hat niemanden, vor dem die Regel schützt -- und könnte
    sonst ein später hinzugekommenes Recht nie vergeben.
    """
    if principal.user_id is not None and is_sole_holder(session, principal.user_id):
        return
    missing = sorted(
        code for code, zone_id in wanted if not has_permission(principal, code, zone_id)
    )
    if missing:
        raise RoomApiError(
            403,
            "Das geht über die eigenen Rechte hinaus (" + ", ".join(dict.fromkeys(missing)) + ").",
            "beyond_own_rights",
        )


def _assigned_grants(session: Session, user: User) -> set[tuple[str, int | None]]:
    """Die über Gruppen zugewiesenen Rechte, unabhängig davon, ob das Konto aktiv ist.

    `principal_for_user` liefert für ein inaktives Konto nichts; wer ein Konto erst
    deaktiviert, dürfte sonst dessen Passwort setzen und es wieder aktivieren.
    """
    return {
        (code, zone_id)
        for code, zone_id in session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(GroupPermission, GroupPermission.permission_id == Permission.id)
            .join(
                UserAccessGroup,
                UserAccessGroup.access_group_id == GroupPermission.access_group_id,
            )
            .where(UserAccessGroup.user_id == user.id)
        )
    }


def _group_grants(session: Session, group_id: int) -> set[tuple[str, int | None]]:
    return {
        (code, zone_id)
        for code, zone_id in session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.access_group_id == group_id)
        )
    }


def _running_session_id(request: Request, session: Session) -> int | None:
    cookie = request.cookies.get(COOKIE_NAME)
    running = resolve_session(session, cookie) if cookie else None
    return running.id if running is not None else None


def _validation(field: str, detail: str, code: str = "validation") -> RoomApiError:
    return RoomApiError(422, detail, code, field=field)


def _expiry(valid_days: int | None) -> datetime | None:
    return utcnow() + timedelta(days=valid_days) if valid_days is not None else None


# -- Ausgabe ----------------------------------------------------------------------------


class GrantModel(BaseModel):
    code: str = Field(max_length=64)
    zone_id: int | None = None


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    is_active: bool
    is_self: bool
    group_id: int | None
    created_at: str
    last_login_at: str | None


class GroupRefOut(BaseModel):
    id: int
    name: str


class UsersOut(BaseModel):
    users: list[UserOut]
    groups: list[GroupRefOut]


class PermissionOut(BaseModel):
    code: str
    description: str
    zone_scoped: bool


class AreaOut(BaseModel):
    title: str
    hint: str
    permissions: list[PermissionOut]


class GroupOut(BaseModel):
    id: int
    name: str
    description: str | None
    is_builtin: bool
    ui_profile: str
    member_count: int
    permissions: list[GrantModel]
    summary: list[str]


class ZoneRefOut(BaseModel):
    id: int
    name: str


class GroupsOut(BaseModel):
    groups: list[GroupOut]
    areas: list[AreaOut]
    zones: list[ZoneRefOut]
    ui_profiles: list[str]


class TokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    state: str
    source: str
    client_name: str | None
    created_at: str
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    permissions: list[GrantModel]
    effective: list[GrantModel]


class AvailableGrantOut(BaseModel):
    code: str
    zone_id: int | None
    description: str


class TokensOut(BaseModel):
    tokens: list[TokenOut]
    available: list[AvailableGrantOut]
    presets: dict[str, list[str]]


class IssuedTokenOut(BaseModel):
    """Die einzige Antwort, in der ein Klartext steht."""

    token: TokenOut
    plaintext: str


class KioskTokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    state: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    zones: list[ZoneRefOut]
    control_allowed: bool


class KioskTokensOut(BaseModel):
    tokens: list[KioskTokenOut]
    # Zeitzone der Anlage: Ablauf und Benutzung der Zugänge werden darin angezeigt,
    # nicht in der des Browsers (wie am früheren Kiosk).
    timezone: str
    zones: list[ZoneRefOut]


class IssuedKioskTokenOut(BaseModel):
    token: KioskTokenOut
    plaintext: str


class AccountOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    ui_profile: str
    created_at: str
    last_login_at: str | None
    passkeys_available: bool


class SessionOut(BaseModel):
    id: int
    created_at: str
    last_seen_at: str
    expires_at: str
    user_agent: str | None
    ip_address: str | None
    current: bool


class PasskeyOut(BaseModel):
    id: int
    label: str
    created_at: str
    last_used_at: str | None


class PasskeysOut(BaseModel):
    available: bool
    passkeys: list[PasskeyOut]


# -- Eingabe ----------------------------------------------------------------------------


class UserCreateIn(BaseModel):
    username: str = Field(max_length=64)
    display_name: str = Field(max_length=128)
    password: str = Field(max_length=_MAX_PASSWORD)
    group_id: int | None = None
    # Vorlage für die Übersicht des neuen Benutzers (`ui_api_dashboard_admin`); leer: Standard.
    template_id: int | None = None


class ActiveIn(BaseModel):
    active: bool


class UserGroupIn(BaseModel):
    group_id: int | None


class PasswordIn(BaseModel):
    password: str = Field(max_length=_MAX_PASSWORD)


class GroupCreateIn(BaseModel):
    name: str = Field(max_length=64)
    description: str | None = Field(default=None, max_length=255)
    ui_profile: str = "admin"


class UiProfileIn(BaseModel):
    ui_profile: str


class PermissionsIn(BaseModel):
    permissions: list[GrantModel]


class TokenIssueIn(BaseModel):
    name: str = Field(max_length=128)
    valid_days: int | None = Field(default=None, ge=1, le=_MAX_VALID_DAYS)
    permissions: list[GrantModel] | None = None
    preset: str | None = None


class KioskIssueIn(BaseModel):
    name: str = Field(max_length=128)
    zone_ids: list[int]
    control_allowed: bool = False
    valid_days: int | None = Field(default=None, ge=1, le=_MAX_VALID_DAYS)


class OwnPasswordIn(BaseModel):
    current_password: str = Field(max_length=_MAX_PASSWORD)
    new_password: str = Field(max_length=_MAX_PASSWORD)


# -- Benutzer ---------------------------------------------------------------------------


def _users(session: Session, principal: Principal) -> UsersOut:
    # Eine Gruppe je Benutzer, wie in der HTML-Liste: bei mehreren gewinnt die kleinste Id.
    group_of: dict[int, int] = {
        user_id: group_id
        for user_id, group_id in session.execute(
            select(UserAccessGroup.user_id, UserAccessGroup.access_group_id).order_by(
                UserAccessGroup.user_id, UserAccessGroup.access_group_id.desc()
            )
        )
    }
    users = session.scalars(select(User).order_by(User.username)).all()
    return UsersOut(
        users=[_user_out(u, group_of.get(u.id), principal) for u in users],
        groups=[
            GroupRefOut(id=g.id, name=g.name)
            for g in session.scalars(select(AccessGroup).order_by(AccessGroup.name))
        ],
    )


def _user_out(user: User, group_id: int | None, principal: Principal) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        is_self=user.id == principal.user_id,
        group_id=group_id,
        created_at=_iso(user.created_at) or "",
        last_login_at=_iso(user.last_login_at),
    )


def _user_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise RoomApiError(404, "Benutzer nicht gefunden.", "not_found")
    return user


def _known_group(session: Session, group_id: int | None) -> None:
    if group_id is not None and session.get(AccessGroup, group_id) is None:
        raise _validation("group_id", "Die Gruppe gibt es nicht.", "unknown_group")


def _one_user(session: Session, principal: Principal, user: User) -> UserOut:
    group_id = session.scalar(
        select(func.min(UserAccessGroup.access_group_id)).where(UserAccessGroup.user_id == user.id)
    )
    return _user_out(user, group_id, principal)


@router.get("/access/users")
def list_users(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> UsersOut:
    _admin(principal, "user.manage")
    return _users(session, principal)


@router.post("/access/users", status_code=201)
def create_account(
    body: UserCreateIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> UserOut:
    _admin(principal, "user.manage")
    if not body.username.strip():
        raise _validation("username", "Der Benutzername darf nicht leer sein.", "invalid_username")
    _known_group(session, body.group_id)
    if body.group_id is not None:
        _within_own_rights(session, principal, _group_grants(session, body.group_id))
    if body.template_id is not None and session.get(DashboardTemplate, body.template_id) is None:
        raise _validation("template_id", "Diese Vorlage gibt es nicht.", "unknown_template")
    try:
        user = create_user(
            session, username=body.username, display_name=body.display_name,
            password=body.password,
            group_ids=[body.group_id] if body.group_id is not None else [],
            actor_id=principal.user_id,
        )
    except PasswordTooShort as exc:
        raise _validation("password", str(exc), "password_too_short") from None
    except AdministrationError as exc:
        raise RoomApiError(409, str(exc), "username_taken", field="username") from None
    if body.template_id is not None:
        apply_template(session, user.id, body.template_id)
        audit.record(
            session, source="web", action="dashboard.template_applied", object_type="user",
            object_id=str(user.id), summary="Übersichtsvorlage auf neuen Benutzer angewendet",
            user_id=principal.user_id,
        )
    return _one_user(session, principal, user)


@router.put("/access/users/{user_id}/active")
def set_active(
    user_id: int,
    body: ActiveIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> UserOut:
    _admin(principal, "user.manage")
    user = _user_or_404(session, user_id)
    if not body.active:
        _not_self(principal, user, "deaktivieren")
    try:
        set_user_active(session, user, body.active, actor_id=principal.user_id)
    except AdministrationError as exc:
        raise RoomApiError(409, str(exc), "last_administrator") from None
    return _one_user(session, principal, user)


@router.put("/access/users/{user_id}/group")
def set_group_of_user(
    user_id: int,
    body: UserGroupIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> UserOut:
    _admin(principal, "user.manage")
    user = _user_or_404(session, user_id)
    _not_self(principal, user, "umgruppieren")
    _known_group(session, body.group_id)
    if body.group_id is not None:
        _within_own_rights(session, principal, _group_grants(session, body.group_id))
    try:
        set_user_group(session, user, body.group_id, actor_id=principal.user_id)
    except AdministrationError as exc:
        raise RoomApiError(409, str(exc), "last_administrator") from None
    return _one_user(session, principal, user)


@router.put("/access/users/{user_id}/password")
def set_user_password(
    user_id: int,
    body: PasswordIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Setzt das Passwort eines anderen Kontos; jede Sitzung dieses Kontos endet.

    Das eigene Passwort ändert nur `PUT /ui-api/account/password` (mit dem alten) --
    sonst wäre diese Route ein Weg an der Abfrage des alten Passworts vorbei.
    """
    _admin(principal, "user.manage")
    user = _user_or_404(session, user_id)
    if user.id == principal.user_id:
        raise RoomApiError(
            409, "Das eigene Passwort ändern Sie unter Konto (mit dem bisherigen Passwort).",
            "own_account",
        )
    _within_own_rights(session, principal, _assigned_grants(session, user))
    try:
        set_password(session, user, body.password, actor_id=principal.user_id)
    except PasswordTooShort as exc:
        raise _validation("password", str(exc), "password_too_short") from None
    return {"ok": True}


# -- Gruppen ----------------------------------------------------------------------------


def _groups(session: Session) -> GroupsOut:
    groups = list(session.scalars(select(AccessGroup).order_by(AccessGroup.name)))
    taken: dict[int, set[tuple[str, int | None]]] = {g.id: set() for g in groups}
    for group_id, code, zone_id in session.execute(
        select(GroupPermission.access_group_id, Permission.code, GroupPermission.zone_id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
    ):
        taken[group_id].add((code, zone_id))
    members: dict[int, int] = dict(
        session.execute(
            select(UserAccessGroup.access_group_id, func.count()).group_by(
                UserAccessGroup.access_group_id
            )
        ).tuples().all()
    )
    permissions = {p.code: p for p in session.scalars(select(Permission))}
    zone_names = {z.id: z.display_name for z in session.scalars(select(Zone))}

    def sentence(entries: set[tuple[str, int | None]]) -> list[str]:
        result = []
        for code, zone_id in sorted(entries, key=lambda p: (p[0], p[1] or 0)):
            where = f" ({zone_names.get(zone_id, 'unbekannte Zone')})" if zone_id else ""
            result.append(permissions[code].description + where)
        return result

    return GroupsOut(
        groups=[
            GroupOut(
                id=g.id,
                name=g.name,
                description=g.description,
                is_builtin=g.is_builtin,
                ui_profile=parse_profile(g.ui_profile).value,
                member_count=members.get(g.id, 0),
                permissions=[
                    GrantModel(code=code, zone_id=zone_id)
                    for code, zone_id in sorted(taken[g.id], key=lambda p: (p[0], p[1] or 0))
                ],
                summary=sentence(taken[g.id]),
            )
            for g in groups
        ],
        areas=[
            AreaOut(
                title=title,
                hint=hint,
                permissions=[
                    PermissionOut(
                        code=code,
                        description=permissions[code].description,
                        zone_scoped=permissions[code].is_zone_scoped,
                    )
                    for code in codes
                    if code in permissions
                ],
            )
            for title, hint, codes in PERMISSION_AREAS
        ],
        zones=[
            ZoneRefOut(id=z.id, name=z.display_name)
            for z in session.scalars(select(Zone).order_by(Zone.name))
        ],
        ui_profiles=[profile.value for profile in WebUiProfile],
    )


def _group_or_404(session: Session, group_id: int) -> AccessGroup:
    group = session.get(AccessGroup, group_id)
    if group is None:
        raise RoomApiError(404, "Gruppe nicht gefunden.", "not_found")
    return group


def _profile(value: str) -> WebUiProfile:
    """Strenger als `parse_profile` in den Views: ein unbekannter Wert ist ein Fehler.

    Die Views fallen bei unbekanntem Wert auf Anlagensicht zurück; eine Schnittstelle,
    die einen Tippfehler still in die weitreichendere Oberfläche übersetzt, wäre kein
    guter Vertrag.
    """
    try:
        return WebUiProfile(value)
    except ValueError:
        raise _validation("ui_profile", "Unbekannte Oberfläche.", "unknown_ui_profile") from None


@router.get("/access/groups")
def list_groups(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupsOut:
    _admin(principal, "group.manage")
    return _groups(session)


@router.post("/access/groups", status_code=201)
def create_access_group(
    body: GroupCreateIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupOut:
    _admin(principal, "group.manage")
    profile = _profile(body.ui_profile)
    if not body.name.strip():
        raise _validation("name", "Der Gruppenname darf nicht leer sein.", "invalid_name")
    try:
        group = create_group(
            session, name=body.name, description=body.description or None,
            actor_id=principal.user_id, ui_profile=profile,
        )
    except AdministrationError as exc:
        raise RoomApiError(409, str(exc), "name_taken", field="name") from None
    return _one_group(session, group)


def _one_group(session: Session, group: AccessGroup) -> GroupOut:
    return next(g for g in _groups(session).groups if g.id == group.id)


@router.put("/access/groups/{group_id}/ui-profile")
def set_ui_profile(
    group_id: int,
    body: UiProfileIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupOut:
    _admin(principal, "group.manage")
    group = _group_or_404(session, group_id)
    profile = _profile(body.ui_profile)
    set_group_ui_profile(session, group, profile, actor_id=principal.user_id)
    return _one_group(session, group)


@router.delete("/access/groups/{group_id}")
def remove_group(
    group_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    _admin(principal, "group.manage")
    group = _group_or_404(session, group_id)
    try:
        delete_group(session, group, actor_id=principal.user_id)
    except AdministrationError as exc:
        raise RoomApiError(
            409, str(exc), "builtin_group" if group.is_builtin else "last_administrator"
        ) from None
    return {"ok": True}


def _no_self_grant(
    session: Session, principal: Principal, group: AccessGroup,
    wanted: set[tuple[str, int | None]],
) -> None:
    """Rechte der eigenen Gruppe dürfen nicht über die Verwaltung wachsen.

    Entziehen bleibt erlaubt. Ausnahme: Ist die handelnde Person die einzige aktive
    mit `group.manage`, schützt die Regel niemanden -- und ohne die Ausnahme ließe sich
    ein später hinzugekommenes Recht in einer Ein-Personen-Anlage nie vergeben.
    """
    mine = set(
        session.scalars(
            select(UserAccessGroup.access_group_id).where(
                UserAccessGroup.user_id == principal.user_id
            )
        )
    )
    if group.id not in mine:
        return
    present = {
        (code, zone_id)
        for code, zone_id in session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.access_group_id == group.id)
        )
    }
    if not wanted - present:
        return
    holders = session.scalar(
        select(func.count(func.distinct(User.id)))
        .join(UserAccessGroup, UserAccessGroup.user_id == User.id)
        .join(GroupPermission, GroupPermission.access_group_id == UserAccessGroup.access_group_id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(
            Permission.code == "group.manage",
            GroupPermission.zone_id.is_(None),
            User.is_active.is_(True),
        )
    )
    if holders and holders <= 1:
        return
    raise RoomApiError(
        409,
        "Der eigenen Gruppe lassen sich hier keine Rechte hinzufügen; "
        "das macht eine andere Person.",
        "own_group",
    )


@router.put("/access/groups/{group_id}/permissions")
def set_permissions(
    group_id: int,
    body: PermissionsIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupOut:
    """Der **gesamte** gewünschte Rechtestand der Gruppe; die Domäne rechnet die Differenz."""
    _admin(principal, "group.manage")
    group = _group_or_404(session, group_id)
    known = {p.code: p for p in session.scalars(select(Permission))}
    zone_ids = set(session.scalars(select(Zone.id)))
    wanted: set[tuple[str, int | None]] = set()
    for grant in body.permissions:
        permission = known.get(grant.code)
        if permission is None:
            raise _validation("permissions", "Dieses Recht gibt es nicht.", "unknown_permission")
        if grant.zone_id is not None and (
            not permission.is_zone_scoped or grant.zone_id not in zone_ids
        ):
            raise _validation(
                "permissions", "Dieses Recht lässt sich so nicht einschränken.", "invalid_scope"
            )
        wanted.add((grant.code, grant.zone_id))
    _within_own_rights(session, principal, wanted - _group_grants(session, group.id))
    _no_self_grant(session, principal, group, wanted)
    try:
        set_group_permissions(session, group, wanted, actor_id=principal.user_id)
    except AdministrationError as exc:
        raise RoomApiError(409, str(exc), "last_administrator") from None
    return _one_group(session, group)


# -- Eigene API-Tokens ------------------------------------------------------------------


def _token_state(token: ApiToken) -> str:
    if token.revoked_at is not None:
        return "revoked"
    if token.expires_at is not None and token.expires_at <= utcnow():
        return "expired"
    return "active"


def _sorted_grants(grants: AbstractSet[tuple[str, int | None]]) -> list[GrantModel]:
    return [
        GrantModel(code=code, zone_id=zone_id)
        for code, zone_id in sorted(grants, key=lambda g: (g[0], g[1] or 0))
    ]


def _stored_grants(
    session: Session, token_ids: list[int]
) -> dict[int, set[tuple[str, int | None]]]:
    stored: dict[int, set[tuple[str, int | None]]] = {i: set() for i in token_ids}
    if token_ids:
        for token_id, code, zone_id in session.execute(
            select(ApiTokenPermission.api_token_id, Permission.code, ApiTokenPermission.zone_id)
            .join(Permission, ApiTokenPermission.permission_id == Permission.id)
            .where(ApiTokenPermission.api_token_id.in_(token_ids))
        ):
            stored[token_id].add((code, zone_id))
    return stored


def _token_out(
    session: Session, token: ApiToken, stored: set[tuple[str, int | None]],
    clients: dict[int, str],
) -> TokenOut:
    # Bewusst ohne `token_hash`: weder Klartext noch Hash verlassen den Server.
    return TokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        state=_token_state(token),
        source=token.source,
        client_name=clients.get(token.oauth_client_id) if token.oauth_client_id else None,
        created_at=_iso(token.created_at) or "",
        expires_at=_iso(token.expires_at),
        last_used_at=_iso(token.last_used_at),
        revoked_at=_iso(token.revoked_at),
        permissions=_sorted_grants(stored),
        effective=_sorted_grants(principal_for_token(session, token).grants),
    )


def _own_tokens(session: Session, principal: Principal) -> list[ApiToken]:
    return list(session.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == principal.user_id, ApiToken.is_kiosk.is_(False))
        .order_by(ApiToken.name)
    ))


def _clients(session: Session) -> dict[int, str]:
    return {row.id: row.name for row in session.scalars(select(OAuthClient))}


def _own_token_or_404(session: Session, principal: Principal, token_id: int) -> ApiToken:
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != principal.user_id or token.is_kiosk:
        # Fremde Tokens sind unauffindbar, nicht verboten -- sonst verriete die Antwort,
        # welche Ids es gibt.
        raise RoomApiError(404, "Token nicht gefunden.", "not_found")
    return token


def _grant_strings(grants: list[GrantModel]) -> list[str]:
    return [g.code if g.zone_id is None else f"{g.code}:{g.zone_id}" for g in grants]


def _not_held() -> RoomApiError:
    # Ein Text für "gibt es nicht", "gilt dort nicht" und "haben Sie nicht" -- die Antwort
    # soll nicht verraten, welches davon zutrifft.
    return RoomApiError(403, "Ein gewähltes Recht fehlt Ihnen.", "permission_not_held")


@router.get("/access/tokens")
def list_tokens(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TokensOut:
    _admin(principal, "token.self")
    tokens = _own_tokens(session, principal)
    stored = _stored_grants(session, [t.id for t in tokens])
    clients = _clients(session)
    descriptions = {p.code: p.description for p in session.scalars(select(Permission))}
    owned_wide = {code for code, zone_id in principal.grants if zone_id is None}
    return TokensOut(
        tokens=[_token_out(session, t, stored[t.id], clients) for t in tokens],
        available=[
            AvailableGrantOut(code=code, zone_id=zone_id, description=descriptions.get(code, code))
            for code, zone_id in sorted(principal.grants, key=lambda g: (g[0], g[1] or 0))
        ],
        presets={name: sorted(codes & owned_wide) for name, codes in PRESETS.items()},
    )


def _grants_text(grants: Iterable[tuple[str, int | None]]) -> str:
    return ", ".join(
        code if zone_id is None else f"{code}@{zone_id}"
        for code, zone_id in sorted(grants, key=lambda g: (g[0], g[1] or 0))
    ) or "-"


@router.post("/access/tokens", status_code=201)
def issue_own_token(
    body: TokenIssueIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    _admin(principal, "token.self")
    owner = _owner(session, principal)
    if not body.name.strip():
        raise _validation(
            "name", "Das Token braucht einen Namen, sonst ist es später nicht auseinanderzuhalten.",
            "invalid_name",
        )
    if body.permissions is not None:
        selected = _grant_strings(body.permissions)
    elif body.preset is not None:
        if body.preset not in PRESETS:
            raise _validation("preset", "Unbekannte Vorlage.", "unknown_preset")
        selected = sorted(
            PRESETS[body.preset] & {code for code, zone_id in principal.grants if zone_id is None}
        )
    else:
        raise _validation("permissions", "Rechte oder Vorlage angeben.", "missing_selection")
    grants = token_grants(session, principal, selected)
    if grants is None:
        raise _not_held()
    try:
        token, plaintext = issue_token(session, owner, body.name, grants, _expiry(body.valid_days))
    except Forbidden:  # pragma: no cover - `token_grants` hat dieselbe Prüfung schon bestanden
        raise _not_held() from None
    audit.record(
        session, source="web", action="token.issued", object_type="api_token",
        object_id=str(token.id), summary=f"Token '{token.name}' ausgestellt",
        user_id=principal.user_id, token_id=principal.token_id,
        detail=(
            f"Rechte: {_grants_text(grants)}; "
            f"Ablauf: {_iso(token.expires_at) or 'unbefristet'}"
        ),
    )
    # Der Klartext steht genau einmal hier: gespeichert wird nur sein Hash.
    result = IssuedTokenOut(
        token=_token_out(session, token, set(grants), _clients(session)), plaintext=plaintext,
    )
    return JSONResponse(
        result.model_dump(), status_code=201,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.put("/access/tokens/{token_id}/permissions")
def change_token_permissions(
    token_id: int,
    body: PermissionsIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TokenOut:
    _admin(principal, "token.self")
    token = _own_token_or_404(session, principal, token_id)
    if _token_state(token) != "active":
        raise RoomApiError(
            409, "Widerrufene oder abgelaufene Token sind nicht bearbeitbar.", "token_not_active"
        )
    grants = token_grants(
        session, principal, list(dict.fromkeys(_grant_strings(body.permissions)))
    )
    if grants is None:
        raise _not_held()
    replace_token_grants(session, token, grants, actor_id=principal.user_id)
    return _token_out(session, token, set(grants), _clients(session))


@router.post("/access/tokens/{token_id}/revoke")
def revoke_own_token(
    token_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> TokenOut:
    _admin(principal, "token.self")
    token = _own_token_or_404(session, principal, token_id)
    revoke_token(session, token, actor_id=principal.user_id)
    if token.source == "oauth":
        # Sperrt zusätzlich die Refresh-Familie, sonst ließe sich das Token erneuern.
        revoke_oauth_access_token(session, token)
    return _token_out(
        session, token, _stored_grants(session, [token.id])[token.id], _clients(session)
    )


# -- Kiosk-Zugänge ----------------------------------------------------------------------


def _kiosk_out(session: Session, token: ApiToken, zone_names: dict[int, str]) -> KioskTokenOut:
    zone_ids, control_allowed = kiosk_scope(session, token)
    return KioskTokenOut(
        id=token.id,
        name=token.name,
        prefix=token.prefix,
        state=_token_state(token),
        created_at=_iso(token.created_at) or "",
        expires_at=_iso(token.expires_at),
        last_used_at=_iso(token.last_used_at),
        revoked_at=_iso(token.revoked_at),
        zones=[ZoneRefOut(id=z, name=zone_names[z]) for z in zone_ids if z in zone_names],
        control_allowed=control_allowed,
    )


def _zone_names(session: Session) -> dict[int, str]:
    return {z.id: z.display_name for z in session.scalars(select(Zone))}


@router.get("/access/kiosk-tokens")
def list_kiosk_tokens(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> KioskTokensOut:
    _admin(principal, "token.manage")
    names = _zone_names(session)
    tokens = session.scalars(
        select(ApiToken).where(ApiToken.is_kiosk.is_(True)).order_by(ApiToken.name)
    ).all()
    settings = session.get(Setting, 1)
    return KioskTokensOut(
        tokens=[_kiosk_out(session, t, names) for t in tokens],
        timezone=settings.timezone if settings is not None else "UTC",
        zones=[
            ZoneRefOut(id=z.id, name=z.display_name)
            for z in session.scalars(select(Zone).order_by(Zone.sort_order, Zone.name))
        ],
    )


@router.post("/access/kiosk-tokens", status_code=201)
def issue_kiosk_access(
    body: KioskIssueIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    _admin(principal, "token.manage")
    owner = _owner(session, principal)
    if not body.name.strip():
        raise _validation(
            "name", "Das Token braucht einen Namen, sonst ist es an einem Tablet später "
            "nicht wiederzuerkennen.", "invalid_name",
        )
    names = _zone_names(session)
    zone_ids = list(dict.fromkeys(body.zone_ids))
    if any(z not in names for z in zone_ids):
        raise _validation("zone_ids", "Unbrauchbare Zone.", "unknown_zone")
    try:
        token, plaintext = issue_kiosk_token(
            session, owner, body.name, zone_ids,
            control_allowed=body.control_allowed, expires_at=_expiry(body.valid_days),
        )
    except KioskError as exc:
        raise _validation("zone_ids", str(exc), "kiosk_scope") from None
    audit.record(
        session, source="web", action="kiosk_token.issued", object_type="api_token",
        object_id=str(token.id), summary=f"Kiosk-Token '{token.name}' ausgestellt",
        user_id=principal.user_id, token_id=principal.token_id,
        detail=(
            "Räume: "
            + (", ".join(names[z] for z in zone_ids) or "-")
            + f"; Bedienung: {'ja' if body.control_allowed else 'nein'}"
            + f"; Ablauf: {_iso(token.expires_at) or 'unbefristet'}"
        ),
    )
    result = IssuedKioskTokenOut(token=_kiosk_out(session, token, names), plaintext=plaintext)
    return JSONResponse(
        result.model_dump(), status_code=201,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/access/kiosk-tokens/{token_id}/revoke")
def revoke_kiosk_access(
    token_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> KioskTokenOut:
    _admin(principal, "token.manage")
    token = session.get(ApiToken, token_id)
    if token is None or not token.is_kiosk:
        raise RoomApiError(404, "Kiosk-Token nicht gefunden.", "not_found")
    revoke_token(session, token, actor_id=principal.user_id)
    return _kiosk_out(session, token, _zone_names(session))


# -- Konto ------------------------------------------------------------------------------


@router.get("/account")
def show_account(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> AccountOut:
    user = _owner(session, principal)
    return AccountOut(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        ui_profile=principal.ui_profile.value,
        created_at=_iso(user.created_at) or "",
        last_login_at=_iso(user.last_login_at),
        passkeys_available=get_settings().passkeys_available(),
    )


@router.put("/account/password")
async def change_own_password(
    request: Request,
    body: OwnPasswordIn,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    """Ändert das eigene Passwort -- nach Prüfung des bisherigen, nur das eigene.

    Wer ein Sitzungs-Cookie hat, kennt damit noch nicht das Passwort; ohne die Abfrage
    würde ein gestohlenes Cookie reichen, um das Konto dauerhaft zu übernehmen. Die
    eigene Sitzung bleibt, jede andere endet (`set_password`).
    """
    user = _owner(session, principal)
    # Same throttle as the login (own key per user, so a wrong guess here does not
    # delay the real sign-in); awaited, and Argon2id off the event loop.
    key = f"passwort:{user.id}"
    previous_failures = await throttle(key)
    if not await to_thread.run_sync(verify_password, body.current_password, user.password_hash):
        record_failure(key, previous_failures)
        raise RoomApiError(
            403, "Das bisherige Passwort stimmt nicht.", "wrong_password", field="current_password"
        )
    reset_failures(key)
    try:
        set_password(
            session, user, body.new_password,
            actor_id=principal.user_id, keep_session_id=_running_session_id(request, session),
        )
    except PasswordTooShort as exc:
        raise _validation("new_password", str(exc), "password_too_short") from None
    return {"ok": True}


@router.get("/account/sessions")
def list_sessions(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> list[SessionOut]:
    running = _running_session_id(request, session)
    rows = session.scalars(
        select(Session_)
        .where(
            Session_.user_id == _uid(principal),
            Session_.revoked_at.is_(None),
            Session_.expires_at > utcnow(),
        )
        .order_by(Session_.created_at.desc(), Session_.id.desc())
    ).all()
    return [
        SessionOut(
            id=row.id,
            created_at=_iso(row.created_at) or "",
            last_seen_at=_iso(row.last_seen_at) or "",
            expires_at=_iso(row.expires_at) or "",
            user_agent=row.user_agent,
            ip_address=row.ip_address,
            current=row.id == running,
        )
        for row in rows
    ]


@router.post("/account/sessions/revoke-others")
def revoke_other_sessions(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, int]:
    ended = revoke_all_sessions(
        session, _uid(principal), keep_id=_running_session_id(request, session)
    )
    audit.record(
        session, source="web", action="session.revoked_others", object_type="user",
        object_id=str(principal.user_id),
        summary=f"{ended} weitere Sitzung(en) beendet", user_id=principal.user_id,
    )
    return {"ended": ended}


@router.post("/account/sessions/{session_id}/revoke")
def revoke_one_session(
    session_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    entry = session.get(Session_, session_id)
    if (
        entry is None
        or entry.user_id != principal.user_id
        or entry.revoked_at is not None
        or entry.expires_at <= utcnow()
    ):
        # Fremde, beendete und unbekannte Sitzungen sind ununterscheidbar.
        raise RoomApiError(404, "Sitzung nicht gefunden.", "not_found")
    if entry.id == _running_session_id(request, session):
        raise RoomApiError(
            409, "Das ist die Sitzung, mit der Sie gerade arbeiten -- dafür gibt es Abmelden.",
            "current_session",
        )
    entry.revoked_at = utcnow()
    audit.record(
        session, source="web", action="session.revoked", object_type="user",
        object_id=str(principal.user_id), summary="Eine Sitzung beendet",
        user_id=principal.user_id,
    )
    return {"ok": True}


@router.get("/account/passkeys")
def list_passkeys(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> PasskeysOut:
    rows = session.scalars(
        select(UserPasskey)
        .where(UserPasskey.user_id == _uid(principal))
        .order_by(UserPasskey.created_at, UserPasskey.id)
    ).all()
    return PasskeysOut(
        available=get_settings().passkeys_available(),
        passkeys=[
            PasskeyOut(
                id=row.id,
                label=row.label,
                created_at=_iso(row.created_at) or "",
                last_used_at=_iso(row.last_used_at),
            )
            for row in rows
        ],
    )


@router.delete("/account/passkeys/{passkey_id}")
def delete_own_passkey(
    passkey_id: int,
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, bool]:
    user = _owner(session, principal)
    entry = session.get(UserPasskey, passkey_id)
    if entry is None or entry.user_id != user.id:
        raise RoomApiError(404, "Passkey nicht gefunden.", "not_found")
    try:
        remove_passkey(session, user, entry)
    except PasskeyError:  # pragma: no cover - der Besitz ist oben schon geprüft
        raise RoomApiError(404, "Passkey nicht gefunden.", "not_found") from None
    return {"ok": True}
