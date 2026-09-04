"""Prefix-aware URL helpers for redirects and cookies.

thermoctl can run stand-alone (no prefix) or be published under a path prefix by a
reverse proxy -- the motivating case is Home Assistant's Ingress, which serves an
add-on's interface under `/api/hassio_ingress/<random-token>/`. The prefix comes from
configuration (`THERMOCTL_ROOT_PATH`, see `thermoctl.config.Settings.root_path`) and is
applied once, as FastAPI's own ASGI `root_path`, in `thermoctl.app.create_app()`. Every
request therefore already carries it in `request.scope["root_path"]`, which is what
Starlette's own `Request.url_for()`/`Request.base_url` read to build correct links --
and what the Jinja context processor in `thermoctl/web/__init__.py` exposes to every
template as `url_prefix`.

The two things request-scope alone doesn't cover are collected here: building the
`Location` string for a hand-built `RedirectResponse`, and scoping a cookie's `path`
attribute to the same prefix (`base_url`, further below, covers a third: telling the
browser's own JavaScript where the prefix is).
"""

from fastapi import Request


def prefixed(request: Request, path: str) -> str:
    """`path` (an absolute, un-prefixed application path such as `/zonen`) with the
    request's configured prefix applied -- for building `RedirectResponse` targets and
    other `Location`-style strings outside of template rendering.
    """
    return f"{request.scope.get('root_path', '')}{path}"


def cookie_path(request: Request) -> str:
    """The `path` a cookie should be scoped to: the configured prefix, or `/` without one.

    Cookies are scoped to the deployment's own prefix -- not the domain-wide `/` -- so
    that two add-ons behind the same Home Assistant Ingress host, each under their own
    random prefix, never see or clobber each other's session cookie. A cookie's `path`
    must match on deletion, too: every `delete_cookie()` call needs the same value that
    was passed to the matching `set_cookie()`.
    """
    return request.scope.get("root_path", "") or "/"
