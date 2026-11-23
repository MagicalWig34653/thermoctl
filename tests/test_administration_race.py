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
from sqlalchemy import Engine, delete, func, select, text
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
    serialized_change,
    set_group_permissions,
    set_user_active,
    set_user_group,
)


@pytest.fixture(params=["on", "off"], ids=["snapshot-on", "snapshot-off"])
def isolation(request: pytest.FixtureRequest, engine: Engine) -> str:
    """MariaDB ab 11.6 lehnt das Überschreiben einer seit dem Snapshot geänderten Zeile ab (1020,
    `innodb_snapshot_isolation=ON`, Standard); davor und mit `OFF` überschreibt sie still. Die
    Absicherung muss in beiden Fällen halten, also läuft jedes Rennen mit beiden Einstellungen
    -- je Sitzung (`SET SESSION`), nicht global."""
    return str(request.param)


def _session(engine: Engine, isolation: str) -> Session:
    session = Session(engine)
    value = "ON" if isolation == "on" else "OFF"
    session.execute(text(f"SET SESSION innodb_snapshot_isolation={value}"))
    return session


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
    engine: Engine, change: Change, isolation: str
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

    one = _session(engine, isolation)
    two = _session(engine, isolation)
    errors: list[BaseException] = []
    refused: list[AdministrationError] = []
    try:
        # Both transactions have taken their snapshot before either commits.
        one.execute(select(User)).all()
        two.execute(select(User)).all()

        change(one, ids[0], ids[1])

        def other_side() -> None:
            try:
                with serialized_change(two):
                    change(two, ids[1], ids[0])
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

    def failing(code: int, message: str = "Konflikt") -> Callable[..., object]:
        def execute(*args: object, **kwargs: object) -> object:
            raise OperationalError("SELECT", {}, Exception(code, message))

        return execute

    for code in (1020, 1205, 1213):
        monkeypatch.setattr(session, "execute", failing(code))
        with pytest.raises(ConcurrentChange, match="erneut versuchen"):
            administration.lock_guarded_permissions(session)
        assert issubclass(ConcurrentChange, AdministrationError)

    # 1305 only counts as the aftermath of a collision when it is about the savepoint; the
    # same number for a missing function is a programming error and stays one.
    gone = failing(1305, "SAVEPOINT sa_savepoint_1 does not exist")
    monkeypatch.setattr(session, "execute", gone)
    with pytest.raises(ConcurrentChange, match="erneut versuchen"):
        administration.lock_guarded_permissions(session)
    for other in (failing(1305, "FUNCTION db.nope does not exist"), failing(1146)):
        monkeypatch.setattr(session, "execute", other)
        with pytest.raises(OperationalError):
            administration.lock_guarded_permissions(session)


