import logging

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


@pytest.mark.filterwarnings(
    # Eng auf diesen einen Test begrenzt, nicht global: Er laesst absichtlich eine
    # Ausnahme durch die Anwendung laufen. Starlettes Fehlerpfad gibt die
    # In-Memory-Verbindung dabei erst frei, wenn der Aufraeumer sie einsammelt --
    # nach dem Testende. Die Anwendung schliesst ihre Engine ordentlich (siehe
    # finally unten); die Warnung sagt hier nichts ueber den Code aus.
    "ignore:unclosed database:ResourceWarning"
)
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
    try:
        antwort = testclient.get("/wirft-ausnahme")
        assert antwort.status_code == 500
        assert request_id_var.get() == ausgangswert
    finally:
        # Dieser Test baut eine eigene Anwendung samt Engine. Ohne Schliessen bleibt
        # eine Datenbankverbindung offen und die Suite meldet eine ResourceWarning --
        # eine Warnung, die man nach dem dritten Mal nicht mehr liest.
        testclient.close()
        app.state.engine.dispose()


def test_warnung_bei_netzbindung_ohne_secure_cookies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Die Warnung ist der einzige Ort, an dem es auffaellt, bevor etwas passiert."""
    from thermoctl.app import _warnen_wenn_ungeschuetzt_erreichbar
    from thermoctl.config import Settings

    def _einstellungen(host: str, sicher: bool) -> Settings:
        return Settings(
            _env_file=None, database_url="sqlite://", secret_key="s" * 32,
            bind_host=host, secure_cookies=sicher,
        )

    with caplog.at_level(logging.WARNING, logger="thermoctl.app"):
        caplog.clear()
        _warnen_wenn_ungeschuetzt_erreichbar(_einstellungen("0.0.0.0", False))  # noqa: S104
        assert "SECURE_COOKIES" in caplog.text

        caplog.clear()
        _warnen_wenn_ungeschuetzt_erreichbar(_einstellungen("127.0.0.1", False))
        assert caplog.text == "", "Oertlich gebunden ist kein Grund zur Warnung."

        caplog.clear()
        _warnen_wenn_ungeschuetzt_erreichbar(_einstellungen("0.0.0.0", True))  # noqa: S104
        assert caplog.text == "", "Mit secure_cookies ist alles in Ordnung."


def test_swagger_ui_haengt_an_keiner_fremden_adresse(client: TestClient) -> None:
    """Die mitgelieferte Fassung zieht ihre Dateien aus einem CDN und das Symbol von
    `fastapi.tiangolo.com`.

    Beides widerspricht dem, was fuer Bootstrap und HTMX schon gilt (static/HERKUNFT.md):
    Im Heimnetz ohne Internetzugang bliebe die Seite leer, und jeder Aufruf verriete einem
    Dritten, wann jemand die Heizungssteuerung oeffnet.
    """
    import re

    antwort = client.get("/docs")
    assert antwort.status_code == 200
    ressourcen = re.findall(r'(?:src|href)="([^"]+)"', antwort.text)
    assert ressourcen, "Die Seite bindet gar nichts ein — dann prueft der Test nichts."
    fremde = [r for r in ressourcen if not r.startswith("/")]
    assert not fremde, "Diese Ressourcen kommen von auswaerts: " + ", ".join(fremde)
    for pfad in ressourcen:
        assert client.get(pfad).status_code == 200, pfad


def test_redoc_ist_abgeschaltet(client: TestClient) -> None:
    """Ersatzlos: dasselbe CDN-Problem, und /docs deckt dieselbe Beschreibung ab."""
    assert client.get("/redoc").status_code == 404


def test_openapi_kennt_das_token_als_sicherheitsverfahren(client: TestClient) -> None:
    """Sonst gibt es in der Oberflaeche keinen Anmelde-Knopf.

    Vorher stand `authorization` an jedem Weg als optionaler Kopfzeilen-Parameter — man
    haette bei jedem einzelnen Aufruf "Bearer <token>" von Hand eintragen muessen, und
    nichts sagte, dass es sich um dasselbe Token handelt.
    """
    beschreibung = client.get("/openapi.json").json()
    verfahren = beschreibung["components"]["securitySchemes"]
    assert verfahren == {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "description": "API-Token, ausgestellt unter /tokens",
        }
    }
    zonen = beschreibung["paths"]["/api/v1/zones"]["get"]
    assert zonen["security"] == [{"HTTPBearer": []}]
    assert "parameters" not in zonen, (
        "Der Token-Header darf nicht zusaetzlich als gewoehnlicher Parameter dastehen."
    )


def test_openapi_beschreibt_nur_die_schnittstelle(client: TestClient) -> None:
    """Die Beschreibung ist der Vertrag der REST-Schnittstelle, nicht ein Abzug aller Routen.

    Ohne diese Trennung stuenden in der Oberflaeche unter /docs neben jedem echten Endpunkt
    die HTML-Formularwege — und ein Klick auf 'Try it out' bei
    `POST /benutzer/{id}/aktiv` wuerde einen Benutzer wirklich deaktivieren.
    """
    wege = client.get("/openapi.json").json()["paths"]
    fremd = sorted(p for p in wege if not p.startswith("/api/") and p != "/healthz")
    assert not fremd, "Diese Wege gehoeren nicht in die Beschreibung: " + ", ".join(fremd)
    assert any(p.startswith("/api/") for p in wege), "Es steht gar nichts drin."
