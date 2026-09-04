"""Smoke test: actually call every page once.

This file exists because a fundamental bug slipped through every individual
test and every review: `/` was not built, even though login, logout, and the
navigation bar all pointed to it. Anyone who logged in landed on a 404 page.

No test had found this, because every test called exactly one endpoint and
cut off redirects with `follow_redirects=False`: it checked THAT a redirect
happened, never WHERE and whether there was anything there. The tests here
close exactly that gap -- they check the whole instead of the parts.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"

# Pages that require a logged-in session.
PROTECTED_PAGES = [
    "/",
    "/users",
    "/groups",
    "/tokens",
    "/kiosk-tokens",
    "/devices",
    "/zones",
    "/modes",
    "/modes/new",
    "/audit",
    "/passkeys",
    "/zones/{zone_id}/setpoints",
    "/zones/{zone_id}/devices",
    "/zones/{zone_id}/schedule",
    "/zones/{zone_id}/schedule/adopt",
    "/zones/{zone_id}/parameters",
    "/control",
    "/settings",
    "/interfaces",
    "/statistics",
    "/relay-wear",
    "/plant",
]


def test_every_page_responds_when_logged_in(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """No page may return 404 or 500 while logged in."""
    zone = create_zone(session, "rauchtest")
    create_settings(session)
    errors = []
    for pattern in PROTECTED_PAGES:
        path = pattern.format(zone_id=zone.id)
        response = angemeldeter_client.get(path)
        if response.status_code != 200:
            errors.append(f"{path}: HTTP {response.status_code}")
    assert not errors, "Pages with an error status: " + ", ".join(errors)


def test_login_leads_to_an_existing_page(client: TestClient, user) -> None:
    """Follow the redirect instead of only checking that it exists.

    Exactly this gap is what hid the missing `/` endpoint.
    """
    response = client.post(
        "/login",
        data={"username": "lino", "password": "passwort-lang-genug"},
        follow_redirects=True,
    )
    assert response.status_code == 200, (
        f"After logging in you land on HTTP {response.status_code} "
        f"({response.url})"
    )


def test_not_logged_in_the_home_page_leads_to_login(
    client: TestClient, user
) -> None:
    """Anyone typing the address into a browser should see a form, not an error message."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert "/login" in str(response.url)


def test_without_a_user_the_home_page_leads_to_setup(client: TestClient) -> None:
    """The same guarantee for the state before that: before setup there is
    nobody who could log in -- a login form would be the dead end this
    smoke test is meant to prevent."""
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert "/setup" in str(response.url)


# Every internal link has been written as ``{{ url_prefix }}/path`` rather than a
# bare ``href="/path"`` since the ingress-prefix work (2026-09-04) -- the prefix
# resolves to "" outside a reverse-proxy setup, so the two forms are equivalent at
# runtime, but a scanner that only recognises the old, unprefixed spelling silently
# stops finding anything once a template is rewritten to the new one. That is exactly
# what happened here: this pattern used to be plain ``href="(/[^"]*)"`` and, after the
# rewrite, matched zero links in every template while
# `test_links_in_templates_point_to_existing_pages` kept reporting green -- it had
# nothing left to check. Both spellings are matched here so a future rename of the
# prefix variable cannot repeat that silently.
_HREF_PATTERN = re.compile(r'href="(?:\{\{\s*url_prefix\s*\}\})?(/[^"]*)"')


def _static_href_targets(text: str) -> set[str]:
    """Static (non-templated) href targets found in raw Jinja source.

    Skips anything still containing `{{` or `{%` after the (optional) leading
    `{{ url_prefix }}` is accounted for -- those are per-object links such as
    `/zones/{{ zone.id }}/schedule`, which this file-level scan cannot resolve
    without a real object; `test_every_link_and_form_on_a_rendered_page_leads_somewhere`
    covers those instead, against the rendered page.
    """
    return {z for z in _HREF_PATTERN.findall(text) if "{{" not in z and "{%" not in z}


