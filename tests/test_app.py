import pytest
from fastapi.testclient import TestClient

from thermoctl.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def test_healthz_antwortet(client: TestClient) -> None:
    antwort = client.get("/healthz")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "ok"


def test_jede_antwort_traegt_eine_anfrage_id(client: TestClient) -> None:
    antwort = client.get("/healthz")
    assert antwort.headers["X-Request-ID"]


def test_mitgegebene_anfrage_id_wird_uebernommen(client: TestClient) -> None:
    antwort = client.get("/healthz", headers={"X-Request-ID": "vorgegeben"})
    assert antwort.headers["X-Request-ID"] == "vorgegeben"
