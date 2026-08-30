import json
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.legacy_system import LegacyReading, reading_from_topic

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
    payload = _heizung_topics()[topic]
    result = reading_from_topic(topic, payload)
    if topic in _KONFIGURATIONSTOPICS:
        # Begruendet uebersprungen: kein Thermostat-Pfadsegment.
        assert result is None
        return
    assert result is not None
    assert isinstance(result, LegacyReading)
    assert result.thermostat_id in {1, 2, 3, 4, 5, 9}
    if result.attribut in {"temperatureActual", "temperatureTarget"}:
        assert result.zahl == Decimal(payload)
        assert result.text is None
    else:
        assert result.text == payload
        assert result.zahl is None


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
    result = reading_from_topic(topic, roh)
    assert result == LegacyReading(thermostat_id, attribut, None, zahl)


@pytest.mark.parametrize(
    ("thermostat_id", "value"),
    [(1, "off"), (5, "heat")],
)
def test_thermostat_aktualzustand_wird_als_text_gelesen(thermostat_id: int, value: str) -> None:
    topic = f"heizung/thermostate/{thermostat_id}/thermostatActualState/get"
    result = reading_from_topic(topic, value)
    assert result == LegacyReading(thermostat_id, "thermostatActualState", value, None)


def test_fremdes_topic_praefix_wird_uebersprungen() -> None:
    assert reading_from_topic("zigbee2mqtt/Bad Thermostat", b"{}") is None


def test_set_statt_get_wird_uebersprungen() -> None:
    assert (
        reading_from_topic("heizung/thermostate/1/thermostatActualState/set", "heat") is None
    )


def test_thermostat_refresh_topic_wird_uebersprungen() -> None:
    # `heizung/thermostate/refresh` — kein numerisches Kennungssegment, kein Attribut.
    assert reading_from_topic("heizung/thermostate/refresh", b"force") is None


def test_konfigurationstopic_wird_uebersprungen() -> None:
    assert reading_from_topic("heizung/config/POLLING_RATE/get", "30") is None


def test_unlesbarer_temperaturwert_wird_uebersprungen() -> None:
    assert reading_from_topic("heizung/thermostate/1/temperatureActual/get", "kaputt") is None


def test_unbekanntes_attribut_wird_uebersprungen() -> None:
    assert reading_from_topic("heizung/thermostate/1/unbekannt/get", "irgendwas") is None


def test_nicht_numerische_thermostat_kennung_wird_uebersprungen() -> None:
    assert (
        reading_from_topic("heizung/thermostate/wohnzimmer/thermostatActualState/get", "off")
        is None
    )


def test_unlesbare_nutzlast_wird_uebersprungen() -> None:
    kaputte_bytes = b"\xff\xfe\x00"
    assert (
        reading_from_topic(
            "heizung/thermostate/1/thermostatActualState/get", kaputte_bytes
        )
        is None
    )


def test_bytes_nutzlast_wird_wie_text_ausgewertet() -> None:
    result = reading_from_topic(
        "heizung/thermostate/5/temperatureTarget/get", b"21.0"
    )
    assert result == LegacyReading(5, "temperatureTarget", None, Decimal("21.0"))