@pytest.mark.parametrize("template", sorted(TEMPLATES_DIR.glob("*.html")))
def test_links_in_templates_point_to_existing_pages(
    template: Path, angemeldeter_client: TestClient, session: Session
) -> None:
    """Every internal link in the templates must lead somewhere.

    The navigation bar linked to `/`, which did not exist -- clicking the
    project name led to an error page. Such a link is inconspicuous in the
    source and only shows up when actually used.
    """
    # The settings row exists in every set-up installation -- setup creates
    # it. Without it, this test would check a state no instance ever has.
    create_settings(session)
    targets = _static_href_targets(template.read_text(encoding="utf-8"))
    dead = []
    for target in sorted(targets):
        response = angemeldeter_client.get(target, follow_redirects=True)
        if response.status_code >= 400:
            dead.append(f"{target}: HTTP {response.status_code}")
    assert not dead, f"Dead links in {template.name}: " + ", ".join(dead)


def test_the_template_link_scanner_still_finds_a_realistic_number_of_links() -> None:
    """Guards `_static_href_targets` itself against going blind again.

    `test_links_in_templates_point_to_existing_pages` only fails on a *dead* link --
    a scanner that stops recognising links altogether (as happened when every href
    moved to `href="{{ url_prefix }}/…"` and the pattern only knew the bare form)
    keeps passing green with nothing left to check, which is worse than no test at
    all. 42 static, non-per-object hrefs exist across the templates as of this
    writing; the floor here is well below that so a template rename or a template
    added or removed does not make this test flaky, while a scanner regression that
    finds only a handful -- or none -- still trips it.
    """
    total = sum(
        len(_static_href_targets(t.read_text(encoding="utf-8")))
        for t in TEMPLATES_DIR.glob("*.html")
    )
    assert total >= 30, (
        f"Only {total} static href targets found across all templates -- "
        "the scanner in _static_href_targets may no longer recognise how links are written"
    )


@pytest.mark.parametrize("template", sorted(TEMPLATES_DIR.glob("*.html")))
def test_no_script_in_the_body_of_a_template(template: Path) -> None:
    """Scripts belong in the head, not in the body.

    `hx-boost` swaps out the contents of ``<body>`` on every navigation, and
    htmx re-executes ``<script>`` tags in the swapped-in content. Bootstrap's
    menu handling thereby registered itself a second time on ``document``:
    the toggle fired twice, the menu opened and immediately closed again in
    the same click -- to the user it looked like it could no longer be
    opened. Reproduced in the browser and measured the same way after the
    fix; only the invariant is pinned down here, so the next script does not
    end up in the body again.
    """
    text = template.read_text(encoding="utf-8")
    head_end = text.find("</head>")
    positions = [match.start() for match in re.finditer(r"<script\b", text)]
    if head_end == -1:
        # Partial templates with no head of their own must not bring any
        # script at all: they are only ever rendered into swapped-in content.
        assert not positions, f"{template.name} carries a script but has no head"
        return
    too_late = [position for position in positions if position > head_end]
    assert not too_late, (
        f"{template.name}: {len(too_late)} script(s) appear after </head>. "
        "hx-boost re-executes them on every navigation."
    )


def test_the_passkey_script_does_not_rely_only_on_domcontentloaded() -> None:
    """`DOMContentLoaded` never fires again after an hx-boost navigation.

    Anyone reaching /passkeys through the menu therefore got a section the
    script never revealed -- passkey management was invisible unless the
    page was reloaded directly. This only surfaced in the browser, not in
    any test.
    """
    source = (
        Path(__file__).resolve().parent.parent / "thermoctl/web/static/passkey.js"
    ).read_text(encoding="utf-8")
    assert 'document.addEventListener("htmx:load"' in source
    assert 'document.addEventListener("DOMContentLoaded"' in source


