import hashlib
import hmac

CSRF_HEADER = "X-CSRF-Token"


def csrf_token(sitzung_geheimnis: str, secret_key: str) -> str:
    """An die Sitzung gebunden: ein Token aus einer fremden Sitzung passt nicht."""
    return hmac.new(
        secret_key.encode(), sitzung_geheimnis.encode(), hashlib.sha256
    ).hexdigest()


def csrf_pruefen(uebermittelt: str | None, sitzung_geheimnis: str, secret_key: str) -> bool:
    if not uebermittelt:
        return False
    return hmac.compare_digest(uebermittelt, csrf_token(sitzung_geheimnis, secret_key))
