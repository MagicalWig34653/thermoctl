import pytest
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten, zone_anlegen
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.domain.authz import Forbidden


def test_token_klartext_erscheint_genau_einmal(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "a", [("zone.read", None), ("token.self", None)])
    token, klartext = token_ausstellen(session, nutzer, "HA", [("zone.read", None)], None)
    assert klartext.startswith("tctl_")
    assert klartext not in (token.token_hash, token.prefix)


def test_token_mit_mehr_rechten_als_der_besitzer_wird_abgewiesen(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "b", [("zone.read", None), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Zuviel", [("zone.manage", None)], None)


def test_token_mit_fremder_zone_wird_abgewiesen(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "c", [("zone.read", bad.id), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Fremd", [("zone.read", kueche.id)], None)


def test_benutzerliste_braucht_user_manage(client_als) -> None:
    ohne = client_als([("zone.read", None)])
    assert ohne.get("/benutzer").status_code == 403
    mit = client_als([("user.manage", None)])
    assert mit.get("/benutzer").status_code == 200


def test_passwort_hash_erscheint_in_keiner_ansicht(client_als) -> None:
    antwort = client_als([("user.manage", None)]).get("/benutzer")
    assert "$argon2id$" not in antwort.text
