import pytest
from sqlalchemy.orm import Session

from tests.helpers import (
    create_zone,
    token_with_permissions,
    user_with_permissions,
)
from thermoctl.domain.authz import (
    Forbidden,
    has_permission,
    principal_for_token,
    principal_for_user,
    require,
    visible_zones,
)


def test_anlagenweites_recht_gilt_fuer_jede_zone(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "a", [("zone.read", None)])
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is True


def test_zonenbezogenes_recht_gilt_nur_dort(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "b", [("zone.read", bad.id)])
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is False


def test_zonenbezogenes_recht_gilt_nicht_anlagenweit(session: Session) -> None:
    """Wer nur das Bad darf, darf nicht 'ueberall'."""
    bad = create_zone(session, "bad")
    nutzer = user_with_permissions(session, "c", [("zone.read", bad.id)])
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "zone.read", None) is False


def test_visible_zones_liefert_nur_erlaubte(session: Session) -> None:
    bad = create_zone(session, "bad")
    create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "d", [("zone.read", bad.id)])
    sichtbar = visible_zones(session, principal_for_user(session, nutzer), "zone.read")
    assert [z.name for z in sichtbar] == ["bad"]


def test_visible_zones_liefert_bei_anlagenweitem_recht_alle(session: Session) -> None:
    create_zone(session, "bad")
    create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "e", [("zone.read", None)])
    sichtbar = visible_zones(session, principal_for_user(session, nutzer), "zone.read")
    assert {z.name for z in sichtbar} == {"bad", "kueche"}


def test_visible_zones_ist_leer_ohne_recht(session: Session) -> None:
    create_zone(session, "bad")
    nutzer = user_with_permissions(session, "f", [])
    assert visible_zones(session, principal_for_user(session, nutzer), "zone.read") == []


def test_require_wirft_ohne_recht(session: Session) -> None:
    bad = create_zone(session, "bad")
    nutzer = user_with_permissions(session, "g", [])
    p = principal_for_user(session, nutzer)
    with pytest.raises(Forbidden):
        require(p, "zone.read", bad.id)


def test_rechte_mehrerer_gruppen_werden_vereinigt(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    nutzer = user_with_permissions(
        session, "h", [("zone.read", bad.id)], second_group=[("zone.read", kueche.id)]
    )
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is True


def test_token_darf_nicht_mehr_als_sein_besitzer(session: Session) -> None:
    """Verliert der Besitzer ein Recht, verliert es das Token bei der Pruefung ebenfalls."""
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "i", [("zone.read", bad.id)])
    token = token_with_permissions(session, nutzer, [("zone.read", bad.id),
                                                ("zone.read", kueche.id)])
    p = principal_for_token(session, token)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.read", kueche.id) is False


def test_token_kann_weniger_als_sein_besitzer(session: Session) -> None:
    bad = create_zone(session, "bad")
    nutzer = user_with_permissions(session, "j", [("zone.read", None),
                                                 ("zone.manage", None)])
    token = token_with_permissions(session, nutzer, [("zone.read", None)])
    p = principal_for_token(session, token)
    assert has_permission(p, "zone.read", bad.id) is True
    assert has_permission(p, "zone.manage", bad.id) is False


def test_inaktiver_benutzer_hat_keine_rechte(session: Session) -> None:
    bad = create_zone(session, "bad")
    nutzer = user_with_permissions(session, "k", [("zone.read", None)])
    nutzer.is_active = False
    session.flush()
    p = principal_for_user(session, nutzer)
    assert has_permission(p, "zone.read", bad.id) is False


def test_widerrufenes_token_hat_keine_rechte(session: Session) -> None:
    from thermoctl.db.base import utcnow

    bad = create_zone(session, "bad")
    nutzer = user_with_permissions(session, "l", [("zone.read", None)])
    token = token_with_permissions(session, nutzer, [("zone.read", None)])
    token.revoked_at = utcnow()
    session.flush()
    assert has_permission(principal_for_token(session, token), "zone.read", bad.id) is False


def test_jedes_recht_liegt_in_genau_einem_bereich() -> None:
    """Die Oberflaeche zeigt Rechte nach Bereichen sortiert. Ein neues Recht, das in
    keinem Bereich steht, waere dort unsichtbar -- vergeben liesse es sich dann nur
    ueber die Datenbank, und niemandem faellt auf, dass es fehlt."""
    from thermoctl.db.models.lookup import PERMISSIONS
    from thermoctl.domain.authz import PERMISSION_AREAS

    einsortiert = [code for _name, _hint, codes in PERMISSION_AREAS for code in codes]
    alle = [code for code, _beschreibung, _zone_scoped in PERMISSIONS]

    assert sorted(einsortiert) == sorted(alle), (
        "Nicht einsortiert: " + ", ".join(sorted(set(alle) - set(einsortiert)))
        + " | unbekannt: " + ", ".join(sorted(set(einsortiert) - set(alle)))
    )
    assert len(einsortiert) == len(set(einsortiert)), "Ein Recht steht in zwei Bereichen"
