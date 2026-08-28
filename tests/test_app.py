import pytest
from fastapi.testclient import TestClient

from thermoctl.app import create_app
from thermoctl.logging import request_id_var


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


def test_zu_lange_anfrage_id_wird_ersetzt(client: TestClient) -> None:
    zu_lang = "a" * 65
    antwort = client.get("/healthz", headers={"X-Request-ID": zu_lang})
    assert antwort.headers["X-Request-ID"] != zu_lang
    assert antwort.headers["X-Request-ID"]


def test_anfrage_id_mit_zeilenumbruch_wird_ersetzt(client: TestClient) -> None:
    mit_zeilenumbruch = "boese\nInjizierte-Zeile: ja"
    antwort = client.get("/healthz", headers={"X-Request-ID": mit_zeilenumbruch})
    assert "\n" not in antwort.headers["X-Request-ID"]
    assert antwort.headers["X-Request-ID"] != mit_zeilenumbruch


def test_anfrage_id_mit_sonderzeichen_wird_ersetzt(client: TestClient) -> None:
    mit_sonderzeichen = "abc$def!"
    antwort = client.get("/healthz", headers={"X-Request-ID": mit_sonderzeichen})
    assert antwort.headers["X-Request-ID"] != mit_sonderzeichen


def test_anfrage_id_wird_nach_ausnahme_zurueckgesetzt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    app = create_app()

    @app.get("/wirft-ausnahme")
    async def wirft_ausnahme() -> None:
        raise RuntimeError("absichtlicher Fehler fuer den Test")

    ausgangswert = request_id_var.get()
    testclient = TestClient(app, raise_server_exceptions=False)
    antwort = testclient.get("/wirft-ausnahme")
    assert antwort.status_code == 500
    assert request_id_var.get() == ausgangswert
