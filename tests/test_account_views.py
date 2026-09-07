"""Der persönliche Bereich -- erreichbar für beide Oberflächen, ohne jedes Recht.

Der Punkt dieser Datei: das eigene Passwort und die eigenen Sitzungen sind kein
privilegierter Vorgang, und ein Mieter muss beides erreichen, obwohl ihm die
Benutzerverwaltung verschlossen ist. Genauso wichtig ist die Gegenrichtung -- von
hier aus darf sich kein fremdes Konto ändern lassen.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, source, user_with_permissions
from thermoctl.auth.passwords import verify_password
from thermoctl.auth.sessions import COOKIE_NAME, create_session
from thermoctl.db.models.identity import User

Client = Callable[[list[tuple[str, int | None]]], TestClient]


@pytest.fixture(autouse=True)
def _audit_source(session: Session) -> None:
    source(session, "web")


def _with_csrf(client: TestClient) -> dict[str, str]:
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.config import get_settings

    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


def test_a_tenant_reaches_their_own_account_page(
    tenant_client: Client, session: Session
) -> None:
    create_settings(session)
    page = tenant_client([("zone.read", None)]).get("/account")
    assert page.status_code == 200
    assert "Konto und Sicherheit" in page.text


def test_an_administrator_reaches_the_same_page(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    assert client_als([("zone.read", None)]).get("/account").status_code == 200


def test_the_page_needs_no_permission_at_all(
    client_als: Client, session: Session
) -> None:
    """Wer sich anmelden kann, kommt an sein eigenes Konto -- sonst gäbe es Konten,
    deren Passwort niemand ändern kann."""
    create_settings(session)
    assert client_als([]).get("/account").status_code == 200


def test_the_tenant_page_uses_the_tenant_shell(
    tenant_client: Client, session: Session
) -> None:
    """Die Hülle kommt aus dem Profil des angemeldeten Benutzers, nicht aus der
    Adresse."""
    create_settings(session)
    page = tenant_client([("zone.read", None)]).get("/account").text
    assert "tc-tenant" in page
    assert "tc-sidenav" not in page


def test_changing_the_own_password_works_and_keeps_this_session(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    client = client_als([])
    response = client.post(
        "/account/password", data={"password": "ein-langes-neues-passwort"},
        headers=_with_csrf(client),
    )
    assert response.status_code == 200
    assert "Passwort geändert" in response.text
    changed = session.query(User).filter_by(username="web-1").one()
    assert verify_password("ein-langes-neues-passwort", changed.password_hash)
    # Dieselbe Sitzung trägt noch: die Folgeanfrage geht durch.
    assert client.get("/account").status_code == 200


def test_a_too_short_password_is_shown_again_as_a_correctable_input(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    client = client_als([])
    response = client.post(
        "/account/password", data={"password": "kurz"}, headers=_with_csrf(client)
    )
    assert response.status_code == 200
    assert "is-invalid" in response.text


def test_the_page_offers_no_way_to_touch_a_different_account(
    client_als: Client, session: Session
) -> None:
    """Es gibt hier keine Benutzer-Id -- weder im Pfad noch im Formular. Ein
    mitgeschicktes Feld darf deshalb wirkungslos bleiben."""
    create_settings(session)
    other = user_with_permissions(session, "fremd", [])
    before = other.password_hash
    client = client_als([])
    client.post(
        "/account/password",
        data={"password": "ein-langes-neues-passwort", "user_id": str(other.id)},
        headers=_with_csrf(client),
    )
    session.refresh(other)
    assert other.password_hash == before


def test_other_sessions_are_ended_and_this_one_survives(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    client = client_als([])
    user_record = session.query(User).filter_by(username="web-1").one()
    _second, second_secret = create_session(session, user_record, 3600)
    response = client.post(
        "/account/sessions/revoke-others", headers=_with_csrf(client)
    )
    assert response.status_code == 200
    assert "Sitzung(en) beendet" in response.text
    assert client.get("/account").status_code == 200

    from thermoctl.auth.sessions import resolve_session

    assert resolve_session(session, second_secret) is None


def test_the_help_page_carries_no_plant_data(
    tenant_client: Client, session: Session
) -> None:
    """Reiner Erklärtext. Wäre dort ein Zonenname, wäre es eine Seite ohne
    Rechteprüfung, die Zonennamen zeigt."""
    from tests.helpers import create_zone

    create_settings(session)
    zone = create_zone(session, "fremdes-bad")
    zone.display_name = "Fremdes Bad"
    session.flush()
    page = tenant_client([]).get("/account/help")
    assert page.status_code == 200
    assert "Fremdes Bad" not in page.text


def test_without_passkeys_configured_no_dead_link_is_offered(
    client_als: Client, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Relying-Party-Id gibt es die Passkey-Routen gar nicht -- dann darf hier
    auch kein Verweis darauf stehen."""
    create_settings(session)
    page = client_als([]).get("/account").text
    assert "/passkeys" not in page
