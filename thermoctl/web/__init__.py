import re
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from jinja2.ext import Extension

from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.web.assets import ASSET_VERSION
from thermoctl.web.assets import STATIC_DIR as STATIC_DIR

TEMPLATES_DIR = Path(__file__).parent / "templates"


class _CsrfFormExtension(Extension):
    """Give every mutating HTML form (today: the OAuth consent page) a CSRF field.

    An ordinary browser submission can only send controls contained in the form.
    Doing this while templates are loaded protects all present and future POST forms.
    """

    _FORM = re.compile(r"(<form\b[^>]*>)(.*?</form>)", re.DOTALL | re.IGNORECASE)
    _POST_METHOD = re.compile(
        r"\bmethod\s*=\s*(?:(['\"])post\1|post(?=\s|>))", re.IGNORECASE
    )

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        def add_field(match: re.Match[str]) -> str:
            opening_tag = match.group(1)
            if not self._POST_METHOD.search(opening_tag):
                return match.group(0)
            field = (
                '<input type="hidden" name="csrf_token" value="{{ session_csrf }}">'
            )
            return f"{match.group(1)}\n{field}{match.group(2)}"

        return self._FORM.sub(add_field, source)


def _template_context(request: Request) -> dict[str, object]:
    """Was die wenigen serverseitig gerenderten Seiten (OAuth-Zustimmung, Kiosk-Hinweis,
    veraltete Seite) brauchen: das CSRF-Token der Sitzung und den Pfadpräfix."""
    session_secret = request.cookies.get(COOKIE_NAME)
    session_csrf = (
        csrf_token(session_secret, get_settings().secret_key.get_secret_value())
        if session_secret is not None
        else ""
    )
    return {
        "session_csrf": session_csrf,
        # Der Ingress-/Reverse-Proxy-Präfix dieser Anfrage (`app.create_app()`,
        # `resolve_root_path`, `thermoctl.web.urls`) oder "". Jede absolute Adresse in den
        # Vorlagen steht als `{{ url_prefix }}/...`.
        "url_prefix": request.scope.get("root_path", ""),
    }


templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        extensions=[_CsrfFormExtension],
    ),
    context_processors=[_template_context],
)

templates.env.globals["asset_version"] = ASSET_VERSION


# The range over which temperatures are rendered as color. Not the input's limits,
# but the range in which living-room temperatures actually move -- otherwise
# everything would sit in the same pale middle.
SPUR_KALT = Decimal("15")
SPUR_WARM = Decimal("23")


def warmth_fraction(temperature: Decimal | None) -> float:
    """0 = coldest representable setpoint, 1 = warmest. Clamped outside that range.

    Lives here and not in either of the two views: the start page and the week view
    show the same quantity, and two scales for the same statement would be two scales
    that drift apart.
    """
    if temperature is None:
        return 0.5
    fraction = (temperature - SPUR_KALT) / (SPUR_WARM - SPUR_KALT)
    return float(min(max(fraction, Decimal(0)), Decimal(1)))
