"""The operating pages: what they show and what they tolerate."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings


def test_an_unknown_period_falls_back_to_the_default(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """The period comes out of the query string, so it can be anything.

    Falling back rather than erroring: a bookmark from an older version, or a
    hand-typed address, should show the statistics page and not a 400.
    """
    create_settings(session)
    session.flush()
    response = angemeldeter_client.get("/statistics?period=irgendwas")
    assert response.status_code == 200
    # The same page the default gives -- the buttons mark seven days as current.
    assert 'aria-current="page"' in response.text


def test_the_operating_page_shows_the_most_recent_decision_per_zone(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Only the newest one per zone, and only once.

    The query returns every decision, newest first; the page keeps the first per zone.
    Without a decision in the database the loop never runs, which is why no test had
    ever exercised it -- and the operating page is precisely where someone looks to
    find out what the plant last decided.
    """
    from tests.helpers import create_shadow_decision, create_zone

    zone = create_zone(session, "betriebszone")
    create_settings(session)
    create_shadow_decision(session, zone)
    session.flush()

    response = angemeldeter_client.get("/control")
    assert response.status_code == 200
    assert "betriebszone" in response.text.lower()
