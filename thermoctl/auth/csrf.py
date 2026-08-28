import hashlib
import hmac

CSRF_HEADER = "X-CSRF-Token"

# Nicht httpOnly: die Oberflaeche (HTMX) muss den Wert lesen koennen, um ihn als
# Header mitzuschicken. Unbedenklich, weil das Token selbst nichts preisgibt, womit
# sich die Sitzung uebernehmen liesse — es ist nur an sie gebunden.
CSRF_COOKIE_NAME = "thermoctl_csrf"


def csrf_token(sitzung_geheimnis: str, secret_key: str) -> str:
    """An die Sitzung gebunden: ein Token aus einer fremden Sitzung passt nicht."""
    return hmac.new(
        secret_key.encode(), sitzung_geheimnis.encode(), hashlib.sha256
    ).hexdigest()


def csrf_pruefen(uebermittelt: str | None, sitzung_geheimnis: str, secret_key: str) -> bool:
    if not uebermittelt:
        return False
    return hmac.compare_digest(uebermittelt, csrf_token(sitzung_geheimnis, secret_key))
