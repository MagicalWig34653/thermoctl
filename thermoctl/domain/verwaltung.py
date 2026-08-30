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


class Verwaltungsfehler(Exception):
    """Eine Aenderung, die fachlich nicht zulaessig ist — kein Fehler des Dienstes."""


VERWALTUNGSRECHT = "user.manage"


def _benutzer_mit_recht(session: Session, code: str) -> list[User]:
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


def _letzter_verwalter(session: Session, benutzer: User) -> bool:
    verwalter = _benutzer_mit_recht(session, VERWALTUNGSRECHT)
    return [b.id for b in verwalter] == [benutzer.id]


def benutzer_anlegen(
    session: Session, *, username: str, display_name: str, passwort: str,
    gruppen_ids: list[int], akteur_id: int | None, quelle: str = "web",
) -> User:
    """Legt einen Benutzer an und ordnet ihn Gruppen zu."""
    if not username.strip():
        raise Verwaltungsfehler("Der Benutzername darf nicht leer sein.")
    vorhanden = session.scalar(select(User).where(User.username == username))
    if vorhanden is not None:
        raise Verwaltungsfehler(f"Den Benutzernamen '{username}' gibt es bereits.")

    # Das Passwort zuerst — `hash_password` wirft bei zu kurzer Eingabe, und eine
    # abgebrochene Anlage darf keine halben Zeilen hinterlassen. Genau dieser Fehler
    # steckte im Einrichtungsformular.
    hash_wert = hash_password(passwort)

    nutzer = User(username=username, display_name=display_name, password_hash=hash_wert)
    session.add(nutzer)
    session.flush()
    for gruppen_id in gruppen_ids:
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppen_id))
    session.flush()
    audit.record(
        session, source=quelle, action="user.created", object_type="user",
        object_id=str(nutzer.id), summary=f"Benutzer '{username}' angelegt",
        user_id=akteur_id,
    )
    return nutzer


def benutzer_aktiv_setzen(
    session: Session, benutzer: User, aktiv: bool, *, akteur_id: int | None,
    quelle: str = "web",
) -> None:
    """Deaktiviert oder reaktiviert einen Benutzer. Geloescht wird nie.

    Ein geloeschter Benutzer risse seine Audit-Eintraege mit sich oder liesse sie ohne
    Namen zurueck. Deaktiviert bleibt nachvollziehbar, wer wann was getan hat.
    """
    if not aktiv and _letzter_verwalter(session, benutzer):
        raise Verwaltungsfehler(
            f"'{benutzer.username}' ist der letzte aktive Benutzer mit dem Recht "
            f"{VERWALTUNGSRECHT}. Wird er deaktiviert, kann niemand mehr Benutzer "
            "verwalten — der Zugang waere nur noch ueber die Datenbank zu retten."
        )
    benutzer.is_active = aktiv
    session.flush()
    audit.record(
        session, source=quelle,
        action="user.activated" if aktiv else "user.deactivated",
        object_type="user", object_id=str(benutzer.id),
        summary=f"Benutzer '{benutzer.username}' {'aktiviert' if aktiv else 'deaktiviert'}",
        user_id=akteur_id,
    )


def passwort_setzen(
    session: Session, benutzer: User, neues_passwort: str, *, akteur_id: int | None,
    quelle: str = "web",
) -> None:
    """Setzt ein neues Passwort. Bestehende Sitzungen bleiben gueltig.

    Bewusst so: Ein Passwortwechsel ist im Alltag meist keine Reaktion auf einen
    Verdacht. Wer alle Sitzungen beenden will, widerruft sie ausdruecklich — dafuer gibt
    es einen eigenen Weg, statt zwei Absichten in einer Handlung zu vermengen.
    """
    benutzer.password_hash = hash_password(neues_passwort)
    session.flush()
    audit.record(
        session, source=quelle, action="user.password_changed", object_type="user",
        object_id=str(benutzer.id),
        summary=f"Passwort von '{benutzer.username}' geaendert", user_id=akteur_id,
    )


def gruppe_anlegen(
    session: Session, *, name: str, beschreibung: str | None, akteur_id: int | None,
    quelle: str = "web",
) -> AccessGroup:
    if not name.strip():
        raise Verwaltungsfehler("Der Gruppenname darf nicht leer sein.")
    if session.scalar(select(AccessGroup).where(AccessGroup.name == name)) is not None:
        raise Verwaltungsfehler(f"Die Gruppe '{name}' gibt es bereits.")
    gruppe = AccessGroup(name=name, description=beschreibung, is_builtin=False)
    session.add(gruppe)
    session.flush()
    audit.record(
        session, source=quelle, action="group.created", object_type="access_group",
        object_id=str(gruppe.id), summary=f"Gruppe '{name}' angelegt", user_id=akteur_id,
    )
    return gruppe


def gruppe_loeschen(
    session: Session, gruppe: AccessGroup, *, akteur_id: int | None, quelle: str = "web"
) -> None:
    if gruppe.is_builtin:
        raise Verwaltungsfehler(
            f"'{gruppe.name}' ist eine eingebaute Gruppe und kann nicht geloescht werden. "
            "Ihre Rechte lassen sich aber aendern."
        )
    _ohne_diese_gruppe_kein_verwalter(session, gruppe)
    name = gruppe.name
    session.delete(gruppe)
    session.flush()
    audit.record(
        session, source=quelle, action="group.deleted", object_type="access_group",
        object_id=str(gruppe.id), summary=f"Gruppe '{name}' geloescht", user_id=akteur_id,
    )


