"""Zwei gleichzeitige Änderungen an den Verwaltungsrechten sperren niemanden aus (Codex-Befund 4).

Zwei Verwaltende ändern im selben Moment je den anderen (deaktivieren, umgruppieren, Recht
entziehen, Gruppe löschen). Jede Prüfung „bleibt noch jemand übrig?" sieht den anderen noch an
seinem Platz, beide Änderungen werden festgeschrieben, und niemand kann mehr verwalten -- zu
retten nur noch über die Datenbank.

Das lässt sich nur mit echten, gleichzeitigen Transaktionen zeigen. SQLite kennt weder
Zeilensperren noch diese Isolationsstufe (ein Schreiber nach dem anderen): dort ist nichts
abzusichern, der Test wird übersprungen und sagt das.
"""

import threading
import time
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from tests.helpers import source, user_with_permissions
from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.administration import (
    ADMIN_PERMISSION,
    AdministrationError,
    ConcurrentChange,
    delete_group,
    revoke_permission,
    set_user_active,
    set_user_group,
)

Change = Callable[[Session, int, int], None]
"""(sitzung, eigene Nummer, die des anderen) -> führt die Änderung am anderen aus."""


def _deactivate(session: Session, mine: int, other: int) -> None:
    user = session.get(User, other)
    assert user is not None
    set_user_active(session, user, False, actor_id=mine)


def _regroup(session: Session, mine: int, other: int) -> None:
    user = session.get(User, other)
    assert user is not None
    set_user_group(session, user, None, actor_id=mine)


def _revoke(session: Session, mine: int, other: int) -> None:
    entry = session.scalar(
        select(GroupPermission)
        .join(UserAccessGroup, UserAccessGroup.access_group_id == GroupPermission.access_group_id)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(UserAccessGroup.user_id == other, Permission.code == ADMIN_PERMISSION)
    )
    assert entry is not None
    revoke_permission(session, entry, actor_id=mine)


def _delete_group(session: Session, mine: int, other: int) -> None:
    group = session.scalar(
        select(AccessGroup)
        .join(UserAccessGroup, UserAccessGroup.access_group_id == AccessGroup.id)
        .where(UserAccessGroup.user_id == other)
    )
    assert group is not None
    delete_group(session, group, actor_id=mine)


def _active_administrators(engine: Engine) -> int:
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
                    Permission.code == ADMIN_PERMISSION,
                    GroupPermission.zone_id.is_(None),
                    User.is_active.is_(True),
                )
            )
            or 0
        )


@pytest.mark.parametrize(
    "change",
    [_deactivate, _regroup, _revoke, _delete_group],
    ids=["deaktivieren", "umgruppieren", "recht-entziehen", "gruppe-loeschen"],
)
def test_two_administrators_cannot_lock_each_other_out_at_the_same_time(
    engine: Engine, change: Change
) -> None:
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite hat keine Zeilensperren "
            "und kein REPEATABLE READ, das Rennen ist dort nicht herstellbar"
        )
    with Session(engine) as setup:
        known_permissions = set(setup.scalars(select(Permission.code)))
        source(setup, "web")
        first = user_with_permissions(setup, "rennen-a", [(ADMIN_PERMISSION, None)])
        second = user_with_permissions(setup, "rennen-b", [(ADMIN_PERMISSION, None)])
        setup.commit()
        ids = (first.id, second.id)
    assert _active_administrators(engine) == 2

    one = Session(engine)
    two = Session(engine)
    errors: list[BaseException] = []
    refused: list[AdministrationError] = []
    try:
        # Both transactions have taken their snapshot before either commits.
        one.execute(select(User)).all()
        two.execute(select(User)).all()

        change(one, ids[0], ids[1])

        def other_side() -> None:
            try:
                change(two, ids[1], ids[0])
                two.commit()
            except AdministrationError as exc:
                refused.append(exc)
                two.rollback()
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread
                errors.append(exc)

        worker = threading.Thread(target=other_side)
        worker.start()
        # With the lock the second change waits for the first one's commit; without it
        # it has long finished. Either way the first commits now.
        time.sleep(1.5)
        one.commit()
        worker.join(timeout=40)
        assert not worker.is_alive(), "die zweite Änderung hängt (Verklemmung?)"
        assert not errors, errors

        assert len(refused) == 1, "beide Änderungen wurden angenommen"
        assert _active_administrators(engine) == 1
        # The loser is told so, in words -- either "last one" or "somebody else was faster".
        assert isinstance(refused[0], AdministrationError)
        assert str(refused[0])
    finally:
        one.rollback()
        two.rollback()
        one.close()
        two.close()
        with Session(engine) as cleanup:
            for model in (AuditEvent, UserAccessGroup, GroupPermission, AccessGroup, User):
                cleanup.execute(delete(model))
            cleanup.execute(delete(Permission).where(Permission.code.not_in(known_permissions)))
            cleanup.commit()


def test_a_collision_is_reported_as_a_concurrent_change_and_nothing_else_is_swallowed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MariaDB's 1020 (snapshot isolation), 1205 and 1213 become `ConcurrentChange`; any other
    database error stays what it is."""
    from sqlalchemy.exc import OperationalError

    import thermoctl.domain.administration as administration

    def failing(code: int) -> Callable[..., object]:
        def execute(*args: object, **kwargs: object) -> object:
            raise OperationalError("SELECT", {}, Exception(code, "Konflikt"))

        return execute

    for code in (1020, 1205, 1213):
        monkeypatch.setattr(session, "execute", failing(code))
        with pytest.raises(ConcurrentChange, match="erneut versuchen"):
            administration.lock_guarded_permissions(session)
        assert issubclass(ConcurrentChange, AdministrationError)

    monkeypatch.setattr(session, "execute", failing(1146))
    with pytest.raises(OperationalError):
        administration.lock_guarded_permissions(session)
