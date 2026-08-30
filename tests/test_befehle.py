"""Befehle, die von aussen auf den eigenen Topics ankommen.

Home Assistant bekommt je Zone einen Thermostat. Wer dort dreht, landet hier -- und darf
dabei genau so viel wie ein Klick in der Oberflaeche, keinen Deut mehr.
"""

from decimal import Decimal

import pytest

from thermoctl.integrations.mqtt.befehle import (
    Befehlsfehler,
    befehls_abonnements,
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


def test_die_abonnements_decken_auch_befehle_mit_unterschluessel_ab() -> None:
    """`+` trifft in MQTT **genau eine** Ebene, nie null und nie zwei.

    Mit nur `.../befehl/+` kaeme `befehl/modus/3` nie an -- die Drehregler je Modus
    haetten in Home Assistant stumm nichts getan.
    """
    muster = befehls_abonnements("thermoctl")
    assert muster == ["thermoctl/zonen/+/befehl/+", "thermoctl/zonen/+/befehl/+/+"]
    assert ist_befehl("thermoctl/zonen/3/befehl/modus/7", "thermoctl")
    assert ist_befehl("thermoctl/zonen/3/befehl/parameter/hysteresis_k", "thermoctl")


def test_die_neuen_befehlsarten_werden_gelesen() -> None:
    boost = zerlegen("thermoctl/zonen/4/befehl/boost", b"boost", "thermoctl")
    assert (boost.art, boost.zone_id) == ("boost", 4)

    modus = zerlegen("thermoctl/zonen/4/befehl/modus/9", b"19.5", "thermoctl")
    assert (modus.art, modus.modus_id, modus.temperatur) == ("modus", 9, Decimal("19.5"))

    parameter = zerlegen(
        "thermoctl/zonen/4/befehl/parameter/hysteresis_k", b"0.4", "thermoctl"
    )
    assert (parameter.art, parameter.parameter, parameter.zahl) == (
        "parameter", "hysteresis_k", Decimal("0.4"),
    )


@pytest.mark.parametrize(
    "topic",
    [
        # Ein Unterschluessel, wo keiner hingehoert: Sonst waere
        # `befehl/sollwert/irgendwas` ein zweiter, ungeprueftder Weg zum selben Ziel.
        "thermoctl/zonen/1/befehl/sollwert/17",
        "thermoctl/zonen/1/befehl/boost/jetzt",
        # Und die Unterschluessel selbst muessen stimmen.
        "thermoctl/zonen/1/befehl/modus/0",
        "thermoctl/zonen/1/befehl/modus/tag",
        "thermoctl/zonen/1/befehl/parameter/Hysterese",
        "thermoctl/zonen/1/befehl/modus",
        "thermoctl/zonen/1/befehl/parameter",
    ],
)
def test_unterschluessel_werden_gepruft(topic: str) -> None:
    with pytest.raises(Befehlsfehler):
        zerlegen(topic, b"21", "thermoctl")


# --- Ausfuehrung in der Anwendung -------------------------------------------


def test_der_sollwert_verstellt_den_geltenden_modus(session) -> None:
    """Der Thermostat in Home Assistant meint den Modus, nicht "die naechsten zwei Stunden".

    Als Uebersteuerung waere der Wert nach dem naechsten Schaltpunkt wieder weg, und der
    Regler spraenge scheinbar von selbst zurueck. Er verstellt deshalb dieselbe Zeile,
    die auch das Thermostat auf der Startseite verstellt.
    """
    from sqlalchemy import select

    from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.zone import ZoneSetpoint

    einstellungen = einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "befehlszone")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id,
            setpoint_mode_id=einstellungen.frost_protection_mode_id,
            temperature_c=Decimal("16.0"),
        )
    )
    session.flush()
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _befehl_ausfuehren(
        session, f"thermoctl/zonen/{zone.id}/befehl/sollwert", b"22.5", umgebung
    )

    geaendert = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == einstellungen.frost_protection_mode_id,
        )
    )
    assert geaendert == Decimal("22.5")
    # Gegenprobe: Es entsteht dabei ausdruecklich *keine* Uebersteuerung mehr.
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()


def test_ein_moduswert_und_ein_regelparameter_kommen_an(session) -> None:
    """Die Drehregler je Modus und je Regelparameter."""
    from sqlalchemy import select

    from tests.hilfen import einstellungen_anlegen, modus_anlegen, quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "reglerzone")
    nacht = modus_anlegen(session, "nacht")
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _befehl_ausfuehren(
        session, f"thermoctl/zonen/{zone.id}/befehl/modus/{nacht.id}", b"17.5", umgebung
    )
    _befehl_ausfuehren(
        session,
        f"thermoctl/zonen/{zone.id}/befehl/parameter/hysteresis_k",
        b"0.4",
        umgebung,
    )

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == nacht.id
        )
    ) == Decimal("17.5")
    assert zone.hysteresis_k == Decimal("0.4")


def test_ein_regelparameter_ausserhalb_der_grenzen_wird_abgewiesen(session, caplog) -> None:
    """Gegenprobe: Der Drehregler in Home Assistant darf nicht mehr duerfen als das
    Formular in der Oberflaeche."""
    import logging

    from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings

    einstellungen_anlegen(session)
    quelle(session, "system")
    zone = zone_anlegen(session, "grenzzone")
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _befehl_ausfuehren(
            session,
            f"thermoctl/zonen/{zone.id}/befehl/parameter/hysteresis_k",
            b"99",
            umgebung,
        )
    assert zone.hysteresis_k is None
    assert "abgelehnt" in caplog.text.lower()


def test_ein_unsinniger_befehl_aendert_nichts(session, caplog) -> None:
    """99 Grad aus Home Assistant sind kein Grund fuer einen Absturz -- die Domaenengrenze
    gilt, und der Grund gehoert ins Protokoll, statt still zu verschwinden."""
    import logging

    from sqlalchemy import select

    from tests.hilfen import einstellungen_anlegen, quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    einstellungen_anlegen(session)
    zone = zone_anlegen(session, "unsinnzone")
    quelle(session, "system")
    einstellungen = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _befehl_ausfuehren(
            session, f"thermoctl/zonen/{zone.id}/befehl/sollwert", b"99", einstellungen
        )
    assert not session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
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


def test_der_boost_knopf_zieht_die_naechste_schaltung_vor(session) -> None:
    """Der Knopf hat keinen Wert, nur ein Ereignis -- die Nutzlast ist gleichgueltig."""
    from datetime import datetime

    from sqlalchemy import select

    from tests.hilfen import einstellungen_anlegen, modus_anlegen, quelle, zone_anlegen
    from thermoctl.app import _befehl_ausfuehren
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    einstellungen_anlegen(session).timezone = "UTC"
    quelle(session, "system")
    zone = zone_anlegen(session, "boostzone")
    nacht = modus_anlegen(session, "nacht")
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=int(datetime.now().isoweekday()),
                minute_of_day=1439, setpoint_mode_id=nacht.id,
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=nacht.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _befehl_ausfuehren(session, f"thermoctl/zonen/{zone.id}/befehl/boost", b"PRESS", umgebung)

    eintrag = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert eintrag.temperature_c == Decimal("18.0")
    # Sie endet an der Schaltung, die sie vorzieht -- nicht irgendwann.
    assert eintrag.ends_at is not None
