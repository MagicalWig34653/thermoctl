"""The Homebridge (``mqtt-thing``) section of the Schnittstellen page.

Its whole value rides on one guarantee: the topics shown are the topics the
production MQTT client actually uses. `test_the_rendered_topics_match_publication_py`
is the guard for that -- it must go red the moment someone renames a topic in
`integrations/mqtt/publication.py` (or hand-writes a second copy of one here instead
of calling it), the same failure mode `tests/test_docs_current.py` already guards for
`docs/homebridge.md`.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.config import Settings
from thermoctl.domain.interfaces import (
    HomebridgeZone,
    homebridge_broker_url,
    homebridge_zone_configs,
)
from thermoctl.integrations.mqtt.publication import command_topics, states_topics


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": "sqlite://",
        "secret_key": "s" * 32,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _configs_from_html(text: str) -> dict[int, dict[str, Any]]:
    """Extracts every zone's copyable configuration from the rendered page.

    The template writes each as ``<pre id="hb-config-<id>">...</pre>``; Jinja's
    autoescaping turns every quote in the JSON into an HTML entity (``&#34;``), so
    the match must be unescaped before parsing -- exactly what a browser's
    `textContent` does for the copy button, and exactly what `json.loads` needs here.
    """
    result: dict[int, dict[str, Any]] = {}
    for match in re.finditer(
        r'<pre id="hb-config-(\d+)"[^>]*>(.*?)</pre>', text, re.DOTALL
    ):
        zone_id = int(match.group(1))
        result[zone_id] = json.loads(html.unescape(match.group(2)))
    return result


# --- the domain function itself --------------------------------------------------


def test_broker_url_is_none_without_a_configured_host() -> None:
    assert homebridge_broker_url(_settings()) is None


def test_broker_url_reflects_host_port_and_tls() -> None:
    plain = homebridge_broker_url(_settings(mqtt_host="broker.local", mqtt_port=1883))
    assert plain == "mqtt://broker.local:1883"

    secured = homebridge_broker_url(
        _settings(mqtt_host="broker.local", mqtt_port=8883, mqtt_tls=True)
    )
    assert secured == "mqtts://broker.local:8883"


def test_zone_configs_use_the_zones_display_name_and_id_in_the_topic(
    session: Session,
) -> None:
    zone = create_zone(session, "über küche")
    session.flush()

    configs = homebridge_zone_configs([zone], _settings())
    assert len(configs) == 1
    zone_config = configs[0]
    assert isinstance(zone_config, HomebridgeZone)
    assert zone_config.zone_name == zone.display_name
    payload = json.loads(zone_config.config_json)
    assert payload["name"] == zone.display_name
    assert f"/zones/{zone.id}/" in payload["topics"]["getCurrentTemperature"]["topic"]


def test_zone_configs_use_the_configured_mqtt_prefix(session: Session) -> None:
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    zone_config = homebridge_zone_configs([zone], _settings(mqtt_prefix="eigenesheim"))[0]
    payload = json.loads(zone_config.config_json)
    expected = states_topics(zone.id, "eigenesheim")
    assert payload["topics"]["getCurrentTemperature"]["topic"] == expected.current_temperature
    assert payload["topics"]["getCurrentTemperature"]["topic"].startswith("eigenesheim/")


def test_zone_configs_never_carry_a_real_broker_credential(session: Session) -> None:
    """Homebridge needs its own account, never thermoctl's (principle 2)."""
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    secret = "streng-geheimes-broker-passwort"
    settings = _settings(
        mqtt_host="broker", mqtt_username="thermoctl", mqtt_password=secret
    )
    zone_config = homebridge_zone_configs([zone], settings)[0]
    assert secret not in zone_config.config_json
    payload = json.loads(zone_config.config_json)
    assert payload["username"] == "homebridge"
    assert payload["username"] != "thermoctl"


def test_target_state_topics_carry_no_apply(session: Session) -> None:
    """Regression guard for the bug where `apply` translated HomeKit's numeric
    heating/cooling state (0/1/2/3) even though `mqtt-thing`'s `multiCharacteristic`
    already looks the *list value* up for `setTargetHeatingCoolingState` (not the
    number) and looks the decoded value back up in that same list for
    `getTargetHeatingCoolingState`. An `apply` here that assumes it sees a HomeKit
    number -- as the previous configuration did -- silently breaks both directions:
    `mqtt-thing` aborts the publish on a mismatched set, and drops the read on a
    mismatched get. `heatingCoolingStateValues` alone must carry this translation."""
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    payload = json.loads(homebridge_zone_configs([zone], _settings())[0].config_json)
    assert "apply" not in payload["topics"]["getTargetHeatingCoolingState"]
    assert "apply" not in payload["topics"]["setTargetHeatingCoolingState"]


def test_heating_cooling_state_values_use_thermoctls_own_vocabulary(
    session: Session,
) -> None:
    """`heatingCoolingStateValues` is the one list `mqtt-thing` shares between the
    current and the target heating/cooling state (docs/homebridge.md, "Die
    Wertzuordnung"). It must hold thermoctl's own mode codes at HomeKit's indices
    0/1/3 -- and index 2 (Kühlen) must be something thermoctl's own
    `operating_mode` topic and `OPERATING_MODES` never produce or accept, so a
    stray HomeKit index 2 ends up rejected, not silently reinterpreted."""
    from thermoctl.integrations.mqtt.commands import OPERATING_MODES

    zone = create_zone(session, "wohnzimmer")
    session.flush()

    payload = json.loads(homebridge_zone_configs([zone], _settings())[0].config_json)
    values = payload["heatingCoolingStateValues"]
    assert values[0] == "off"
    assert values[1] == "manual"
    assert values[3] == "auto"
    assert values[2] not in OPERATING_MODES


def test_current_heating_cooling_state_apply_decodes_into_the_shared_vocabulary(
    session: Session,
) -> None:
    """`getCurrentHeatingCoolingState`'s `apply` is the one exception that still
    needs a translation (`would_heat`'s `true`/`false` speaks a different language
    than `operating_mode`) -- but it must decode into `heatingCoolingStateValues`'
    own strings, not into a HomeKit number, and it must call `.toString()` on the
    incoming message: `mqtt-thing` hands `apply` the raw MQTT payload (a Buffer),
    and a bare `message === 'true'` never matches one, regardless of content."""
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    payload = json.loads(homebridge_zone_configs([zone], _settings())[0].config_json)
    apply = payload["topics"]["getCurrentHeatingCoolingState"]["apply"]
    values = payload["heatingCoolingStateValues"]
    assert "message.toString()" in apply
    assert "'manual'" in apply and "manual" == values[1]
    assert "'off'" in apply and "off" == values[0]


def test_current_temperature_apply_skips_an_empty_payload(session: Session) -> None:
    """A zone without a measurement publishes an empty payload on
    `state/current_temperature` (`services/publishing.py::_as_text(None)`).
    `mqtt-thing`'s own float parser turns that into `NaN`, which HAP then rejects
    as a temperature below `minTemperature` -- this is exactly the
    `characteristic was supplied illegal value: number 0 exceeded minimum of 10`
    failure from the field. `apply` must return `undefined` for an empty payload
    so `mqtt-thing` drops the message instead of acting on it."""
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    payload = json.loads(homebridge_zone_configs([zone], _settings())[0].config_json)
    apply = payload["topics"]["getCurrentTemperature"]["apply"]
    assert "undefined" in apply
    assert "message.toString() === ''" in apply


def test_zone_configs_placeholder_broker_when_none_is_configured(
    session: Session,
) -> None:
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    zone_config = homebridge_zone_configs([zone], _settings())[0]
    assert "<broker-adresse>" in json.loads(zone_config.config_json)["url"]


# --- the rendered page -------------------------------------------------------------


def test_the_rendered_topics_match_publication_py(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """The guard: every topic shown must equal what `publication.py` builds for the
    same zone and the same configured prefix -- not a second, hand-written copy."""
    create_settings(session)
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    assert response.status_code == 200

    configs = _configs_from_html(response.text)
    assert zone.id in configs, "Die Zone erscheint nicht im Homebridge-Abschnitt."
    topics = configs[zone.id]["topics"]

    expected_state = states_topics(zone.id, "thermoctl")
    expected_command = command_topics(zone.id, "thermoctl")
    assert topics["getCurrentTemperature"]["topic"] == expected_state.current_temperature
    assert topics["getTargetTemperature"]["topic"] == expected_state.setpoint
    assert topics["setTargetTemperature"]["topic"] == expected_command.setpoint
    assert topics["getCurrentHeatingCoolingState"]["topic"] == expected_state.would_heat
    assert topics["getTargetHeatingCoolingState"]["topic"] == expected_state.operating_mode
    assert topics["setTargetHeatingCoolingState"]["topic"] == expected_command.operating_mode


def test_the_page_names_the_zone_and_carries_placeholder_credentials(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    create_zone(session, "schlafzimmer")
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    assert response.status_code == 200
    assert "Schlafzimmer" in response.text
    assert "mqtt-thing" in response.text
    assert "docs/homebridge.md" in response.text
    assert "docs/mqtt.md" in response.text


def test_details_are_collapsed_by_default(
    angemeldeter_client: TestClient, session: Session
) -> None:
    """With many zones this section must not fill the page with open JSON blocks --
    see the design note in interfaces.html. `<details>` without `open` is closed."""
    create_settings(session)
    for name in ("zone-a", "zone-b", "zone-c"):
        create_zone(session, name)
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    assert response.status_code == 200
    for match in re.finditer(r"<details\b[^>]*>", response.text):
        assert "open" not in match.group(), match.group()


def test_no_zone_visible_without_zone_read(client_als, session: Session) -> None:
    """`setting.manage` alone must not leak zone names -- the same distinction that
    fixed the controller-device page's zone-name leak
    (docs/sicherheitsdurchsicht-2026-09-02.md)."""
    create_settings(session)
    create_zone(session, "verborgene-zone")
    session.flush()

    response = client_als([("setting.manage", None)]).get("/interfaces")
    assert response.status_code == 200
    assert "verborgene-zone" not in response.text.lower()
    assert "Keine Zone sichtbar" in response.text


def test_only_the_zone_read_scoped_zone_is_shown(client_als, session: Session) -> None:
    create_settings(session)
    visible = create_zone(session, "sichtbare-zone")
    hidden = create_zone(session, "verborgene-zone")
    session.flush()

    client = client_als(
        [("setting.manage", None), ("zone.read", visible.id)]
    )
    response = client.get("/interfaces")
    assert response.status_code == 200
    assert visible.display_name in response.text
    assert hidden.display_name not in response.text


def test_global_zone_read_shows_every_zone(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    first = create_zone(session, "erste-zone")
    second = create_zone(session, "zweite-zone")
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    assert response.status_code == 200
    assert first.display_name in response.text
    assert second.display_name in response.text


def test_broker_placeholder_hint_when_mqtt_host_is_not_configured(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    assert response.status_code == 200
    configs = _configs_from_html(response.text)
    assert "<broker-adresse>" in configs[zone.id]["url"]
    assert "THERMOCTL_MQTT_HOST" in response.text


def test_password_field_is_a_placeholder_not_thermoctls_own(
    angemeldeter_client: TestClient, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "wohnzimmer")
    session.flush()

    response = angemeldeter_client.get("/interfaces")
    configs = _configs_from_html(response.text)
    assert configs[zone.id]["username"] == "homebridge"
    assert "<" in configs[zone.id]["password"]
