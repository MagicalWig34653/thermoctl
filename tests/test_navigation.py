import ast
import importlib
import inspect
import re
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.web.navigation import NAVIGATION_ITEMS


def _navigation(html: str) -> str:
    match = re.search(r'<nav class="tc-head.*?</nav>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_user_without_user_management_neither_sees_link_nor_gets_access(
    client_als: Callable[[list[tuple[str, int | None]]], TestClient],
) -> None:
    client = client_als([("zone.read", None)])
    navigation = _navigation(client.get("/").text)
    assert "Benutzer" not in navigation
    assert client.get("/users").status_code == 403


def test_user_with_all_permissions_sees_every_navigation_item(
    angemeldeter_client: TestClient,
) -> None:
    navigation = _navigation(angemeldeter_client.get("/").text)
    for item in NAVIGATION_ITEMS:
        assert f'href="{item.path}"' in navigation
        assert item.label in navigation


def test_settings_menu_is_absent_when_none_of_its_items_is_available(
    client_als: Callable[[list[tuple[str, int | None]]], TestClient],
) -> None:
    navigation = _navigation(client_als([]).get("/").text)
    assert "Einstellungen" not in navigation
    assert "dropdown-menu" in navigation  # The account menu still exists.


@pytest.mark.parametrize(
    ("permission", "visible_path", "hidden_path"),
    [("zone.read", "/zones", "/control"), ("device.read", "/controllers", "/devices")],
)
def test_zone_scoped_permission_shows_only_zone_filtering_destinations(
    client_als: Callable[[list[tuple[str, int | None]]], TestClient],
    session: Session,
    permission: str,
    visible_path: str,
    hidden_path: str,
) -> None:
    zone = create_zone(session, f"navigation-{permission}")
    create_settings(session)
    navigation = _navigation(client_als([(permission, zone.id)]).get("/").text)
    assert f'href="{visible_path}"' in navigation
    assert f'href="{hidden_path}"' not in navigation


def _permission_checks(endpoint: str) -> tuple[set[str], set[str]]:
    module_name, function_name = endpoint.rsplit(".", 1)
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    tree = ast.parse(inspect.getsource(module))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    plant: set[str] = set()
    any_zone: set[str] = set()
    pending = [function.__name__]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited or name not in functions:
            continue
        visited.add(name)
        for call in (node for node in ast.walk(functions[name]) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name):
                continue
            if call.func.id in functions:
                pending.append(call.func.id)
            if call.func.id not in {"require", "visible_zones", "_zones"}:
                continue
            for argument in call.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    (plant if call.func.id == "require" else any_zone).add(argument.value)
    return plant, any_zone


def test_navigation_permissions_match_destination_guards() -> None:
    """Changing a view guard without its navigation contract must fail here."""
    for item in NAVIGATION_ITEMS:
        plant, any_zone = _permission_checks(item.endpoint)
        actual = any_zone if item.scope == "any_zone" else plant
        assert item.permission in actual, (
            f"{item.path}: navigation requires {item.permission}, but "
            f"{item.endpoint} checks {sorted(actual)}"
        )
