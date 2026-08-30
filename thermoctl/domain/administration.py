"""Benutzer, Gruppen und Rechte aendern — die Regeln dazu, nicht die Masken.

Liegt in der Domaene, damit Oberflaeche, REST und MCP dieselben Regeln benutzen. Zwei
davon sind der eigentliche Grund fuer dieses Modul:

- **Man kann sich nicht aussperren.** Der letzte aktive Benutzer mit `user.manage` laesst
  sich weder deaktivieren noch aus seiner Gruppe entfernen. Ohne diese Regel genuegt ein
  Fehlgriff, um eine laufende Heizungssteuerung unbedienbar zu machen — mit Zugriff nur
  noch ueber die Datenbank.
- **Ein nicht zonenbezogenes Recht darf keine Zone tragen.** `hat_recht()` fragt solche
  Rechte immer ohne Zonenangabe ab; ein mit Zone vergebenes `user.manage` steht in der
  Liste, greift aber nie. Das Modell haelt das seit Teilprojekt 1 als Zusicherung der
  Domaenenlogik fest — bis hierher hat sie niemand eingeloest.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.auth.passwords import hash_password
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.identity import (
    AccessGroup,
    GroupPermission,
    User,
    UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission


class AdministrationError(Exception):
    """Eine Aenderung, die fachlich nicht zulaessig ist — kein Fehler des Dienstes."""


ADMIN_PERMISSION = "user.manage"


def _user_with_permission(session: Session, code: str) -> list[User]:
    """Alle aktiven Benutzer, die dieses Recht anlagenweit besitzen."""
    return list(
        session.scalars(
            select(User)
            .join(UserAccessGroup, UserAccessGroup.user_id == User.id)
            .join(
                GroupPermission,
                GroupPermission.access_group_id == UserAccessGroup.access_group_id,
            )
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(
                Permission.code == code,
                GroupPermission.zone_id.is_(None),
                User.is_active.is_(True),
            )
            .distinct()
        )
    )


def _last_administrator(session: Session, user: User) -> bool:
    administrator = _user_with_permission(session, ADMIN_PERMISSION)
    return [b.id for b in administrator] == [user.id]


def create_user(
    session: Session, *, username: str, display_name: str, password: str,
    group_ids: list[int], akteur_id: int | None, source: str = "web",
) -> User:
    """Legt einen Benutzer an und ordnet ihn Gruppen zu."""
    if not username.strip():
        raise AdministrationError("Der Benutzername darf nicht leer sein.")
    vorhanden = session.scalar(select(User).where(User.username == username))
    if vorhanden is not None:
        raise AdministrationError(f"Den Benutzernamen '{username}' gibt es bereits.")

    # Das Passwort zuerst — `hash_password` wirft bei zu kurzer Eingabe, und eine
    # abgebrochene Anlage darf keine halben Zeilen hinterlassen. Genau dieser Fehler
    # steckte im Einrichtungsformular.
    hash_value = hash_password(password)

    nutzer = User(username=username, display_name=display_name, password_hash=hash_value)
    session.add(nutzer)
    session.flush()
    for group_id in group_ids:
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=group_id))
    session.flush()
    audit.record(
        session, source=source, action="user.created", object_type="user",
        object_id=str(nutzer.id), summary=f"Benutzer '{username}' angelegt",
        user_id=akteur_id,
    )
    return nutzer


def set_user_active(
    session: Session, user: User, active: bool, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    """Deaktiviert oder reaktiviert einen Benutzer. Geloescht wird nie.

    Ein geloeschter Benutzer risse seine Audit-Eintraege mit sich oder liesse sie ohne
    Namen zurueck. Deaktiviert bleibt nachvollziehbar, wer wann was getan hat.
    """
    if not active and _last_administrator(session, user):
        raise AdministrationError(
            f"'{user.username}' ist der letzte aktive Benutzer mit dem Recht "
            f"{ADMIN_PERMISSION}. Wird er deaktiviert, kann niemand mehr Benutzer "
            "verwalten — der Zugang waere nur noch ueber die Datenbank zu retten."
        )
    user.is_active = active
    session.flush()
    audit.record(
        session, source=source,
        action="user.activated" if active else "user.deactivated",
        object_type="user", object_id=str(user.id),
        summary=f"Benutzer '{user.username}' {'aktiviert' if active else 'deaktiviert'}",
        user_id=akteur_id,
    )


def set_password(
    session: Session, user: User, new_password: str, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    """Setzt ein neues Passwort. Bestehende Sitzungen bleiben gueltig.

    Bewusst so: Ein Passwortwechsel ist im Alltag meist keine Reaktion auf einen
    Verdacht. Wer alle Sitzungen beenden will, widerruft sie ausdruecklich — dafuer gibt
    es einen eigenen Weg, statt zwei Absichten in einer Handlung zu vermengen.
    """
    user.password_hash = hash_password(new_password)
    session.flush()
    audit.record(
        session, source=source, action="user.password_changed", object_type="user",
        object_id=str(user.id),
        summary=f"Passwort von '{user.username}' geaendert", user_id=akteur_id,
    )


def create_group(
    session: Session, *, name: str, beschreibung: str | None, akteur_id: int | None,
    source: str = "web",
) -> AccessGroup:
    if not name.strip():
        raise AdministrationError("Der Gruppenname darf nicht leer sein.")
    if session.scalar(select(AccessGroup).where(AccessGroup.name == name)) is not None:
        raise AdministrationError(f"Die Gruppe '{name}' gibt es bereits.")
    group = AccessGroup(name=name, description=beschreibung, is_builtin=False)
    session.add(group)
    session.flush()
    audit.record(
        session, source=source, action="group.created", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' angelegt", user_id=akteur_id,
    )
    return group


def delete_group(
    session: Session, group: AccessGroup, *, akteur_id: int | None, source: str = "web"
) -> None:
    if group.is_builtin:
        raise AdministrationError(
            f"'{group.name}' ist eine eingebaute Gruppe und kann nicht geloescht werden. "
            "Ihre Rechte lassen sich aber aendern."
        )
    _without_this_group_no_administrator(session, group)
    name = group.name
    session.delete(group)
    session.flush()
    audit.record(
        session, source=source, action="group.deleted", object_type="access_group",
        object_id=str(group.id), summary=f"Gruppe '{name}' geloescht", user_id=akteur_id,
    )


def _without_this_group_no_administrator(session: Session, group: AccessGroup) -> None:
    """Verhindert, dass die letzte Quelle des Verwaltungsrechts verschwindet."""
    administrator = _user_with_permission(session, ADMIN_PERMISSION)
    if not administrator:
        return
    other_source = session.scalar(
        select(GroupPermission.id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .join(
            UserAccessGroup,
            UserAccessGroup.access_group_id == GroupPermission.access_group_id,
        )
        .join(User, User.id == UserAccessGroup.user_id)
        .where(
            Permission.code == ADMIN_PERMISSION,
            GroupPermission.zone_id.is_(None),
            GroupPermission.access_group_id != group.id,
            User.is_active.is_(True),
        )
        .limit(1)
    )
    if other_source is None:
        raise AdministrationError(
            f"Ueber '{group.name}' laeuft das einzige verbliebene {ADMIN_PERMISSION}. "
            "Ohne sie kann niemand mehr Benutzer verwalten."
        )


def grant_permission(
    session: Session, group: AccessGroup, code: str, zone_id: int | None, *,
    akteur_id: int | None, source: str = "web",
) -> GroupPermission:
    """Vergibt ein Recht an eine Gruppe, wahlweise auf eine Zone eingeschraenkt."""
    permission = session.scalar(select(Permission).where(Permission.code == code))
    if permission is None:
        raise AdministrationError(f"Das Recht '{code}' gibt es nicht.")
    if not permission.is_zone_scoped and zone_id is not None:
        # `hat_recht()` fragt ein solches Recht immer ohne Zonenangabe ab. Mit Zone
        # vergeben stuende es in der Liste und griffe nie — eine Rechtevergabe, die
        # aussieht, als haette sie gewirkt, ist schlimmer als eine abgelehnte.
        raise AdministrationError(
            f"Das Recht '{code}' gilt fuer die ganze Anlage und laesst sich nicht auf "
            "eine einzelne Zone einschraenken."
        )
    vorhanden = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission.id,
            GroupPermission.zone_id.is_(None) if zone_id is None
            else GroupPermission.zone_id == zone_id,
        )
    )
    if vorhanden is not None:
        return vorhanden
    entry = GroupPermission(
        access_group_id=group.id, permission_id=permission.id, zone_id=zone_id
    )
    session.add(entry)
    session.flush()
    audit.record(
        session, source=source, action="group.permission_granted",
        object_type="access_group", object_id=str(group.id),
        summary=f"Recht {code} an '{group.name}' vergeben",
        detail=None if zone_id is None else f"eingeschraenkt auf Zone {zone_id}",
        user_id=akteur_id,
    )
    return entry


def revoke_permission(
    session: Session, entry: GroupPermission, *, akteur_id: int | None,
    source: str = "web",
) -> None:
    group = session.get(AccessGroup, entry.access_group_id)
    permission = session.get(Permission, entry.permission_id)
    assert group is not None and permission is not None
    if permission.code == ADMIN_PERMISSION and entry.zone_id is None:
        _without_this_group_no_administrator(session, group)
    session.delete(entry)
    session.flush()
    audit.record(
        session, source=source, action="group.permission_revoked",
        object_type="access_group", object_id=str(group.id),
        summary=f"Recht {permission.code} von '{group.name}' entzogen", user_id=akteur_id,
    )


def revoke_token(
    session: Session, token: ApiToken, *, akteur_id: int | None, source: str = "web"
) -> None:
    """Widerruft ein Token. Zeilen werden nie geloescht — sie sind die Historie."""
    if token.revoked_at is not None:
        return
    token.revoked_at = utcnow()
    session.flush()
    audit.record(
        session, source=source, action="token.revoked", object_type="api_token",
        object_id=str(token.id), summary=f"Token '{token.name}' widerrufen",
        user_id=akteur_id,
    )


def set_group_permissions(
    session: Session,
    group: AccessGroup,
    gewuenscht: set[tuple[str, int | None]],
    *,
    akteur_id: int | None,
    source: str = "web",
) -> tuple[int, int]:
    """Bringt die Rechte einer Gruppe auf den gewuenschten Stand. Gibt (vergeben, entzogen).

    Statt einzelner Vergeben- und Entziehen-Klicks: Die Oberflaeche schickt den ganzen
    gewuenschten Stand, hier wird die Differenz gebildet. Wer eine Gruppe einrichtet,
    denkt in "das soll sie duerfen" und nicht in einer Folge von sechzehn Einzelschritten.

    Ruft ausdruecklich `recht_vergeben` und `recht_entziehen` auf, statt selbst Zeilen
    anzufassen: Dort haengen die Pruefung auf zonenlose Rechte, die Sperre gegen den
    Verlust des letzten Verwalters und die Audit-Eintraege. Eine zweite Fassung davon
    waere genau die Art Abkuerzung, die spaeter jemanden aussperrt.
    """
    vorhanden: dict[tuple[str, int | None], GroupPermission] = {}
    for entry, code in session.execute(
        select(GroupPermission, Permission.code)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(GroupPermission.access_group_id == group.id)
    ):
        vorhanden[(code, entry.zone_id)] = entry

    vergeben = 0
    for code, zone_id in sorted(gewuenscht - set(vorhanden), key=lambda p: (p[0], p[1] or 0)):
        grant_permission(
            session, group, code, zone_id, akteur_id=akteur_id, source=source
        )
        vergeben += 1

    entzogen = 0
    # Erst vergeben, dann entziehen: Wer das Verwaltungsrecht von "ganze Anlage" auf
    # einzelne Zonen umstellt, wuerde sonst mittendrin an der Verwaltersperre scheitern.
    for schluessel, entry in sorted(vorhanden.items(), key=lambda p: (p[0][0], p[0][1] or 0)):
        if schluessel not in gewuenscht:
            revoke_permission(session, entry, akteur_id=akteur_id, source=source)
            entzogen += 1
    return vergeben, entzogen
