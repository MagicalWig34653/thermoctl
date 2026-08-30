"""Befehle, die von aussen auf den eigenen Topics ankommen.

Home Assistant bekommt je Zone einen Thermostat. Wer dort dreht, landet hier -- und darf
dabei genau so viel wie ein Klick in der Oberflaeche, keinen Deut mehr.
"""

from decimal import Decimal

import pytest

from thermoctl.integrations.mqtt.commands import (
    CommandError,
    commands_abonnements,
    ist_command,
    zerlegen,
)


def test_sollwert_wird_gelesen() -> None:
    command = zerlegen("thermoctl/zones/7/command/setpoint", b"21.5", "thermoctl")
    assert command.zone_id == 7
    assert command.temperature == Decimal("21.5")


def test_komma_wird_angenommen() -> None:
    """Nicht jeder Absender schickt einen Punkt -- und ein verworfener Befehl waere hier
    ein Regler, der sich dreht und nichts tut."""
    command = zerlegen("thermoctl/zones/1/command/setpoint", b"20,5", "thermoctl")
    assert command.temperature == Decimal("20.5")


def test_betriebsart_wird_gelesen() -> None:
    command = zerlegen("thermoctl/zones/2/command/operating_mode", b"off", "thermoctl")
    assert command.operating_mode == "off"


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("thermoctl/zones/1/command/setpoint", b"warm bitte"),
        ("thermoctl/zones/1/command/operating_mode", b"gemuetlich"),
        ("thermoctl/zones/1/command/farbe", b"blau"),
        ("thermoctl/zones/0/command/setpoint", b"21"),
    ],
)
def test_unbrauchbares_faellt_durch(topic: str, payload: bytes) -> None:
    with pytest.raises(CommandError):
        zerlegen(topic, payload, "thermoctl")


def test_fremdes_praefix_gehoert_uns_nicht() -> None:
    """Ein Broker traegt mehr als unsere Topics. Eine Nachricht von woanders darf hier
    nichts ausloesen -- auch dann nicht, wenn sie zufaellig passend aussieht."""
    topic = "andereanlage/zones/1/command/setpoint"
    assert not ist_command(topic, "thermoctl")
    with pytest.raises(CommandError):
        zerlegen(topic, b"21", "thermoctl")


def test_zustands_topics_sind_keine_befehle() -> None:
    """Sonst loeste die eigene Veroeffentlichung den eigenen Befehl aus -- eine
    Rueckkopplung, die sich selbst am Leben haelt."""
    assert not ist_command("thermoctl/zones/1/state/setpoint", "thermoctl")


def test_die_abonnements_decken_auch_befehle_mit_unterschluessel_ab() -> None:
    """`+` trifft in MQTT **genau eine** Ebene, nie null und nie zwei.

    Mit nur `.../befehl/+` kaeme `befehl/modus/3` nie an -- die Drehregler je Modus
    haetten in Home Assistant stumm nichts getan.
    """
    pattern = commands_abonnements("thermoctl")
    assert pattern == ["thermoctl/zones/+/command/+", "thermoctl/zones/+/command/+/+"]
    assert ist_command("thermoctl/zones/3/command/mode/7", "thermoctl")
    assert ist_command("thermoctl/zones/3/command/parameter/hysteresis_k", "thermoctl")


def test_die_neuen_befehlsarten_werden_gelesen() -> None:
    boost = zerlegen("thermoctl/zones/4/command/boost", b"boost", "thermoctl")
    assert (boost.kind, boost.zone_id) == ("boost", 4)

    mode = zerlegen("thermoctl/zones/4/command/mode/9", b"19.5", "thermoctl")
    assert (mode.kind, mode.mode_id, mode.temperature) == ("mode", 9, Decimal("19.5"))

    parameter = zerlegen(
        "thermoctl/zones/4/command/parameter/hysteresis_k", b"0.4", "thermoctl"
    )
    assert (parameter.kind, parameter.parameter, parameter.zahl) == (
        "parameter", "hysteresis_k", Decimal("0.4"),
    )


@pytest.mark.parametrize(
    "topic",
    [
        # Ein Unterschluessel, wo keiner hingehoert: Sonst waere
        # `befehl/sollwert/irgendwas` ein zweiter, ungeprueftder Weg zum selben Ziel.
        "thermoctl/zones/1/command/setpoint/17",
        "thermoctl/zones/1/command/boost/jetzt",
        # Und die Unterschluessel selbst muessen stimmen.
        "thermoctl/zones/1/command/mode/0",
        "thermoctl/zones/1/command/mode/tag",
        "thermoctl/zones/1/command/parameter/Hysterese",
        "thermoctl/zones/1/command/mode",
        "thermoctl/zones/1/command/parameter",
    ],
)
def test_unterschluessel_werden_gepruft(topic: str) -> None:
    with pytest.raises(CommandError):
        zerlegen(topic, b"21", "thermoctl")


# --- Ausfuehrung in der Anwendung -------------------------------------------


