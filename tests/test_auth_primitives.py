import pytest

from thermoctl.auth.passwords import (
    MIN_PASSWORT_LAENGE,
    PasswordTooShort,
    hash_password,
    verify_password,
)
from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis, neues_token


def test_hash_ist_kein_klartext() -> None:
    wert = hash_password("ein-sehr-gutes-passwort")
    assert "ein-sehr-gutes-passwort" not in wert
    assert wert.startswith("$argon2id$")


def test_gleiches_passwort_ergibt_verschiedene_hashes() -> None:
    """Argon2 salzt selbst; zwei gleiche Passwoerter duerfen nicht gleich aussehen."""
    assert hash_password("passwort-genug-lang") != hash_password("passwort-genug-lang")


def test_pruefung_erkennt_richtig_und_falsch() -> None:
    wert = hash_password("passwort-genug-lang")
    assert verify_password("passwort-genug-lang", wert) is True
    assert verify_password("etwas-anderes-langes", wert) is False


def test_zu_kurzes_passwort_wird_abgewiesen() -> None:
    with pytest.raises(PasswordTooShort):
        hash_password("a" * (MIN_PASSWORT_LAENGE - 1))


def test_pruefung_gegen_unsinnigen_hash_wirft_nicht() -> None:
    assert verify_password("egal-welches-passwort", "kein-gueltiger-hash") is False


def test_geheimnis_ist_lang_genug_und_jedes_mal_neu() -> None:
    werte = {neues_geheimnis() for _ in range(100)}
    assert len(werte) == 100
    assert all(len(w) >= 43 for w in werte)  # 256 Bit base64url


def test_geheimnis_hash_ist_stabil_und_sechzig_vier_zeichen() -> None:
    g = neues_geheimnis()
    assert hash_geheimnis(g) == hash_geheimnis(g)
    assert len(hash_geheimnis(g)) == 64


def test_token_hat_erwartete_form() -> None:
    klartext, prefix, hash_wert = neues_token()
    assert klartext.startswith("tctl_")
    assert klartext.split("_")[1] == prefix
    assert hash_wert == hash_geheimnis(klartext.split("_", 2)[2])
    assert len(prefix) == 8


def test_gespeicherter_hash_enthaelt_keinen_klartext() -> None:
    """Unabhaengiger Nachweis, ohne hash_geheimnis zur Erwartung zu benutzen.

    Der Test darueber vergleicht den Hash mit dem Ergebnis derselben Funktion, die
    ihn erzeugt hat — er wuerde auch bestehen, wenn diese Funktion das Geheimnis
    bloss auffuellte statt es zu hashen. Deshalb hier die direkte Probe.
    """
    klartext, _prefix, hash_wert = neues_token()
    geheimnis = klartext.split("_", 2)[2]
    assert geheimnis not in hash_wert
    assert klartext not in hash_wert
    assert len(hash_wert) == 64
    assert all(zeichen in "0123456789abcdef" for zeichen in hash_wert)
