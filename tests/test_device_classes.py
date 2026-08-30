import json
from pathlib import Path

import pytest

from thermoctl.domain.device_classes import (
    DeviceDescription,
    capabilities_from_exposes,
    descriptions_from_bridge_list,
)


def _anlagenname(teil: str) -> str:
    pfad = Path(__file__).parent / "daten" / "anlage-beispiele.json"
    namen = json.loads(pfad.read_text(encoding="utf-8"))["geraete"]
    return next(name for name in namen if teil in name)


def test_ventil_wird_allein_aus_exposes_erkannt() -> None:
    exposes = [
        {
            "type": "climate",
            "features": [
                {"type": "numeric", "property": "current_heating_setpoint"},
                {"type": "numeric", "property": "local_temperature"},
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset({"setpoint", "temperature"})


def test_fensterkontakt_wird_erkannt() -> None:
    exposes = [{"type": "binary", "property": "contact"}]

    assert capabilities_from_exposes(exposes) == frozenset({"contact"})


def test_multisensor_traegt_echten_anlagennamen_und_metadaten() -> None:
    name = _anlagenname("Über Küche")
    payload = json.dumps(
        [
            {
                "friendly_name": name,
                "ieee_address": "aus-der-bridge-liste",
                "type": "EndDevice",
                "definition": {
                    "model": "Modell aus der Bridge",
                    "vendor": "Hersteller aus der Bridge",
                    "exposes": [
                        {"type": "numeric", "property": "temperature"},
                        {"type": "numeric", "property": "humidity"},
                        {"type": "numeric", "property": "battery"},
                        {"type": "numeric", "property": "linkquality"},
                    ],
                },
            }
        ]
    )

    assert descriptions_from_bridge_list(payload) == [
        DeviceDescription(
            name=name,
            adresse="aus-der-bridge-liste",
            modell="Modell aus der Bridge",
            manufacturer="Hersteller aus der Bridge",
            ist_group=False,
            capabilities=frozenset(
                {"temperature", "humidity", "battery", "link_quality"}
            ),
        )
    ]


def test_coordinator_und_bruecke_werden_aussortiert() -> None:
    payload = json.dumps(
        [
            {"friendly_name": "anderer Name", "type": "Coordinator"},
            {"friendly_name": "Coordinator", "type": "Router"},
            {"friendly_name": "bridge", "type": "Router"},
        ]
    )

    assert descriptions_from_bridge_list(payload) == []


def test_gruppe_bleibt_sichtbar_und_ist_markiert() -> None:
    name = _anlagenname("wohnraum")

    [group] = descriptions_from_bridge_list(
        json.dumps([{"friendly_name": name, "definition": {"exposes": []}}])
    )

    assert group.name == name
    assert group.ist_group is True
    assert group.adresse is None


def test_unerkanntes_geraet_hat_leere_beschreibung() -> None:
    name = _anlagenname("Thermostat")

    [device] = descriptions_from_bridge_list(
        json.dumps([{"friendly_name": name, "ieee_address": "aus-der-bridge-liste"}])
    )

    assert device.modell is None
    assert device.manufacturer is None
    assert device.capabilities == frozenset()


def test_leere_geraeteliste_bleibt_leer() -> None:
    assert descriptions_from_bridge_list("[]") == []


def test_kaputtes_json_wird_verstaendlich_gemeldet() -> None:
    with pytest.raises(ValueError, match="kein gueltiges JSON"):
        descriptions_from_bridge_list("[")


def test_json_objekt_statt_liste_wird_abgelehnt() -> None:
    with pytest.raises(ValueError, match="JSON-Liste"):
        descriptions_from_bridge_list("{}")


def test_unvollstaendige_fremde_elemente_werden_toleriert() -> None:
    payload = json.dumps(
        [
            None,
            {"type": "Router"},
            {
                "friendly_name": "aus-der-bridge-liste",
                "ieee_address": "aus-der-bridge-liste",
                "definition": "unbekannt",
            },
        ]
    )

    [device] = descriptions_from_bridge_list(payload)

    assert device.capabilities == frozenset()


def test_features_werden_beliebig_tief_durchlaufen() -> None:
    exposes = [
        {
            "type": "switch",
            "features": [
                {
                    "type": "composite",
                    "features": [
                        {"type": "binary", "property": "state"},
                        {"type": "numeric", "property": "power"},
                    ],
                }
            ],
        }
    ]

    assert capabilities_from_exposes(exposes) == frozenset({"switch", "power"})


def test_unbekannte_und_unstrukturierte_exposes_werden_uebersprungen() -> None:
    exposes: list[dict[str, object]] = [
        {"type": "numeric", "property": "voltage"},
        {"type": "composite", "features": [None, "fremd"]},
    ]

    assert capabilities_from_exposes(exposes) == frozenset()
