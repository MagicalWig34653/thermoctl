"""Den eigenen Zustand veröffentlichen — und die Zonen bei Home Assistant anmelden.

Der Vertrag stand seit Teilprojekt 2 vollständig da (`integrations/mqtt/veroeffentlichung.py`:
Topics, Discovery-Nutzlast, An- und Abmeldung, mit Tests). Was fehlte, war der Aufrufer.

**Er sitzt hinter demselben Riegel wie das Schalten.** Solange die Anlage im Trockenlauf
läuft, wird nichts angemeldet und nichts veröffentlicht. Das ist kein Übermaß an Vorsicht,
sondern die einzige ehrliche Möglichkeit: Eine Zone, die sich in Home Assistant als
Thermostat anmeldet, bekommt dort einen Regler, den man drehen kann, und eine Anzeige
„heizt". Beides wäre im Trockenlauf gelogen — in einer fremden Oberfläche, in der niemand
nachsehen würde, warum.

Beim Zurücknehmen in den Trockenlauf werden die Zonen wieder **abgemeldet**. Ohne das
bliebe in Home Assistant ein Thermostat stehen, der niemandem mehr gehört; er zeigte den
letzten bekannten Wert für immer weiter.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.zone import Zone
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.schedule import aufgeloester_sollwert
from thermoctl.integrations.aktoren import MqttVeroeffentlicher, schalten_erlaubt
from thermoctl.integrations.mqtt.veroeffentlichung import (
    discovery_abmeldung,
    discovery_anmeldung,
    verfuegbarkeits_topic,
    zustands_topics,
)

log = logging.getLogger(__name__)


@dataclass
class Veroeffentlichungsstand:
    """Welche Zonen gerade angemeldet sind.

    Der Stand lebt im Prozess, nicht in der Datenbank: Er beschreibt, was *dieser* Lauf
    gesendet hat. Nach einem Neustart ist er leer, und der erste scharfe Zyklus meldet
    alles neu an — was richtig ist, denn ob die Nachrichten von damals noch beim Broker
    liegen, weiß niemand.
    """

    angemeldet: set[int]

    def __init__(self) -> None:
        self.angemeldet = set()


def _als_text(wert: object) -> str:
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "true" if wert else "false"
    return str(wert)


async def zyklus(
    session: Session,
    client: MqttVeroeffentlicher,
    stand: Veroeffentlichungsstand,
    praefix: str,
    jetzt: datetime,
) -> int:
    """Ein Veröffentlichungszyklus. Gibt die Zahl der gesendeten Nachrichten zurück.

    Im Trockenlauf meldet er ab, was angemeldet war, und sendet sonst nichts.
    """
    scharf = schalten_erlaubt(session)
    if not scharf:
        return await _abmelden(client, stand, praefix)

    zonen = list(session.scalars(select(Zone).order_by(Zone.id)))
    gesendet = 0

    # Verfügbarkeit zuerst: Sie ist die Aussage „was gleich kommt, ist aktuell".
    if await client.veroeffentlichen(
        verfuegbarkeits_topic(praefix), "online", scharf=True
    ):
        gesendet += 1

    vorhandene = {zone.id for zone in zonen}
    for zone in zonen:
        if zone.id not in stand.angemeldet:
            nachricht = discovery_anmeldung(zone.id, zone.display_name, praefix=praefix)
            if await client.veroeffentlichen(
                nachricht.topic, nachricht.nutzlast, scharf=True
            ):
                stand.angemeldet.add(zone.id)
                gesendet += 1
                log.info(
                    "Zone bei Home Assistant angemeldet",
                    extra={"zone_id": zone.id, "topic": nachricht.topic},
                )

    # Zonen, die es nicht mehr gibt: abmelden, sonst bleibt in Home Assistant ein
    # Thermostat stehen, den niemand mehr bedient.
    for zone_id in sorted(stand.angemeldet - vorhandene):
        nachricht = discovery_abmeldung(zone_id, praefix)
        if await client.veroeffentlichen(nachricht.topic, nachricht.nutzlast, scharf=True):
            stand.angemeldet.discard(zone_id)
            gesendet += 1

    gesendet += await _zustaende_senden(session, client, zonen, praefix, jetzt)
    return gesendet


async def _zustaende_senden(
    session: Session,
    client: MqttVeroeffentlicher,
    zonen: list[Zone],
    praefix: str,
    jetzt: datetime,
) -> int:
    zustaende = {
        zone_id: (temperatur, statuscode)
        for zone_id, temperatur, statuscode in session.execute(
            select(ZoneState.zone_id, ZoneState.temperature_c, SensorStatus.code).join(
                SensorStatus, SensorStatus.id == ZoneState.sensor_status_id
            )
        )
    }
    entscheidungen: dict[int, bool] = {}
    for zone_id, heizt in session.execute(
        select(ShadowDecision.zone_id, ShadowDecision.would_heat).order_by(
            ShadowDecision.decided_at.desc(), ShadowDecision.id.desc()
        )
    ):
        entscheidungen.setdefault(zone_id, heizt)

    gesendet = 0
    for zone in zonen:
        topics = zustands_topics(zone.id, praefix)
        temperatur, statuscode = zustaende.get(zone.id, (None, "keine_quelle"))
        sollwert = aufgeloester_sollwert(session, zone, jetzt)
        werte = (
            (topics.ist_temperatur, _als_text(temperatur)),
            (topics.sollwert, _als_text(sollwert.temperature_c)),
            (topics.betriebsart, zone.operating_mode.code),
            (topics.sensorzustand, statuscode),
            (topics.wuerde_heizen, _als_text(entscheidungen.get(zone.id))),
        )
        for topic, wert in werte:
            # Ein leerer Wert wird nicht gesendet: In MQTT loescht eine leere Nutzlast
            # eine behaltene Nachricht, und „noch kein Messwert" ist etwas anderes als
            # „diesen Wert gibt es nicht mehr".
            if wert and await client.veroeffentlichen(topic, wert, scharf=True):
                gesendet += 1
    return gesendet


async def _abmelden(
    client: MqttVeroeffentlicher, stand: Veroeffentlichungsstand, praefix: str
) -> int:
    """Nimmt zurück, was dieser Lauf angemeldet hat."""
    if not stand.angemeldet:
        return 0
    gesendet = 0
    for zone_id in sorted(stand.angemeldet):
        nachricht = discovery_abmeldung(zone_id, praefix)
        if await client.veroeffentlichen(nachricht.topic, nachricht.nutzlast, scharf=True):
            gesendet += 1
    if await client.veroeffentlichen(
        verfuegbarkeits_topic(praefix), "offline", scharf=True
    ):
        gesendet += 1
    log.info(
        "Zonen bei Home Assistant abgemeldet — die Anlage ist im Trockenlauf",
        extra={"anzahl": len(stand.angemeldet)},
    )
    stand.angemeldet.clear()
    return gesendet
