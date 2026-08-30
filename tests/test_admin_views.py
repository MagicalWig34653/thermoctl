import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_zone, source, user_with_permissions
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.domain.authz import Forbidden


def test_token_klartext_erscheint_genau_einmal(session: Session) -> None:
    nutzer = user_with_permissions(session, "a", [("zone.read", None), ("token.self", None)])
    token, plaintext = token_ausstellen(session, nutzer, "HA", [("zone.read", None)], None)
    assert plaintext.startswith("tctl_")
    assert plaintext not in (token.token_hash, token.prefix)


def test_a_token_with_more_permissions_than_its_owner_is_refused(session: Session) -> None:
    nutzer = user_with_permissions(session, "b", [("zone.read", None), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Zuviel", [("zone.manage", None)], None)


def test_a_token_for_a_foreign_zone_is_refused(session: Session) -> None:
    bad = create_zone(session, "bad")
    kueche = create_zone(session, "kueche")
    nutzer = user_with_permissions(session, "c", [("zone.read", bad.id), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Fremd", [("zone.read", kueche.id)], None)


def test_the_user_list_needs_user_manage(client_als) -> None:
    ohne = client_als([("zone.read", None)])
    assert ohne.get("/users").status_code == 403
    mit = client_als([("user.manage", None)])
    assert mit.get("/users").status_code == 200


def test_the_password_hash_appears_in_no_view(client_als) -> None:
    response = client_als([("user.manage", None)]).get("/users")
    assert "$argon2id$" not in response.text


@pytest.fixture(autouse=True)
def _actor_source(session: Session) -> None:
    """Die Quelle `web` legt in Produktion die Referenzdatenmigration an.

    `Base.metadata.create_all()` in der Fixture `engine` legt nur das Schema an, keine
    Referenzdaten — ohne diese Zeile scheitert jeder Audit-Eintrag der aendernden
    Ansichten an der NOT-NULL-Bedingung auf `audit_event.source_id`.
    """
    source(session, "web")


def _with_csrf(client, session):  # type: ignore[no-untyped-def]
    """Kopfzeile mit gueltigem CSRF-Token fuer aendernde Anfragen."""
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME
    from thermoctl.config import get_settings

    geheimnis = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(geheimnis, get_settings().secret_key.get_secret_value())}


def test_creating_a_user_through_the_interface(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "neuling", "display_name": "Neuling",
              "password": "passwort-lang-genug", "group_id": ""},
        headers=_with_csrf(c, session),
        follow_redirects=False,
    )
    assert response.status_code == 303
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    assert session.scalar(select(User).where(User.username == "neuling")) is not None


def test_a_password_that_is_too_short_returns_to_the_form(client_als, session: Session) -> None:
    """Kein 500, keine leere Maske — und der Benutzername bleibt stehen."""
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "kurzpass", "display_name": "Kurz", "password": "kurz",
              "group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "mindestens 12 Zeichen" in response.text
    assert "kurzpass" in response.text
    assert "kurz\"" not in response.text.replace('value="kurzpass"', "")


def test_a_password_never_appears_in_the_response(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users",
        data={"username": "", "display_name": "X", "password": "ein-auffaelliges-geheimnis",
              "group_id": ""},
        headers=_with_csrf(c, session),
    )
    assert "ein-auffaelliges-geheimnis" not in response.text


def test_the_last_administrator_cannot_deactivate_themselves(
    client_als, session: Session
) -> None:
    """Die Aussperrsperre wirkt auch ueber die Oberflaeche, nicht nur in der Domaene."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/active", data={"active": "nein"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "letzte aktive Benutzer" in response.text
    assert ich.is_active is True


def test_an_unknown_user_yields_404(client_als, session: Session) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/active", data={"active": "ja"}, headers=_with_csrf(c, session)
    )
    assert response.status_code == 404


def test_creating_a_group_and_granting_a_permission(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.helpers import ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    ensure_permission(session, "zone.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    assert c.post(
        "/groups", data={"name": "Gaeste", "description": "Nur schauen"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Gaeste"))
    assert group is not None

    # Der Endpunkt nimmt den ganzen gewuenschten Stand entgegen, nicht ein einzelnes
    # Recht: `recht=<code>` fuer die ganze Anlage, `recht=<code>:<zone>` fuer eine Zone.
    assert c.post(
        f"/groups/{group.id}/permissions", data={"permission": ["zone.read"]},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    ) is not None


def test_an_installation_wide_permission_on_one_zone_is_refused_in_the_view(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from tests.helpers import create_zone, ensure_permission
    from thermoctl.db.models.identity import AccessGroup

    ensure_permission(session, "user.manage", zone_scoped=False)
    zone = create_zone(session, "bad-fuer-rechte")
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Falsch", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Falsch"))
    assert group is not None
    response = c.post(
        f"/groups/{group.id}/permissions", data={"permission": [f"user.manage:{zone.id}"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "ganze Anlage" in response.text


def test_issuing_a_token_shows_the_plaintext_exactly_once(client_als, session: Session) -> None:
    c = client_als([("token.self", None), ("zone.read", None)])
    response = c.post(
        "/tokens", data={"name": "Anzeigetafel", "code": "zone.read", "gueltig_tage": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "tctl_" in response.text
    # Beim naechsten Aufruf der Seite ist er weg — gespeichert wird nur der Hash.
    assert "tctl_" not in c.get("/tokens").text


def test_a_token_without_a_name_is_refused(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    response = c.post(
        "/tokens", data={"name": "  ", "code": "", "gueltig_tage": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "braucht einen Namen" in response.text


def test_a_token_with_too_many_permissions_is_refused_understandably(
    client_als, session: Session
) -> None:
    from tests.helpers import ensure_permission

    ensure_permission(session, "zone.manage", zone_scoped=True)
    c = client_als([("token.self", None)])
    response = c.post(
        "/tokens", data={"name": "Zuviel", "code": "zone.manage", "gueltig_tage": ""},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "kann kein Token" in response.text


def test_a_foreign_token_cannot_be_found(client_als, session: Session) -> None:
    """404 statt 403 — sonst verriete die Antwort, welche Kennungen es gibt."""
    from tests.helpers import token_with_permissions, user_with_permissions

    fremder = user_with_permissions(session, "fremder", [("token.self", None)])
    fremdes = token_with_permissions(session, fremder, [])
    c = client_als([("token.self", None)])
    response = c.post(
        f"/tokens/{fremdes.id}/revoke", headers=_with_csrf(c, session)
    )
    assert response.status_code == 404
    assert fremdes.revoked_at is None


def test_your_own_token_can_be_revoked(client_als, session: Session) -> None:
    c = client_als([("token.self", None)])
    c.post("/tokens", data={"name": "Weg damit", "code": "", "gueltig_tage": "30"},
           headers=_with_csrf(c, session))
    from sqlalchemy import select

    from thermoctl.db.models.credential import ApiToken

    token = session.scalar(select(ApiToken).where(ApiToken.name == "Weg damit"))
    assert token is not None
    assert c.post(
        f"/tokens/{token.id}/revoke", headers=_with_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert token.revoked_at is not None


def test_changing_your_own_password_without_user_manage(client_als, session: Session) -> None:
    """Wer nur sich selbst betrifft, braucht kein Verwaltungsrecht — sonst koennte
    niemand sein eigenes Passwort wechseln, ohne Verwalter zu sein."""
    from sqlalchemy import select

    from thermoctl.auth.passwords import verify_password
    from thermoctl.db.models.identity import User

    c = client_als([("zone.read", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/password", data={"password": "mein-neues-langes-passwort"},
        headers=_with_csrf(c, session), follow_redirects=False,
    )
    assert response.status_code == 303
    assert verify_password("mein-neues-langes-passwort", ich.password_hash)


def test_changing_someone_elses_password_needs_user_manage(client_als, session: Session) -> None:
    from tests.helpers import create_user

    fremder = create_user(session, "fremdes-passwort")
    c = client_als([("zone.read", None)])
    response = c.post(
        f"/users/{fremder.id}/password", data={"password": "egal-was-hier-steht"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 403


def test_deleting_a_group_through_the_interface(client_als, session: Session) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Entbehrlich", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Entbehrlich"))
    assert group is not None
    assert c.post(
        f"/groups/{group.id}/delete", headers=_with_csrf(c, session),
        follow_redirects=False,
    ).status_code == 303
    assert session.get(AccessGroup, group.id) is None


def test_revoking_a_permission_through_the_interface(client_als, session: Session) -> None:
    from sqlalchemy import select

    from tests.helpers import ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission

    ensure_permission(session, "device.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Rechteweg", "description": ""},
           headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Rechteweg"))
    assert group is not None
    c.post(f"/groups/{group.id}/permissions", data={"permission": ["device.read"]},
           headers=_with_csrf(c, session))
    entry = session.scalar(
        select(GroupPermission).where(GroupPermission.access_group_id == group.id)
    )
    assert entry is not None
    # Entzogen wird durch Weglassen: Das Formular schickt den ganzen gewuenschten Stand,
    # und was nicht darin steht, faellt weg. Ein leeres Formular nimmt der Gruppe alles.
    assert c.post(
        f"/groups/{group.id}/permissions", data={},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert session.get(GroupPermission, entry.id) is None


def test_permissions_of_an_unknown_group_yield_404(client_als, session: Session) -> None:
    """Frueher stand die Kennung des Rechteintrags im Pfad und der Test pruefte, dass ein
    Eintrag einer fremden Gruppe nicht entzogen werden kann. Den Pfad gibt es nicht mehr
    -- der Sammel-Endpunkt kennt nur die Gruppe, und die muss es geben."""
    from tests.helpers import ensure_permission

    ensure_permission(session, "device.read", zone_scoped=True)
    c = client_als([("group.manage", None)])
    response = c.post(
        "/groups/999999/permissions", data={"permission": ["device.read"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 404


def test_an_unknown_group_yields_404(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    assert c.post("/groups/999999/delete", headers=_with_csrf(c, session)).status_code == 404
    assert c.post(
        "/groups/999999/permissions", data={"code": "zone.read", "zone_id": ""},
        headers=_with_csrf(c, session),
    ).status_code == 404


def test_a_duplicate_group_name_stays_in_the_form(client_als, session: Session) -> None:
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Zweimal", "description": ""},
           headers=_with_csrf(c, session))
    response = c.post("/groups", data={"name": "Zweimal", "description": ""},
                     headers=_with_csrf(c, session))
    assert response.status_code == 200
    assert "gibt es bereits" in response.text


def test_the_group_holding_the_last_admin_permission_stays(
    client_als, session: Session
) -> None:
    """Auch ueber die Oberflaeche laesst sich die letzte Quelle nicht entfernen."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup

    c = client_als([("group.manage", None), ("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert group is not None
    response = c.post(f"/groups/{group.id}/delete", headers=_with_csrf(c, session))
    assert response.status_code == 200
    assert "einzige verbliebene" in response.text
    assert session.get(AccessGroup, group.id) is not None


def test_a_too_short_password_on_your_own_change_stays_in_the_form(
    client_als, session: Session
) -> None:
    from sqlalchemy import select

    from thermoctl.db.models.identity import User

    c = client_als([("user.manage", None)])
    ich = session.scalar(select(User).where(User.username.like("web-%")).order_by(User.id.desc()))
    assert ich is not None
    response = c.post(
        f"/users/{ich.id}/password", data={"password": "kurz"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "mindestens 12 Zeichen" in response.text


def test_deactivating_and_reactivating_a_user(client_als, session: Session) -> None:
    from tests.helpers import create_user

    anderer = create_user(session, "kommt-und-geht")
    c = client_als([("user.manage", None)])
    assert c.post(
        f"/users/{anderer.id}/active", data={"active": "nein"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is False
    assert c.post(
        f"/users/{anderer.id}/active", data={"active": "ja"},
        headers=_with_csrf(c, session), follow_redirects=False,
    ).status_code == 303
    assert anderer.is_active is True


def test_a_password_change_for_an_unknown_user_yields_404(
    client_als, session: Session
) -> None:
    c = client_als([("user.manage", None)])
    response = c.post(
        "/users/999999/password", data={"password": "ein-langes-passwort-hier"},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 404


def test_the_last_admin_permission_cannot_be_revoked(
    client_als, session: Session
) -> None:
    """Der zweite Weg in dieselbe Sackgasse: nicht die Gruppe loeschen, sondern ihr das
    Recht nehmen. Beide muessen gesperrt sein, sonst ist die Sperre umgehbar."""
    from sqlalchemy import select

    from thermoctl.db.models.identity import AccessGroup, GroupPermission
    from thermoctl.db.models.lookup import Permission

    c = client_als([("group.manage", None), ("user.manage", None)])
    group = session.scalar(
        select(AccessGroup).where(AccessGroup.name.like("gruppe-web-%")).order_by(
            AccessGroup.id.desc()
        )
    )
    assert group is not None
    permission_id = session.scalar(select(Permission.id).where(Permission.code == "user.manage"))
    entry = session.scalar(
        select(GroupPermission).where(
            GroupPermission.access_group_id == group.id,
            GroupPermission.permission_id == permission_id,
        )
    )
    assert entry is not None
    # Weglassen ist der neue Weg zu entziehen -- die Sperre muss auch dort greifen.
    response = c.post(
        f"/groups/{group.id}/permissions", data={"permission": ["group.manage"]},
        headers=_with_csrf(c, session),
    )
    assert response.status_code == 200
    assert "einzige verbliebene" in response.text
    assert session.get(GroupPermission, entry.id) is not None


def test_setting_permissions_grants_and_revokes_in_one_step(
    client_als, session: Session
) -> None:
    """Der eigentliche Gewinn des Sammel-Endpunkts: Eine Gruppe umzustellen ist ein
    Vorgang, nicht eine Folge von Einzelschritten, zwischen denen sie halb eingerichtet
    dasteht."""
    from sqlalchemy import select

    from tests.helpers import create_zone, ensure_permission
    from thermoctl.db.models.identity import AccessGroup, GroupPermission
    from thermoctl.db.models.lookup import Permission

    ensure_permission(session, "zone.read", zone_scoped=True)
    ensure_permission(session, "device.read", zone_scoped=True)
    bad = create_zone(session, "bad-umstellen")
    c = client_als([("group.manage", None)])
    c.post("/groups", data={"name": "Umbau", "description": ""}, headers=_with_csrf(c, session))
    group = session.scalar(select(AccessGroup).where(AccessGroup.name == "Umbau"))
    assert group is not None

    c.post(f"/groups/{group.id}/permissions", data={"permission": ["zone.read"]},
           headers=_with_csrf(c, session))
    c.post(
        f"/groups/{group.id}/permissions",
        data={"permission": [f"zone.read:{bad.id}", "device.read"]},
        headers=_with_csrf(c, session),
    )

    state = {
        (code, zone_id)
        for code, zone_id in session.execute(
            select(Permission.code, GroupPermission.zone_id)
            .join(Permission, Permission.id == GroupPermission.permission_id)
            .where(GroupPermission.access_group_id == group.id)
        )
    }
    assert state == {("zone.read", bad.id), ("device.read", None)}


def test_the_group_page_shows_permissions_by_area_in_plain_words(
    client_als, session: Session
) -> None:
    """Vorher stand dort eine flache Liste aus Codes. Der Code bleibt sichtbar -- er
    steht in Fehlermeldungen und in der Dokumentation --, aber er ist nicht mehr das
    Einzige, was dasteht."""
    from tests.helpers import create_all_permissions
    from thermoctl.domain.authz import PERMISSION_AREAS

    create_all_permissions(session)
    page = client_als([("group.manage", None)]).get("/groups")
    assert page.status_code == 200
    for name, _hint, _codes in PERMISSION_AREAS:
        assert name in page.text, f"Bereich '{name}' fehlt auf der Seite"
    assert "Zonen und ihren Zustand sehen" in page.text
    assert "zone.read" in page.text
