"""Documentation checks must also run with only the dev extra installed."""

import subprocess
import sys
from pathlib import Path


def test_screenshot_documentation_check_without_playwright() -> None:
    # A fresh interpreter prevents an earlier test's imports from hiding the
    # optional dependency. Run the real consumer so its import cannot drift from
    # a separately maintained import statement in this regression test.
    result = subprocess.run(  # noqa: S603 -- fixed code in the current interpreter
        [
            sys.executable,
            "-c",
            """
import sys
sys.modules["playwright"] = None
from tests.test_docs_current import (
    test_documented_screenshots_match_what_the_documentation_actually_embeds,
)
test_documented_screenshots_match_what_the_documentation_actually_embeds()

# These helpers are shared with the browser tooling but must not need Playwright.
import browser_tests.live_server
import tools.screenshot_seed
import browser_tests.seed
""",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
