import json
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.altsystem import AltsystemBeobachtung, beobachtung_aus_topic

DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"

# Diese drei Topics sind Konfigurationswerte, keine Thermostat-Zustaende — es gibt keine
# Thermostat-Kennung im Pfad, an der sie haengen koennten.
_KONFIGURATIONSTOPICS = frozenset(
    {
        "heizung/config/OFF_TARGET_TEMP/get",
        "heizung/config/POLLING_RATE/get",
        "heizung/config/lastSeen/get",
    }
)


def _heizung_topics() -> dict[str, str]:
    daten = json.loads(DATENPFAD.read_text(encoding="utf-8"))
    topics: dict[str, str] = daten["heizung_topics"]
    return topics


def test_alle_vierzig_topics_vorhanden() -> None:
    # Haelt die Grundannahme des Tests nach: die Beispieldatei hat sich nicht geaendert.
    assert len(_heizung_topics()) == 40


@pytest.mark.parametrize("topic", sorted(_heizung_topics()))
def test_jedes_thermostat_topic_wird_ausgewertet(topic: str) -> None:
    nutzlast = _heizung_topics()[topic]
    ergebnis = beobachtung_aus_topic(topic, nutzlast)
    if topic in _KONFIGURATIONSTOPICS:
        # Begruendet uebersprungen: kein Thermostat-Pfadsegment.
        assert ergebnis is None
        return
    assert ergebnis is not None
    assert isinstance(ergebnis, AltsystemBeobachtung)
    assert ergebnis.thermostat_id in {1, 2, 3, 4, 5, 9}
    if ergebnis.attribut in {"temperatureActual", "temperatureTarget"}:
        assert ergebnis.zahl == Decimal(nutzlast)
        assert ergebnis.text is None
    else:
        assert ergebnis.text == nutzlast
        assert ergebnis.zahl is None


@pytest.mark.parametrize(
    ("thermostat_id", "attribut", "zahl", "roh"),
    [
        (1, "temperatureTarget", Decimal("20.0"), "20.0"),
        (1, "temperatureActual", Decimal("24.58"), "24.58"),
        (5, "temperatureActual", Decimal("19.7"), "19.7"),
    ],
)
def test_temperaturwerte_werden_als_dezimalzahl_gelesen(
    thermostat_id: int, attribut: str, zahl: Decimal, roh: str
) -> None:
    topic = f"heizung/thermostate/{thermostat_id}/{attribut}/get"
    ergebnis = beobachtung_aus_topic(topic, roh)
    assert ergebnis == AltsystemBeobachtung(thermostat_id, attribut, None, zahl)


@pytest.mark.parametrize(
    ("thermostat_id", "wert"),
    [(1, "off"), (5, "heat")],
)
def test_thermostat_aktualzustand_wird_als_text_gelesen(thermostat_id: int, wert: str) -> None:
    topic = f"heizung/thermostate/{thermostat_id}/thermostatActualState/get"
    ergebnis = beobachtung_aus_topic(topic, wert)
    assert ergebnis == AltsystemBeobachtung(thermostat_id, "thermostatActualState", wert, None)


def test_fremdes_topic_praefix_wird_uebersprungen() -> None:
    assert beobachtung_aus_topic("zigbee2mqtt/Bad Thermostat", b"{}") is None


def test_set_statt_get_wird_uebersprungen() -> None:
    assert (
        beobachtung_aus_topic("heizung/thermostate/1/thermostatActualState/set", "heat") is None
    )


def test_thermostat_refresh_topic_wird_uebersprungen() -> None:
    # `heizung/thermostate/refresh` — kein numerisches Kennungssegment, kein Attribut.
    assert beobachtung_aus_topic("heizung/thermostate/refresh", b"force") is None


def test_konfigurationstopic_wird_uebersprungen() -> None:
    assert beobachtung_aus_topic("heizung/config/POLLING_RATE/get", "30") is None


def test_unlesbarer_temperaturwert_wird_uebersprungen() -> None:
    assert beobachtung_aus_topic("heizung/thermostate/1/temperatureActual/get", "kaputt") is None


def test_unbekanntes_attribut_wird_uebersprungen() -> None:
    assert beobachtung_aus_topic("heizung/thermostate/1/unbekannt/get", "irgendwas") is None


def test_nicht_numerische_thermostat_kennung_wird_uebersprungen() -> None:
    assert (
        beobachtung_aus_topic("heizung/thermostate/wohnzimmer/thermostatActualState/get", "off")
        is None
    )


def test_unlesbare_nutzlast_wird_uebersprungen() -> None:
    kaputte_bytes = b"\xff\xfe\x00"
    assert (
        beobachtung_aus_topic(
            "heizung/thermostate/1/thermostatActualState/get", kaputte_bytes
        )
        is None
    )


def test_bytes_nutzlast_wird_wie_text_ausgewertet() -> None:
    ergebnis = beobachtung_aus_topic(
        "heizung/thermostate/5/temperatureTarget/get", b"21.0"
    )
    assert ergebnis == AltsystemBeobachtung(5, "temperatureTarget", None, Decimal("21.0"))
