import hashlib
import hmac

CSRF_HEADER = "X-CSRF-Token"

# Not httpOnly: the UI (HTMX) needs to be able to read the value to send it as a
# header. Harmless, because the token itself does not reveal anything that could be
# used to take over the session — it is only bound to it.
CSRF_COOKIE_NAME = "thermoctl_csrf"


def csrf_token(session_secret: str, secret_key: str) -> str:
    """Bound to the session: a token from a foreign session does not match."""
    return hmac.new(
        secret_key.encode(), session_secret.encode(), hashlib.sha256
    ).hexdigest()


def check_csrf(uebermittelt: str | None, session_secret: str, secret_key: str) -> bool:
    if not uebermittelt:
        return False
    return hmac.compare_digest(uebermittelt, csrf_token(session_secret, secret_key))
