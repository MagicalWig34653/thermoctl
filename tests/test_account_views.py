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
from thermoctl.auth.sessions import COOKIE_NAME

Client = Callable[[list[tuple[str, int | None]]], TestClient]


@pytest.fixture(autouse=True)
def _audit_source(session: Session) -> None:
    source(session, "web")


def _with_csrf(client: TestClient) -> dict[str, str]:
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.config import get_settings

    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}




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
