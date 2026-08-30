"""Die Steuerungsseite -- Betriebszustand, Scharfschalten, globale Vorgaben.

Scharfschalten ist die einzige Bedienhandlung im Projekt, die unmittelbar ein Ventil
bewegt. Die Tests hier pruefen deshalb nicht nur, dass sie funktioniert, sondern auch,
dass sie ohne das eigene Recht **nicht** funktioniert -- und dass der Weg zurueck in den
Trockenlauf an nichts scheitert.
"""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone, source
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.domain.control import (
    LIMITS,
    ControlError,
    arm,
    check_zahl,
)

Clientbauer = Callable[[list[tuple[str, int | None]]], TestClient]

ALL_PERMISSIONS: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _defaults(**abweichungen: str) -> dict[str, str]:
    values = {feld: str(LIMITS[feld][0]) for feld in LIMITS}
    values["timezone"] = "Europe/Berlin"
    values.update(abweichungen)
    return values


# --- Domaene ---------------------------------------------------------------


def test_zahl_pruefen_nimmt_das_komma_an() -> None:
    """Auf einer deutschen Tastatur tippt man 0,5 -- das ist keine Fehleingabe."""
    assert check_zahl("default_hysteresis_k", "0,5") == Decimal("0.5")


@pytest.mark.parametrize(
    ("feld", "eingabe"),
    [
        ("default_min_on_seconds", "0"),
        ("default_hysteresis_k", "0"),
        ("shadow_interval_seconds", "0"),
        ("default_min_on_seconds", "99999"),
        ("default_hysteresis_k", "keine Zahl"),
        ("default_min_on_seconds", "60,5"),
        ("polling_interval_seconds", ""),
    ],
)
def test_unbrauchbare_vorgaben_werden_abgewiesen(feld: str, eingabe: str) -> None:
    """Null Sekunden Mindestschaltdauer und null Kelvin Hysterese sind genau der Defekt
    des Altsystems: Takten am Sollwert in jedem Zyklus."""
    with pytest.raises(ControlError) as errors:
        check_zahl(feld, eingabe)
    assert errors.value.feld == feld


def test_scharfschalten_verlangt_eine_begruendung(session: Session) -> None:
    create_settings(session)
    source(session, "web")
    with pytest.raises(ControlError):
        arm(session, True, reason="   ", user_id=None)
    assert session.get(Setting, 1).control_armed is False


