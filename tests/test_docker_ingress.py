"""Tests for ``docker/thermoctl_ingress.py``.

This script determines ``THERMOCTL_ROOT_PATH`` under Home Assistant Ingress at
start-up by asking the Supervisor's own API for the add-on's ``ingress_entry``. It
lives under ``docker/`` for the same reason as ``thermoctl_optionen.py``: it runs
before the application is even installed, so it is loaded here by file path.

No real network calls: a small local HTTP server stands in for the Supervisor's API.
"""

import http.server
import importlib.util
import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docker" / "thermoctl_ingress.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("thermoctl_ingress", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingress = _load_module()


@contextmanager
def _stellvertreter_supervisor(
    *, status: int = 200, body: bytes | None = b"", verzögerung: float = 0.0
) -> Iterator[tuple[str, dict[str, str]]]:
    """Ein kleiner Stellvertreter für ``http://supervisor/addons/self/info``.

    Läuft lokal, beantwortet jede Anfrage mit dem vorgegebenen Status/Body und
    merkt sich den empfangenen ``Authorization``-Header, damit ein Test prüfen kann,
    was tatsächlich beim (Fake-)Supervisor ankam -- ohne je einen echten Netzaufruf
    zu machen.
    """
    empfangene_header: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- von http.server vorgegebener Name
            empfangene_header["authorization"] = self.headers.get("Authorization", "")
            if verzögerung:
                time.sleep(verzögerung)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if body is not None:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # keine Testausgabe zumüllen

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", empfangene_header
    finally:
        server.shutdown()
        thread.join()


# --- _gueltiger_pfad ------------------------------------------------------------------


@pytest.mark.parametrize(
    "wert",
    [
        "/api/hassio_ingress/abcdef0123456789",
        "/api/hassio_ingress/abcdef0123456789/",
        "/x",
    ],
)
def test_gueltige_pfade_werden_akzeptiert(wert: str) -> None:
    assert ingress._gueltiger_pfad(wert) is True


@pytest.mark.parametrize(
    "wert",
    [
        "",
        None,
        42,
        "http://fremd/",
        "https://fremd/api/hassio_ingress/x",
        "/../etwas",
        "/api/hassio_ingress/../../etc/passwd",
        "/mit\nzeilenumbruch",
        "/mit\rzeilenumbruch",
        '/mit"anführungszeichen',
        "/mit'anführungszeichen",
        "kein-führender-slash",
        "//evil.example.com/",
    ],
)
def test_boesartige_oder_ungueltige_pfade_werden_verworfen(wert: object) -> None:
    assert ingress._gueltiger_pfad(wert) is False


# --- ermittle_root_path -----------------------------------------------------------


def test_ohne_token_wird_nichts_abgefragt() -> None:
    aufrufe = []

    def opener(*args: object, **kwargs: object) -> None:
        aufrufe.append((args, kwargs))
        raise AssertionError("darf ohne Token nicht aufgerufen werden")

    assert ingress.ermittle_root_path(None, opener=opener) is None
    assert ingress.ermittle_root_path("", opener=opener) is None
    assert aufrufe == []


def test_gueltige_antwort_liefert_den_pfad() -> None:
    with _stellvertreter_supervisor(
        body=json.dumps(
            {"result": "ok", "data": {"ingress_entry": "/api/hassio_ingress/deadbeef"}}
        ).encode()
    ) as (url, header):
        pfad = ingress.ermittle_root_path("mein-token", url=url, timeout=2)
    assert pfad == "/api/hassio_ingress/deadbeef"
    assert header["authorization"] == "Bearer mein-token"


@pytest.mark.parametrize(
    "ingress_entry",
    [
        "http://fremd/",
        "/../etwas",
        "/mit\nzeilenumbruch",
        '/mit"anführungszeichen',
    ],
)
def test_boesartige_antwort_wird_verworfen(
    ingress_entry: str, capsys: pytest.CaptureFixture
) -> None:
    with _stellvertreter_supervisor(
        body=json.dumps({"result": "ok", "data": {"ingress_entry": ingress_entry}}).encode()
    ) as (url, _header):
        pfad = ingress.ermittle_root_path("mein-token", url=url, timeout=2)
    assert pfad is None
    assert "mein-token" not in capsys.readouterr().err


def test_fehlerstatus_liefert_none_und_bricht_nicht_ab(capsys: pytest.CaptureFixture) -> None:
    with _stellvertreter_supervisor(status=401, body=b"nicht autorisiert") as (url, _header):
        pfad = ingress.ermittle_root_path("mein-token", url=url, timeout=2)
    assert pfad is None
    fehlerausgabe = capsys.readouterr().err
    assert "mein-token" not in fehlerausgabe
    assert fehlerausgabe.strip() != ""


def test_ungueltiges_json_liefert_none(capsys: pytest.CaptureFixture) -> None:
    with _stellvertreter_supervisor(body=b"das ist kein json") as (url, _header):
        pfad = ingress.ermittle_root_path("mein-token", url=url, timeout=2)
    assert pfad is None
    assert "mein-token" not in capsys.readouterr().err


def test_unerwartete_antwortform_liefert_none() -> None:
    with _stellvertreter_supervisor(body=json.dumps({"result": "error"}).encode()) as (url, _h):
        assert ingress.ermittle_root_path("mein-token", url=url, timeout=2) is None
    with _stellvertreter_supervisor(body=json.dumps({"result": "ok"}).encode()) as (url, _h):
        assert ingress.ermittle_root_path("mein-token", url=url, timeout=2) is None
    with _stellvertreter_supervisor(body=json.dumps(["nicht", "ein", "objekt"]).encode()) as (
        url,
        _h,
    ):
        assert ingress.ermittle_root_path("mein-token", url=url, timeout=2) is None
    with _stellvertreter_supervisor(
        body=json.dumps({"result": "ok", "data": "kein-objekt"}).encode()
    ) as (url, _h):
        assert ingress.ermittle_root_path("mein-token", url=url, timeout=2) is None


def test_zeitueberschreitung_liefert_none_und_bricht_nicht_ab(
    capsys: pytest.CaptureFixture,
) -> None:
    with _stellvertreter_supervisor(verzögerung=1.0) as (url, _header):
        pfad = ingress.ermittle_root_path("mein-token", url=url, timeout=0.05)
    assert pfad is None
    fehlerausgabe = capsys.readouterr().err
    assert "mein-token" not in fehlerausgabe
    assert fehlerausgabe.strip() != ""


def test_nicht_erreichbarer_supervisor_liefert_none_und_bricht_nicht_ab(
    capsys: pytest.CaptureFixture,
) -> None:
    # Port, auf dem garantiert nichts lauscht -- steht für "kein Netz".
    pfad = ingress.ermittle_root_path(
        "mein-token", url="http://127.0.0.1:1/", timeout=0.5
    )
    assert pfad is None
    assert "mein-token" not in capsys.readouterr().err


# --- main(), als eigener Prozess -- so, wie docker/entrypoint.sh es tatsächlich
# aufruft. Vermeidet auch, dass die einmal geladene Testmodul-Instanz (SUPERVISOR_URL
# als Modulkonstante, einmal beim ersten Import aus der Umgebung gelesen) zwischen
# Tests hängen bleibt -- ein frischer Prozess liest sie jedesmal neu.


def _lauf(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_main_ohne_supervisor_token_tut_nichts() -> None:
    ergebnis = _lauf({"PATH": "/usr/bin:/bin"})
    assert ergebnis.returncode == 0
    assert ergebnis.stdout == ""
    assert ergebnis.stderr == ""


def test_main_mit_bereits_gesetztem_root_path_fragt_nichts_ab() -> None:
    # Absichtlich eine Supervisor-Adresse, die garantiert nicht erreichbar ist --
    # würde das Skript sie trotzdem abfragen, liefe dieser Test in die
    # Zeitüberschreitung statt sofort durchzulaufen.
    ergebnis = _lauf(
        {
            "PATH": "/usr/bin:/bin",
            "SUPERVISOR_TOKEN": "mein-token",
            "THERMOCTL_ROOT_PATH": "/vom-betreiber-gesetzt",
            "THERMOCTL_INGRESS_SUPERVISOR_URL": "http://127.0.0.1:1/",
        }
    )
    assert ergebnis.returncode == 0
    assert ergebnis.stdout == ""


def test_main_gibt_export_zeile_fuer_gueltigen_pfad_aus() -> None:
    with _stellvertreter_supervisor(
        body=json.dumps(
            {"result": "ok", "data": {"ingress_entry": "/api/hassio_ingress/deadbeef"}}
        ).encode()
    ) as (url, header):
        ergebnis = _lauf(
            {
                "PATH": "/usr/bin:/bin",
                "SUPERVISOR_TOKEN": "mein-token",
                "THERMOCTL_INGRESS_SUPERVISOR_URL": url,
            }
        )
    assert ergebnis.returncode == 0
    assert ergebnis.stdout.strip() == "export THERMOCTL_ROOT_PATH=/api/hassio_ingress/deadbeef"
    assert header["authorization"] == "Bearer mein-token"
    assert "mein-token" not in ergebnis.stderr


def test_main_gibt_bei_fehlgeschlagener_abfrage_nichts_aus() -> None:
    with _stellvertreter_supervisor(status=500, body=b"kaputt") as (url, _header):
        ergebnis = _lauf(
            {
                "PATH": "/usr/bin:/bin",
                "SUPERVISOR_TOKEN": "mein-token",
                "THERMOCTL_INGRESS_SUPERVISOR_URL": url,
            }
        )
    assert ergebnis.returncode == 0
    assert ergebnis.stdout == ""
    assert "mein-token" not in ergebnis.stderr
