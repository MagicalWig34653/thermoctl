from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten, betriebsart, quelle
from thermoctl.auth.tokens import token_ausstellen
from thermoctl.db.models.zone import Zone


@pytest.fixture
def token_fuer(session: Session) -> Callable[[list[tuple[str, str | None]]], dict[str, str]]:
    art = betriebsart(session)
    bad = Zone(id=1, name="bad", display_name="Bad", operating_mode_id=art.id)
    andere = Zone(id=2, name="andere", display_name="Andere", operating_mode_id=art.id)
    session.add_all([bad, andere])
    session.flush()
    quelle(session, "api")

    zaehler = 0

    def _token_fuer(rechte: list[tuple[str, str | None]]) -> dict[str, str]:
        nonlocal zaehler
        zaehler += 1
        aufgeloest = [(code, bad.id if zone == "bad" else None) for code, zone in rechte]
        besitzer = benutzer_mit_rechten(session, f"api-{zaehler}", aufgeloest)
        _token, klartext = token_ausstellen(
            session, besitzer, f"test-{zaehler}", aufgeloest, None
        )
        return {"Authorization": f"Bearer {klartext}"}

    return _token_fuer


def test_ohne_token_kein_zugriff(client) -> None:
    assert client.get("/api/v1/zones").status_code == 401


def test_ungueltiges_token_wird_abgewiesen(client) -> None:
    antwort = client.get("/api/v1/zones", headers={"Authorization": "Bearer tctl_x_y"})
    assert antwort.status_code == 401


def test_token_sieht_nur_erlaubte_zonen(client, token_fuer) -> None:
    """visible_zones muss auch hier wirken — sonst leckt die API, was die UI verbirgt."""
    kopf = token_fuer([("zone.read", "bad")])
    namen = [z["name"] for z in client.get("/api/v1/zones", headers=kopf).json()]
    assert namen == ["bad"]


def test_zugriff_auf_fremde_zone_ergibt_404(client, token_fuer) -> None:
    """404 und nicht 403: ein 403 verraet, dass die Zone existiert."""
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/2", headers=kopf).status_code == 404


def test_uebersteuern_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 403


def test_uebersteuern_mit_recht_legt_eintrag_an(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201
    eintrag = session.query(ZoneOverride).one()
    assert eintrag.ends_at is not None  # Dauer wird beim Anlegen ausgerechnet
    assert eintrag.created_by_token_id is not None


def test_api_braucht_kein_csrf_token(client, token_fuer) -> None:
    """Token-Anfragen schicken kein Cookie und sind damit nicht CSRF-gefaehrdet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201


def test_token_hash_erscheint_in_keiner_antwort(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad"), ("token.self", None)])
    assert "token_hash" not in client.get("/api/v1/me", headers=kopf).text
