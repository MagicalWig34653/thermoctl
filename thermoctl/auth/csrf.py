import hashlib
import hmac

CSRF_HEADER = "X-CSRF-Token"

# Nicht httpOnly: die Oberflaeche (HTMX) muss den Wert lesen koennen, um ihn als
# Header mitzuschicken. Unbedenklich, weil das Token selbst nichts preisgibt, womit
# sich die Sitzung uebernehmen liesse — es ist nur an sie gebunden.
CSRF_COOKIE_NAME = "thermoctl_csrf"


def csrf_token(session_secret: str, secret_key: str) -> str:
    """An die Sitzung gebunden: ein Token aus einer fremden Sitzung passt nicht."""
    return hmac.new(
        secret_key.encode(), session_secret.encode(), hashlib.sha256
    ).hexdigest()


def check_csrf(uebermittelt: str | None, session_secret: str, secret_key: str) -> bool:
    if not uebermittelt:
        return False
    return hmac.compare_digest(uebermittelt, csrf_token(session_secret, secret_key))
