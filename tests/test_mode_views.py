from decimal import Decimal

from fastapi.testclient import TestClient

from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    token = csrf_token(secret, get_settings().secret_key.get_secret_value())
    return {"X-CSRF-Token": token}
















































def test_the_setpoint_limit_exists_in_exactly_one_place() -> None:
    """The limit has already stood differently in three places at once.

    Back then the UI checked by hand with no decimal places, the REST interface
    checked via its schema, and the MCP server did not check at all. It has lived
    in the domain ever since -- but a copied-down number sneaks back in easily:
    when moving from 5 to 1 degree it still appeared once more in `alltag_views.py`,
    in the discovery payload, and in the form's markup.

    The test therefore looks for bare limit values outside the domain. It is
    coarse -- a 5 in a line about hysteresis means something else -- so it only
    looks for the pattern in which a temperature limit occurs.

    **Also in the templates.** Its first version only looked at Python files and
    missed `sollwerte.html`, where `min="5"` and `max="35"` appeared as strings.
    That surfaced only at the next move of the limit -- exactly the moment the
    guard should have caught it.
    """
    import re
    from pathlib import Path

    wurzel = Path(__file__).resolve().parent.parent / "thermoctl"
    match = []

    # Templates: a page that asks for temperatures must not contain a bare limit --
    # neither as `min="5"` nor as the argument `"35"` to `number_field`. Such a page is
    # recognized by the degree sign.
    # Two classes: the upper limit and the new lower limit are unambiguous as a
    # number -- a `35` or `-20` in quotes is never anything else here. The old lower
    # limits `5` and `1`, however, also stand for minutes or sort order; they only
    # count if the line itself talks about temperature. That is exactly where the
    # first version failed: it flagged the override's minute field.
    eindeutig = {"35", "35.0", "-20", "-20.0"}
    mehrdeutig = {"5", "5.0", "1", "1.0"}
    zahl_in_anfuehrung = re.compile(r"""["'](-?\d{1,2}(?:\.\d)?)["']""")
    for file in sorted(wurzel.parent.rglob("web/templates/*.html")):
        text = file.read_text(encoding="utf-8")
        if "°C" not in text:
            continue
        for nummer, row in enumerate(text.splitlines(), 1):
            if "temperatur" in row.lower():
                continue  # refers to the passed-through constants
            found = set(zahl_in_anfuehrung.findall(row))
            above_temperature = "°C" in row or "sollwert" in row.lower()
            if found & eindeutig or (above_temperature and found & mehrdeutig):
                match.append(f"{file.name}:{nummer}: {row.strip()}")

    # Python: a number at a spot where a temperature limit stands. The context is
    # often in the line before (`temperature_c: Decimal = Field(` wraps), hence a
    # small window.
    grenzstelle = re.compile(
        r"""(?:ge=|le=|min_temp["']?\s*:\s*|max_temp["']?\s*:\s*)"""
        r"""(?:Decimal\(["'])?-?\d+(?:\.\d+)?"""
    )
    for file in sorted(wurzel.rglob("*.py")):
        if file.name == "modes.py":
            continue  # that is where it belongs
        rows = file.read_text(encoding="utf-8").splitlines()
        for nummer, row in enumerate(rows, 1):
            if "MINIMUM_TEMPERATURE_C" in row or "MAXIMUM_TEMPERATURE_C" in row:
                continue  # refers to the constants
            if not grenzstelle.search(row):
                continue
            # Narrowed to the setpoint field: a bare "temp" in the surroundings also
            # matched `sensor_timeout_seconds` next to `temperature_offset_k` --
            # both temperature-adjacent with entirely different limits.
            umfeld = " ".join(rows[max(0, nummer - 3) : nummer + 1])
            if "temperature_c" in umfeld or "min_temp" in umfeld or "max_temp" in umfeld:
                match.append(f"{file.relative_to(wurzel)}:{nummer}: {row.strip()}")

    assert not match, "setpoint limit outside the domain:\n" + "\n".join(match)


def test_the_message_names_the_limit_that_applies() -> None:
    """It is built from the constants, not copied down -- otherwise, after the next
    move, it would name a number that no longer applies."""
    import pytest as _pytest

    from thermoctl.domain.modes import (
        MAXIMUM_TEMPERATURE_C,
        MINIMUM_TEMPERATURE_C,
        DomainError,
        check_temperature,
    )

    with _pytest.raises(DomainError) as errors:
        check_temperature(MINIMUM_TEMPERATURE_C - Decimal("0.1"))
    notice = errors.value.notice
    assert f"{MINIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
    assert f"{MAXIMUM_TEMPERATURE_C:.1f}".replace(".", ",") in notice
