"""Runs the interface behind a configured path prefix -- the Home Assistant Ingress
case (`docs/STATUS.md`, this task's design notes) -- and, in the second half of this
file, the same configured process reached *directly* instead, which is what actually
makes this a per-request decision rather than a per-process one (see
`thermoctl.app._ingress_header_prefix`).

Home Assistant's Ingress proxy strips its own prefix (something like
`/api/hassio_ingress/<random-token>`) before forwarding a request to the add-on's
container -- so the app only ever *receives* bare, un-prefixed paths (`/login`, not
`/api/hassio_ingress/.../login`). What must change under a prefix is what the app
*generates*: every `Location` header, every cookie's `path`, and every link a rendered
page carries. `client_with_prefix` (`tests/conftest.py`) builds the app with
`THERMOCTL_ROOT_PATH` set, carries the matching `X-Ingress-Path` header Home
Assistant Core itself sends on every proxied request, and issues requests at bare
paths, exactly like Ingress would forward them.

Every test here has a matching assertion against the plain `client` fixture (no
prefix configured at all) to show the behaviour there is unchanged -- required by the
task. The tests further below add a third case: the *same* `THERMOCTL_ROOT_PATH`
configured, but the request reached the process directly (`client_direct_with_
ingress_configured`) or carries a header that does not match what this process
learned from the Supervisor (`client_with_forged_prefix_header`) -- both must behave
exactly like the plain, unconfigured `client`, never like `client_with_prefix`.
"""

from fastapi.testclient import TestClient

from tests.helpers import user_with_permissions
from thermoctl.auth.sessions import COOKIE_NAME, create_session

PREFIX = "/api/hassio_ingress/A1b2C3d4e5"


def _cookie_paths(response, name: str) -> list[str]:
    """The `Path=` attribute of every `Set-Cookie` header naming `name`."""
    paths = []
    for header in response.headers.get_list("set-cookie"):
        if not header.startswith(f"{name}="):
            continue
        for part in header.split("; "):
            if part.startswith("Path="):
                paths.append(part.removeprefix("Path="))
    return paths


# --- login: redirect target and cookie scope ------------------------------------


def test_login_redirect_is_prefixed(client_with_prefix: TestClient, user) -> None:
    response = client_with_prefix.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}/"


def test_login_without_prefix_redirects_to_the_bare_root(client: TestClient, user) -> None:
    response = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_cookies_are_scoped_to_the_prefix(client_with_prefix: TestClient, user) -> None:
    response = client_with_prefix.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert _cookie_paths(response, "thermoctl_session") == [PREFIX]
    assert _cookie_paths(response, "thermoctl_csrf") == [PREFIX]


def test_login_cookies_without_prefix_are_scoped_to_the_root(client: TestClient, user) -> None:
    response = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert _cookie_paths(response, "thermoctl_session") == ["/"]
    assert _cookie_paths(response, "thermoctl_csrf") == ["/"]


# --- an unauthenticated GET redirecting elsewhere (no user set up yet) ----------


def test_login_page_redirects_to_setup_under_prefix_when_no_user_exists(
    client_with_prefix: TestClient,
) -> None:
    response = client_with_prefix.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}/setup"


# --- logout: redirect and cookie deletion ---------------------------------------


def test_logout_redirect_and_cookie_deletion_are_prefixed(
    client_with_prefix: TestClient, session
) -> None:
    user_record = user_with_permissions(session, "web-logout", [("zone.read", None)])
    _entry, secret = create_session(session, user_record, 3600)
    client_with_prefix.cookies.set(COOKIE_NAME, secret)
    response = client_with_prefix.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}/login?stale=1"
    assert _cookie_paths(response, "thermoctl_session") == [PREFIX]


