"""Asset invalidation, validators and honest vendor delivery."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thermoctl.web.assets import ASSET_VERSION, STATIC_DIR, asset_version


@pytest.mark.parametrize("fixture", ["client", "client_with_prefix"])
@pytest.mark.parametrize("version", [None, "old", ASSET_VERSION])
def test_asset_cache_policy_and_conditional_response(
    request: pytest.FixtureRequest, fixture: str, version: str | None
) -> None:
    client: TestClient = request.getfixturevalue(fixture)
    url = "/static/thermoctl.css" + (f"?v={version}" if version else "")
    response = client.get(url)
    assert response.status_code == 200
    expected = (
        "public, max-age=31536000, immutable" if version == ASSET_VERSION else "no-cache"
    )
    assert response.headers["cache-control"] == expected
    cached = client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304
    assert not cached.content
    assert cached.headers["cache-control"] == expected
    missing = client.get(f"/static/missing.css?v={ASSET_VERSION}")
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("cache-control", "")


def test_content_changes_and_releases_invalidate_the_whole_asset_set(tmp_path: Path) -> None:
    css = tmp_path / "style.css"
    css.write_text("old")
    old = asset_version(tmp_path, "1")
    css.write_text("new")
    assert asset_version(tmp_path, "1") != old
    assert asset_version(tmp_path, "2") != asset_version(tmp_path, "1")
    # Documentation edits need not evict the browser cache.
    before_docs = asset_version(tmp_path, "1")
    (tmp_path / "HERKUNFT.md").write_text("docs")
    assert asset_version(tmp_path, "1") == before_docs


def test_no_vendor_source_map_reference_points_to_a_missing_file() -> None:
    for path in (STATIC_DIR / "vendor").rglob("*"):
        if path.suffix in {".css", ".js"}:
            for target in re.findall(r"sourceMappingURL=([^\s*]+)", path.read_text()):
                assert target.startswith("data:") or (path.parent / target).is_file(), path


def test_html_announces_asset_set_for_tabs_open_during_an_update(client: TestClient) -> None:
    response = client.get("/login")
    assert response.headers["X-Thermoctl-Assets"] == ASSET_VERSION
    assert f"?v={ASSET_VERSION}" in response.text
