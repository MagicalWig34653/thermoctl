import pytest

from thermoctl.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordTooShort,
    hash_password,
    verify_password,
)
from thermoctl.auth.secrets import hash_secret, neues_token, new_secret


def test_hash_ist_kein_klartext() -> None:
    value = hash_password("ein-sehr-gutes-passwort")
    assert "ein-sehr-gutes-passwort" not in value
    assert value.startswith("$argon2id$")


def test_gleiches_passwort_ergibt_verschiedene_hashes() -> None:
    """Argon2 salzt selbst; zwei gleiche Passwoerter duerfen nicht gleich aussehen."""
    assert hash_password("passwort-genug-lang") != hash_password("passwort-genug-lang")


def test_pruefung_erkennt_richtig_und_falsch() -> None:
    value = hash_password("passwort-genug-lang")
    assert verify_password("passwort-genug-lang", value) is True
    assert verify_password("etwas-anderes-langes", value) is False


def test_zu_kurzes_passwort_wird_abgewiesen() -> None:
    with pytest.raises(PasswordTooShort):
        hash_password("a" * (MIN_PASSWORD_LENGTH - 1))


def test_pruefung_gegen_unsinnigen_hash_wirft_nicht() -> None:
    assert verify_password("egal-welches-passwort", "kein-gueltiger-hash") is False


def test_geheimnis_ist_lang_genug_und_jedes_mal_neu() -> None:
    values = {new_secret() for _ in range(100)}
    assert len(values) == 100
    assert all(len(w) >= 43 for w in values)  # 256 Bit base64url


def test_geheimnis_hash_ist_stabil_und_sechzig_vier_zeichen() -> None:
    g = new_secret()
    assert hash_secret(g) == hash_secret(g)
    assert len(hash_secret(g)) == 64


def test_token_hat_erwartete_form() -> None:
    plaintext, prefix, hash_value = neues_token()
    assert plaintext.startswith("tctl_")
    assert plaintext.split("_")[1] == prefix
    assert hash_value == hash_secret(plaintext.split("_", 2)[2])
    assert len(prefix) == 8


def test_gespeicherter_hash_enthaelt_keinen_klartext() -> None:
    """Unabhaengiger Nachweis, ohne hash_geheimnis zur Erwartung zu benutzen.

    Der Test darueber vergleicht den Hash mit dem Ergebnis derselben Funktion, die
    ihn erzeugt hat — er wuerde auch bestehen, wenn diese Funktion das Geheimnis
    bloss auffuellte statt es zu hashen. Deshalb hier die direkte Probe.
    """
    plaintext, _prefix, hash_value = neues_token()
    geheimnis = plaintext.split("_", 2)[2]
    assert geheimnis not in hash_value
    assert plaintext not in hash_value
    assert len(hash_value) == 64
    assert all(zeichen in "0123456789abcdef" for zeichen in hash_value)
