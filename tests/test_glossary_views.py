"""Die Seite `/glossar` und die Hilfe-Symbole, die dorthin verweisen.

Zugriff: wie die anderen reinen Leseseiten -- anmelden muss sich jeder (Grundsatz 4),
ein Recht braucht es nicht, weil die Seite nur Erklärtext zeigt. Beides wird hier
festgehalten, nicht nur das Erfreuliche.
"""

import re
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone

Client = Callable[[list[tuple[str, int | None]]], TestClient]

PREFIX = "/api/hassio_ingress/A1b2C3d4e5"




def test_without_a_session_the_glossary_is_not_served_but_leads_to_the_login(
    client: TestClient, user: object
) -> None:
    """Auth ist Pflicht: Der Erklärtext ist kein Geheimnis, die Seite aber ein Teil der
    Anwendung und gibt ohne Anmeldung keinen Hinweis, dass hier eine Heizung läuft."""
    plain = client.get("/glossar", follow_redirects=False)
    # Dasselbe Verhalten wie jede andere geschützte Seite (`current_principal`).
    assert plain.status_code == client.get("/users", follow_redirects=False).status_code
    assert plain.status_code in {303, 401}
    assert "Hysterese" not in plain.text
















# --- Hülle und Navigation --------------------------------------------------------------












def test_the_glossary_page_carries_no_plant_data(
    client_als: Client, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "fremdes-bad")
    zone.display_name = "Fremdes Bad"
    session.flush()
    assert "Fremdes Bad" not in client_als([]).get("/glossar").text




# --- Die Hilfe-Symbole in den Seiten ---------------------------------------------------


def _help_links(page: str) -> list[str]:
    return re.findall(r'<a class="tc-help" href="([^"]+)"', page)










