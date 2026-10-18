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

from tests.helpers import create_zone, user_with_permissions
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


def test_the_navigation_follows_the_rights_whatever_the_profile(session: Session) -> None:
    """Das Profil filtert die Navigation nicht (Entscheidung des Projektinhabers)."""
    rights = [("zone.read", None), ("user.manage", None), ("audit.read", None)]
    tenant = user_with_permissions(session, "mieter-nav", rights, ui_profile=WebUiProfile.TENANT)
    admin = user_with_permissions(session, "admin-nav", rights)
    tenant_paths = {i.path for i in visible_navigation(principal_for_user(session, tenant))}
    admin_paths = {i.path for i in visible_navigation(principal_for_user(session, admin))}
    assert tenant_paths == admin_paths
    for path in ("/users", "/audit", "/schedule", "/heating-time", "/account"):
        assert path in tenant_paths, path


def test_a_tenant_profile_without_the_rights_sees_no_plant_navigation(session: Session) -> None:
    tenant = user_with_permissions(
        session, "mieter-eng", [("zone.read", None)], ui_profile=WebUiProfile.TENANT
    )
    paths = {item.path for item in visible_navigation(principal_for_user(session, tenant))}
    for plant_path in ("/users", "/audit", "/devices", "/groups"):
        assert plant_path not in paths, plant_path


# -- Der Wächter an den Routen ------------------------------------------------












# -- Die Gruppenverwaltung ----------------------------------------------------














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




# -- Wächtertest über alle Routen ----------------------------------------------------


def _dependency_names(dependant: object) -> list[str]:
    call = getattr(dependant, "call", None)
    found = [getattr(call, "__qualname__", str(call))]
    for nested in getattr(dependant, "dependencies", []):
        found += _dependency_names(nested)
    return found


def test_no_ui_api_endpoint_decides_by_the_profile() -> None:
    """Der Riegel gegen den Rückfall: Zugriff entscheidet allein das Recht.

    Das Gruppenprofil bestimmt nur Darstellung (Startseite, Wortwahl). Wer wieder eine
    Profilprüfung (`wrong_profile`) in einen Endpunkt setzt, scheitert hier.
    """
    import pathlib

    import thermoctl.web as web_package

    root = pathlib.Path(web_package.__file__).parent
    offenders = [
        path.name
        for path in sorted(root.glob("*.py"))
        if any(
            marker in path.read_text(encoding="utf-8")
            for marker in ("wrong_profile", "_require_admin_ui", "_need_admin_ui", "_need_profile")
        )
    ]
    assert not offenders, offenders


def test_every_mutating_html_route_still_carries_the_csrf_protection() -> None:
    """`tests/test_csrf.py` prüft dasselbe für den Bestand -- hier noch einmal über
    alle Routen, damit die neuen Router der Wohnungssicht und der Problemmeldung
    nicht daran vorbeigehen."""
    from tests.helpers import alle_api_routen
    from thermoctl.app import create_app

    unprotected: list[str] = []
    for route in alle_api_routen(create_app()):
        if route.include_in_schema:
            continue
        for method in route.methods:
            if method in {"GET", "HEAD", "OPTIONS"}:
                continue
            names = [name.lower() for name in _dependency_names(route.dependant)]
            if not any("csrf" in name for name in names):
                unprotected.append(f"{method} {route.path}")
    assert not unprotected, "\n".join(sorted(unprotected))
