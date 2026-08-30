from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()


class PasswordTooShort(ValueError):
    pass


def hash_password(plaintext: str) -> str:
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort(
            f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
        )
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hash_value: str) -> bool:
    """Verifies a password. Returns False instead of raising — even for a broken hash."""
    try:
        return _hasher.verify(hash_value, plaintext)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
