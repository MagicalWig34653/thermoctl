"""Den eigenen Zustand veröffentlichen — und die Zonen bei Home Assistant anmelden.

Der Vertrag steht in `integrations/mqtt/veroeffentlichung.py`: Topics, Discovery-Nutzlasten,
An- und Abmeldung, mit Tests. Hier ist der Aufrufer.

**Das läuft auch im Trockenlauf** — und zwar mit Absicht. Eine Zustandsmeldung bewegt
nichts, und eine Anbindung, die man erst nach dem Scharfschalten ausprobieren kann, lässt
sich genau dann nicht mehr gefahrlos prüfen, wenn ein Fehler noch folgenlos wäre. Wer die
Anlage in Home Assistant einrichten, den Thermostat drehen und nachsehen will, ob der
Sollwert ankommt, soll das vorher tun können.

**Der Trockenlauf steht nicht mehr im Namen der Zone.** Er stand dort, weil es sichtbar
war — und war genau deshalb falsch: Home Assistant leitet die Entitätskennung beim ersten
Auftauchen aus dem Namen ab. Eine Zone, die zuerst im Trockenlauf erschien, hieß danach für
immer `climate.thermoctl_zone_1_trockenlauf`, auch scharf geschaltet. Stattdessen sagt es
jetzt eine eigene Entität für den ganzen Dienst (`binary_sensor`, „Regelung scharf"), und
die Zonen behalten ihre Kennung über den ganzen Umstieg.

**Alles Bleibende geht mit dem retain-Flag hinaus** — Anmeldungen wie Zustände. Ohne das
steht in Home Assistant nach jedem Neustart eine leere Karte, bis dieser Dienst das nächste
Mal etwas sendet; bei einem Regelzyklus von einer Minute ist das eine Minute Ratlosigkeit,
und beim Umschalten eines Modus sah es aus, als sei der Befehl verschluckt worden.

**Abgemeldet wird nur, was es nicht mehr gibt.** Eine gelöschte Zone bekommt die leere
Nutzlast auf jedem ihrer Config-Topics; sonst bliebe in Home Assistant ein Thermostat
stehen, der niemandem mehr gehört.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import SensorStatus
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.db.models.zustand import ShadowDecision, ZoneState
from thermoctl.domain.schedule import aufgeloester_sollwert, ende_der_naechsten_schaltung
from thermoctl.domain.zone_settings import PARAMETER, regelparameter
from thermoctl.integrations.aktoren import MqttVeroeffentlicher, schalten_erlaubt
from thermoctl.integrations.mqtt.veroeffentlichung import (
    DiscoveryNachricht,
    boost_anmeldung,
    discovery_anmeldung,
    modus_anmeldung,
    modus_topics,
    parameter_anmeldung,
    parameter_topics,
    scharf_anmeldung,
    scharf_topic,
    verfuegbarkeits_topic,
    zeitstempel_anmeldung,
    zustands_topics,
)

log = logging.getLogger(__name__)


@dataclass
class Veroeffentlichungsstand:
    """Welche Config-Topics dieser Lauf gesendet hat, je Zone.

    Der Stand lebt im Prozess, nicht in der Datenbank: Er beschreibt, was *dieser* Lauf
    gesendet hat. Nach einem Neustart ist er leer, und der erste Zyklus meldet alles neu
    an -- was richtig ist, denn ob die Nachrichten von damals noch beim Broker liegen,
    weiß niemand.

    Die Topics statt nur der Zonenkennungen, damit eine gelöschte Zone vollständig
    abgemeldet werden kann: Sie hat je Modus und je Regelparameter eine eigene Entität,
    und deren Config-Topics ließen sich hinterher nicht mehr herleiten -- die Modi der
    gelöschten Zone stehen dann nirgends mehr.
    """

    angemeldet: dict[int, list[str]] = field(default_factory=dict)
    dienst_angemeldet: bool = False


def _als_text(wert: object) -> str:
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, datetime):
        # Mit Zeitzone: `device_class: timestamp` verlangt sie, und eine naive Angabe
        # legt Home Assistant als Ortszeit aus -- bei uns wäre sie UTC.
        return wert.replace(tzinfo=ZoneInfo("UTC")).isoformat()
    return str(wert)


def _anmeldungen(session: Session, zone: Zone, praefix: str) -> list[DiscoveryNachricht]:
    """Alles, was zu einer Zone in Home Assistant erscheint."""
    name = zone.display_name
    nachrichten = [
        discovery_anmeldung(zone.id, name, praefix=praefix),
        boost_anmeldung(zone.id, name, praefix),
        zeitstempel_anmeldung(zone.id, name, "letzte_schaltung", "Letzte Schaltung", praefix),
        zeitstempel_anmeldung(
            zone.id, name, "naechste_schaltung", "Nächster Moduswechsel", praefix
        ),
    ]
    for modus in session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order)):
        nachrichten.append(modus_anmeldung(zone.id, name, modus.id, modus.name, praefix))
    for beschreibung in PARAMETER:
        nachrichten.append(
            parameter_anmeldung(
                zone.id, name, beschreibung.name, beschreibung.beschriftung,
                beschreibung.minimum, beschreibung.maximum, beschreibung.schritt,
                beschreibung.einheit, praefix,
            )
        )
    return nachrichten


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
        verfuegbarkeits_topic(praefix), "online", schaltet=False, behalten=True
    ):
        gesendet += 1

    if not stand.dienst_angemeldet:
        nachricht = scharf_anmeldung(praefix)
        if await client.veroeffentlichen(
            nachricht.topic, nachricht.nutzlast, schaltet=False, behalten=True
        ):
            stand.dienst_angemeldet = True
            gesendet += 1
    if await client.veroeffentlichen(
        scharf_topic(praefix), _als_text(scharf), schaltet=False, behalten=True
    ):
        gesendet += 1

    for zone in zonen:
        if zone.id in stand.angemeldet:
            continue
        gesendet += await _zone_anmelden(session, client, stand, zone, praefix)

    gesendet += await _geloeschte_abmelden(client, stand, {zone.id for zone in zonen})
    for zone in zonen:
        gesendet += await zone_zustand_senden(session, client, zone, praefix, jetzt)
    return gesendet


async def _zone_anmelden(
    session: Session,
    client: MqttVeroeffentlicher,
    stand: Veroeffentlichungsstand,
    zone: Zone,
    praefix: str,
) -> int:
    gesendet = 0
    gemeldet: list[str] = []
    for nachricht in _anmeldungen(session, zone, praefix):
        if await client.veroeffentlichen(
            nachricht.topic, nachricht.nutzlast, schaltet=False, behalten=True
        ):
            gemeldet.append(nachricht.topic)
            gesendet += 1
    if gemeldet:
        stand.angemeldet[zone.id] = gemeldet
        log.info(
            "Zone bei Home Assistant angemeldet",
            extra={"zone_id": zone.id, "entitaeten": len(gemeldet)},
        )
    return gesendet


async def _geloeschte_abmelden(
    client: MqttVeroeffentlicher,
    stand: Veroeffentlichungsstand,
    vorhandene: set[int],
) -> int:
    """Der einzige Grund für eine Abmeldung: Die Zone gibt es nicht mehr.

    Ohne sie bliebe in Home Assistant ein Thermostat stehen, den niemand mehr bedient —
    er zeigte den letzten bekannten Wert für immer weiter.
    """
    gesendet = 0
    for zone_id in sorted(set(stand.angemeldet) - vorhandene):
        for topic in stand.angemeldet[zone_id]:
            if await client.veroeffentlichen(topic, "", schaltet=False, behalten=True):
                gesendet += 1
        del stand.angemeldet[zone_id]
        log.info("Geloeschte Zone bei Home Assistant abgemeldet", extra={"zone_id": zone_id})
    return gesendet


def _letzte_schaltung(session: Session, zone_id: int) -> datetime | None:
    """Wann die Entscheidung zuletzt gekippt ist — nicht, wann zuletzt gerechnet wurde.

    `previous_would_heat` steht im Schattenprotokoll ohnehin; ohne den Vergleich wäre
    „letzte Schaltung" der letzte Regelzyklus, also immer „vor einer Minute".
    """
    return session.scalar(
        select(ShadowDecision.decided_at)
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.previous_would_heat.is_not(None),
            ShadowDecision.previous_would_heat != ShadowDecision.would_heat,
        )
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(1)
    )


def _wuerde_heizen(session: Session, zone_id: int) -> bool | None:
    return session.scalar(
        select(ShadowDecision.would_heat)
        .where(ShadowDecision.zone_id == zone_id)
        .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
        .limit(1)
    )


async def zone_zustand_senden(
    session: Session,
    client: MqttVeroeffentlicher,
    zone: Zone,
    praefix: str,
    jetzt: datetime,
) -> int:
    """Alle Zustandswerte **einer** Zone.

    Einzeln aufrufbar, weil ein Befehl aus Home Assistant sofort eine Antwort braucht:
    Die Climate-Karte dort ist nicht optimistisch, sie wartet auf den Zustand. Kam der
    erst im nächsten Regelzyklus, sprang der eben gewählte Modus für eine Minute auf den
    alten zurück — und sah aus, als funktioniere die Moduswahl nicht.
    """
    topics = zustands_topics(zone.id, praefix)
    zustand = session.get(ZoneState, zone.id)
    statuscode = "keine_quelle"
    if zustand is not None:
        statuscode = (
            session.scalar(
                select(SensorStatus.code).where(SensorStatus.id == zustand.sensor_status_id)
            )
            or statuscode
        )
    sollwert = aufgeloester_sollwert(session, zone, jetzt)
    werte: list[tuple[str, str]] = [
        (topics.ist_temperatur, _als_text(zustand.temperature_c if zustand else None)),
        (topics.sollwert, _als_text(sollwert.temperature_c)),
        (topics.betriebsart, zone.operating_mode.code),
        (topics.sensorzustand, statuscode),
        (topics.wuerde_heizen, _als_text(_wuerde_heizen(session, zone.id))),
        (topics.letzte_schaltung, _als_text(_letzte_schaltung(session, zone.id))),
        (topics.naechste_schaltung, _als_text(ende_der_naechsten_schaltung(session, zone, jetzt))),
    ]

    sollwerte: dict[int, Decimal] = {
        modus_id: temperatur
        for modus_id, temperatur in session.execute(
            select(ZoneSetpoint.setpoint_mode_id, ZoneSetpoint.temperature_c).where(
                ZoneSetpoint.zone_id == zone.id
            )
        )
    }
    for modus in session.scalars(select(SetpointMode).order_by(SetpointMode.sort_order)):
        werte.append(
            (modus_topics(zone.id, modus.id, praefix)[0], _als_text(sollwerte.get(modus.id)))
        )

    wirksam = regelparameter(session, zone)
    for beschreibung in PARAMETER:
        werte.append(
            (
                parameter_topics(zone.id, beschreibung.name, praefix)[0],
                _als_text(getattr(wirksam, beschreibung.name)),
            )
        )

    gesendet = 0
    for topic, wert in werte:
        # Ein leerer Wert wird nicht gesendet: In MQTT löscht eine leere Nutzlast
        # eine behaltene Nachricht, und „noch kein Messwert" ist etwas anderes als
        # „diesen Wert gibt es nicht mehr".
        if wert and await client.veroeffentlichen(
            topic, wert, schaltet=False, behalten=True
        ):
            gesendet += 1
    return gesendet
