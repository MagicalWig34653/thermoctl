"""Befehle, die von aussen auf den eigenen Topics ankommen.

Home Assistant bekommt je Zone einen Thermostat. Wer dort dreht, landet hier -- und darf
dabei genau so viel wie ein Klick in der Oberflaeche, keinen Deut mehr.
"""

from decimal import Decimal

import pytest

from thermoctl.integrations.mqtt.befehle import (
    Befehlsfehler,
    befehls_abonnement,
    ist_befehl,
    zerlegen,
)


def test_sollwert_wird_gelesen() -> None:
    befehl = zerlegen("thermoctl/zonen/7/befehl/sollwert", b"21.5", "thermoctl")
    assert befehl.zone_id == 7
    assert befehl.temperatur == Decimal("21.5")


def test_komma_wird_angenommen() -> None:
    """Nicht jeder Absender schickt einen Punkt -- und ein verworfener Befehl waere hier
    ein Regler, der sich dreht und nichts tut."""
    befehl = zerlegen("thermoctl/zonen/1/befehl/sollwert", b"20,5", "thermoctl")
    assert befehl.temperatur == Decimal("20.5")


def test_betriebsart_wird_gelesen() -> None:
    befehl = zerlegen("thermoctl/zonen/2/befehl/betriebsart", b"off", "thermoctl")
    assert befehl.betriebsart == "off"


@pytest.mark.parametrize(
    ("topic", "nutzlast"),
    [
        ("thermoctl/zonen/1/befehl/sollwert", b"warm bitte"),
        ("thermoctl/zonen/1/befehl/betriebsart", b"gemuetlich"),
        ("thermoctl/zonen/1/befehl/farbe", b"blau"),
        ("thermoctl/zonen/0/befehl/sollwert", b"21"),
    ],
)
def test_unbrauchbares_faellt_durch(topic: str, nutzlast: bytes) -> None:
    with pytest.raises(Befehlsfehler):
        zerlegen(topic, nutzlast, "thermoctl")


def test_fremdes_praefix_gehoert_uns_nicht() -> None:
    """Ein Broker traegt mehr als unsere Topics. Eine Nachricht von woanders darf hier
    nichts ausloesen -- auch dann nicht, wenn sie zufaellig passend aussieht."""
    topic = "andereanlage/zonen/1/befehl/sollwert"
    assert not ist_befehl(topic, "thermoctl")
    with pytest.raises(Befehlsfehler):
        zerlegen(topic, b"21", "thermoctl")


def test_zustands_topics_sind_keine_befehle() -> None:
    """Sonst loeste die eigene Veroeffentlichung den eigenen Befehl aus -- eine
    Rueckkopplung, die sich selbst am Leben haelt."""
    assert not ist_befehl("thermoctl/zonen/1/zustand/sollwert", "thermoctl")


def test_das_abonnement_deckt_beide_arten_ab() -> None:
    muster = befehls_abonnement("thermoctl")
    assert muster == "thermoctl/zonen/+/befehl/+"


# --- Ausfuehrung in der Anwendung -------------------------------------------


def test_ein_befehl_legt_eine_uebersteuerung_an(session) -> None:
    """Der Thermostat in Home Assistant setzt eine Zieltemperatur. Hier wird daraus das,
    was die Domaene fuer diesen Wunsch vorsieht -- mit denselben Grenzen wie jeder
    andere Weg."""
    from sqlalchemy import select

    from tests.hilfen import quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride

    zone = zone_anlegen(session, "befehlszone")
    quelle(session, "system")
    einstellungen = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _befehl_ausfuehren(
        session, f"thermoctl/zonen/{zone.id}/befehl/sollwert", b"22.5", einstellungen
    )
    eintrag = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert eintrag.temperature_c == Decimal("22.5")


def test_ein_unsinniger_befehl_aendert_nichts(session, caplog) -> None:
    """99 Grad aus Home Assistant sind kein Grund fuer einen Absturz -- die Domaenengrenze
    gilt, und der Grund gehoert ins Protokoll, statt still zu verschwinden."""
    import logging

    from sqlalchemy import select

    from tests.hilfen import quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride

    zone = zone_anlegen(session, "unsinnzone")
    quelle(session, "system")
    einstellungen = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _befehl_ausfuehren(
            session, f"thermoctl/zonen/{zone.id}/befehl/sollwert", b"99", einstellungen
        )
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()
    assert "abgelehnt" in caplog.text.lower()


def test_ein_befehl_fuer_eine_unbekannte_zone_wird_verworfen(session, caplog) -> None:
    import logging

    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings

    einstellungen = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _befehl_ausfuehren(
            session, "thermoctl/zonen/999999/befehl/sollwert", b"21", einstellungen
        )
    assert "unbekannte zone" in caplog.text.lower()


def test_ein_unbrauchbares_topic_wird_verworfen(session, caplog) -> None:
    import logging

    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings

    einstellungen = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _befehl_ausfuehren(session, "thermoctl/zonen/1/befehl/farbe", b"blau", einstellungen)
    assert "unbrauchbar" in caplog.text.lower()