def test_a_collision_leaves_the_database_error_in_the_log(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The caller gets a neutral sentence; what the database said must stay findable."""
    from sqlalchemy.exc import OperationalError

    import thermoctl.domain.administration as administration

    def execute(*args: object, **kwargs: object) -> object:
        raise OperationalError("SELECT", {}, Exception(1213, "Deadlock found when trying"))

    monkeypatch.setattr(session, "execute", execute)
    with caplog.at_level("WARNING", logger="thermoctl.domain.administration"):
        with pytest.raises(ConcurrentChange):
            administration.lock_guarded_permissions(session)

    assert any(
        "Deadlock found" in str(getattr(record, "fehler", "")) for record in caplog.records
    )


# -- Umgruppieren gegen Entzug/Löschung der Zielgruppe ----------------------------------------
#
# Eine einzige Person hält `user.manage` über die Gruppe G1. Die leere Gruppe G2 trägt das Recht
# ebenfalls. Wer sie von G1 nach G2 verschiebt, tut nichts Verbotenes -- solange G2 das Recht noch
# trägt. Entzieht ihr im selben Moment jemand das Recht (oder löscht G2), sieht jede Seite die
# andere noch nicht: die Prüfung "G2 trägt das Recht" bzw. "es gibt noch eine andere Quelle"
# liest einen alten Stand, beide werden festgeschrieben, und die Person hält danach nichts mehr.


def _group_named(session: Session, name: str) -> AccessGroup:
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == name))
    assert group is not None
    return group


def _regroup_wanderer(session: Session, wanderer: int) -> None:
    user = session.get(User, wanderer)
    assert user is not None
    set_user_group(session, user, _group_named(session, "ziel").id, actor_id=None)


def _revoke_from_target(session: Session, wanderer: int) -> None:
    entry = session.scalar(
        select(GroupPermission)
        .join(Permission, Permission.id == GroupPermission.permission_id)
        .where(
            GroupPermission.access_group_id == _group_named(session, "ziel").id,
            Permission.code == ADMIN_PERMISSION,
        )
    )
    assert entry is not None
    revoke_permission(session, entry, actor_id=None)


def _delete_target(session: Session, wanderer: int) -> None:
    delete_group(session, _group_named(session, "ziel"), actor_id=None)


def _empty_target_permissions(session: Session, wanderer: int) -> None:
    set_group_permissions(session, _group_named(session, "ziel"), set(), actor_id=None)


_TARGET_CHANGES = {
    "recht-entziehen": _revoke_from_target,
    "gruppe-loeschen": _delete_target,
    "rechte-leeren": _empty_target_permissions,
}


@pytest.mark.parametrize(
    "regroup_first", [True, False], ids=["umgruppieren-zuerst", "entzug-zuerst"]
)
@pytest.mark.parametrize("target_change", list(_TARGET_CHANGES), ids=list(_TARGET_CHANGES))
def test_regrouping_the_last_administrator_cannot_race_a_change_of_the_target_group(
    engine: Engine, target_change: str, regroup_first: bool, isolation: str
) -> None:
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite hat keine Zeilensperren "
            "und kein REPEATABLE READ, das Rennen ist dort nicht herstellbar"
        )
    change_target = _TARGET_CHANGES[target_change]
    with Session(engine) as setup:
        known_permissions = set(setup.scalars(select(Permission.code)))
        source(setup, "web")
        wanderer = user_with_permissions(setup, "wanderer", [(ADMIN_PERMISSION, None)])
        # Die Zielgruppe trägt das Recht, hat aber noch niemanden.
        target = AccessGroup(name="ziel", ui_profile="admin")
        setup.add(target)
        setup.flush()
        permission = setup.scalar(select(Permission).where(Permission.code == ADMIN_PERMISSION))
        assert permission is not None
        setup.add(
            GroupPermission(access_group_id=target.id, permission_id=permission.id, zone_id=None)
        )
        setup.commit()
        wanderer_id = wanderer.id
    assert _active_administrators(engine) == 1

    def regroup(session: Session) -> None:
        _regroup_wanderer(session, wanderer_id)

    def retarget(session: Session) -> None:
        change_target(session, wanderer_id)

    first_change, second_change = (regroup, retarget) if regroup_first else (retarget, regroup)
    one = _session(engine, isolation)
    two = _session(engine, isolation)
    errors: list[BaseException] = []
    refused: list[AdministrationError] = []
    try:
        one.execute(select(User)).all()
        two.execute(select(User)).all()

        first_change(one)

        def other_side() -> None:
            try:
                with serialized_change(two):
                    second_change(two)
            except AdministrationError as exc:
                refused.append(exc)
                two.rollback()
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread
                errors.append(exc)

        worker = threading.Thread(target=other_side)
        worker.start()
        time.sleep(1.5)
        with serialized_change(one):
            pass
        worker.join(timeout=40)
        assert not worker.is_alive(), "die zweite Änderung hängt (Verklemmung?)"
        assert not errors, errors

        assert len(refused) == 1, "beide Änderungen wurden angenommen"
        assert _active_administrators(engine) == 1, "niemand kann mehr verwalten"
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


def test_a_foreign_key_broken_by_the_other_side_is_a_concurrent_change(engine: Engine) -> None:
    """Wird die Zielgruppe zwischen Prüfung und Einfügen gelöscht (1452) -- oder lehnt
    Snapshot-Isolation das Überschreiben ab (1020) --, wird daraus `ConcurrentChange`, nicht
    ein 500."""
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip("braucht MariaDB: Fremdschlüsselfehler 1451/1452 gibt es nur dort")
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as setup:
        known_permissions = set(setup.scalars(select(Permission.code)))
        source(setup, "web")
        user = user_with_permissions(setup, "fk-nutzer", [(ADMIN_PERMISSION, None)])
        target = AccessGroup(name="fk-ziel", ui_profile="admin")
        setup.add(target)
        setup.commit()
        user_id, target_id = user.id, target.id
    session = Session(engine)
    try:
        with pytest.raises(ConcurrentChange):
            with serialized_change(session):
                session.add(UserAccessGroup(user_id=user_id, access_group_id=target_id))
                with Session(engine) as other:
                    other.execute(delete(AccessGroup).where(AccessGroup.id == target_id))
                    other.commit()
        # Ein Schlüssel, den die andere Seite gerade vergeben hat (1062: derselbe Name zweimal
        # angelegt), ist dieselbe Kollision.
        with pytest.raises(ConcurrentChange):
            with serialized_change(session):
                session.add(AccessGroup(name="doppelt-a", ui_profile="admin"))
                session.add(AccessGroup(name="doppelt-a", ui_profile="admin"))
        # Ein anderer Integritätsfehler (hier: Pflichtfeld leer, 1048) bleibt, was er ist.
        with pytest.raises(IntegrityError):
            with serialized_change(session):
                session.add(AccessGroup(name=None, ui_profile="admin"))
    finally:
        session.rollback()
        session.close()
        with Session(engine) as cleanup:
            for model in (AuditEvent, UserAccessGroup, GroupPermission, AccessGroup, User):
                cleanup.execute(delete(model))
            cleanup.execute(delete(Permission).where(Permission.code.not_in(known_permissions)))
            cleanup.commit()


def test_two_regroupings_of_the_same_person_leave_exactly_one_group(
    engine: Engine, isolation: str
) -> None:
    """Zwei gleichzeitige Umgruppierungen derselben Person (G1 -> G2 und G1 -> G3): die zweite
    liest nach dem Warten die Mitgliedschaften, die die erste hinterlassen hat -- nicht ihren
    alten Stand. Sonst gehört die Person danach zu G2 *und* G3 (die Oberfläche kennt nur eine
    Gruppe je Person)."""
    if engine.dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite hat keine Zeilensperren "
            "und kein REPEATABLE READ, das Rennen ist dort nicht herstellbar"
        )
    with Session(engine) as setup:
        known_permissions = set(setup.scalars(select(Permission.code)))
        source(setup, "web")
        # Zwei Verwalter, damit die Umgruppierung selbst nicht am Alleinverwalter-Schutz hängt.
        user_with_permissions(setup, "verwalter", [(ADMIN_PERMISSION, None)])
        wanderer = user_with_permissions(setup, "gruppenwechsler", [("zone.read", None)])
        for name in ("ziel-zwei", "ziel-drei"):
            setup.add(AccessGroup(name=name, ui_profile="admin"))
        setup.commit()
        wanderer_id = wanderer.id
        target_two = _group_named(setup, "ziel-zwei").id
        target_three = _group_named(setup, "ziel-drei").id

    def move(session: Session, group_id: int) -> None:
        user = session.get(User, wanderer_id)
        assert user is not None
        set_user_group(session, user, group_id, actor_id=None)

    one = _session(engine, isolation)
    two = _session(engine, isolation)
    errors: list[BaseException] = []
    try:
        one.execute(select(User)).all()
        two.execute(select(User)).all()
        move(one, target_two)

        def other_side() -> None:
            try:
                with serialized_change(two):
                    move(two, target_three)
            except AdministrationError:
                two.rollback()
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread
                errors.append(exc)

        worker = threading.Thread(target=other_side)
        worker.start()
        time.sleep(1.5)
        with serialized_change(one):
            pass
        worker.join(timeout=40)
        assert not worker.is_alive(), "die zweite Änderung hängt (Verklemmung?)"
        assert not errors, errors

        with Session(engine) as check:
            groups = list(
                check.scalars(
                    select(UserAccessGroup.access_group_id).where(
                        UserAccessGroup.user_id == wanderer_id
                    )
                )
            )
        assert len(groups) == 1, groups
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
