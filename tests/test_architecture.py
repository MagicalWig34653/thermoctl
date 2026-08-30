import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "thermoctl"
FORBIDDEN_FOR_DOMAIN = ("thermoctl.web", "thermoctl.api", "fastapi")


def _imports(file: Path) -> set[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_domain_knows_no_adapter() -> None:
    """A rule gets implemented once (principle 6).

    As soon as the domain imports an adapter, this separation quietly erodes --
    which is why this is a test and not just an intention in the specification.
    """
    violations = [
        f"{file.relative_to(ROOT)} imports {name}"
        for file in (ROOT / "domain").rglob("*.py")
        for name in _imports(file)
        if name.startswith(FORBIDDEN_FOR_DOMAIN)
    ]
    assert not violations, "\n".join(violations)


def test_mcp_knows_no_other_adapter() -> None:
    """The three adapters remain equal neighbors."""
    mcp_path = ROOT / "mcp"
    violations = [
        f"{file.relative_to(ROOT)} imports {name}"
        for file in mcp_path.rglob("*.py")
        for name in _imports(file)
        if name.startswith(("thermoctl.web", "thermoctl.api"))
    ]
    assert not violations, "\n".join(violations)


def test_no_model_uses_forbidden_column_types() -> None:
    """No ENUM, no SET, no JSON column -- SQLite cannot handle them."""
    violations = [
        f"{file.relative_to(ROOT)}: {word}"
        for file in (ROOT / "db" / "models").rglob("*.py")
        for word in ("Enum(", "JSON(", "SET(")
        if word in file.read_text(encoding="utf-8")
    ]
    assert not violations, "\n".join(violations)


def test_no_second_template_environment() -> None:
    """All views use `thermoctl.web.templates`, not one of their own.

    The reason is a real bug: `start_views.py` built its own `Jinja2Templates`
    instance with the **relative** path `thermoctl/web/templates`. That worked
    locally, because the tests run inside the project directory -- in the
    container the package lives in `site-packages` and the working directory
    is `/app`. There, the home page would have responded with an error, and
    no test would have noticed.

    Second, an environment of its own does not see the shared filters. That is
    ultimately how it was noticed: a new filter took effect on every page
    except this one.
    """
    violations = [
        str(file.relative_to(ROOT))
        for file in (ROOT / "web").rglob("*.py")
        if "Jinja2Templates(" in file.read_text(encoding="utf-8")
        and file.name != "__init__.py"
    ]
    assert not violations, (
        "Own template environment instead of the shared one from thermoctl.web: "
        + ", ".join(violations)
    )
