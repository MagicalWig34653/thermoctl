import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from thermoctl.domain.reading import Reading, readings_from_payload

DATENPFAD = Path(__file__).parent / "daten" / "anlage-beispiele.json"
EMPFANGEN_AM = datetime(2035, 1, 2, 3, 4, 5)


def _values(readings: list[Reading]) -> list[tuple[str, Decimal | None, str | None]]:
    return [(b.capability, b.zahl, b.text) for b in readings]


@pytest.mark.parametrize(
    ("device", "expected", "gemessen_am"),
    [
        (
            "Wohnraum Couchlicht",
            [("link_quality", Decimal("102"), None), ("switch", None, "OFF")],
            datetime(2026, 8, 29, 6, 43, 58, 479000),
        ),
        (
            "Küche Esstisch",
            [("link_quality", Decimal("96"), None), ("switch", None, "OFF")],
            datetime(2026, 8, 29, 6, 44, 29, 61000),
        ),
        (
            "Bewegungsmelder außen",
            [
                ("battery", Decimal("100"), None),
                ("illuminance", Decimal("5241"), None),
                ("link_quality", Decimal("99"), None),
                ("occupancy", None, "false"),
                ("temperature", Decimal("18.85"), None),
            ],
            datetime(2026, 8, 29, 6, 43, 50, 547000),
        ),
        (
            "Abstellraum Multisensor",
            [
                ("battery", Decimal("100"), None),
                ("humidity", Decimal("49.7"), None),
                ("link_quality", Decimal("129"), None),
                ("temperature", Decimal("24.3"), None),
            ],
            datetime(2026, 8, 29, 6, 45, 13, 963000),
        ),
        (
            "Zimmer 1 Fernbedienung",
            [
                ("battery", Decimal("10.5"), None),
                ("link_quality", Decimal("144"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 59, 394000),
        ),
        (
            "Bad Steckdose Entfeuchter",
            [
                ("energy", Decimal("28.52"), None),
                ("link_quality", Decimal("45"), None),
                ("power", Decimal("0"), None),
                ("switch", None, "OFF"),
            ],
            datetime(2026, 8, 29, 6, 44, 55, 241000),
        ),
        (
            "Schlafzimmer Multisensor",
            [
                ("battery", Decimal("95.5"), None),
                ("humidity", Decimal("80"), None),
                ("link_quality", Decimal("144"), None),
                ("temperature", Decimal("24.12"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 40, 822000),
        ),
        (
            "Zimmer 3 Multisensor",
            [
                ("battery", Decimal("87"), None),
                ("humidity", Decimal("53.63"), None),
                ("link_quality", Decimal("90"), None),
                ("temperature", Decimal("25.83"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 45, 1000),
        ),
        (
            "Zimmer 3 Pflanzensensor",
            [
                ("battery", Decimal("100"), None),
                ("humidity", Decimal("59"), None),
                ("link_quality", Decimal("132"), None),
                ("soil_moisture", Decimal("24"), None),
                ("temperature", Decimal("26.5"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 57, 891000),
        ),
        (
            "Bewegungsmelder innen",
            [
                ("battery", Decimal("60.5"), None),
                ("illuminance", Decimal("651"), None),
                ("link_quality", Decimal("123"), None),
                ("occupancy", None, "false"),
                ("temperature", Decimal("24.58"), None),
            ],
            datetime(2026, 8, 29, 6, 44, 59, 186000),
        ),
    ],
)
def test_echte_zustandsnachrichten(
    device: str,
    expected: list[tuple[str, Decimal | None, str | None]],
    gemessen_am: datetime,
) -> None:
    daten = json.loads(DATENPFAD.read_text(encoding="utf-8"))

    result = readings_from_payload(
        json.dumps(daten["zustaende"][device]), EMPFANGEN_AM
    )

    assert _values(result) == expected
    assert {b.gemessen_am for b in result} == {gemessen_am}


def test_spannung_wird_weder_als_batteriestand_noch_als_messwert_gedeutet() -> None:
    daten = json.loads(DATENPFAD.read_text(encoding="utf-8"))["zustaende"]

    steckdose = _values(
        readings_from_payload(json.dumps(daten["Bad Steckdose Entfeuchter"]), EMPFANGEN_AM)
    )
    battery_device = _values(
        readings_from_payload(json.dumps(daten["Schlafzimmer Multisensor"]), EMPFANGEN_AM)
    )

    assert ("battery", Decimal("230"), None) not in steckdose
    assert ("battery", Decimal("2900"), None) not in battery_device
    assert all(capability != "voltage" for capability, _number, _text in steckdose + battery_device)


def test_null_objekte_und_unbekanntes_feld_stoeren_bekannten_wert_nicht() -> None:
    payload = json.dumps(
        {
            "temperature": 21.5,
            "battery": None,
            "update": {"state": "idle"},
            "color": {"x": 0.1},
            "neues_feld": 7,
        }
    )

    assert readings_from_payload(payload, EMPFANGEN_AM) == [
        Reading("temperature", Decimal("21.5"), None, EMPFANGEN_AM)
    ]


def test_unlesbares_last_seen_verwendet_empfangszeitpunkt() -> None:
    result = readings_from_payload(
        '{"temperature": 20, "last_seen": "gestern"}', EMPFANGEN_AM
    )

    assert result[0].gemessen_am == EMPFANGEN_AM


@pytest.mark.parametrize("payload", ["", "{kaputt", b"\xff"])
def test_kaputtes_json_wird_protokolliert(
    payload: str | bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="thermoctl.domain.reading"):
        assert readings_from_payload(payload, EMPFANGEN_AM) == []

    assert "kein gueltiges JSON" in caplog.text


def test_bytes_nutzlast_wird_ausgewertet() -> None:
    result = readings_from_payload(b'{"state":"ON"}', EMPFANGEN_AM)

    assert result == [Reading("switch", None, "ON", EMPFANGEN_AM)]


def test_ventil_und_fensterkontakt_werden_ohne_beispieldaten_erkannt() -> None:
    result = readings_from_payload(
        b'{"contact":false,"local_temperature":19.25,"current_heating_setpoint":21}',
        EMPFANGEN_AM,
    )

    assert _values(result) == [
        ("contact", None, "false"),
        ("temperature", Decimal("19.25"), None),
        ("setpoint", Decimal("21"), None),
    ]


def test_last_seen_ohne_zeitzone_wird_verworfen() -> None:
    """Ohne Zeitzonenangabe ist der Zeitstempel nicht in UTC umrechenbar.

    Zigbee2MQTT laesst sich auf Ortszeit ohne Offset einstellen. Ein solcher Wert als UTC
    gedeutet laege im Sommer zwei Stunden daneben — und am Alter des Messwerts haengt die
    Stoerungserkennung. Der Empfangszeitpunkt ist ungenauer, aber nicht falsch.
    """
    empfangen = datetime(2026, 8, 29, 12, 0, 0)
    readings = readings_from_payload(
        json.dumps({"last_seen": "2026-08-29T06:00:00", "temperature": 21.5}), empfangen
    )
    assert [b.gemessen_am for b in readings] == [empfangen]


def test_gueltiges_json_ohne_objekt_ergibt_nichts() -> None:
    """Eine Liste oder ein nackter Wert ist keine Zustandsnachricht, aber auch kein Fehler."""
    empfangen = datetime(2026, 8, 29, 12, 0, 0)
    assert readings_from_payload("[1, 2, 3]", empfangen) == []
    assert readings_from_payload("42", empfangen) == []
