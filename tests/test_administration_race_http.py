"""Gleichzeitige Verwaltungsänderungen über die echten Endpunkte: nie 500, nie ohne Verwalter.

`test_administration_race.py` führt die Domänenfunktionen gleichzeitig aus. Das reicht nicht:
Die Endpunkte legen um die Änderung einen Savepoint (`_no_self_made_sole_holder`), und rollt
MariaDB bei einer Kollision (1020, Verklemmung 1213) die ganze Transaktion zurück, scheitert
dessen `ROLLBACK TO SAVEPOINT` mit 1305 -- im Endpunkt ein 500 statt der Antwort „bitte erneut
versuchen". Der Fehler zeigt sich nur mit echten Anfragen gegen eine echte Datenbank, und
SQLite kennt weder Zeilensperren noch diese Isolationsstufe: dort wird übersprungen.

Jeder Fall läuft mit `innodb_snapshot_isolation` ON und OFF (je Verbindung, nicht global).
"""

import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import ensure_permission, source
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import Settings, get_settings
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent

UM = "user.manage"
GM = "group.manage"
EVERYTHING = [UM, GM, "token.self", "token.manage"]
ROUNDS = 6


@dataclass
class Request:
    secret: str
    method: str
    url: str
    body: dict[str, object] | None


@pytest.fixture(params=["on", "off"], ids=["snapshot-on", "snapshot-off"])
def app_client(
    request: pytest.FixtureRequest,
    engine: Engine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], TestClient]]:
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite hat keine Zeilensperren "
            "und kein REPEATABLE READ, das Rennen ist dort nicht herstellbar"
        )
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    app = create_app()
    app_engine = app.state.session_factory.kw["bind"]
    value = "ON" if request.param == "on" else "OFF"

    @event.listens_for(app_engine, "connect")
    def _isolation(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute(f"SET SESSION innodb_snapshot_isolation={value}")
        cursor.close()

    app_engine.dispose()  # connections made before the listener keep the server default

    def make(secret: str) -> TestClient:
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, secret)
        client.headers[CSRF_HEADER] = csrf_token(secret, settings.secret_key.get_secret_value())
        return client

    yield make
    app_engine.dispose()
    get_settings.cache_clear()


def _group(session: Session, name: str, codes: list[str]) -> int:
    group = AccessGroup(name=name, ui_profile="admin")
    session.add(group)
    session.flush()
    for code in codes:
        permission = ensure_permission(session, code)
        session.add(
            GroupPermission(
                access_group_id=group.id, permission_id=permission.id, zone_id=None
            )
        )
    session.flush()
    return group.id


def _person(session: Session, name: str, group_id: int | None) -> tuple[int, str]:
    user = User(username=name, display_name=name, password_hash="x")
    session.add(user)
    session.flush()
    if group_id is not None:
        session.add(UserAccessGroup(user_id=user.id, access_group_id=group_id))
    _entry, secret = create_session(session, user, 3600)
    session.flush()
    return user.id, secret


def _holders(engine: Engine, code: str) -> int:
    with Session(engine) as check:
        return int(
            check.scalar(
                select(func.count(func.distinct(User.id)))
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
            )
            or 0
        )


# -- Szenarien: je zwei Anfragen, die gleichzeitig abgeschickt werden -------------------


