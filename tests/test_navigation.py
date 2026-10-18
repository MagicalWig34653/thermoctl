import ast
import importlib
import inspect
import re

from thermoctl.web.navigation import NAVIGATION_ITEMS

_PERMISSION = re.compile(r"^[a-z]+\.[a-z]+$")

# Einträge, deren Endpunkt bewusst ein schwächeres Recht prüft als die Navigation
# verlangt. Die Navigation ist nur Darstellung und darf strenger sein; der Endpunkt ist die
# Sicherheitsgrenze. Beide Verlaufseinträge lesen dieselbe Messreihe (`show_history`), die
# je Zone auf `zone.read` filtert; die Verwaltung zeigt den Eintrag nur Verwaltern.
STRICTER_THAN_ENDPOINT = {
    "/zones/history": "zone.manage",
    "/temperature-history": "zone.read",
}


def _permission_checks(endpoint: str) -> set[str]:
    """Alle Rechtecodes, die der Endpunkt oder die Hilfen seines Moduls nennen."""
    module_name, function_name = endpoint.rsplit(".", 1)
    module = importlib.import_module(module_name)
    getattr(module, function_name)  # der Endpunkt muss existieren
    tree = ast.parse(inspect.getsource(module))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found: set[str] = set()
    pending = [function_name]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited or name not in functions:
            continue
        visited.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _PERMISSION.match(node.value):
                    found.add(node.value)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                pending.append(node.func.id)
    return found


def test_navigation_permissions_match_destination_guards() -> None:
    """Changing an endpoint guard without its navigation contract must fail here."""
    for item in NAVIGATION_ITEMS:
        if item.permission is None:
            # Genau ein Eintrag trägt kein Recht: der persönliche Bereich
            # `/account`. Das eigene Passwort zu ändern oder die eigenen anderen
            # Sitzungen zu beenden ist kein privilegierter Vorgang -- ein Recht
            # dafür zu verlangen hieße, dass es Konten gäbe, deren Passwort
            # niemand ändern kann. Der Endpunkt prüft deshalb tatsächlich keins,
            # und dieser Test hätte hier nichts zu vergleichen.
            assert item.path == "/account", item.path
            _permission_checks(item.endpoint)
            continue
        actual = _permission_checks(item.endpoint)
        if STRICTER_THAN_ENDPOINT.get(item.path) == item.permission:
            continue
        assert item.permission in actual, (
            f"{item.path}: navigation requires {item.permission}, but "
            f"{item.endpoint} checks {sorted(actual)}"
        )


def test_every_navigation_endpoint_is_a_ui_api_handler() -> None:
    """Die Navigation verweist auf Endpunkte der Svelte-Oberfläche, nicht auf Alt-Seiten."""
    for item in NAVIGATION_ITEMS:
        assert item.endpoint.startswith("thermoctl.web.ui_api_"), item.endpoint
