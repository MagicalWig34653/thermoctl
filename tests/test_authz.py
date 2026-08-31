import pytest
from sqlalchemy.orm import Session

from tests.helpers import (
    create_zone,
    token_with_permissions,
    user_with_permissions,
)
from thermoctl.auth.tokens import issue_token, resolve_token
from thermoctl.domain.authz import (
    Forbidden,
    has_permission,
    principal_for_token,
    principal_for_user,
    require,
    visible_zones,
)


def test_an_installation_wide_permission_applies_to_every_zone(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    user = user_with_permissions(session, "a", [("zone.read", None)])
    p = principal_for_user(session, user)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is True


def test_a_zone_scoped_permission_applies_only_there(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    user = user_with_permissions(session, "b", [("zone.read", bad.id)])
    p = principal_for_user(session, user)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is False


def test_a_zone_scoped_permission_does_not_apply_installation_wide(session: Session) -> None:
    """Someone allowed only in the bathroom is not allowed 'everywhere'."""
    bad = create_zone(session, "bad")
    user = user_with_permissions(session, "c", [("zone.read", bad.id)])
    p = principal_for_user(session, user)
    assert has_permission(p, "zone.read", None) is False


def test_visible_zones_returns_only_permitted_zones(session: Session) -> None:
    bad = create_zone(session, "bad")
    create_zone(session, "kueche")
    user = user_with_permissions(session, "d", [("zone.read", bad.id)])
    visible = visible_zones(session, principal_for_user(session, user), "zone.read")
    assert [z.name for z in visible] == ["bad"]


def test_visible_zones_returns_all_zones_with_an_installation_wide_permission(
    session: Session,
) -> None:
    create_zone(session, "bad")
    create_zone(session, "kueche")
    user = user_with_permissions(session, "e", [("zone.read", None)])
    visible = visible_zones(session, principal_for_user(session, user), "zone.read")
    assert {z.name for z in visible} == {"bad", "kueche"}


def test_visible_zones_is_empty_without_a_permission(session: Session) -> None:
    create_zone(session, "bad")
    user = user_with_permissions(session, "f", [])
    assert visible_zones(session, principal_for_user(session, user), "zone.read") == []


def test_require_raises_without_a_permission(session: Session) -> None:
    bad = create_zone(session, "bad")
    user = user_with_permissions(session, "g", [])
    p = principal_for_user(session, user)
    with pytest.raises(Forbidden):
        require(p, "zone.read", bad.id)


def test_permissions_from_multiple_groups_are_combined(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    user = user_with_permissions(
        session, "h", [("zone.read", bad.id)], second_group=[("zone.read", kueche.id)]
    )
    p = principal_for_user(session, user)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is True


def test_a_token_may_not_have_more_than_its_owner(session: Session) -> None:
    """If the owner loses a permission, the token loses it too when checked."""
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    user = user_with_permissions(session, "i", [("zone.read", bad.id)])
    token = token_with_permissions(session, user, [("zone.read", bad.id),
                                                ("zone.read", kueche.id)])
    p = principal_for_token(session, token)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is False


def test_a_token_can_have_less_than_its_owner(session: Session) -> None:
    bad = create_zone(session, "bad")
    user = user_with_permissions(session, "j", [("zone.read", None),
                                                 ("zone.manage", None)])
    token = token_with_permissions(session, user, [("zone.read", None)])
    p = principal_for_token(session, token)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.manage", bad.id) is False


def test_an_inactive_user_has_no_permissions(session: Session) -> None:
    bad = create_zone(session, "bad")
    user = user_with_permissions(session, "k", [("zone.read", None)])
    user.is_active = False
    session.flush()
    p = principal_for_user(session, user)
    assert has_permission(p, "zone.read", bad.id) is False


def test_a_revoked_token_has_no_permissions(session: Session) -> None:
    from thermoctl.db.base import utcnow

    bad = create_zone(session, "bad")
    user = user_with_permissions(session, "l", [("zone.read", None)])
    token = token_with_permissions(session, user, [("zone.read", None)])
    token.revoked_at = utcnow()
    session.flush()
    assert has_permission(principal_for_token(session, token), "zone.read", bad.id) is False


def test_every_permission_belongs_to_exactly_one_area() -> None:
    """The interface shows permissions grouped by area. A new permission belonging to
    no area would be invisible there -- it could then only be granted through the
    database, and no one would notice it was missing."""
    from thermoctl.db.models.lookup import PERMISSIONS
    from thermoctl.domain.authz import PERMISSION_AREAS

    categorized = [code for _name, _hint, codes in PERMISSION_AREAS for code in codes]
    all_permissions = [code for code, _description, _zone_scoped in PERMISSIONS]

    assert sorted(categorized) == sorted(all_permissions), (
        "Not categorized: " + ", ".join(sorted(set(all_permissions) - set(categorized)))
        + " | unknown: " + ", ".join(sorted(set(categorized) - set(all_permissions)))
    )
    assert len(categorized) == len(set(categorized)), "A permission appears in two areas"


def test_a_token_whose_owner_is_gone_carries_no_permissions(session: Session) -> None:
    """A token is never more than its owner -- and an owner who no longer exists has
    nothing to lend.

    The scope is intersected with the owner's on every request precisely so that
    losing a permission takes it away from the tokens too. A deleted account is the
    extreme case of that, and it must end in an empty set rather than in the token's
    own stored scope, which would otherwise outlive the person it belonged to.
    """
    owner = user_with_permissions(session, "verschwundener", [("zone.read", None)])
    _token, plaintext = issue_token(session, owner, "sein-token", [("zone.read", None)], None)
    session.flush()
    token = resolve_token(session, plaintext)
    assert token is not None

    session.delete(owner)
    session.flush()

    principal = principal_for_token(session, token)
    assert principal.grants == frozenset()
    assert not has_permission(principal, "zone.read", None)
