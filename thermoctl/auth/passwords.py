from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_PASSWORT_LAENGE = 12

_hasher = PasswordHasher()


class PasswordTooShort(ValueError):
    pass


def hash_password(klartext: str) -> str:
    if len(klartext) < MIN_PASSWORT_LAENGE:
        raise PasswordTooShort(
            f"Das Passwort muss mindestens {MIN_PASSWORT_LAENGE} Zeichen haben."
        )
    return _hasher.hash(klartext)


def verify_password(klartext: str, hash_wert: str) -> bool:
    """Prueft ein Passwort. Gibt False zurueck statt zu werfen — auch bei kaputtem Hash."""
    try:
        return _hasher.verify(hash_wert, klartext)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