def _ohne_diese_gruppe_kein_verwalter(session: Session, gruppe: AccessGroup) -> None:
    """Verhindert, dass die letzte Quelle des Verwaltungsrechts verschwindet."""
    verwalter = _benutzer_mit_recht(session, VERWALTUNGSRECHT)
    if not verwalter:
        return
    andere_quelle = session.scalar(
        select(GroupPermission.id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .join(
            UserAccessGroup,
            UserAccessGroup.access_group_id == GroupPermission.access_group_id,
        )
        .join(User, User.id == UserAccessGroup.user_id)
        .where(
            Permission.code == VERWALTUNGSRECHT,
            GroupPermission.zone_id.is_(None),
            GroupPermission.access_group_id != gruppe.id,
            User.is_active.is_(True),
        )
        .limit(1)
    )
    if andere_quelle is None:
        raise Verwaltungsfehler(
            f"Ueber '{gruppe.name}' laeuft das einzige verbliebene {VERWALTUNGSRECHT}. "
            "Ohne sie kann niemand mehr Benutzer verwalten."
        )


def recht_vergeben(
    session: Session, gruppe: AccessGroup, code: str, zone_id: int | None, *,
    akteur_id: int | None, quelle: str = "web",
) -> GroupPermission:
    """Vergibt ein Recht an eine Gruppe, wahlweise auf eine Zone eingeschraenkt."""
    recht = session.scalar(select(Permission).where(Permission.code == code))
    if recht is None:
        raise Verwaltungsfehler(f"Das Recht '{code}' gibt es nicht.")
    if not recht.is_zone_scoped and zone_id is not None:
        # `hat_recht()` fragt ein solches Recht immer ohne Zonenangabe ab. Mit Zone
        # vergeben stuende es in der Liste und griffe nie — eine Rechtevergabe, die
        # aussieht, als haette sie gewirkt, ist schlimmer als eine abgelehnte.
        raise Verwaltungsfehler(
            f"Das Recht '{code}' gilt fuer die ganze Anlage und laesst sich nicht auf "
            "eine einzelne Zone einschraenken."
        )
    vorhanden = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == gruppe.id,
            GroupPermission.permission_id == recht.id,
            GroupPermission.zone_id.is_(None) if zone_id is None
            else GroupPermission.zone_id == zone_id,
        )
    )
    if vorhanden is not None:
        return vorhanden
    eintrag = GroupPermission(
        access_group_id=gruppe.id, permission_id=recht.id, zone_id=zone_id
    )
    session.add(eintrag)
    session.flush()
    audit.record(
        session, source=quelle, action="group.permission_granted",
        object_type="access_group", object_id=str(gruppe.id),
        summary=f"Recht {code} an '{gruppe.name}' vergeben",
        detail=None if zone_id is None else f"eingeschraenkt auf Zone {zone_id}",
        user_id=akteur_id,
    )
    return eintrag


def recht_entziehen(
    session: Session, eintrag: GroupPermission, *, akteur_id: int | None,
    quelle: str = "web",
) -> None:
    gruppe = session.get(AccessGroup, eintrag.access_group_id)
    recht = session.get(Permission, eintrag.permission_id)
    assert gruppe is not None and recht is not None
    if recht.code == VERWALTUNGSRECHT and eintrag.zone_id is None:
        _ohne_diese_gruppe_kein_verwalter(session, gruppe)
    session.delete(eintrag)
    session.flush()
    audit.record(
        session, source=quelle, action="group.permission_revoked",
        object_type="access_group", object_id=str(gruppe.id),
        summary=f"Recht {recht.code} von '{gruppe.name}' entzogen", user_id=akteur_id,
    )


def token_widerrufen(
    session: Session, token: ApiToken, *, akteur_id: int | None, quelle: str = "web"
) -> None:
    """Widerruft ein Token. Zeilen werden nie geloescht — sie sind die Historie."""
    if token.revoked_at is not None:
        return
    token.revoked_at = utcnow()
    session.flush()
    audit.record(
        session, source=quelle, action="token.revoked", object_type="api_token",
        object_id=str(token.id), summary=f"Token '{token.name}' widerrufen",
        user_id=akteur_id,
    )


def gruppenrechte_setzen(
    session: Session,
    gruppe: AccessGroup,
    gewuenscht: set[tuple[str, int | None]],
    *,
    akteur_id: int | None,
    quelle: str = "web",
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
    for eintrag, code in session.execute(
        select(GroupPermission, Permission.code)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(GroupPermission.access_group_id == gruppe.id)
    ):
        vorhanden[(code, eintrag.zone_id)] = eintrag

    vergeben = 0
    for code, zone_id in sorted(gewuenscht - set(vorhanden), key=lambda p: (p[0], p[1] or 0)):
        recht_vergeben(
            session, gruppe, code, zone_id, akteur_id=akteur_id, quelle=quelle
        )
        vergeben += 1

    entzogen = 0
    # Erst vergeben, dann entziehen: Wer das Verwaltungsrecht von "ganze Anlage" auf
    # einzelne Zonen umstellt, wuerde sonst mittendrin an der Verwaltersperre scheitern.
    for schluessel, eintrag in sorted(vorhanden.items(), key=lambda p: (p[0][0], p[0][1] or 0)):
        if schluessel not in gewuenscht:
            recht_entziehen(session, eintrag, akteur_id=akteur_id, quelle=quelle)
            entzogen += 1
    return vergeben, entzogen
