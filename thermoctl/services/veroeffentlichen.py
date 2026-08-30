"""Den eigenen Zustand veröffentlichen — und die Zonen bei Home Assistant anmelden.

Der Vertrag stand seit Teilprojekt 2 vollständig da (`integrations/mqtt/veroeffentlichung.py`:
Topics, Discovery-Nutzlast, An- und Abmeldung, mit Tests). Hier ist der Aufrufer.

**Das läuft auch im Trockenlauf** — und zwar mit Absicht. Eine Zustandsmeldung bewegt
nichts, und eine Anbindung, die man erst nach dem Scharfschalten ausprobieren kann, lässt
sich genau dann nicht mehr gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre. Wer die
Anlage in Home Assistant einrichten, den Thermostat drehen und nachsehen will, ob der
Sollwert ankommt, soll das vorher tun können.

Gelogen wird dabei nicht: Solange die Regelung nicht scharf ist, trägt jede Zone in ihrem
Namen ein `(Trockenlauf)`. Das steht in Home Assistant an jeder Karte, und es verschwindet,
sobald wirklich geschaltet wird. Bewegt wird trotzdem nichts — dafür sorgen die beiden
Riegel im Aktorpfad, an denen sich nichts geändert hat.

**Abgemeldet wird nur, was es nicht mehr gibt.** Eine gelöschte Zone bekommt die leere
Nutzlast auf ihrem Config-Topic; sonst bliebe in Home Assistant ein Thermostat stehen, der
niemandem mehr gehört. Der Wechsel zurück in den Trockenlauf meldet dagegen **nicht** ab —
er benennt nur um. Abmelden und Neuanmelden bei jedem Umschalten würde die Entität in Home
Assistant kurz verschwinden lassen und dort Verlaufsdaten und Automatisierungen ins Leere
laufen lassen.
"""

import logging
from dataclasses import dataclass, field
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

# Was im Namen jeder Zone steht, solange nicht wirklich geschaltet wird. In Home Assistant
# ist der Name das Einzige, was an jeder Karte sichtbar ist -- eine Notiz an anderer Stelle
# läse dort niemand.
TROCKENLAUF_ZUSATZ = " (Trockenlauf)"


@dataclass
class Veroeffentlichungsstand:
    """Welche Zonen angemeldet sind, und unter welchem Betriebszustand.

    Der Stand lebt im Prozess, nicht in der Datenbank: Er beschreibt, was *dieser* Lauf
    gesendet hat. Nach einem Neustart ist er leer, und der erste Zyklus meldet alles neu
    an — was richtig ist, denn ob die Nachrichten von damals noch beim Broker liegen,
    weiß niemand.
    """

    angemeldet: set[int] = field(default_factory=set)
    # Unter welchem Betriebszustand die Anmeldung hinausging. Wechselt er, muss die
    # Anmeldung erneuert werden -- sonst trüge die Zone in Home Assistant für immer den
    # Namen von damals.
    angemeldet_scharf: bool | None = None


def _als_text(wert: object) -> str:
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "true" if wert else "false"
    return str(wert)


def anzeigename(zone: Zone, scharf: bool) -> str:
    return zone.display_name if scharf else zone.display_name + TROCKENLAUF_ZUSATZ


async def zyklus(
    session: Session,
    client: MqttVeroeffentlicher,
    stand: Veroeffentlichungsstand,
    praefix: str,
    jetzt: datetime,
) -> int:
    """Ein Veröffentlichungszyklus. Gibt die Zahl der gesendeten Nachrichten zurück."""
    scharf = schalten_erlaubt(session)
    zonen = list(session.scalars(select(Zone).order_by(Zone.id)))
    gesendet = 0

    # Verfügbarkeit zuerst: Sie ist die Aussage „was gleich kommt, ist aktuell".
    if await client.veroeffentlichen(
        verfuegbarkeits_topic(praefix), "online", schaltet=False
    ):
        gesendet += 1

    # Beim Wechsel des Betriebszustands muss jede Anmeldung erneuert werden -- der Name
    # trägt ihn.
    if stand.angemeldet_scharf is not None and stand.angemeldet_scharf != scharf:
        log.info(
            "Betriebszustand gewechselt — Zonen werden bei Home Assistant erneuert",
            extra={"scharf": scharf, "anzahl": len(stand.angemeldet)},
        )
        stand.angemeldet.clear()
    stand.angemeldet_scharf = scharf

    for zone in zonen:
        if zone.id not in stand.angemeldet:
            nachricht = discovery_anmeldung(
                zone.id, anzeigename(zone, scharf), praefix=praefix
            )
            if await client.veroeffentlichen(
                nachricht.topic, nachricht.nutzlast, schaltet=False
            ):
                stand.angemeldet.add(zone.id)
                gesendet += 1
                log.info(
                    "Zone bei Home Assistant angemeldet",
                    extra={"zone_id": zone.id, "topic": nachricht.topic},
                )

    gesendet += await _geloeschte_abmelden(
        client, stand, praefix, {zone.id for zone in zonen}
    )
    gesendet += await _zustaende_senden(session, client, zonen, praefix, jetzt)
    return gesendet


async def _geloeschte_abmelden(
    client: MqttVeroeffentlicher,
    stand: Veroeffentlichungsstand,
    praefix: str,
    vorhandene: set[int],
) -> int:
    """Der einzige Grund für eine Abmeldung: Die Zone gibt es nicht mehr.

    Ohne sie bliebe in Home Assistant ein Thermostat stehen, den niemand mehr bedient —
    er zeigte den letzten bekannten Wert für immer weiter.
    """
    gesendet = 0
    for zone_id in sorted(stand.angemeldet - vorhandene):
        nachricht = discovery_abmeldung(zone_id, praefix)
        if await client.veroeffentlichen(
            nachricht.topic, nachricht.nutzlast, schaltet=False
        ):
            stand.angemeldet.discard(zone_id)
            gesendet += 1
            log.info(
                "Geloeschte Zone bei Home Assistant abgemeldet",
                extra={"zone_id": zone_id},
            )
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
            # Ein leerer Wert wird nicht gesendet: In MQTT löscht eine leere Nutzlast
            # eine behaltene Nachricht, und „noch kein Messwert" ist etwas anderes als
            # „diesen Wert gibt es nicht mehr".
            if wert and await client.veroeffentlichen(topic, wert, schaltet=False):
                gesendet += 1
    return gesendet
