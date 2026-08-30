import pytest

from thermoctl.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordTooShort,
    hash_password,
    verify_password,
)
from thermoctl.auth.secrets import hash_secret, new_secret, new_token


def test_hash_is_not_plaintext() -> None:
    value = hash_password("ein-sehr-gutes-passwort")
    assert "ein-sehr-gutes-passwort" not in value
    assert value.startswith("$argon2id$")


def test_the_same_password_produces_different_hashes() -> None:
    """Argon2 salts on its own; two identical passwords must not look the same."""
    assert hash_password("passwort-genug-lang") != hash_password("passwort-genug-lang")


def test_verification_recognizes_correct_and_incorrect() -> None:
    value = hash_password("passwort-genug-lang")
    assert verify_password("passwort-genug-lang", value) is True
    assert verify_password("etwas-anderes-langes", value) is False


def test_a_too_short_password_is_rejected() -> None:
    with pytest.raises(PasswordTooShort):
        hash_password("a" * (MIN_PASSWORD_LENGTH - 1))


def test_verification_against_a_nonsense_hash_does_not_raise() -> None:
    assert verify_password("egal-welches-passwort", "kein-gueltiger-hash") is False


def test_a_secret_is_long_enough_and_different_every_time() -> None:
    values = {new_secret() for _ in range(100)}
    assert len(values) == 100
    assert all(len(value) >= 43 for value in values)  # 256 bits base64url


def test_secret_hash_is_stable_and_sixty_four_characters() -> None:
    secret = new_secret()
    assert hash_secret(secret) == hash_secret(secret)
    assert len(hash_secret(secret)) == 64


def test_token_has_the_expected_shape() -> None:
    plaintext, prefix, hash_value = new_token()
    assert plaintext.startswith("tctl_")
    assert plaintext.split("_")[1] == prefix
    assert hash_value == hash_secret(plaintext.split("_", 2)[2])
    assert len(prefix) == 8


def test_stored_hash_contains_no_plaintext() -> None:
    """Independent proof, without using hash_secret itself as the expectation.

    The test above compares the hash to the result of the very function that
    produced it -- it would also pass if that function merely padded the secret
    instead of hashing it. Hence the direct check here.
    """
    plaintext, _prefix, hash_value = new_token()
    secret = plaintext.split("_", 2)[2]
    assert secret not in hash_value
    assert plaintext not in hash_value
    assert len(hash_value) == 64
    assert all(character in "0123456789abcdef" for character in hash_value)
