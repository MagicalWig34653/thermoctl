"""Gleichzeitige Verwaltungsänderungen über die echten Endpunkte: nie 500, nie ohne Verwalter.

`test_administration_race.py` führt die Domänenfunktionen gleichzeitig aus. Das reicht nicht:
Die Endpunkte legen um die Änderung einen Savepoint (`_no_self_made_sole_holder`), und rollt
MariaDB bei einer Kollision (1020, Verklemmung 1213) die ganze Transaktion zurück, scheitert
dessen `ROLLBACK TO SAVEPOINT` mit 1305 -- im Endpunkt ein 500 statt der Antwort „bitte erneut
versuchen". Der Fehler zeigt sich nur mit echten Anfragen gegen eine echte Datenbank, und
SQLite kennt weder Zeilensperren noch diese Isolationsstufe: dort wird übersprungen.

Jeder Fall läuft mit `innodb_snapshot_isolation` ON und OFF (je Verbindung, nicht global).
"""

import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import ensure_permission, source
from thermoctl.app import create_app
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.passwords import hash_password
from thermoctl.auth.secrets import hash_secret
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, Session_
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.db.models.passkey import UserPasskey

UM = "user.manage"
GM = "group.manage"
EVERYTHING = [UM, GM, "token.self", "token.manage"]
ROUNDS = int(os.environ.get("THERMOCTL_RACE_ROUNDS", "12"))  # more rounds for a stress run


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


OLD_PASSWORD = "das-alte-passwort-1"


def _person(
    session: Session, name: str, group_id: int | None, password: str | None = None
) -> tuple[int, str]:
    user = User(
        username=name, display_name=name,
        password_hash=hash_password(password) if password else "x",
    )
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


def _create_same_group_name_twice(s: Session) -> list[Request]:
    admins = _group(s, "g", EVERYTHING)
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    body: dict[str, object] = {"name": "doppelt", "description": "", "ui_profile": "admin"}
    return [
        Request(sa, "POST", "/ui-api/access/groups", body),
        Request(sb, "POST", "/ui-api/access/groups", body),
    ]


