"""Prefix-aware URL helpers for redirects and cookies.

thermoctl can run stand-alone (no prefix), be published under a path prefix by a
reverse proxy -- the motivating case is Home Assistant's Ingress, which serves an
add-on's interface under `/api/hassio_ingress/<random-token>/` -- or **both at once**:
the same process reachable through Ingress *and* directly (a reverse proxy of the
operator's own, pointed straight at the exposed container port). The configured
prefix (`THERMOCTL_ROOT_PATH`, see `thermoctl.config.Settings.root_path`) is therefore
not simply stamped onto every request; `thermoctl.app.create_app()`'s
`resolve_root_path` middleware decides it per request (whether the request's
`X-Ingress-Path` header reproduces the configured value exactly -- see
`thermoctl.app._ingress_header_prefix` for why) and sets `scope["root_path"]`
accordingly before anything else runs. Every request past that point carries the
right value in `request.scope["root_path"]`, which is what Starlette's own
`Request.url_for()`/`Request.base_url` read to build correct links -- and what the
Jinja context processor in `thermoctl/web/__init__.py` exposes to every template as
`url_prefix`.

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

    Since `root_path` is now resolved per request (`thermoctl.app.resolve_root_path`),
    this also separates a session started through Ingress from one started through a
    direct connection to the same process: the former gets `Path=<prefix>`, the latter
    `Path=/`, distinct cookie scopes that coexist in the same browser rather than
    overwriting each other. That holds as long as the two are reachable under
    different origins (different host, or a genuinely different registrable domain) --
    the ordinary case, since Ingress is served under Home Assistant's own host. Cookie
    scoping by the browser ignores the *port*, though: an operator who reverse-proxies
    thermoctl directly under the very same hostname Home Assistant itself runs on (only
    the port differing) still has both cookies sent together on every request under the
    Ingress path (`/` is a prefix of everything), and if two different people are
    signed in through the two entry points in the same browser at once, whichever
    cookie the browser happens to order first decides which session answers. Not a
    cross-user data leak (session identity is still checked server-side; the visible
    effect is at most an unexpected "please sign in again") and not a scenario this
    project's typical single-operator household deployment realistically hits -- see
    the task's own STATUS.md entry for the reasoning not to build separately-named
    cookies for it.
    """
    return request.scope.get("root_path", "") or "/"
