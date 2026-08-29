import json
from pathlib import Path

import pytest

from thermoctl.domain.geraeteklassen import (
    Geraetebeschreibung,
    beschreibungen_aus_bridge_liste,
    faehigkeiten_aus_exposes,
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

    assert faehigkeiten_aus_exposes(exposes) == frozenset({"setpoint", "temperature"})


def test_fensterkontakt_wird_erkannt() -> None:
    exposes = [{"type": "binary", "property": "contact"}]

    assert faehigkeiten_aus_exposes(exposes) == frozenset({"contact"})


def test_multisensor_traegt_echten_anlagennamen_und_metadaten() -> None:
    name = _anlagenname("Über Küche")
    nutzlast = json.dumps(
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

    assert beschreibungen_aus_bridge_liste(nutzlast) == [
        Geraetebeschreibung(
            name=name,
            adresse="aus-der-bridge-liste",
            modell="Modell aus der Bridge",
            hersteller="Hersteller aus der Bridge",
            ist_gruppe=False,
            faehigkeiten=frozenset(
                {"temperature", "humidity", "battery", "link_quality"}
            ),
        )
    ]


def test_coordinator_und_bruecke_werden_aussortiert() -> None:
    nutzlast = json.dumps(
        [
            {"friendly_name": "anderer Name", "type": "Coordinator"},
            {"friendly_name": "Coordinator", "type": "Router"},
            {"friendly_name": "bridge", "type": "Router"},
        ]
    )

    assert beschreibungen_aus_bridge_liste(nutzlast) == []


def test_gruppe_bleibt_sichtbar_und_ist_markiert() -> None:
    name = _anlagenname("wohnraum")

    [gruppe] = beschreibungen_aus_bridge_liste(
        json.dumps([{"friendly_name": name, "definition": {"exposes": []}}])
    )

    assert gruppe.name == name
    assert gruppe.ist_gruppe is True
    assert gruppe.adresse is None


def test_unerkanntes_geraet_hat_leere_beschreibung() -> None:
    name = _anlagenname("Thermostat")

    [geraet] = beschreibungen_aus_bridge_liste(
        json.dumps([{"friendly_name": name, "ieee_address": "aus-der-bridge-liste"}])
    )

    assert geraet.modell is None
    assert geraet.hersteller is None
    assert geraet.faehigkeiten == frozenset()


def test_leere_geraeteliste_bleibt_leer() -> None:
    assert beschreibungen_aus_bridge_liste("[]") == []


def test_kaputtes_json_wird_verstaendlich_gemeldet() -> None:
    with pytest.raises(ValueError, match="kein gueltiges JSON"):
        beschreibungen_aus_bridge_liste("[")


def test_json_objekt_statt_liste_wird_abgelehnt() -> None:
    with pytest.raises(ValueError, match="JSON-Liste"):
        beschreibungen_aus_bridge_liste("{}")


def test_unvollstaendige_fremde_elemente_werden_toleriert() -> None:
    nutzlast = json.dumps(
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

    [geraet] = beschreibungen_aus_bridge_liste(nutzlast)

    assert geraet.faehigkeiten == frozenset()


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

    assert faehigkeiten_aus_exposes(exposes) == frozenset({"switch", "power"})


def test_unbekannte_und_unstrukturierte_exposes_werden_uebersprungen() -> None:
    exposes: list[dict[str, object]] = [
        {"type": "numeric", "property": "voltage"},
        {"type": "composite", "features": [None, "fremd"]},
    ]

    assert faehigkeiten_aus_exposes(exposes) == frozenset()
