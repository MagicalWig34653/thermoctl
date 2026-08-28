import hashlib
import secrets

# noqa S105: Bandit sieht "TOKEN" im Namen und vermutet ein fest verdrahtetes
# Geheimnis. "tctl" ist das oeffentliche Praefix, das in jedem Token sichtbar
# steht und der Wiedererkennung dient — kein Geheimnis. Gezielt hier statt als
# Ausnahme fuer die ganze Datei: in dieser Datei steht echte Geheimnis-Logik.
TOKEN_PRAEFIX = "tctl"  # noqa: S105
PREFIX_LAENGE = 8


def neues_geheimnis() -> str:
    """256 Bit Zufall, base64url-kodiert."""
    return secrets.token_urlsafe(32)


def hash_geheimnis(geheimnis: str) -> str:
    """SHA-256 statt Argon2id.

    Bei 256 Bit Zufall traegt ein langsamer Hash nichts bei, muss aber bei jeder
    API-Anfrage berechnet werden. Fuer Passwoerter gilt das Gegenteil — siehe passwords.py.
    """
    return hashlib.sha256(geheimnis.encode("utf-8")).hexdigest()


def neues_token() -> tuple[str, str, str]:
    """Liefert (klartext, prefix, hash). Der Klartext erscheint genau einmal."""
    prefix = secrets.token_hex(PREFIX_LAENGE // 2)
    geheimnis = neues_geheimnis()
    return f"{TOKEN_PRAEFIX}_{prefix}_{geheimnis}", prefix, hash_geheimnis(geheimnis)
