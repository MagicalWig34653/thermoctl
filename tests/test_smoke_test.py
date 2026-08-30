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
    targets = {
        z
        for z in re.findall(r'href="(/[^"]*)"', template.read_text(encoding="utf-8"))
        if "{{" not in z and "{%" not in z
    }
    dead = []
    for target in sorted(targets):
        response = angemeldeter_client.get(target, follow_redirects=True)
        if response.status_code >= 400:
            dead.append(f"{target}: HTTP {response.status_code}")
    assert not dead, f"Dead links in {template.name}: " + ", ".join(dead)


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
            typ = re.search(r'type="([^"]+)"', field)
            rendered = re.search(r'value="([^"]*)"', field)
            if typ and typ.group(1) == "time":
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

    response = client.post(
        f"/zones/{zone.id}/schedule/points", data=fields, headers=_csrf(client)
    )

    assert response.status_code in (200, 303)
    points = session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id)).all()
    assert [(p.weekday, p.minute_of_day) for p in points] == [(1, 7 * 60 + 15)], (
        "Das Formular hat nichts angelegt — Feldnamen in Vorlage und Ansicht gehen auseinander"
    )
