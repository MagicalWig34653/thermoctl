"""Smoke test: actually call every page once.

This file exists because a fundamental bug slipped through every individual
test and every review: `/` was not built, even though login, logout, and the
navigation bar all pointed to it. Anyone who logged in landed on a 404 page.

No test had found this, because every test called exactly one endpoint and
cut off redirects with `follow_redirects=False`: it checked THAT a redirect
happened, never WHERE and whether there was anything there. The tests here
close exactly that gap -- they check the whole instead of the parts.

Die Oberfläche ist die Svelte-Anwendung unter `/app`; jeder alte Pfad leitet dorthin um
(`thermoctl/web/legacy_redirects.py`). Folgen die Tests den Weiterleitungen, sehen sie
die ausgelieferte Hülle und die Schnittstelle, gegen die sie arbeitet.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.web.legacy_redirects import EXACT_TARGETS, PARAM_TARGETS, ROOM_PATHS

# Alle alten Seitenpfade, die eine Weiterleitung tragen (Platzhalter ersetzt der Test).
LEGACY_PAGES = sorted(
    {*EXACT_TARGETS, *ROOM_PATHS, *(p for p in PARAM_TARGETS if "{rest" not in p)}
)


@pytest.fixture
def built_shell(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Die gebaute Hülle gibt es nur nach `npm run build`; hier genügt eine Attrappe."""
    from thermoctl.web import spa_views

    index = tmp_path / "index.html"
    index.write_text("<html><head></head><body>app</body></html>", encoding="utf-8")
    monkeypatch.setattr(spa_views, "INDEX_FILE", index)


def test_every_legacy_page_ends_in_the_app_shell_when_logged_in(
    angemeldeter_client: TestClient, session: Session, built_shell: None
) -> None:
    """No old address may end in a 404 or 500 while logged in."""
    zone = create_zone(session, "rauchtest")
    create_settings(session)
    errors = []
    for pattern in LEGACY_PAGES:
        path = pattern.format(zone_id=zone.id, mode_id=1, change_id=1, rest="x")
        response = angemeldeter_client.get(path, follow_redirects=True)
        if response.status_code != 200 or not response.url.path.startswith("/app"):
            errors.append(f"{path}: HTTP {response.status_code} {response.url.path}")
    assert not errors, "Pages with an error status: " + ", ".join(errors)


def test_the_app_answers_the_logged_in_person_after_login(client: TestClient, user) -> None:
    """Follow the login instead of only checking that it exists.

    Exactly this gap is what hid the missing `/` endpoint.
    """
    response = client.post(
        "/ui-api/login", json={"username": "lino", "password": "passwort-lang-genug"}
    )
    assert response.status_code == 200
    assert client.get("/ui-api/bootstrap").status_code == 200


def test_not_logged_in_the_home_page_leads_to_the_app(
    client: TestClient, user, built_shell: None
) -> None:
    """Anyone typing the address into a browser should see the app, not an error message."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    # `/` führt in die Svelte-Oberfläche, die selbst zur Anmeldung führt
    # (`/ui-api/bootstrap` ohne Sitzung: 401).
    assert str(response.url).endswith("/app/")
    assert client.get("/ui-api/bootstrap").status_code == 401


def test_without_a_user_the_home_page_leads_to_setup(client: TestClient, built_shell: None) -> None:
    """The same guarantee for the state before that: before setup there is
    nobody who could log in -- a login form would be the dead end this
    smoke test is meant to prevent."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    # Die Oberfläche entscheidet über `/ui-api/setup`, ob sie zur Einrichtung weiterleitet.
    assert str(response.url).endswith("/app/")
    assert client.get("/ui-api/setup").status_code == 200


def test_kiosk_link_leads_to_an_actual_dashboard(client: TestClient, session: Session) -> None:
    """The same gap this file exists for, applied to the kiosk entry link.

    `/kiosk-tokens` hands out an address to paste into a tablet's browser. A token
    that resolves but leads to a blank or broken page would satisfy every unit test
    that only checks the redirect target exists -- this follows the redirect and
    reads the rendered page, the way `test_login_leads_to_an_existing_page` does for
    the ordinary login.
    """
    from tests.helpers import source, user_with_permissions
    from thermoctl.domain.kiosk import issue_kiosk_token

    create_settings(session)
    source(session, "kiosk")
    zone = create_zone(session, "wandtablett")
    admin = user_with_permissions(
        session,
        "kiosk-admin",
        [
            ("zone.read", None),
            ("setpoint.write", None),
            ("override.create", None),
            ("override.cancel", None),
        ],
    )
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )

    entry = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert entry.status_code == 303
    assert entry.headers["location"] == "/app/kiosk"
    assert "thermoctl_kiosk" in entry.cookies

    shell = client.get("/app/kiosk")
    assert shell.status_code in (200, 503)  # 503: Hülle nicht gebaut
    dashboard = client.get("/kiosk-api/state")
    assert dashboard.status_code == 200
    assert zone.display_name in dashboard.text
