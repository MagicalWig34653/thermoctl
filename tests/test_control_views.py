"""The operating pages: what they show and what they tolerate."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings


def test_the_operating_page_shows_nothing_extra_for_a_normal_zone(
    angemeldeter_client: TestClient, session: Session
) -> None:
    from tests.helpers import create_zone

    create_settings(session)
    create_zone(session, "ruhige-zone")

    response = angemeldeter_client.get("/control")

    assert response.status_code == 200
    assert "Ersatzquelle ↔ Wandfühler" not in response.text


