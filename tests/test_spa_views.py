"""Auslieferung der Svelte-Hülle: Präfix-Einsetzung, Direktlinks, Cache, fehlender Build."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thermoctl.web import spa_views

# So sieht die Ausgabe von `vite build` mit base './' aus.
BUILT = (
    '<!doctype html><html><head><title>t</title>'
    '<script type="module" crossorigin src="./assets/index-ab.js"></script>'
    '<link rel="stylesheet" href="./assets/index-ab.css">'
    '</head><body><div id="app"></div></body></html>'
)


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = tmp_path / "index.html"
    index.write_text(BUILT, encoding="utf-8")
    monkeypatch.setattr(spa_views, "INDEX_FILE", index)


def test_shell_without_prefix(client: TestClient, built: None) -> None:
    response = client.get("/app/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<meta name="thermoctl-prefix" content="">' in response.text
    assert 'src="/static/app/assets/index-ab.js"' in response.text
    assert 'href="/static/app/assets/index-ab.css"' in response.text
    assert "./assets/" not in response.text


def test_shell_with_ingress_prefix(client_with_prefix: TestClient, built: None) -> None:
    response = client_with_prefix.get("/app/")
    assert response.status_code == 200
    prefix = client_with_prefix.headers["X-Ingress-Path"]
    assert f'<meta name="thermoctl-prefix" content="{prefix}">' in response.text
    assert f'src="{prefix}/static/app/assets/index-ab.js"' in response.text


def test_foreign_ingress_header_is_not_trusted(
    client_with_forged_prefix_header: TestClient, built: None
) -> None:
    response = client_with_forged_prefix_header.get("/app/")
    assert 'content=""' in response.text
    assert "hassio_ingress" not in response.text


def test_deep_links_and_bare_app_get_the_same_shell(client: TestClient, built: None) -> None:
    bare = client.get("/app")
    deep = client.get("/app/zonen/7/zeitplan")
    single = client.get("/app/zonen")
    assert bare.status_code == deep.status_code == single.status_code == 200
    assert bare.text == deep.text == single.text


def test_shell_must_be_revalidated(client: TestClient, built: None) -> None:
    assert client.get("/app/").headers["cache-control"] == "no-cache"


def test_prefix_is_html_escaped() -> None:
    out = spa_views.render_shell(BUILT, '/x"><b>')
    assert "<b>" not in out
    assert "&quot;&gt;&lt;b&gt;" in out


def test_missing_build_is_an_honest_503(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spa_views, "INDEX_FILE", tmp_path / "fehlt.html")
    response = client.get("/app/")
    assert response.status_code == 503
    assert "npm run build" in response.text


def test_shell_links_icons_and_manifest_under_the_prefix(
    client_with_prefix: TestClient, built: None
) -> None:
    prefix = client_with_prefix.headers["X-Ingress-Path"]
    text = client_with_prefix.get("/app/").text
    assert f'rel="apple-touch-icon" href="{prefix}/static/apple-touch-icon.png"' in text
    assert f'rel="manifest" href="{prefix}/app/manifest.webmanifest"' in text
    assert 'name="apple-mobile-web-app-capable" content="yes"' in text


def test_manifest_and_icons_are_served_under_the_prefix(client_with_prefix: TestClient) -> None:
    prefix = client_with_prefix.headers["X-Ingress-Path"]
    response = client_with_prefix.get("/app/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["start_url"] == f"{prefix}/app/"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert icon["src"].startswith(f"{prefix}/static/")
        served = client_with_prefix.get(icon["src"].removeprefix(prefix))
        assert served.status_code == 200, icon["src"]
        assert served.content.startswith(b"\x89PNG"), icon["src"]
    # Die Startseite für iOS gibt es ebenfalls als echtes PNG.
    apple = client_with_prefix.get("/static/apple-touch-icon.png")
    assert apple.status_code == 200
    assert apple.content.startswith(b"\x89PNG")