# --- Guards for the frame pages ------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_boosted_navigation_returns_the_full_page(
    path: str, angemeldeter_client: TestClient, session: Session
) -> None:
    """Boosted navigation is a page change, not a partial swap.

    `hx-boost="true"` on <body> makes every navigation carry an
    `HX-Request` header. Six views mistook that for a partial swap and
    returned only their content with no frame: anyone going to /geraete or
    /audit through the menu lost the header bar and could only get it back
    by reloading. A direct request was fine -- which is why nobody noticed
    who simply opened the page to check it.
    """
    zone = create_zone(session, "boostzone")
    create_settings(session)
    response = angemeldeter_client.get(
        path.format(zone_id=zone.id),
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "tc-head" in response.text, f"{path} returns no header bar when boosted"


@pytest.mark.parametrize("path", ["/devices", "/audit", "/users", "/groups", "/tokens"])
def test_a_real_partial_swap_still_returns_only_the_content(
    path: str, angemeldeter_client: TestClient, session: Session
) -> None:
    """Counter-check: without it, the test above would also be satisfied by
    a version that abolished partial swaps entirely -- and every table
    refresh would drag along half the page."""
    create_settings(session)
    response = angemeldeter_client.get(path, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "tc-head" not in response.text


@pytest.mark.parametrize("template", sorted(TEMPLATES_DIR.glob("*.html")))
def test_every_table_scrolls_within_itself(template: Path) -> None:
    """A wide table must not stretch the page.

    On a phone /benutzer overran the edge by 190 pixels, and the whole page
    could be scrolled sideways -- including the header bar and every line
    of text. A table inside `.table-responsive` scrolls within its own
    frame instead.
    """
    text = template.read_text(encoding="utf-8")
    if "<table" not in text:
        return
    for position in [t.start() for t in re.finditer(r"<table\b", text)]:
        before_it = text[:position]
        assert "table-responsive" in before_it[-400:], (
            f"{template.name}: table without a .table-responsive frame"
        )


def test_no_custom_toggle_for_the_color_scheme() -> None:
    """The color scheme follows the operating system, and only that.

    A custom toggle was a third setting for something every device already
    knows, and got lost again on the next browser.
    """
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in base
    assert "localStorage" not in base
    assert "schema-umschalten" not in base


def test_the_schedule_grid_does_not_jump_out_from_under_the_mouse() -> None:
    """A click into the schedule grid must not scroll the page.

    Previously `vorbelegen` pulled the create form into view with
    `scrollIntoView`. Anyone trying to set two points in a row would click
    the same screen position the second time and hit a completely different
    time: measured in the browser, a single click shifted the grid by 377 px,
    which at roughly 415 px for the whole day is more than twelve hours off.
    `focus({preventScroll: true})` was not enough on its own -- the call
    still scrolled in Chromium, measured at 377 versus 0 without it.

    The test reads the source, because the suite has no browser. It only
    pins down the invariant; it was verified with Playwright.
    """
    source = (
        Path(__file__).resolve().parent.parent / "thermoctl/web/static/schedule.js"
    ).read_text(encoding="utf-8")
    assert "scrollIntoView" not in source.replace("`scrollIntoView`", ""), (
        "schedule.js scrolls on its own again"
    )
    # Focus only after the visibility check -- otherwise the browser scrolls by itself.
    assert "if (visible) {" in source
    before_focus = source[: source.index(".focus(")]
    assert "getBoundingClientRect" in before_focus and "innerHeight" in before_focus, (
        "focus() no longer comes after the visibility check"
    )


def _csrf(client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Header with a valid CSRF token for mutating requests."""
    from thermoctl.auth.csrf import csrf_token
    from thermoctl.auth.sessions import COOKIE_NAME
    from thermoctl.config import get_settings

    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


def _rendered_form_fields(html: str, action: str) -> dict[str, str]:
    """Fills the form whose `action` is given, exactly as a browser would.

    The field **names** come out of the markup and are never supplied by the test --
    that is the whole point. A `<select>` gets its first real option, a time field a
    time, everything else keeps its rendered value.
    """
    import re

    for form in re.findall(r"<form\b[^>]*>.*?</form>", html, re.DOTALL):
        if f'action="{action}"' not in form:
            continue
        values: dict[str, str] = {}
        for field in re.findall(r"<input\b[^>]*>", form):
            name = re.search(r'name="([^"]+)"', field)
            if name is None:
                continue
            feature_type = re.search(r'type="([^"]+)"', field)
            rendered = re.search(r'value="([^"]*)"', field)
            if feature_type and feature_type.group(1) == "time":
                values[name.group(1)] = "07:15"
            else:
                values[name.group(1)] = rendered.group(1) if rendered else ""
        for select in re.findall(r"<select\b[^>]*>.*?</select>", form, re.DOTALL):
            name = re.search(r'name="([^"]+)"', select)
            if name is None:
                continue
            options = [o for o in re.findall(r'<option value="([^"]*)"', select) if o]
            values[name.group(1)] = options[0] if options else ""
        return values
    raise AssertionError(f"Kein Formular mit action={action!r} gefunden")


def test_every_mutating_html_form_has_a_non_javascript_csrf_field() -> None:
    """Every POST form must remain usable when HTMX and JavaScript are absent."""
    import re
    from pathlib import Path

    from thermoctl.web import templates

    template_dir = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"
    for path in template_dir.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        rendered_source = templates.env.preprocess(source, name=path.name)
        forms = re.findall(r"<form\b[^>]*>.*?</form>", rendered_source, re.DOTALL | re.I)
        for form in forms:
            opening_tag = form[: form.index(">") + 1]
            method = re.search(
                r"\bmethod\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
                opening_tag,
                re.I,
            )
            method_value = next((value for value in method.groups() if value), "") if method else ""
            mutating = method_value.lower() == "post" or re.search(
                r"\bhx-post(?:\s*=|\s|>)", opening_tag, re.I
            )
            if not mutating:
                continue
            assert 'name="csrf_token"' in form, f"CSRF field missing in {path.name}"


def test_empty_hidden_fields_are_confined_to_script_only_forms() -> None:
    """An empty hidden value must not be the only usable control for a normal form."""
    allowed = {
        ("schedule.html", "point_id"),
        ("schedule.html", "weekday"),
        ("schedule.html", "time_of_day"),
        ("device_assignment.html", "device_id"),
        ("device_assignment.html", "role_id"),
        ("device_assignment.html", "assignment_id"),
    }
    found: set[tuple[str, str]] = set()
    template_dir = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"
    for path in template_dir.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        for form in re.findall(r"<form\b[^>]*>.*?</form>", source, re.DOTALL | re.I):
            for field in re.findall(r"<input\b[^>]*type=\"hidden\"[^>]*>", form, re.I):
                name = re.search(r'name="([^"]+)"', field)
                value = re.search(r'value="([^"]*)"', field)
                if name is not None and (value is None or value.group(1) == ""):
                    assert re.search(r"<form\b[^>]*\bhidden(?:\s|>)", form, re.I), (
                        f"Empty hidden field {name.group(1)!r} in visible form {path.name}"
                    )
                    found.add((path.name, name.group(1)))
    assert found == allowed


def test_templates_do_not_label_stored_utc_as_display_time() -> None:
    """Absolute browser times must cross the configured-local-time boundary."""
    template_dir = Path(__file__).resolve().parent.parent / "thermoctl/web/templates"
    offenders = [
        path.name
        for path in template_dir.glob("*.html")
        if " UTC" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_rendered_form_carries_the_field_names_the_view_reads(
    client_als, session: Session
) -> None:
    """The fields a browser sends must be the ones the view looks for.

    This gap has bitten twice, and both times no test noticed: the other tests post
    `data={"time_of_day": …}` straight at the endpoint and walk past the template. In
    the browser the field was still called `uhrzeit`, the view read `time_of_day`, and
    a created schedule point vanished without an error message.

    So this test does what a browser does: it takes the field **names** out of the
    rendered form and fills only their values. Renaming a field on one side alone makes
    it fail.
    """
    from sqlalchemy import select

    from tests.helpers import create_mode, create_settings, create_zone, source
    from thermoctl.db.models.schedule import SchedulePoint

    create_settings(session)
    source(session)
    zone = create_zone(session, "formularzone")
    create_mode(session, "tag")
    client = client_als([("schedule.manage", zone.id), ("zone.read", zone.id)])

    page = client.get(f"/zones/{zone.id}/schedule")
    assert page.status_code == 200
    fields = _rendered_form_fields(page.text, f"/zones/{zone.id}/schedule/points")

    response = client.post(f"/zones/{zone.id}/schedule/points", data=fields)

    assert response.status_code in (200, 303)
    points = session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)).all()
    assert [(p.weekday, p.minute_of_day) for p in points] == [(1, 7 * 60 + 15)], (
        "Das Formular hat nichts angelegt — Feldnamen in Vorlage und Ansicht gehen auseinander"
    )


def test_no_page_reads_a_name_its_view_does_not_supply(
    angemeldeter_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renders every page and fails on the first name Jinja cannot resolve.

    This closes the gap that three defects slipped through in one day, all of the
    same shape: a view was renamed, its template was not, and Jinja answers an
    unknown name with the empty string. The page keeps returning 200 and simply
    shows nothing where the setpoint's reason used to be -- or, in the worse case,
    the form posts a field name the view never reads and creating a schedule point
    silently does nothing.

    A test cannot see that; only a person looking at the page can. So this makes
    the silence audible: an undefined name becomes a recorded miss, and a miss
    fails the test.

    `Undefined` is deliberately not swapped for `StrictUndefined`: several
    templates legitimately ask whether an optional value exists. Only *reading*
    one -- printing it, taking an attribute off it -- is recorded, which is
    exactly the case that renders wrongly.
    """
    from typing import NoReturn

    from jinja2 import Undefined

    from thermoctl.web import templates

    misses: list[str] = []

    class ReportingUndefined(Undefined):
        def _fail_with_undefined_error(
            self, *args: object, **kwargs: object
        ) -> NoReturn:
            misses.append(self._undefined_name or "<unnamed>")
            super()._fail_with_undefined_error(*args, **kwargs)

        def __str__(self) -> str:
            misses.append(self._undefined_name or "<unnamed>")
            return ""

        def __getattr__(self, name: str) -> object:
            if name.startswith("__"):
                raise AttributeError(name)
            misses.append(f"{self._undefined_name}.{name}")
            return self

    monkeypatch.setattr(templates.env, "undefined", ReportingUndefined)
    zone = create_zone(session, "undefined-check")
    create_settings(session)
    for pattern in PROTECTED_PAGES:
        path = pattern.format(zone_id=zone.id)
        vorher = len(misses)
        assert angemeldeter_client.get(path).status_code == 200, path
        for i in range(vorher, len(misses)):
            misses[i] = f"{path}: {misses[i]}"
    assert not misses, (
        "Vorlagen lesen Namen, die ihre View nicht liefert: " + ", ".join(sorted(set(misses)))
    )


def _route_exists(app: object, path: str) -> bool:
    """Whether any registered POST route matches this path.

    Asked of the route table instead of by sending a request: a form action is a
    state-changing endpoint, and finding out whether it exists must not run it.
    """
    from starlette.routing import Match

    scope = {"type": "http", "method": "POST", "path": path, "path_params": {}}
    return any(
        route.matches(scope)[0] is not Match.NONE
        for route in app.routes  # type: ignore[attr-defined]
    )


def test_every_link_and_form_on_a_rendered_page_leads_somewhere(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Renders every page and follows every URL it actually emits.

    The older check reads the template source and skips anything containing `{{` or
    `{%`. That is most of them: the navigation bar builds its links through a macro
    (`nav_link("/zones", "Zonen")`), and every form action interpolates a zone id. So
    the whole navigation bar was invisible to it -- and stayed invisible while it
    pointed at `/zonen`, `/geraete` and `/steuerung`, none of which exist since the
    endpoints were translated. Every page rendered fine; only *clicking* anything
    failed, which no test ever did.

    Reading the rendered page instead of its source closes that: a macro-built link
    and an interpolated action look exactly like any other URL by then.

    A POST target is matched against the application's route table rather than
    posted to. The first version did post -- with an empty body, to every action on
    every page -- and one of those actions is "set this group's permissions". An empty
    body means "no permissions", so the check revoked the rights of the very account
    it was logged in with, and every later page came back 403. A test that changes the
    thing it is inspecting reports on a state that never existed.
    """
    zone = create_zone(session, "linkcheck")
    create_settings(session)
    dead: list[str] = []
    for pattern in PROTECTED_PAGES:
        path = pattern.format(zone_id=zone.id)
        page = angemeldeter_client.get(path)
        assert page.status_code == 200, path
        for target in sorted(set(re.findall(r'href="(/[^"#?]*)"', page.text))):
            answer = angemeldeter_client.get(target, follow_redirects=True)
            if answer.status_code >= 400:
                dead.append(f"{path} -> {target}: HTTP {answer.status_code}")
        for action in sorted(set(re.findall(r'action="(/[^"?]*)"', page.text))):
            if not _route_exists(angemeldeter_client.app, action):
                dead.append(f"{path} -> POST {action}: keine solche Route")
    assert not dead, "Verweise, die ins Leere führen:\n  " + "\n  ".join(dead)

def test_kiosk_link_leads_to_an_actual_dashboard(
    client: TestClient, session: Session
) -> None:
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
        session, "kiosk-admin",
        [("zone.read", None), ("setpoint.write", None), ("override.create", None)],
    )
    _token, plaintext = issue_kiosk_token(
        session, admin, "Flur", [zone.id], control_allowed=True, expires_at=None
    )

    entry = client.get(f"/kiosk/{plaintext}", follow_redirects=False)
    assert entry.status_code == 303
    assert entry.headers["location"] == "/kiosk"
    assert "thermoctl_kiosk" in entry.cookies

    dashboard = client.get("/kiosk")
    assert dashboard.status_code == 200
    assert zone.display_name in dashboard.text


def test_the_new_zone_form_posts_to_a_real_endpoint(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Found by actually clicking through the kiosk feature's own setup: `zone_form.html`
    still posted to `/zonen` -- a leftover from before the English rename -- while the
    router only ever answered `/zones`. Every other link on the same page (Abbrechen,
    Löschen) had already been renamed; only the form's own `action` was missed. Nothing
    in the suite caught it, because the smoke test's link guard only follows `href=`,
    never a form's `action`, and this form was never posted end to end anywhere else.
    """
    from sqlalchemy import select

    from tests.helpers import operating_mode
    from thermoctl.db.models.zone import Zone

    create_settings(session)
    kind = operating_mode(session)
    page = angemeldeter_client.get("/zones/new")
    assert page.status_code == 200
    action = re.search(r'<form method="post" action="([^"]+)"', page.text)
    assert action is not None, "Kein Formular auf /zones/new gefunden"

    # The operating mode goes out as its **id**, which is what the rendered `<option
    # value=…>` carries -- posting the code `"auto"` would fail the form's own check
    # and look exactly like a broken route while telling nothing about one.
    response = angemeldeter_client.post(
        action.group(1),
        data={
            "name": "smoketestzone", "display_name": "Smoketestzone",
            "operating_mode": str(kind.id), "sort_order": "0",
            "temperature_source_device_id": "",
        },
        headers=_csrf(angemeldeter_client),
        follow_redirects=False,
    )
    assert response.status_code != 404, f"{action.group(1)} führt ins Leere"
    assert session.scalar(select(Zone).filter_by(name="smoketestzone")) is not None


def test_the_thermostat_buttons_send_values_their_view_accepts(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """Takes the button values out of the rendered page instead of naming them here.

    The sibling of the field-name guard above, for a value rather than a name. The
    setpoint buttons post `direction=…`, and the view compares that against a fixed
    pair. Renaming the pair in the view without touching the template leaves both
    halves looking correct on their own -- and the buttons silently do nothing, which
    is exactly what happened to the kiosk dashboard: the view was changed to
    `up`/`down` while the page went on sending `hoch`/`runter`.

    A test that writes the value itself cannot see that; it agrees with whichever half
    it was written against.
    """
    from tests.helpers import create_mode, create_settings, create_zone

    zone = create_zone(session, "knopfzone")
    mode = create_mode(session, "tag")
    create_settings(session)
    session.flush()

    page = angemeldeter_client.get("/")
    directions = set(re.findall(r'name="direction" value="([^"]+)"', page.text))
    assert directions, "Keine Sollwert-Knöpfe auf der Startseite gefunden"

    for direction in sorted(directions):
        answer = angemeldeter_client.post(
            f"/zones/{zone.id}/thermostat",
            data={"mode_id": str(mode.id), "direction": direction},
            headers=_csrf(angemeldeter_client),
            follow_redirects=False,
        )
        assert answer.status_code != 400, (
            f"Die Startseite schickt direction={direction!r}, die View lehnt es ab"
        )


@pytest.mark.parametrize(
    "script", sorted((TEMPLATES_DIR.parent / "static").glob("*.js"))
)
def test_no_script_marks_itself_wired_in_the_markup(script: Path) -> None:
    """The "already wired" marker must not live in an attribute.

    htmx stores a snapshot of the page in its history cache and restores it when the
    browser goes back. Attributes survive that; event handlers do not. A `data-wired`
    written by the script therefore came back **without** its handlers -- the marker
    said "already wired", `setUp()` returned early, and from then on the page reacted
    to nothing. Reported from use: after assigning a device by drag, going to another
    page and coming back, nothing could be dragged any more.

    A `WeakSet` cannot have that problem: it only ever contains elements of the current
    document, and an element parsed back out of the cache is a new one.

    Checked in the source rather than in a browser because the suite has no browser --
    the behaviour itself was measured by hand, this only keeps the pattern from
    returning.
    """
    text = script.read_text(encoding="utf-8")
    offenders = re.findall(r"dataset\.\w*[Ww]ired\s*=", text)
    assert not offenders, (
        f"{script.name} schreibt seine Verdrahtungsmarke ins Markup: {offenders}. "
        "Nach einer Wiederherstellung aus dem htmx-Verlauf ist die Marke da und der "
        "Ereignisbehandler weg."
    )
