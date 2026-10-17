"""Auslieferung der gebauten Oberfläche: Cache-Dauer gehashter Dateien und Komprimierung."""

from pathlib import Path

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

import thermoctl.app as app_module

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ein kleines Abbild der Build-Ausgabe, damit der Test keinen Frontend-Bau braucht."""
    assets = tmp_path / "app" / "assets"
    assets.mkdir(parents=True)
    (assets / "App-CVd1vxkW.js").write_text("a" * 5000)
    (assets / "index-DNQgunSK.css").write_text("b" * 5000)
    (assets / "ohne-hash.js").write_text("c" * 5000)
    (assets / "klein-AbCdEfGh.js").write_text("x")
    (tmp_path / "andere").mkdir()
    (tmp_path / "andere" / "Datei-AbCdEfGh.js").write_text("d" * 5000)
    monkeypatch.setattr(
        app_module,
        "_static_gzip",
        GZipMiddleware(StaticFiles(directory=str(tmp_path)), minimum_size=1024, compresslevel=6),
    )
    return tmp_path


@pytest.mark.parametrize("name", ["App-CVd1vxkW.js", "index-DNQgunSK.css"])
def test_hashed_build_files_are_cached_for_good_without_a_version(
    client: TestClient, built: Path, name: str
) -> None:
    response = client.get(f"/static/app/assets/{name}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE
    cached = client.get(
        f"/static/app/assets/{name}", headers={"If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304
    assert cached.headers["cache-control"] == IMMUTABLE


def test_only_a_real_hash_in_the_build_directory_earns_the_long_lifetime(
    client: TestClient, built: Path
) -> None:
    # Ohne Hash im Namen, oder ein hash-ähnlicher Name außerhalb des Build-Verzeichnisses:
    # beides muss weiter bei jedem Aufruf geprüft werden.
    assert client.get("/static/app/assets/ohne-hash.js").headers["cache-control"] == "no-cache"
    assert client.get("/static/andere/Datei-AbCdEfGh.js").headers["cache-control"] == "no-cache"
    # Ein Pfad, der nur so aussieht, ist kein Treffer: keine Umgehung über `..`.
    assert "immutable" not in client.get(
        "/static/app/assets/../andere/Datei-AbCdEfGh.js"
    ).headers.get("cache-control", "")


def test_large_scripts_are_compressed_small_ones_are_not(client: TestClient, built: Path) -> None:
    big = client.get("/static/app/assets/App-CVd1vxkW.js", headers={"Accept-Encoding": "gzip"})
    assert big.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in big.headers["vary"]
    assert big.text == "a" * 5000  # der Client entpackt; der Inhalt bleibt unverändert
    small = client.get("/static/app/assets/klein-AbCdEfGh.js", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in small.headers


def test_without_accept_encoding_the_file_comes_back_plain(client: TestClient, built: Path) -> None:
    plain = client.get(
        "/static/app/assets/App-CVd1vxkW.js", headers={"Accept-Encoding": "identity"}
    )
    assert "content-encoding" not in plain.headers
    assert len(plain.content) == 5000
