import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "thermoctl"
FORBIDDEN_FOR_DOMAIN = ("thermoctl.web", "thermoctl.api", "fastapi")
TEMPLATES = ROOT / "web" / "templates"
STYLESHEET = ROOT / "web" / "static" / "thermoctl.css"


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


def test_template_project_classes_have_css_rules() -> None:
    """Every project-specific class used by a template has a stylesheet rule."""
    class_attributes = re.compile(r'''class\s*=\s*(?:"([^"]*)"|'([^']*)')''', re.DOTALL)
    project_class = re.compile(r"(?<![\w-])(?:t|tc)-[A-Za-z0-9_-]+")
    used: dict[str, set[str]] = {}
    for template in TEMPLATES.rglob("*.html"):
        source = template.read_text(encoding="utf-8")
        for attribute in class_attributes.finditer(source):
            value = next(group for group in attribute.groups() if group is not None)
            for name in project_class.findall(value):
                # A trailing dash belongs to a Jinja-composed class such as
                # `tc-node-{{ kind }}` and is not itself a browser class.
                if not name.endswith("-"):
                    used.setdefault(name, set()).add(template.name)

    defined = {
        match[1:]
        for match in re.findall(
            r"\.(?:t|tc)-[A-Za-z0-9_-]+", STYLESHEET.read_text(encoding="utf-8")
        )
    }
    missing = [
        f"{name}: {', '.join(sorted(used[name]))}" for name in sorted(used.keys() - defined)
    ]
    assert not missing, "Project classes without a CSS rule:\n" + "\n".join(missing)


def test_no_emoji_in_any_of_our_own_interface_files() -> None:
    """Die neue Oberfläche kommt ohne Emojis aus -- Arbeitsanweisung §7.

    Ein Emoji ist eine Aussage, die je nach Schriftart, Betriebssystem und
    Vorlesewerkzeug etwas anderes bedeutet, und ein Vorlesewerkzeug spricht es als
    Namen aus ("Feuer", "Warnzeichen") mitten im Satz. Zustände tragen hier
    stattdessen Text, CSS-Formen oder lokale SVGs.

    Die mitgelieferten Fremdbibliotheken unter `static/vendor/` sind ausgenommen:
    ihr Inhalt ist nicht unsere Aussage, und ändern könnten wir ihn ohnehin nicht,
    ohne die Herkunft zu verlieren (siehe `static/HERKUNFT.md`).
    """
    # Die Blöcke, in denen Emoji und emojiartige Piktogramme tatsächlich liegen.
    # Bewusst **nicht** "alles über U+2000": in diesem Bereich stehen auch die
    # typografischen Anführungszeichen, der Gedankenstrich, das Gradzeichen und die
    # einfachen Richtungspfeile (U+2190–U+21FF), die im ASCII-Anlagenbild als
    # Verbindungslinien gebraucht werden und keine Aussage tragen, die ein
    # Vorlesewerkzeug falsch benennen könnte.
    emoji = re.compile(
        "[\U0001F000-\U0001FAFF"   # Emoji im eigentlichen Sinn
        "\u2600-\u27BF"            # Verschiedene Symbole und Dingbats (☀ … ➿)
        "\u2B00-\u2BFF"            # Zusätzliche Pfeile und geometrische Formen
        "\uFE0F]"                   # Variantenselektor "als Emoji darstellen"
    )
    roots = [
        TEMPLATES,
        STYLESHEET.parent,
    ]
    findings: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "vendor" in path.parts:
                continue
            if path.suffix not in {".html", ".css", ".js", ".svg"}:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                found = emoji.findall(line)
                if found:
                    findings.append(f"{path.name}:{number}: {''.join(found)}")
    assert not findings, "Emoji in der Oberfläche:\n" + "\n".join(findings)