def _password_against_deactivation(s: Session) -> list[Request]:
    admins, plain = _group(s, "g", EVERYTHING), _group(s, "p", ["token.self"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    target, _secret = _person(s, "t", plain)
    return [
        Request(
            sa, "PUT", f"/ui-api/access/users/{target}/password",
            {"password": "ein-neues-langes-Passwort"},
        ),
        Request(sb, "PUT", f"/ui-api/access/users/{target}/active", {"active": False}),
    ]


def _password_twice(s: Session) -> list[Request]:
    admins, plain = _group(s, "g", EVERYTHING), _group(s, "p", ["token.self"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    target, _secret = _person(s, "t", plain)
    return [
        Request(
            sa, "PUT", f"/ui-api/access/users/{target}/password",
            {"password": "ein-neues-langes-Passwort"},
        ),
        Request(
            sb, "PUT", f"/ui-api/access/users/{target}/password",
            {"password": "ein-anderes-langes-Passwort"},
        ),
    ]


def _profile_against_delete(s: Session) -> list[Request]:
    admins, target = _group(s, "g", EVERYTHING), _group(s, "gt", ["token.self"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    return [
        Request(
            sa, "PUT", f"/ui-api/access/groups/{target}/ui-profile", {"ui_profile": "tenant"}
        ),
        Request(sb, "DELETE", f"/ui-api/access/groups/{target}", None),
    ]


def _delete_same_group_twice(s: Session) -> list[Request]:
    admins, target = _group(s, "g", EVERYTHING), _group(s, "gt", ["token.self"])
    (_a, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    return [
        Request(sa, "DELETE", f"/ui-api/access/groups/{target}", None),
        Request(sb, "DELETE", f"/ui-api/access/groups/{target}", None),
    ]


def _own_password_against_deactivation(s: Session) -> list[Request]:
    admins, plain = _group(s, "g", EVERYTHING), _group(s, "p", ["token.self"])
    _a, sa = _person(s, "a", admins)
    target, secret = _person(s, "t", plain, OLD_PASSWORD)
    return [
        Request(
            secret, "PUT", "/ui-api/account/password",
            {"current_password": OLD_PASSWORD, "new_password": "ein-neues-langes-Passwort"},
        ),
        Request(sa, "PUT", f"/ui-api/access/users/{target}/active", {"active": False}),
    ]


def _own_token_revoked_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    _entry, second = create_session(s, s.get(User, user_id), 3600)  # type: ignore[arg-type]
    token = ApiToken(user_id=user_id, name="t", prefix="thermo_x", token_hash="h" * 64)
    s.add(token)
    s.flush()
    url = f"/ui-api/access/tokens/{token.id}/revoke"
    return [Request(first, "POST", url, None), Request(second, "POST", url, None)]


def _kiosk_token_revoked_twice(s: Session) -> list[Request]:
    admins = _group(s, "g", EVERYTHING)
    (owner, sa), (_b, sb) = _person(s, "a", admins), _person(s, "b", admins)
    token = ApiToken(
        user_id=owner, name="k", prefix="thermo_k", token_hash="k" * 64, is_kiosk=True
    )
    s.add(token)
    s.flush()
    url = f"/ui-api/access/kiosk-tokens/{token.id}/revoke"
    return [Request(sa, "POST", url, None), Request(sb, "POST", url, None)]


def _other_sessions_revoked_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    _entry, second = create_session(s, s.get(User, user_id), 3600)  # type: ignore[arg-type]
    return [
        Request(first, "POST", "/ui-api/account/sessions/revoke-others", None),
        Request(second, "POST", "/ui-api/account/sessions/revoke-others", None),
    ]


# -- Konto: Sitzungen, Abmelden, Token-Rechte, Passkey, Token ausstellen -----------------
# Zwei (oder mehr) Anfragen desselben Kontos oder eines Verwalters treffen dieselben Zeilen.
# Die Sitzungen sind „länger nicht gesehen“: dann schreibt schon das Auflösen des Cookies
# (`last_seen_at`) in die eigene Zeile -- das ist der gewöhnliche Fall eines Browsers, der
# eine Weile offen stand, und es macht jede Anfrage zum Mitspieler im Rennen um die Zeile.


def _more_sessions(s: Session, user_id: int, count: int) -> list[tuple[int, str]]:
    """`count` further sessions of the same account: (row id, cookie secret)."""
    user = s.get(User, user_id)
    assert user is not None
    result = []
    for _ in range(count):
        entry, secret = create_session(s, user, 3600)
        result.append((entry.id, secret))
    return result


def _idle(s: Session, user_id: int) -> None:
    """Every session of the account was last seen minutes ago."""
    for entry in s.scalars(select(Session_).where(Session_.user_id == user_id)):
        entry.last_seen_at = utcnow() - timedelta(minutes=5)
    s.flush()


def _own_session_id(s: Session, secret: str) -> int:
    return int(s.scalar(select(Session_.id).where(Session_.token_hash == hash_secret(secret))))


def _others_revoked_by_each(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    rest = _more_sessions(s, user_id, 3)
    _idle(s, user_id)
    return [
        Request(first, "POST", "/ui-api/account/sessions/revoke-others", None),
        *(Request(secret, "POST", "/ui-api/account/sessions/revoke-others", None)
          for _id, secret in rest),
    ]


def _one_session_revoked_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    (_i2, second), (target, _third) = _more_sessions(s, user_id, 2)
    _idle(s, user_id)
    url = f"/ui-api/account/sessions/{target}/revoke"
    return [Request(first, "POST", url, None), Request(second, "POST", url, None)]


def _one_session_revoked_against_revoke_others(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    (_i2, second), (target, _third) = _more_sessions(s, user_id, 2)
    _idle(s, user_id)
    return [
        Request(first, "POST", f"/ui-api/account/sessions/{target}/revoke", None),
        Request(second, "POST", "/ui-api/account/sessions/revoke-others", None),
    ]


def _logout_against_revoke(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    ((_i2, second),) = _more_sessions(s, user_id, 1)
    _idle(s, user_id)
    return [
        Request(first, "POST", "/ui-api/logout", None),
        Request(second, "POST", f"/ui-api/account/sessions/{_own_session_id(s, first)}/revoke", None),
    ]


def _logout_against_revoke_others(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    ((_i2, second),) = _more_sessions(s, user_id, 1)
    _idle(s, user_id)
    return [
        Request(first, "POST", "/ui-api/logout", None),
        Request(second, "POST", "/ui-api/account/sessions/revoke-others", None),
    ]


def _logout_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    _user_id, first = _person(s, "t", plain)
    _idle(s, _user_id)
    return [Request(first, "POST", "/ui-api/logout", None) for _ in range(3)]


def _own_token(s: Session, user_id: int, *, name: str = "t", prefix: str = "thermo_x") -> int:
    """A token that already carries two grants (so changing them deletes and inserts rows)."""
    token = ApiToken(user_id=user_id, name=name, prefix=prefix, token_hash="h" * 64)
    s.add(token)
    s.flush()
    for code in ("token.self", "token.manage"):
        s.add(
            ApiTokenPermission(
                api_token_id=token.id, permission_id=ensure_permission(s, code).id, zone_id=None
            )
        )
    s.flush()
    return token.id


def _token_permissions_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self", "token.manage"])
    user_id, first = _person(s, "t", plain)
    ((_i2, second),) = _more_sessions(s, user_id, 1)
    _idle(s, user_id)
    url = f"/ui-api/access/tokens/{_own_token(s, user_id)}/permissions"
    return [
        Request(first, "PUT", url, {"permissions": [{"code": "token.self"}]}),
        Request(second, "PUT", url, {"permissions": [{"code": "token.manage"}]}),
    ]


def _token_permissions_against_revoke(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self", "token.manage"])
    user_id, first = _person(s, "t", plain)
    ((_i2, second),) = _more_sessions(s, user_id, 1)
    _idle(s, user_id)
    token_id = _own_token(s, user_id)
    return [
        Request(
            first, "PUT", f"/ui-api/access/tokens/{token_id}/permissions",
            {"permissions": [{"code": "token.self"}]},
        ),
        Request(second, "POST", f"/ui-api/access/tokens/{token_id}/revoke", None),
    ]


def _passkey_deleted_twice(s: Session) -> list[Request]:
    plain = _group(s, "p", ["token.self"])
    user_id, first = _person(s, "t", plain)
    ((_i2, second),) = _more_sessions(s, user_id, 1)
    _idle(s, user_id)
    passkey = UserPasskey(
        user_id=user_id, credential_id="cred-" + "x" * 20, public_key="k", label="Schluessel"
    )
    s.add(passkey)
    s.flush()
    url = f"/ui-api/account/passkeys/{passkey.id}"
    return [Request(first, "DELETE", url, None), Request(second, "DELETE", url, None)]


def _token_issued_against_password(s: Session) -> list[Request]:
    admins, plain = _group(s, "g", EVERYTHING), _group(s, "p", ["token.self"])
    _a, sa = _person(s, "a", admins)
    target, secret = _person(s, "t", plain, OLD_PASSWORD)
    rest = _more_sessions(s, target, 2)
    _idle(s, target)
    issue: dict[str, object] = {"name": "neu", "permissions": [{"code": "token.self"}]}
    return [
        Request(secret, "POST", "/ui-api/access/tokens", issue),
        *(Request(other, "POST", "/ui-api/access/tokens", issue) for _id, other in rest),
        Request(
            sa, "PUT", f"/ui-api/access/users/{target}/password",
            {"password": "ein-neues-langes-Passwort"},
        ),
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
    "gruppe-gleicher-name-zweimal-anlegen": _create_same_group_name_twice,
    "passwort-gegen-deaktivieren": _password_against_deactivation,
    "passwort-zweimal-setzen": _password_twice,
    "profil-gegen-loeschen": _profile_against_delete,
    "dieselbe-gruppe-zweimal-loeschen": _delete_same_group_twice,
    "eigenes-passwort-gegen-deaktivieren": _own_password_against_deactivation,
    "eigenen-token-zweimal-widerrufen": _own_token_revoked_twice,
    "kiosk-token-zweimal-widerrufen": _kiosk_token_revoked_twice,
    "andere-sitzungen-zweimal-beenden": _other_sessions_revoked_twice,
    "gegenseitig-deaktivieren": _mutual_deactivation,
    "andere-sitzungen-von-vier-sitzungen-beenden": _others_revoked_by_each,
    "dieselbe-sitzung-zweimal-beenden": _one_session_revoked_twice,
    "sitzung-beenden-gegen-andere-beenden": _one_session_revoked_against_revoke_others,
    "abmelden-gegen-sitzung-beenden": _logout_against_revoke,
    "abmelden-gegen-andere-beenden": _logout_against_revoke_others,
    "dreimal-abmelden": _logout_twice,
    "token-rechte-zweimal-aendern": _token_permissions_twice,
    "token-rechte-gegen-widerruf": _token_permissions_against_revoke,
    "passkey-zweimal-loeschen": _passkey_deleted_twice,
    "token-ausstellen-gegen-passwort-setzen": _token_issued_against_password,
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
            before = (_holders(engine, UM), _holders(engine, GM))
            clients = [app_client(r.secret) for r in requests]
            barrier = threading.Barrier(len(requests))
            statuses: list[int] = [0] * len(requests)

            failures: list[str] = []

            def send(i: int, requests: list[Request] = requests,
                     clients: list[TestClient] = clients,
                     barrier: threading.Barrier = barrier,
                     statuses: list[int] = statuses,
                     failures: list[str] = failures) -> None:
                barrier.wait(timeout=20)
                mine = requests[i]
                try:
                    reply = clients[i].request(mine.method, mine.url, json=mine.body)
                except Exception as exc:  # noqa: BLE001 - an unhandled error is a 500
                    statuses[i] = 500
                    cause = getattr(exc, "orig", None)
                    failures.append(f"{type(exc).__name__}: {cause or exc}"[:200])
                    return
                statuses[i] = reply.status_code

            threads = [threading.Thread(target=send, args=(i,)) for i in range(len(requests))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
                assert not thread.is_alive(), "eine Anfrage hängt (Verklemmung?)"

            outcomes[tuple(str(s) for s in sorted(statuses))] += 1
            assert set(statuses) <= ACCEPTED, f"{name}: {sorted(statuses)} {failures}"
            # Whoever held a guarded permission before still has someone holding it.
            after = (_holders(engine, UM), _holders(engine, GM))
            assert all(a >= 1 for a, b in zip(after, before, strict=True) if b >= 1), name
            assert _duplicate_permission_rows(engine) == 0, f"{name}: doppelte Rechte-Zeilen"
            assert _usernames_twice(engine) == 0, f"{name}: Benutzername doppelt"
            _clean(engine, known_permissions)
    finally:
        _clean(engine, known_permissions)
    assert sum(outcomes.values()) == ROUNDS


@pytest.mark.parametrize("offset_ms", [-120, -40, 0, 40, 120, 250], ids=lambda ms: f"versatz-{ms}ms")
def test_a_login_with_the_old_password_leaves_no_session_when_the_password_is_set_meanwhile(
    offset_ms: int, engine: Engine, app_client: Callable[[str], TestClient]
) -> None:
    """Passwort setzen parallel zur Anmeldung: nach beiden lebt keine Sitzung des Kontos.

    Der Verwalter setzt das Passwort und beendet damit jede Sitzung. Eine Anmeldung, die das
    alte Passwort geprüft hat und ihre Sitzung erst danach anlegt, überlebte das (gemessen:
    6 von 6 Läufen bei 0 bis 400 ms Versatz). Die Anmeldung gilt nur für den Stand, gegen den
    sie geprüft wurde -- entweder sie scheitert (401), oder ihre Sitzung ist beendet.
    Negativer Versatz: die Anmeldung startet später als das Setzen.
    """
    known_permissions: set[str] = set()
    try:
        for _round in range(ROUNDS):
            with Session(engine) as setup:
                if not known_permissions:
                    known_permissions = set(setup.scalars(select(Permission.code)))
                source(setup, "web")
                admins, plain = _group(setup, "g", EVERYTHING), _group(setup, "p", ["token.self"])
                _a, admin_secret = _person(setup, "a", admins)
                target, _secret = _person(setup, "t", plain, OLD_PASSWORD)
                setup.commit()
            anonymous, admin = app_client("anonymous"), app_client(admin_secret)
            barrier = threading.Barrier(2)
            statuses: dict[str, int] = {}

            def login(
                anonymous: TestClient = anonymous, barrier: threading.Barrier = barrier,
                statuses: dict[str, int] = statuses,
            ) -> None:
                barrier.wait(timeout=20)
                time.sleep(max(0, -offset_ms) / 1000)
                reply = anonymous.post(
                    "/ui-api/login", json={"username": "t", "password": OLD_PASSWORD}
                )
                statuses["login"] = reply.status_code

            def set_password(
                admin: TestClient = admin, barrier: threading.Barrier = barrier,
                statuses: dict[str, int] = statuses, target: int = target,
            ) -> None:
                barrier.wait(timeout=20)
                time.sleep(max(0, offset_ms) / 1000)
                reply = admin.put(
                    f"/ui-api/access/users/{target}/password",
                    json={"password": "ein-neues-langes-Passwort"},
                )
                statuses["password"] = reply.status_code

            threads = [threading.Thread(target=login), threading.Thread(target=set_password)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
                assert not thread.is_alive(), "eine Anfrage hängt (Verklemmung?)"

            assert statuses["login"] in (200, 401, 409, 429), statuses
            assert statuses["password"] in (200, 409), statuses  # never a 5xx
            with Session(engine) as check:
                alive = check.scalar(
                    select(func.count()).select_from(Session_).where(
                        Session_.user_id == target,
                        Session_.revoked_at.is_(None),
                        Session_.expires_at > utcnow(),
                    )
                )
            if statuses["password"] == 200:
                assert alive == 0, f"Sitzung mit altem Passwort lebt: {statuses}"
            _clean(engine, known_permissions)
    finally:
        _clean(engine, known_permissions)


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
        for model in (
            ApiTokenPermission, ApiToken, Session_, AuditEvent, UserPasskey, UserAccessGroup, GroupPermission, AccessGroup, User
        ):
            cleanup.execute(delete(model))
        if known_permissions:
            cleanup.execute(delete(Permission).where(Permission.code.not_in(known_permissions)))
        cleanup.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        cleanup.commit()