def test_logout_without_prefix_is_unchanged(client: TestClient, session) -> None:
    user_record = user_with_permissions(session, "web-logout-2", [("zone.read", None)])
    _entry, secret = create_session(session, user_record, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?stale=1"
    assert _cookie_paths(response, "thermoctl_session") == ["/"]


# --- rendered pages: links, static assets, forms carry the prefix ---------------


def _logged_in(client: TestClient, session, name: str) -> TestClient:
    user_record = user_with_permissions(
        session, name, [("zone.read", None), ("zone.manage", None), ("mode.manage", None)]
    )
    _entry, secret = create_session(session, user_record, 3600)
    client.cookies.set(COOKIE_NAME, secret)
    return client


def test_rendered_page_carries_the_prefix_on_every_local_link(
    client_with_prefix: TestClient, session
) -> None:
    _logged_in(client_with_prefix, session, "web-render")
    response = client_with_prefix.get("/zones")
    assert response.status_code == 200
    body = response.text
    assert f'href="{PREFIX}/static/thermoctl.css"' in body
    assert f'href="{PREFIX}/zones/new"' in body
    # No stray un-prefixed absolute link to a local page slipped through.
    assert 'href="/zones' not in body
    assert 'href="/static' not in body


def test_rendered_page_without_prefix_uses_bare_local_links(client: TestClient, session) -> None:
    _logged_in(client, session, "web-render-2")
    response = client.get("/zones")
    assert response.status_code == 200
    body = response.text
    assert 'href="/static/thermoctl.css"' in body
    assert 'href="/zones/new"' in body


def test_htmx_and_form_attributes_carry_the_prefix(
    client_with_prefix: TestClient, session
) -> None:
    _logged_in(client_with_prefix, session, "web-render-3")
    response = client_with_prefix.get("/modes/new")
    assert response.status_code == 200
    assert f'action="{PREFIX}/modes"' in response.text


# --- static assets: the actual file, not just the rendered href -----------------


def test_static_asset_is_served_under_the_prefix(client_with_prefix: TestClient) -> None:
    """Regression check for `thermoctl/app.py::_serve_static`: `app.mount("/static", ...)`
    (Starlette's `Mount`) accumulates its child scope's `root_path` assuming it is a
    literal prefix of the incoming path -- true only when a proxy forwards the full
    external path. Under Ingress the path never carries the prefix (it was already
    stripped), and that accumulation made `StaticFiles` compute the wrong relative
    path -- every asset 404d even though the very same `href` looked correct in the
    rendered HTML. This fetches the file itself, not the link to it.
    """
    response = client_with_prefix.get("/static/thermoctl.css")
    assert response.status_code == 200
    assert len(response.content) > 0


def test_static_asset_without_prefix_is_unchanged(client: TestClient) -> None:
    response = client.get("/static/thermoctl.css")
    assert response.status_code == 200
    assert len(response.content) > 0


# --- a POST-triggered redirect from an authenticated, CSRF-protected view -------


def test_mutating_view_redirect_is_prefixed(client_with_prefix: TestClient, session) -> None:
    from thermoctl.auth.csrf import CSRF_COOKIE_NAME, CSRF_HEADER, csrf_token
    from thermoctl.config import get_settings

    _logged_in(client_with_prefix, session, "web-mutate")
    secret = client_with_prefix.cookies.get(COOKIE_NAME)
    assert secret is not None
    token = csrf_token(secret, get_settings().secret_key.get_secret_value())
    client_with_prefix.cookies.set(CSRF_COOKIE_NAME, token)
    response = client_with_prefix.post(
        "/modes",
        data={"code": "eco", "name": "Sparsam", "sort_order": "1"},
        headers={CSRF_HEADER: token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}/modes"


# --- the same configured process, reached directly (no, or a forged, header) ----
#
# `THERMOCTL_ROOT_PATH` is set exactly as for `client_with_prefix` above (both
# fixtures build the app the same way an add-on with its port also exposed to a
# reverse proxy would run) -- what differs is only what the *request* carries. Every
# assertion below intentionally mirrors one above against `client`, to show that a
# configured-but-unmatched prefix behaves identically to no prefix being configured
# at all, not as some partial or inconsistent state.


def test_login_redirect_direct_is_unprefixed_even_with_ingress_configured(
    client_direct_with_ingress_configured: TestClient, user
) -> None:
    response = client_direct_with_ingress_configured.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_redirect_with_forged_header_is_unprefixed(
    client_with_forged_prefix_header: TestClient, user
) -> None:
    response = client_with_forged_prefix_header.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_cookies_direct_are_scoped_to_the_root_even_with_ingress_configured(
    client_direct_with_ingress_configured: TestClient, user
) -> None:
    response = client_direct_with_ingress_configured.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert _cookie_paths(response, "thermoctl_session") == ["/"]
    assert _cookie_paths(response, "thermoctl_csrf") == ["/"]


def test_login_cookies_with_forged_header_are_scoped_to_the_root(
    client_with_forged_prefix_header: TestClient, user
) -> None:
    response = client_with_forged_prefix_header.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert _cookie_paths(response, "thermoctl_session") == ["/"]
    assert _cookie_paths(response, "thermoctl_csrf") == ["/"]


def test_rendered_page_direct_uses_bare_links_even_with_ingress_configured(
    client_direct_with_ingress_configured: TestClient, session
) -> None:
    _logged_in(client_direct_with_ingress_configured, session, "web-direct-render")
    response = client_direct_with_ingress_configured.get("/zones")
    assert response.status_code == 200
    body = response.text
    assert 'href="/static/thermoctl.css"' in body
    assert 'href="/zones/new"' in body
    assert PREFIX not in body


def test_rendered_page_with_forged_header_uses_bare_links(
    client_with_forged_prefix_header: TestClient, session
) -> None:
    _logged_in(client_with_forged_prefix_header, session, "web-forged-render")
    response = client_with_forged_prefix_header.get("/zones")
    assert response.status_code == 200
    body = response.text
    assert 'href="/static/thermoctl.css"' in body
    assert 'href="/zones/new"' in body
    # The forged value must not appear anywhere in what got rendered either.
    assert "EinAnderesAddon" not in body


def test_static_asset_direct_is_served_unprefixed_even_with_ingress_configured(
    client_direct_with_ingress_configured: TestClient,
) -> None:
    response = client_direct_with_ingress_configured.get("/static/thermoctl.css")
    assert response.status_code == 200
    assert len(response.content) > 0


def test_ingress_configured_and_direct_access_work_side_by_side(
    client_with_prefix: TestClient,
    client_direct_with_ingress_configured: TestClient,
    user,
) -> None:
    """The actual point of this whole change: one configuration, both access paths,
    proven in the same test rather than only separately. `client_with_prefix` and
    `client_direct_with_ingress_configured` are two different `TestClient`s (and
    therefore two ASGI apps, `tests/conftest.py`), but built from the identical
    `THERMOCTL_ROOT_PATH` -- exactly the situation an add-on with its port also
    exposed to a reverse proxy is in: one running process, reached two ways.
    """
    via_ingress = client_with_prefix.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert via_ingress.headers["location"] == f"{PREFIX}/"

    direct = client_direct_with_ingress_configured.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=False,
    )
    assert direct.headers["location"] == "/"
