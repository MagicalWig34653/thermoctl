"""Ein OAuth-Token gilt nur am MCP-Eingang -- nicht in der REST-Schnittstelle."""

from sqlalchemy.orm import Session

from tests.helpers import user_with_permissions
from thermoctl.auth.tokens import issue_token


def _bearer(session: Session, name: str, source: str) -> dict[str, str]:
    owner = user_with_permissions(session, name, [("zone.read", None)])
    token, plaintext = issue_token(session, owner, name, [("zone.read", None)], None)
    token.source = source
    session.flush()
    return {"Authorization": f"Bearer {plaintext}"}


def test_oauth_token_is_refused_by_rest(client, session: Session) -> None:
    response = client.get("/api/v1/zones", headers=_bearer(session, "oauth-rest", "oauth"))
    assert response.status_code == 401
    assert "MCP" in response.json()["detail"]


def test_bearer_token_still_works_in_rest(client, session: Session) -> None:
    response = client.get("/api/v1/zones", headers=_bearer(session, "bearer-rest", "bearer"))
    assert response.status_code == 200
