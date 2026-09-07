"""Das UI-Profil: welche Oberfläche jemand bekommt -- und was es ausdrücklich nicht tut.

Der Kern dieser Datei ist die Trennung. Ein Profil darf keine Berechtigung ersetzen,
und eine Berechtigung darf kein Profil setzen. Beide Richtungen stehen hier als
eigener Test, weil beide Verwechslungen naheliegen und beide teuer wären: die eine
öffnet einem Mieter die Anlage, die andere nimmt einer bestehenden Installation beim
Upgrade den Zugang.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, user_with_permissions
from thermoctl.db.models.identity import AccessGroup, UserAccessGroup
from thermoctl.domain.administration import AdministrationError, set_group_ui_profile
from thermoctl.domain.authz import principal_for_user
from thermoctl.domain.ui_profile import (
    DEFAULT_PROFILE,
    WebUiProfile,
    combined_profile,
    parse_profile,
)
from thermoctl.web.navigation import visible_navigation

Client = Callable[[list[tuple[str, int | None]]], TestClient]


def _with_csrf(client: TestClient) -> dict[str, str]:
    """Kopfzeile mit gültigem CSRF-Token -- dieselbe Hilfe wie in
    `tests/test_admin_views.py`; ändernde Anfragen werden sonst als veraltete Seite
    abgewiesen."""
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME
    from thermoctl.config import get_settings

    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


@pytest.fixture(autouse=True)
def _audit_source(session: Session) -> None:
    """Die Quelle 'web' -- ohne sie scheitert jeder Audit-Eintrag der ändernden
    Ansichten an der NOT-NULL-Bedingung auf `audit_event.source_id`."""
    from tests.helpers import source

    source(session, "web")


# -- Das Modell ---------------------------------------------------------------


def test_a_group_without_an_explicit_choice_is_an_admin_group(session: Session) -> None:
    """Der Vorgabewert der Spalte, nicht nur der Migration.

    Eine Gruppe, die über das Modell angelegt wird -- beim Einrichten, in einem Test,
    in einer künftigen Importfunktion -- muss denselben Wert bekommen wie eine Gruppe,
    die die Migration vorfindet. Sonst hinge es davon ab, auf welchem Weg sie
    entstanden ist.
    """
    group = AccessGroup(name="frisch")
    session.add(group)
    session.flush()
    assert parse_profile(group.ui_profile) is WebUiProfile.ADMIN


@pytest.mark.parametrize(
    "stored", [None, "", "verwaltung", "Admin", "TENANT", "mieter"]
)
def test_an_unknown_stored_value_falls_back_to_the_admin_interface(stored: str) -> None:
    """Ein Wert, den diese Fassung nicht kennt, darf die Anmeldung nicht abweisen.

    Er kann nur aus einer von Hand veränderten Datenbank oder aus einer künftigen
    Fassung stammen. Der Rückfall ist derselbe wie beim Upgrade und nimmt niemandem
    etwas weg -- die Rechte bleiben davon unberührt.
    """
    assert parse_profile(stored) is DEFAULT_PROFILE


def test_one_admin_group_is_enough_for_the_admin_interface() -> None:
    assert combined_profile([WebUiProfile.TENANT, WebUiProfile.ADMIN]) is WebUiProfile.ADMIN


def test_only_tenant_groups_make_a_tenant() -> None:
    assert combined_profile([WebUiProfile.TENANT, WebUiProfile.TENANT]) is WebUiProfile.TENANT


def test_without_any_group_the_default_applies() -> None:
    assert combined_profile([]) is DEFAULT_PROFILE


def test_the_principal_carries_the_profile_of_its_groups(session: Session) -> None:
    tenant = user_with_permissions(
        session, "mieterin", [("zone.read", None)], ui_profile=WebUiProfile.TENANT
    )
    assert principal_for_user(session, tenant).ui_profile is WebUiProfile.TENANT


def test_the_profile_changes_no_single_grant(session: Session) -> None:
    """Dieselben Rechte, zwei Profile -- die Rechtemengen müssen gleich sein.

    Wäre das nicht so, hätte das Profil klammheimlich doch eine Berechtigungswirkung,
    und jede Prüfung auf `has_permission` gälte je nach Oberfläche anders.
    """
    zone = create_zone(session, "bad")
    rights = [("zone.read", zone.id), ("setpoint.write", zone.id)]
    admin = user_with_permissions(session, "admin-gleich", rights)
    tenant = user_with_permissions(
        session, "mieter-gleich", rights, ui_profile=WebUiProfile.TENANT
    )
    assert principal_for_user(session, admin).grants == principal_for_user(
        session, tenant
    ).grants


def test_a_token_inherits_the_profile_of_its_owner(session: Session) -> None:
    from tests.helpers import token_with_permissions
    from thermoctl.domain.authz import principal_for_token

    owner = user_with_permissions(
        session, "mieter-token", [("zone.read", None)], ui_profile=WebUiProfile.TENANT
    )
    token = token_with_permissions(session, owner, [("zone.read", None)])
    assert principal_for_token(session, token).ui_profile is WebUiProfile.TENANT


# -- Die Navigation -----------------------------------------------------------


def test_a_tenant_sees_no_plant_navigation(session: Session) -> None:
    tenant = user_with_permissions(
        session,
        "mieter-nav",
        [("zone.read", None), ("user.manage", None), ("audit.read", None)],
        ui_profile=WebUiProfile.TENANT,
    )
    # Ausdrücklich mit weitreichenden Rechten: die Einträge dürfen am Profil
    # scheitern, nicht bloß an fehlenden Rechten.
    paths = {item.path for item in visible_navigation(principal_for_user(session, tenant))}
    for plant_path in ("/users", "/audit", "/settings", "/control", "/devices", "/zones"):
        assert plant_path not in paths, plant_path
    # Was übrig bleibt, gehört zur Wohnung -- und nur das.
    assert paths == {"/schedule", "/heating-time", "/account"}


def test_an_administrator_still_sees_the_plant_navigation(session: Session) -> None:
    admin = user_with_permissions(session, "admin-nav", [("user.manage", None)])
    paths = {item.path for item in visible_navigation(principal_for_user(session, admin))}
    assert "/users" in paths


# -- Der Wächter an den Routen ------------------------------------------------


ADMIN_PAGES = [
    "/settings",
    "/control",
    "/users",
    "/groups",
    "/interfaces",
    "/relay-wear",
    "/statistics",
    "/audit",
    "/device-commands",
    "/devices",
    "/zones",
    "/modes",
    "/controllers",
    "/vacation",
    "/kiosk-tokens",
    "/tokens",
]


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_a_tenant_cannot_open_an_admin_page_even_with_the_permission(
    tenant_client: Client, session: Session, path: str
) -> None:
    """Der eigentliche Riegel: nicht die ausgeblendete Verknüpfung, sondern die Route.

    Die Rechte sind hier absichtlich vollständig vergeben. Ein 403, das nur aus einem
    fehlenden Recht käme, würde diesen Test bestehen lassen, ohne dass der Wächter
    überhaupt existiert -- deshalb bekommt der Mieter hier alles, was die Seiten
    verlangen, und muss trotzdem abgewiesen werden.
    """
    create_settings(session)
    client = tenant_client(
        [
            ("zone.read", None), ("zone.manage", None), ("device.read", None),
            ("device.manage", None), ("user.manage", None), ("group.manage", None),
            ("token.self", None), ("token.manage", None), ("audit.read", None),
            ("setting.manage", None), ("mode.manage", None), ("setpoint.write", None),
            ("schedule.manage", None), ("control.arm", None), ("vacation.manage", None),
        ]
    )
    assert client.get(path).status_code == 403


def test_an_administrator_reaches_the_same_pages(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Die Gegenprobe. Ohne sie könnte der Wächter alles abweisen und der Test oben
    trotzdem grün sein."""
    create_settings(session)
    for path in ADMIN_PAGES:
        assert angemeldeter_client.get(path).status_code == 200, path


