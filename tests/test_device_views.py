import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_state,
    create_settings,
    create_zone,
    role,
)
from thermoctl.db.base import utcnow
from thermoctl.db.models.device import DeviceCapabilityLink, ZoneDevice
from thermoctl.db.models.lookup import DeviceCapability


def test_the_device_page_needs_device_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/devices").status_code == 403


def test_an_empty_device_page_explains_the_normal_case(client_als) -> None:
    response = client_als([("device.read", None)]).get("/devices")
    assert response.status_code == 200
    assert "MQTT nicht eingerichtet" in response.text
    assert "Gerätenachricht eingegangen" in response.text


def test_the_device_page_shows_signs_of_life_capability_and_zone(
    client_als, session: Session
) -> None:
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    device = create_device(session, beispiele["geraete"][1])
    device.display_name = "Prüfsensor"
    device.model = "Modell A"
    device.is_group = True
    zone = create_zone(session, "pruefzone")
    zone.temperature_source_device_id = device.id
    second_zone = create_zone(session, "zweite-pruefzone")
    session.add(
        ZoneDevice(
            zone_id=second_zone.id,
            device_id=device.id,
            device_role_id=role(session, "controller").id,
        )
    )
    capability = DeviceCapability(code="temperature", label="Temperaturmessung")
    session.add(capability)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=device.id, capability_id=capability.id))
    state = create_device_state(session, device)
    state.battery_percent = Decimal("71.50")
    state.link_quality = 88
    state.availability = "online"
    session.flush()

    response = client_als([("device.read", None)]).get("/devices")

    assert response.status_code == 200
    for expected in (
        "Prüfsensor",
        "Modell A",
        "Temperaturmessung",
        "Pruefzone",
        "Zweite-pruefzone",
        "Gruppe",
    ):
        assert expected in response.text
    # Rounded: a hundredth of a percent of battery is noise, and the number is here
    # so you can see whether a cell will soon need replacing.
    assert "72 %" in response.text
    assert "71.50" not in response.text
    assert "LQI 88" in response.text
    # Availability is only shown when it is a problem. A column that says "online"
    # for every healthy device carries no information -- it buries the two rows
    # that actually matter.
    assert "die Brücke führt es als offline" not in response.text


def test_the_start_page_shows_zone_state_without_a_zero_temperature(
    client_als, session: Session
) -> None:
    from thermoctl.db.models.lookup import SensorStatus
    from thermoctl.db.models.state import ZoneState

    create_settings(session)
    zone_with_value = create_zone(session, "mit-wert")
    zone_without_value = create_zone(session, "ohne-wert")
    zone_without_state = create_zone(session, "ohne-zustand")
    status = SensorStatus(code="ok", label="In Ordnung")
    session.add(status)
    session.flush()
    moment = datetime(2026, 8, 29, 8, 15)
    session.add_all(
        [
            ZoneState(
                zone_id=zone_with_value.id,
                temperature_c=Decimal("20.25"),
                measured_at=moment,
                sensor_status_id=status.id,
                updated_at=moment,
            ),
            ZoneState(
                zone_id=zone_without_value.id,
                temperature_c=None,
                measured_at=None,
                sensor_status_id=status.id,
                updated_at=moment,
            ),
        ]
    )
    session.flush()

    response = client_als([("zone.read", None)]).get("/")

    assert response.status_code == 200
    # Rounded to one decimal place: a living room is not determined to a hundredth
    # of a degree, and the second digit is noise in the page's most important value.
    assert "20,2" in response.text
    assert "20.25" not in response.text
    assert "20,25" not in response.text
    # Both cases -- a state row without a value and no state row at all -- tell the
    # reader the same thing and therefore appear the same way.
    assert response.text.count("kein Messwert") == 2
    assert zone_without_state.display_name in response.text


def test_age_in_words_answers_the_question_of_freshness() -> None:
    """A raw timestamp with microseconds demands mental arithmetic.

    But for a heating control system the question is always: fresh or stale?
    That is why the overview shows the age, and the timestamp only in the tooltip.
    """
    from datetime import datetime, timedelta

    from thermoctl.web import age_in_words

    now = datetime(2026, 8, 29, 12, 0, 0)
    assert age_in_words(None) == "noch nie"
    assert age_in_words(now - timedelta(seconds=1), now) == "vor 1 Sekunde"
    assert age_in_words(now - timedelta(seconds=42), now) == "vor 42 Sekunden"
    assert age_in_words(now - timedelta(minutes=1), now) == "vor 1 Minute"
    assert age_in_words(now - timedelta(minutes=59), now) == "vor 59 Minuten"
    assert age_in_words(now - timedelta(hours=3), now) == "vor 3 Stunden"
    assert age_in_words(now - timedelta(days=1), now) == "vor 1 Tag"
    assert age_in_words(now - timedelta(days=9), now) == "vor 9 Tagen"
    # A slightly misconfigured sensor must not display 'in -3 minutes'.
    assert age_in_words(now + timedelta(minutes=3), now) == "gerade eben"
    # Without an explicit now-moment, the project's clock takes over.
    assert age_in_words(datetime(2000, 1, 1)).endswith("Tagen")


def test_the_device_page_puts_conspicuous_devices_on_top(client_als, session: Session) -> None:
    """The counter-check to the rounding above: what is wrong must actually be shown.

    Two devices, one healthy and alphabetically first, one with an empty battery and
    reported as offline by the bridge. The page is only useful once the second one
    sits on top and carries its finding in plain words.
    """
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    gesund = create_device(session, beispiele["geraete"][0])
    gesund.display_name = "Alpha gesund"
    healthy_state = create_device_state(session, gesund)
    healthy_state.last_payload_at = utcnow()
    healthy_state.availability = "online"

    krank = create_device(session, beispiele["geraete"][1])
    krank.display_name = "Zulu krank"
    unhealthy_state = create_device_state(session, krank)
    unhealthy_state.last_payload_at = utcnow()
    unhealthy_state.battery_percent = Decimal("7.00")
    unhealthy_state.availability = "offline"
    session.flush()

    text = client_als([("device.read", None)]).get("/devices").text

    assert "Batterie bei 7 %" in text
    assert "die Brücke führt es als offline" in text
    assert text.index("Zulu krank") < text.index("Alpha gesund")
    assert "1 davon fällt auf" in text


def test_battery_and_radio_appear_as_a_number_not_as_a_chip(
    client_als, session: Session
) -> None:
    """A "battery level" chip next to "58 %" says nothing the number does not already say.

    The counter-check is in the second part: whoever suppresses the chips must not
    turn that into "no capabilities reported". A remote control button that reports
    exactly battery and radio quality can do something -- it just already shows on
    the right.
    """
    beispiele = json.loads(
        (Path(__file__).parent / "daten/anlage-beispiele.json").read_text(encoding="utf-8")
    )
    only_battery = create_device(session, beispiele["geraete"][0])
    only_battery.display_name = "Nur Batterie"
    silent = create_device(session, beispiele["geraete"][1])
    silent.display_name = "Meldet nichts"
    for moment in (only_battery, silent):
        state = create_device_state(session, moment)
        state.last_payload_at = utcnow()
    battery = DeviceCapability(code="battery", label="Batteriestand")
    session.add(battery)
    session.flush()
    session.add(DeviceCapabilityLink(device_id=only_battery.id, capability_id=battery.id))
    session.flush()

    text = client_als([("device.read", None)]).get("/devices").text

    assert "Batteriestand" not in text
    # Exactly once -- for the device without any capability, not for the one with battery.
    assert text.count("keine Fähigkeiten gemeldet") == 1