def test_der_sollwert_verstellt_den_geltenden_modus(session) -> None:
    """Der Thermostat in Home Assistant meint den Modus, nicht "die naechsten zwei Stunden".

    Als Uebersteuerung waere der Wert nach dem naechsten Schaltpunkt wieder weg, und der
    Regler spraenge scheinbar von selbst zurueck. Er verstellt deshalb dieselbe Zeile,
    die auch das Thermostat auf der Startseite verstellt.
    """
    from sqlalchemy import select

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.zone import ZoneSetpoint

    settings = create_settings(session)
    source(session, "system")
    zone = create_zone(session, "befehlszone")
    session.add(
        ZoneSetpoint(
            zone_id=zone.id,
            setpoint_mode_id=settings.frost_protection_mode_id,
            temperature_c=Decimal("16.0"),
        )
    )
    session.flush()
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/setpoint", b"22.5", umgebung
    )

    changed = session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id,
            ZoneSetpoint.setpoint_mode_id == settings.frost_protection_mode_id,
        )
    )
    assert changed == Decimal("22.5")
    # Gegenprobe: Es entsteht dabei ausdruecklich *keine* Uebersteuerung mehr.
    assert not session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).all()


def test_ein_moduswert_und_ein_regelparameter_kommen_an(session) -> None:
    """Die Drehregler je Modus und je Regelparameter."""
    from sqlalchemy import select

    from tests.helpers import create_mode, create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "reglerzone")
    night = create_mode(session, "nacht")
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(
        session, f"thermoctl/zones/{zone.id}/command/mode/{night.id}", b"17.5", umgebung
    )
    _execute_command(
        session,
        f"thermoctl/zones/{zone.id}/command/parameter/hysteresis_k",
        b"0.4",
        umgebung,
    )

    assert session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == night.id
        )
    ) == Decimal("17.5")
    assert zone.hysteresis_k == Decimal("0.4")


def test_ein_regelparameter_ausserhalb_der_grenzen_wird_abgewiesen(session, caplog) -> None:
    """Gegenprobe: Der Drehregler in Home Assistant darf nicht mehr duerfen als das
    Formular in der Oberflaeche."""
    import logging

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    create_settings(session)
    source(session, "system")
    zone = create_zone(session, "grenzzone")
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _execute_command(
            session,
            f"thermoctl/zones/{zone.id}/command/parameter/hysteresis_k",
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

    from tests.helpers import create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session)
    zone = create_zone(session, "unsinnzone")
    source(session, "system")
    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    with caplog.at_level(logging.WARNING):
        _execute_command(
            session, f"thermoctl/zones/{zone.id}/command/setpoint", b"99", settings
        )
    assert not session.scalars(
        select(ZoneSetpoint).where(ZoneSetpoint.zone_id == zone.id)
    ).all()
    assert "abgelehnt" in caplog.text.lower()


def test_ein_befehl_fuer_eine_unbekannte_zone_wird_verworfen(session, caplog) -> None:
    import logging

    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _execute_command(
            session, "thermoctl/zones/999999/command/setpoint", b"21", settings
        )
    assert "unbekannte zone" in caplog.text.lower()


def test_ein_unbrauchbares_topic_wird_verworfen(session, caplog) -> None:
    import logging

    from thermoctl.app import _execute_command
    from thermoctl.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)
    with caplog.at_level(logging.WARNING):
        _execute_command(session, "thermoctl/zones/1/command/farbe", b"blau", settings)
    assert "unbrauchbar" in caplog.text.lower()


def test_der_boost_knopf_zieht_die_naechste_schaltung_vor(session) -> None:
    """Der Knopf hat keinen Wert, nur ein Ereignis -- die Nutzlast ist gleichgueltig."""
    from datetime import datetime

    from sqlalchemy import select

    from tests.helpers import create_mode, create_settings, create_zone, source
    from thermoctl.app import _execute_command
    from thermoctl.config import Settings
    from thermoctl.db.models.override import ZoneOverride
    from thermoctl.db.models.schedule import SchedulePoint
    from thermoctl.db.models.zone import ZoneSetpoint

    create_settings(session).timezone = "UTC"
    source(session, "system")
    zone = create_zone(session, "boostzone")
    night = create_mode(session, "nacht")
    session.add_all(
        [
            SchedulePoint(
                zone_id=zone.id, weekday=int(datetime.now().isoweekday()),
                minute_of_day=1439, setpoint_mode_id=night.id,
            ),
            ZoneSetpoint(
                zone_id=zone.id, setpoint_mode_id=night.id, temperature_c=Decimal("18.0")
            ),
        ]
    )
    session.flush()
    umgebung = Settings(_env_file=None, database_url="sqlite://", secret_key="s" * 32)

    _execute_command(session, f"thermoctl/zones/{zone.id}/command/boost", b"PRESS", umgebung)

    entry = session.scalars(
        select(ZoneOverride).where(ZoneOverride.zone_id == zone.id)
    ).one()
    assert entry.temperature_c == Decimal("18.0")
    # Sie endet an der Schaltung, die sie vorzieht -- nicht irgendwann.
    assert entry.ends_at is not None
