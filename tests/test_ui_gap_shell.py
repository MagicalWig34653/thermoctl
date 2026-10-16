"""Testlücke der Alt-Oberfläche: Direktzugriff bei Ingress-Konfiguration, Hülle ohne Präfix."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _INGRESS_PREFIX
from thermoctl.web import spa_views

BUILT = (
    "<!doctype html><html><head><title>t</title>"
    '<script type="module" crossorigin src="./assets/index-ab.js"></script>'
    '<link rel="stylesheet" href="./assets/index-ab.css">'
    '</head><body><div id="app"></div></body></html>'
)


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = tmp_path / "index.html"
    index.write_text(BUILT, encoding="utf-8")
    monkeypatch.setattr(spa_views, "INDEX_FILE", index)


def test_the_shell_reached_directly_carries_no_prefix_although_ingress_is_configured(
    client_direct_with_ingress_configured: TestClient, built: None
) -> None:
    """Derselbe Prozess, der hinter Ingress ein Präfix einsetzt, liefert beim Direktzugriff
    (eigener Reverse-Proxy, Heimnetz; kein `X-Ingress-Path`) die Hülle ohne jedes Präfix."""
    for path in ("/app/", "/app", "/app/zonen/7/zeitplan"):
        response = client_direct_with_ingress_configured.get(path)
        assert response.status_code == 200, path
        assert '<meta name="thermoctl-prefix" content="">' in response.text
        assert 'src="/static/app/assets/index-ab.js"' in response.text
        assert 'href="/static/app/assets/index-ab.css"' in response.text
        assert _INGRESS_PREFIX not in response.text
        assert "hassio_ingress" not in response.text
        assert "./assets/" not in response.text