def _same_group_permissions(s: Session) -> list[Request]:
    admins, target = _group(s, "g", EVERYTHING), _group(s, "gt", ["token.self", "token.manage"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    body: dict[str, object] = {"permissions": [{"code": "token.self"}]}
    return [
        Request(sa, "PUT", f"/ui-api/access/groups/{target}/permissions", body),
        Request(sb, "PUT", f"/ui-api/access/groups/{target}/permissions", body),
    ]


def _same_group_grant_the_same_permission(s: Session) -> list[Request]:
    admins, target = _group(s, "g", EVERYTHING), _group(s, "gt", ["token.self"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    body: dict[str, object] = {"permissions": [{"code": "token.self"}, {"code": "token.manage"}]}
    return [
        Request(sa, "PUT", f"/ui-api/access/groups/{target}/permissions", body),
        Request(sb, "PUT", f"/ui-api/access/groups/{target}/permissions", body),
    ]


def _permissions_against_delete(s: Session) -> list[Request]:
    admins, target = _group(s, "g", EVERYTHING), _group(s, "gt", ["token.self", "token.manage"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    return [
        Request(
            sa, "PUT", f"/ui-api/access/groups/{target}/permissions",
            {"permissions": [{"code": "token.self"}]},
        ),
        Request(sb, "DELETE", f"/ui-api/access/groups/{target}", None),
    ]


def _regroup_same_person_twice(s: Session) -> list[Request]:
    admins, g2, g3 = _group(s, "g", EVERYTHING), _group(s, "g2", []), _group(s, "g3", [])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    target, _secret = _person(s, "t", g2)
    return [
        Request(sa, "PUT", f"/ui-api/access/users/{target}/group", {"group_id": g3}),
        Request(sb, "PUT", f"/ui-api/access/users/{target}/group", {"group_id": None}),
    ]


def _regroup_against_revoke(s: Session) -> list[Request]:
    g1, g2 = _group(s, "g1", [UM, GM]), _group(s, "g2", [UM, GM])
    g3 = _group(s, "g3", [GM])
    x, _sx = _person(s, "x", g1)
    _z, sz = _person(s, "z", g2)
    _y, sy = _person(s, "y", g3)
    return [
        Request(sz, "PUT", f"/ui-api/access/users/{x}/group", {"group_id": g2}),
        Request(
            sy, "PUT", f"/ui-api/access/groups/{g2}/permissions",
            {"permissions": [{"code": GM}]},
        ),
    ]


def _regroup_against_delete(s: Session) -> list[Request]:
    g1, g2 = _group(s, "g1", [UM, GM]), _group(s, "g2", [UM, GM])
    g3 = _group(s, "g3", [GM])
    x, _sx = _person(s, "x", g1)
    _z, sz = _person(s, "z", g2)
    _y, sy = _person(s, "y", g3)
    return [
        Request(sz, "PUT", f"/ui-api/access/users/{x}/group", {"group_id": g2}),
        Request(sy, "DELETE", f"/ui-api/access/groups/{g2}", None),
    ]


def _create_into_deleted_group(s: Session) -> list[Request]:
    admins, doomed = _group(s, "ga", EVERYTHING), _group(s, "gb", [])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    new_user: dict[str, object] = {
        "username": "neu", "display_name": "Neu", "password": "langes-Passwort-123",
        "group_id": doomed,
    }
    return [
        Request(sa, "POST", "/ui-api/access/users", new_user),
        Request(sb, "DELETE", f"/ui-api/access/groups/{doomed}", None),
    ]


def _create_same_name_twice(s: Session) -> list[Request]:
    admins = _group(s, "g", EVERYTHING)
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    new_user: dict[str, object] = {
        "username": "doppelt", "display_name": "D", "password": "langes-Passwort-123",
    }
    return [
        Request(sa, "POST", "/ui-api/access/users", new_user),
        Request(sb, "POST", "/ui-api/access/users", new_user),
    ]


def _mutual_deactivation(s: Session) -> list[Request]:
    group = _group(s, "g", EVERYTHING)
    (a, sa), (b, sb) = _person(s, "a", group), _person(s, "b", group)
    return [
        Request(sa, "PUT", f"/ui-api/access/users/{b}/active", {"active": False}),
        Request(sb, "PUT", f"/ui-api/access/users/{a}/active", {"active": False}),
    ]


SCENARIOS = {
    "dieselbe-gruppe-rechte": _same_group_permissions,
    "dieselbe-gruppe-recht-hinzufuegen": _same_group_grant_the_same_permission,
    "rechte-gegen-loeschen": _permissions_against_delete,
    "dieselbe-person-zweimal-umgruppieren": _regroup_same_person_twice,
    "umgruppieren-gegen-rechteentzug": _regroup_against_revoke,
    "umgruppieren-gegen-loeschen": _regroup_against_delete,
    "anlegen-in-geloeschte-gruppe": _create_into_deleted_group,
    "gleicher-name-zweimal-anlegen": _create_same_name_twice,
    "gegenseitig-deaktivieren": _mutual_deactivation,
}

# The answers a loser may get: nothing changed (409), the group is gone (404), or its own
# session ended because the other one deactivated the account (401). Never a 5xx.
ACCEPTED = {200, 201, 401, 404, 409}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_concurrent_administration_requests_never_end_in_a_500_or_without_an_administrator(
    name: str, engine: Engine, app_client: Callable[[str], TestClient]
) -> None:
    outcomes: Counter[tuple[str, ...]] = Counter()
    known_permissions: set[str] = set()
    try:
        for _round in range(ROUNDS):
            with Session(engine) as setup:
                if not known_permissions:
                    known_permissions = set(setup.scalars(select(Permission.code)))
                source(setup, "web")
                requests = SCENARIOS[name](setup)
                setup.commit()
            clients = [app_client(r.secret) for r in requests]
            barrier = threading.Barrier(len(requests))
            statuses: list[int] = [0] * len(requests)

            def send(i: int, requests: list[Request] = requests,
                     clients: list[TestClient] = clients,
                     barrier: threading.Barrier = barrier,
                     statuses: list[int] = statuses) -> None:
                barrier.wait(timeout=20)
                mine = requests[i]
                reply = clients[i].request(mine.method, mine.url, json=mine.body)
                statuses[i] = reply.status_code

            threads = [threading.Thread(target=send, args=(i,)) for i in range(len(requests))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
                assert not thread.is_alive(), "eine Anfrage hängt (Verklemmung?)"

            outcomes[tuple(str(s) for s in sorted(statuses))] += 1
            assert set(statuses) <= ACCEPTED, f"{name}: {sorted(statuses)}"
            assert _holders(engine, UM) >= 1 and _holders(engine, GM) >= 1, name
            assert _duplicate_permission_rows(engine) == 0, f"{name}: doppelte Rechte-Zeilen"
            assert _usernames_twice(engine) == 0, f"{name}: Benutzername doppelt"
            _clean(engine, known_permissions)
    finally:
        _clean(engine, known_permissions)
    assert sum(outcomes.values()) == ROUNDS


def _duplicate_permission_rows(engine: Engine) -> int:
    """The unique key does not apply to `zone_id IS NULL`, so two administrators who add the
    same permission to one group in the same moment can leave it there twice -- and revoking
    it later removes one row and leaves the permission in place."""
    with Session(engine) as check:
        return len(
            check.execute(
                select(GroupPermission.access_group_id)
                .group_by(
                    GroupPermission.access_group_id,
                    GroupPermission.permission_id,
                    func.coalesce(GroupPermission.zone_id, 0),
                )
                .having(func.count() > 1)
            ).all()
        )


def _usernames_twice(engine: Engine) -> int:
    with Session(engine) as check:
        return len(
            check.execute(
                select(User.username).group_by(User.username).having(func.count() > 1)
            ).all()
        )


def _clean(engine: Engine, known_permissions: set[str]) -> None:
    with Session(engine) as cleanup:
        cleanup.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for model in (Session_, AuditEvent, UserAccessGroup, GroupPermission, AccessGroup, User):
            cleanup.execute(delete(model))
        if known_permissions:
            cleanup.execute(delete(Permission).where(Permission.code.not_in(known_permissions)))
        cleanup.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        cleanup.commit()