def test_the_guard_does_not_replace_the_permission_check(
    client_als: Client, session: Session
) -> None:
    """Ein Administratorprofil ohne das nötige Recht bleibt draußen.

    Das ist die andere Richtung derselben Trennung: der Wächter lässt das Profil
    durch, die Rechteprüfung im Endpunkt weist trotzdem ab.
    """
    create_settings(session)
    assert client_als([("zone.read", None)]).get("/users").status_code == 403


def test_the_profile_cannot_be_switched_from_the_client(
    tenant_client: Client, session: Session
) -> None:
    """Weder Abfrageparameter noch Kopfzeile noch Cookie ändern das Profil.

    Das Profil kommt aus der Gruppe des angemeldeten Benutzers und aus sonst nichts.
    """
    create_settings(session)
    client = tenant_client([("user.manage", None), ("zone.read", None)])
    assert client.get("/users?ui_profile=admin").status_code == 403
    assert client.get("/users", headers={"X-UI-Profile": "admin"}).status_code == 403
    client.cookies.set("ui_profile", "admin")
    assert client.get("/users").status_code == 403
    client.cookies.delete("ui_profile")


# -- Die Gruppenverwaltung ----------------------------------------------------


def test_the_group_page_offers_the_interface_choice(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    page = angemeldeter_client.get("/groups").text
    assert 'name="ui_profile"' in page
    assert "Wohnung (Mieter)" in page


def test_a_group_can_be_switched_to_the_tenant_interface(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    group = AccessGroup(name="Wohnung Nord")
    session.add(group)
    session.flush()
    response = angemeldeter_client.post(
        f"/groups/{group.id}/ui-profile", data={"ui_profile": "tenant"},
        headers=_with_csrf(angemeldeter_client), follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(group)
    assert group.ui_profile == "tenant"


def test_a_new_group_can_be_created_as_a_tenant_group(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    angemeldeter_client.post(
        "/groups", data={"name": "Wohnung Süd", "description": "", "ui_profile": "tenant"},
        headers=_with_csrf(angemeldeter_client), follow_redirects=False,
    )
    created = session.query(AccessGroup).filter_by(name="Wohnung Süd").one()
    assert created.ui_profile == "tenant"


def test_creating_a_group_with_a_taken_name_keeps_the_chosen_interface(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Der Fehlerfall darf die Auswahl nicht verschlucken -- sonst legt man die
    Gruppe beim zweiten Versuch versehentlich als Anlagengruppe an."""
    create_settings(session)
    session.add(AccessGroup(name="Doppelt"))
    session.flush()
    page = angemeldeter_client.post(
        "/groups", data={"name": "Doppelt", "description": "", "ui_profile": "tenant"},
        headers=_with_csrf(angemeldeter_client),
    ).text
    assert 'value="tenant"\n                            selected' in page or (
        'value="tenant" selected' in page
    )


def test_an_unknown_profile_value_from_the_form_creates_an_admin_group(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    angemeldeter_client.post(
        "/groups", data={"name": "Krumm", "description": "", "ui_profile": "gibt-es-nicht"},
        headers=_with_csrf(angemeldeter_client), follow_redirects=False,
    )
    assert session.query(AccessGroup).filter_by(name="Krumm").one().ui_profile == "admin"


def test_switching_a_group_that_does_not_exist_is_a_404(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    response = angemeldeter_client.post(
        "/groups/999999/ui-profile", data={"ui_profile": "tenant"},
        headers=_with_csrf(angemeldeter_client),
    )
    assert response.status_code == 404


def test_the_last_administration_group_cannot_become_a_tenant_group(
    session: Session
) -> None:
    """Sonst fiele die Verwaltung zu: die Rechte wären noch vergeben, aber die
    Rechtematrix für niemanden mehr erreichbar."""
    user_record = user_with_permissions(session, "einzige", [("user.manage", None)])
    membership = session.query(UserAccessGroup).filter_by(user_id=user_record.id).one()
    only_group = session.get(AccessGroup, membership.access_group_id)
    assert only_group is not None
    with pytest.raises(AdministrationError):
        set_group_ui_profile(
            session, only_group, WebUiProfile.TENANT, actor_id=user_record.id
        )


def test_setting_the_profile_a_group_already_has_changes_nothing(
    session: Session
) -> None:
    group = AccessGroup(name="Unverändert")
    session.add(group)
    session.flush()
    set_group_ui_profile(session, group, WebUiProfile.ADMIN, actor_id=None)
    assert group.ui_profile == "admin"


def test_the_page_explains_why_the_last_admin_group_stays_an_admin_group(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Die Absage der Domäne muss auf der Seite ankommen, nicht als 500.

    Sonst klickt jemand auf „Übernehmen“, es passiert nichts Sichtbares, und die
    Gruppe steht danach noch genauso da wie vorher — ohne dass irgendwo stünde,
    warum.
    """
    create_settings(session)
    only_group = session.query(AccessGroup).filter(
        AccessGroup.name.like("gruppe-web-%")
    ).one()
    page = angemeldeter_client.post(
        f"/groups/{only_group.id}/ui-profile", data={"ui_profile": "tenant"},
        headers=_with_csrf(angemeldeter_client),
    )
    assert page.status_code == 200
    session.refresh(only_group)
    assert only_group.ui_profile == "admin"