def test_zurueck_in_den_trockenlauf_verlangt_keine(session: Session) -> None:
    """Der Weg zurueck ist der, den jemand in Eile geht. Er darf an keiner Formalie
    scheitern."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Schattenlauf geprüft", user_id=None)
    assert arm(session, False, reason="", user_id=None) is True
    assert session.get(Setting, 1).control_armed is False


def test_zweimal_dasselbe_schreibt_keinen_zweiten_eintrag(session: Session) -> None:
    """Sonst steht im Audit-Protokoll eine Scharfschaltung, die gar keine war."""
    create_settings(session)
    source(session, "web")
    assert arm(session, True, reason="erste", user_id=None) is True
    assert arm(session, True, reason="zweite", user_id=None) is False
    entries = list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "arm"))
    )
    assert len(entries) == 1
    assert entries[0].detail == "erste"


# --- Oberflaeche -----------------------------------------------------------


def test_seite_zeigt_den_trockenlauf(client_als: Clientbauer, session: Session) -> None:
    create_settings(session)
    create_zone(session, "bad")
    response = client_als(ALL_PERMISSIONS).get("/control")
    assert response.status_code == 200
    assert "Trockenlauf" in response.text


def test_scharfschalten_ueber_die_oberflaeche(
    client_als: Clientbauer, session: Session
) -> None:
    create_settings(session)
    source(session, "web")
    mandant = client_als(ALL_PERMISSIONS)
    response = mandant.post(
        "/control/arm",
        data={"armed": "ja", "begruendung": "Vier Tage Schattenlauf verglichen"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.get(Setting, 1).control_armed is True
    entry = session.scalars(select(AuditEvent).where(AuditEvent.action == "arm")).one()
    assert entry.detail == "Vier Tage Schattenlauf verglichen"


def test_ohne_control_arm_bleibt_die_anlage_im_trockenlauf(
    client_als: Clientbauer, session: Session
) -> None:
    """`setting.manage` allein reicht nicht. Wer Zeitzone und Aufbewahrungsdauer pflegen
    darf, soll die Heizung nicht nebenbei scharf schalten koennen."""
    create_settings(session)
    mandant = client_als([("zone.read", None), ("setting.manage", None)])
    response = mandant.post(
        "/control/arm",
        data={"armed": "ja", "begruendung": "trotzdem"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert session.get(Setting, 1).control_armed is False


def test_fehlende_begruendung_fuehrt_zurueck_ins_formular(
    client_als: Clientbauer, session: Session
) -> None:
    create_settings(session)
    mandant = client_als(ALL_PERMISSIONS)
    response = mandant.post(
        "/control/arm", data={"armed": "ja", "begruendung": ""},
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    assert "Bitte kurz festhalten" in response.text
    assert session.get(Setting, 1).control_armed is False


def test_vorgaben_speichern(client_als: Clientbauer, session: Session) -> None:
    create_settings(session)
    source(session, "web")
    mandant = client_als(ALL_PERMISSIONS)
    response = mandant.post(
        "/settings",
        data=_defaults(default_hysteresis_k="0,4", shadow_interval_seconds="90"),
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert response.status_code == 303
    zeile = session.get(Setting, 1)
    assert zeile.default_hysteresis_k == Decimal("0.4")
    assert zeile.shadow_interval_seconds == 90


def test_eine_abgelehnte_vorgabe_laesst_nichts_halb_stehen(
    client_als: Clientbauer, session: Session
) -> None:
    """Der Fehler, der die Einrichtung schon einmal halb angelegt hinterlassen hat:
    schreiben, bevor alles geprueft ist."""
    create_settings(session)
    vorher = session.get(Setting, 1).shadow_interval_seconds
    mandant = client_als(ALL_PERMISSIONS)
    response = mandant.post(
        "/settings",
        data=_defaults(shadow_interval_seconds="90", default_min_on_seconds="0"),
        headers=_csrf(mandant),
    )
    assert response.status_code == 200
    session.expire_all()
    assert session.get(Setting, 1).shadow_interval_seconds == vorher


def test_ohne_setting_manage_nur_lesen(client_als: Clientbauer, session: Session) -> None:
    create_settings(session)
    read_only = client_als([("zone.read", None)])
    assert read_only.get("/control").status_code == 200
    assert read_only.get("/settings").status_code == 200
    assert (
        read_only.post(
            "/settings", data=_defaults(), headers=_csrf(read_only)
        ).status_code
        == 403
    )


def test_betriebsseite_nennt_den_zweiten_riegel(
    client_als: Clientbauer, session: Session
) -> None:
    """Scharf entschieden, aber nichts gesendet: Wer diesen Zustand nicht kennt, sucht
    stundenlang den Fehler an der falschen Stelle."""
    create_settings(session)
    source(session, "web")
    arm(session, True, reason="Test", user_id=None)

    page = client_als(ALL_PERMISSIONS).get("/control")
    assert page.status_code == 200
    assert "noch nichts geschaltet" in page.text


def test_im_trockenlauf_steht_der_hinweis_nicht_da(
    client_als: Clientbauer, session: Session
) -> None:
    """Gegenprobe: Ohne sie waere der Test oben auch von einer Fassung erfuellt, die den
    Hinweis immer anzeigt -- und dann steht auf jeder Seite eine Warnung, die niemand
    mehr liest."""
    create_settings(session)
    page = client_als(ALL_PERMISSIONS).get("/control")
    assert "noch nichts geschaltet" not in page.text
