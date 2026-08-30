import hashlib
import secrets

# noqa S105: Bandit sees "TOKEN" in the name and suspects a hardcoded secret.
# "tctl" is the public prefix visible in every token, used for recognition —
# not a secret. Scoped here rather than as an exception for the whole file:
# this file contains actual secret-handling logic.
TOKEN_PRAEFIX = "tctl"  # noqa: S105
PREFIX_LAENGE = 8


def new_secret() -> str:
    """256 bits of randomness, base64url-encoded."""
    return secrets.token_urlsafe(32)


def hash_secret(geheimnis: str) -> str:
    """SHA-256 instead of Argon2id.

    With 256 bits of randomness, a slow hash adds nothing, yet it would have to be
    computed on every API request. The opposite holds for passwords — see passwords.py.
    """
    return hashlib.sha256(geheimnis.encode("utf-8")).hexdigest()


def neues_token() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, hash). The plaintext appears exactly once."""
    prefix = secrets.token_hex(PREFIX_LAENGE // 2)
    geheimnis = new_secret()
    return f"{TOKEN_PRAEFIX}_{prefix}_{geheimnis}", prefix, hash_secret(geheimnis)
