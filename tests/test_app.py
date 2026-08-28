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


def test_forbidden_wird_global_als_403_beantwortet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der globale Handler fuer `Forbidden` ist die Auflage aus dem Abschlussreview:

    Eine Route, die eine Rechtsverweigerung nicht selbst in eine HTTPException
    uebersetzt, soll trotzdem 403 liefern statt 500."""
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings
    from thermoctl.domain.authz import Forbidden

    get_settings.cache_clear()
    app = create_app()

    @app.get("/wirft-forbidden")
    async def wirft_forbidden() -> None:
        raise Forbidden("Recht fehlt")

    testclient = TestClient(app, raise_server_exceptions=False)
    antwort = testclient.get("/wirft-forbidden")
    assert antwort.status_code == 403
    assert "Recht fehlt" in antwort.text


def test_statische_dateien_werden_ausgeliefert(client: TestClient) -> None:
    antwort = client.get("/static/vendor/bootstrap/bootstrap.min.css")
    assert antwort.status_code == 200
    antwort = client.get("/static/vendor/htmx/htmx.min.js")
    assert antwort.status_code == 200


def test_anmeldeseite_bindet_das_stylesheet_ein(client: TestClient) -> None:
    antwort = client.get("/login")
    assert "/static/vendor/bootstrap/bootstrap.min.css" in antwort.text


def test_lifespan_erzeugt_einrichtungstoken_bei_fehlender_einrichtung(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Der Lifespan-Handler laeuft nur beim echten Start (`with TestClient(...)`), nicht
    beim blossen Bau der App -- deshalb testet dieser Test ihn ausdruecklich ueber den
    `with`-Block statt ueber die `client`-Fixture. `configure_logging()` ersetzt beim
    Start die Root-Handler (auch den von `caplog`), deshalb wird hier ueber `capsys`
    auf der tatsaechlichen Log-Ausgabe geprueft statt ueber `caplog`."""
    db_pfad = tmp_path / "lifespan.db"
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", f"sqlite:///{db_pfad}")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings
    from thermoctl.db.base import Base
    from thermoctl.db.engine import create_engine_from_settings

    get_settings.cache_clear()
    vorab_engine = create_engine_from_settings(get_settings())
    Base.metadata.create_all(vorab_engine)
    vorab_engine.dispose()

    with TestClient(create_app()):
        pass
    ausgabe = capsys.readouterr().out
    assert "Einrichtung erforderlich" in ausgabe


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
