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

from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.domain.steuerung import (
    GRENZEN,
    Steuerungsfehler,
    scharf_schalten,
    zahl_pruefen,
)

Clientbauer = Callable[[list[tuple[str, int | None]]], TestClient]

ALLE_RECHTE: list[tuple[str, int | None]] = [
    ("zone.read", None),
    ("setting.manage", None),
    ("control.arm", None),
]


def _csrf(client: TestClient) -> dict[str, str]:
    sitzung = client.cookies.get(COOKIE_NAME)
    assert sitzung is not None
    return {CSRF_HEADER: csrf_token(sitzung, get_settings().secret_key.get_secret_value())}


def _vorgaben(**abweichungen: str) -> dict[str, str]:
    werte = {feld: str(GRENZEN[feld][0]) for feld in GRENZEN}
    werte["timezone"] = "Europe/Berlin"
    werte.update(abweichungen)
    return werte


# --- Domaene ---------------------------------------------------------------


def test_zahl_pruefen_nimmt_das_komma_an() -> None:
    """Auf einer deutschen Tastatur tippt man 0,5 -- das ist keine Fehleingabe."""
    assert zahl_pruefen("default_hysteresis_k", "0,5") == Decimal("0.5")


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
    with pytest.raises(Steuerungsfehler) as fehler:
        zahl_pruefen(feld, eingabe)
    assert fehler.value.feld == feld


def test_scharfschalten_verlangt_eine_begruendung(session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    with pytest.raises(Steuerungsfehler):
        scharf_schalten(session, True, begruendung="   ", user_id=None)
    assert session.get(Setting, 1).control_armed is False


def test_zurueck_in_den_trockenlauf_verlangt_keine(session: Session) -> None:
    """Der Weg zurueck ist der, den jemand in Eile geht. Er darf an keiner Formalie
    scheitern."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    scharf_schalten(session, True, begruendung="Schattenlauf geprüft", user_id=None)
    assert scharf_schalten(session, False, begruendung="", user_id=None) is True
    assert session.get(Setting, 1).control_armed is False


def test_zweimal_dasselbe_schreibt_keinen_zweiten_eintrag(session: Session) -> None:
    """Sonst steht im Audit-Protokoll eine Scharfschaltung, die gar keine war."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    assert scharf_schalten(session, True, begruendung="erste", user_id=None) is True
    assert scharf_schalten(session, True, begruendung="zweite", user_id=None) is False
    eintraege = list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "arm"))
    )
    assert len(eintraege) == 1
    assert eintraege[0].detail == "erste"


# --- Oberflaeche -----------------------------------------------------------


def test_seite_zeigt_den_trockenlauf(client_als: Clientbauer, session: Session) -> None:
    einstellungen_anlegen(session)
    zone_anlegen(session, "bad")
    antwort = client_als(ALLE_RECHTE).get("/steuerung")
    assert antwort.status_code == 200
    assert "Trockenlauf" in antwort.text


def test_scharfschalten_ueber_die_oberflaeche(
    client_als: Clientbauer, session: Session
) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    mandant = client_als(ALLE_RECHTE)
    antwort = mandant.post(
        "/steuerung/scharf",
        data={"scharf": "ja", "begruendung": "Vier Tage Schattenlauf verglichen"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    assert session.get(Setting, 1).control_armed is True
    eintrag = session.scalars(select(AuditEvent).where(AuditEvent.action == "arm")).one()
    assert eintrag.detail == "Vier Tage Schattenlauf verglichen"


def test_ohne_control_arm_bleibt_die_anlage_im_trockenlauf(
    client_als: Clientbauer, session: Session
) -> None:
    """`setting.manage` allein reicht nicht. Wer Zeitzone und Aufbewahrungsdauer pflegen
    darf, soll die Heizung nicht nebenbei scharf schalten koennen."""
    einstellungen_anlegen(session)
    mandant = client_als([("zone.read", None), ("setting.manage", None)])
    antwort = mandant.post(
        "/steuerung/scharf",
        data={"scharf": "ja", "begruendung": "trotzdem"},
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert antwort.status_code == 403
    assert session.get(Setting, 1).control_armed is False


def test_fehlende_begruendung_fuehrt_zurueck_ins_formular(
    client_als: Clientbauer, session: Session
) -> None:
    einstellungen_anlegen(session)
    mandant = client_als(ALLE_RECHTE)
    antwort = mandant.post(
        "/steuerung/scharf", data={"scharf": "ja", "begruendung": ""},
        headers=_csrf(mandant),
    )
    assert antwort.status_code == 200
    assert "Bitte kurz festhalten" in antwort.text
    assert session.get(Setting, 1).control_armed is False


def test_vorgaben_speichern(client_als: Clientbauer, session: Session) -> None:
    einstellungen_anlegen(session)
    quelle(session, "web")
    mandant = client_als(ALLE_RECHTE)
    antwort = mandant.post(
        "/einstellungen",
        data=_vorgaben(default_hysteresis_k="0,4", shadow_interval_seconds="90"),
        headers=_csrf(mandant),
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    zeile = session.get(Setting, 1)
    assert zeile.default_hysteresis_k == Decimal("0.4")
    assert zeile.shadow_interval_seconds == 90


def test_eine_abgelehnte_vorgabe_laesst_nichts_halb_stehen(
    client_als: Clientbauer, session: Session
) -> None:
    """Der Fehler, der die Einrichtung schon einmal halb angelegt hinterlassen hat:
    schreiben, bevor alles geprueft ist."""
    einstellungen_anlegen(session)
    vorher = session.get(Setting, 1).shadow_interval_seconds
    mandant = client_als(ALLE_RECHTE)
    antwort = mandant.post(
        "/einstellungen",
        data=_vorgaben(shadow_interval_seconds="90", default_min_on_seconds="0"),
        headers=_csrf(mandant),
    )
    assert antwort.status_code == 200
    session.expire_all()
    assert session.get(Setting, 1).shadow_interval_seconds == vorher


def test_ohne_setting_manage_nur_lesen(client_als: Clientbauer, session: Session) -> None:
    einstellungen_anlegen(session)
    nur_lesen = client_als([("zone.read", None)])
    assert nur_lesen.get("/steuerung").status_code == 200
    assert nur_lesen.get("/einstellungen").status_code == 200
    assert (
        nur_lesen.post(
            "/einstellungen", data=_vorgaben(), headers=_csrf(nur_lesen)
        ).status_code
        == 403
    )


def test_betriebsseite_nennt_den_zweiten_riegel(
    client_als: Clientbauer, session: Session
) -> None:
    """Scharf entschieden, aber nichts gesendet: Wer diesen Zustand nicht kennt, sucht
    stundenlang den Fehler an der falschen Stelle."""
    einstellungen_anlegen(session)
    quelle(session, "web")
    scharf_schalten(session, True, begruendung="Test", user_id=None)

    seite = client_als(ALLE_RECHTE).get("/steuerung")
    assert seite.status_code == 200
    assert "noch nicht gesendet" in seite.text


def test_im_trockenlauf_steht_der_hinweis_nicht_da(
    client_als: Clientbauer, session: Session
) -> None:
    """Gegenprobe: Ohne sie waere der Test oben auch von einer Fassung erfuellt, die den
    Hinweis immer anzeigt -- und dann steht auf jeder Seite eine Warnung, die niemand
    mehr liest."""
    einstellungen_anlegen(session)
    seite = client_als(ALLE_RECHTE).get("/steuerung")
    assert "noch nicht gesendet" not in seite.text
