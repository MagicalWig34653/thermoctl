"""Integrationstest: die Matter-Brücke gegen die ECHTE Befehlsverarbeitung von thermoctl.

Die Tests unter `matter-bridge/test/` benutzen einen JS-Simulator statt thermoctl, die Tests
von `set_temporary_setpoint` und `_process_mqtt_message` dagegen keine Brücke. Hier laufen
beide Seiten zusammen:

    Matter-Controller (wie Apple Home)  --Matter, virtuelles Netz-->  Brücke (Node)
    Brücke  --MQTT, 127.0.0.1-->  Aedes-Broker (Node)  <--MQTT--  thermoctl (dieser Prozess)

Node-Prozess (`matter-bridge/integration/driver.mjs`): Broker, Brücke, Controller. Er wird als
Unterprozess gestartet und über JSON-Zeilen auf stdin/stdout gesteuert.

Python-Seite, ECHT (nichts davon ist nachgebaut):

* `MqttClient` aus `thermoctl.integrations.mqtt.client`, mit echtem aiomqtt, gegen den Broker,
  mit den echten Befehls-Abonnements; er ruft für jede Nachricht `_process_mqtt_message`;
* darin `_execute_command` -> `set_temporary_setpoint` / `set_operating_mode` -> Audit ->
  `_send_zone_state` (Zustand zurück, retained, über denselben Client);
* die Domäne (`resolved_setpoint`, Übersteuerungen, Abwesenheit, Zeitplan), eine echte
  Datenbank (SQLite-Datei je Test, Schema aus den Modellen).

Simuliert: die Uhr (feste Anfangszeit Montag 11:00 Berlin, läuft ab da weiter und lässt sich
vorstellen), Messwert und Heizbedarf (direkt in `zone_state` bzw. `shadow_decision` geschrieben)
und die Regelschleife: Zustände gehen erst auf `publish_cycle()` hinaus, wie im echten
Zyklus -- Änderungen "von der Oberfläche" werden nicht sofort veröffentlicht.

Was das NICHT beweist: einen Broker mit Zugangsdaten und ACLs (Aedes lässt alles zu), echtes
Apple Home oder Google Home (der Controller ist Matter.js im virtuellen Netz, kein Gerät und
kein mDNS), die Zertifizierung, MariaDB (die Datenbank ist hier immer SQLite).
Skipt sich, wenn node oder `matter-bridge/node_modules` fehlt (`npm ci` in `matter-bridge/`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import aiomqtt
import pytest
from sqlalchemy import select

import thermoctl.app as thermoctl_app
from tests.helpers import create_mode, create_settings, create_zone, create_zone_state
from thermoctl.app import _process_mqtt_message
from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings, session_factory, session_scope
from thermoctl.db.models.lookup import (
    ACTOR_SOURCES,
    OPERATING_MODES,
    SENSOR_STATUS,
    ActorSource,
    OperatingMode,
    SensorStatus,
)
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.state import ShadowDecision, ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint
from thermoctl.domain.absence import start_absence
from thermoctl.domain.modes import update_setpoints
from thermoctl.domain.schedule import cancel_override, resolved_setpoint, running_override
from thermoctl.domain.zones import set_operating_mode
from thermoctl.integrations.mqtt.client import MqttClient
from thermoctl.integrations.mqtt.commands import command_subscriptions
from thermoctl.services.publishing import _send_zone_state

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT / "matter-bridge"
DRIVER = BRIDGE_DIR / "integration" / "driver.mjs"

PREFIX = "thermoctl"
BERLIN = ZoneInfo("Europe/Berlin")
# Montag 2026-11-23 11:00 Berlin: mitten im Tag (06:00-22:00), nächste Schaltung 22:00.
START = datetime(2026, 11, 23, 11, 0, tzinfo=BERLIN).astimezone(UTC).replace(tzinfo=None)
TAG_C, NACHT_C, FROST_C = "21.0", "18.0", "16.0"
# Grenzen der Brücke (eng gewählt, damit die Brückenprüfung vor thermoctl greift);
# die Domäne von thermoctl lässt -20 bis 35 zu.
BRUECKE_MIN, BRUECKE_MAX = "5.0", "30.0"

# Matter: Thermostat.SystemMode und Statuscodes (Wert 1/100 Grad bei Temperaturen)
MATTER_AUS, MATTER_HEIZEN = 0, 4
STATUS_CONSTRAINT_ERROR, STATUS_INVALID_IN_STATE = 0x87, 0xCB

BEFEHL = re.compile(rf"^{PREFIX}/zones/(\d+)/command/(temporary_setpoint|operating_mode)$")
ZUSTAND = re.compile(
    rf"^{PREFIX}/zones/(\d+)/state/(current_temperature|setpoint|operating_mode|would_heat|override_active)$"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _node_voraussetzungen() -> str | None:
    """Grund zum Überspringen, oder None."""
    node = shutil.which("node")
    if node is None:
        return "node ist nicht installiert"
    if not (BRIDGE_DIR / "node_modules").is_dir():
        return "matter-bridge/node_modules fehlt -- `npm ci` in matter-bridge/ ausführen"
    if not DRIVER.is_file():
        return "matter-bridge/integration/driver.mjs fehlt"
    try:
        version = subprocess.run(  # noqa: S603
            [node, "--version"], capture_output=True, text=True, check=True, timeout=20
        ).stdout.strip()
        major, minor = (int(part) for part in version.lstrip("v").split(".")[:2])
    except subprocess.SubprocessError, ValueError, OSError:  # pragma: no cover
        return "node-Version nicht lesbar"
    if (major, minor) < (22, 13):
        return f"node {version} ist zu alt (matter-bridge braucht >= 22.13)"
    return None


# -- Uhr ----------------------------------------------------------------------------------


class Uhr:
    """Feste Anfangszeit, läuft ab dort in Echtzeit weiter, lässt sich vorstellen.

    Nicht eingefroren: thermoctl legt eine Übersteuerung mit dem Zeitpunkt *innerhalb* von
    `_apply` an und meldet den Zustand danach. Bei stehender Uhr fiele der Unterschied
    zwischen Empfangszeit und frischem Zeitpunkt weg (`received_at` statt `utcnow()`) --
    dann wäre dieser Fehler im Test unsichtbar.
    """

    def __init__(self, start: datetime) -> None:
        self._start = start
        self._seit = time.monotonic()
        self._vorgestellt = timedelta()

    def __call__(self) -> datetime:
        return self._start + self._vorgestellt + timedelta(seconds=time.monotonic() - self._seit)

    def vorstellen(self, delta: timedelta) -> None:
        self._vorgestellt += delta


# -- Node-Unterprozess --------------------------------------------------------------------


class Knoten:
    """Der Node-Treiber: Broker + Brücke + Matter-Controller."""

    def __init__(self, arbeitsverzeichnis: Path) -> None:
        self.arbeitsverzeichnis = arbeitsverzeichnis
        self.proc: asyncio.subprocess.Process | None = None
        self.url = ""
        self.warnungen: list[str] = []
        self._wartend: dict[int, asyncio.Future[Any]] = {}
        self._naechste = 0
        self._leser: asyncio.Task[None] | None = None
        self._broker: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._log = arbeitsverzeichnis / "treiber.log"

    async def starten(self) -> None:
        self.arbeitsverzeichnis.mkdir(parents=True, exist_ok=True)
        with self._log.open("wb") as log:
            self.proc = await asyncio.create_subprocess_exec(
                shutil.which("node") or "node",
                str(DRIVER),
                str(self.arbeitsverzeichnis),
                cwd=BRIDGE_DIR,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
            )
        self._leser = asyncio.create_task(self._lesen())
        self.url = await asyncio.wait_for(self._broker, 60)

    async def _lesen(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        while line := await self.proc.stdout.readline():
            text = line.decode("utf-8", "replace").rstrip("\n")
            if not text.startswith("@@"):
                continue
            nachricht = json.loads(text[2:])
            if nachricht.get("event") == "broker":
                self._broker.set_result(nachricht["url"])
            elif nachricht.get("event") == "bridge-warning":
                self.warnungen.append(nachricht["message"])
            elif (zukunft := self._wartend.pop(nachricht.get("id"), None)) is not None:
                if nachricht["ok"]:
                    zukunft.set_result(nachricht["result"])
                else:
                    zukunft.set_exception(RuntimeError(nachricht["error"]))
        ende = RuntimeError(
            f"Node-Treiber beendet. Log: {self._log.read_text(errors='replace')[-1500:]}"
        )
        if not self._broker.done():
            self._broker.set_exception(ende)
        for zukunft in self._wartend.values():
            if not zukunft.done():
                zukunft.set_exception(ende)

    async def aufruf(self, op: str, timeout: float = 60, **argumente: Any) -> Any:
        assert self.proc is not None and self.proc.stdin is not None
        self._naechste += 1
        zukunft: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._wartend[self._naechste] = zukunft
        zeile = json.dumps({"id": self._naechste, "op": op, **argumente}) + "\n"
        self.proc.stdin.write(zeile.encode())
        await self.proc.stdin.drain()
        return await asyncio.wait_for(zukunft, timeout)

    async def beenden(self) -> None:
        """Sauber über `stop`, sonst über die PID des Kindprozesses (nie über ein Muster)."""
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.returncode is None:
                try:
                    await self.aufruf("stop", timeout=20)
                except RuntimeError, TimeoutError, ConnectionError, OSError:
                    pass
                await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:  # pragma: no cover
            pass
        finally:
            if proc.returncode is None:  # pragma: no cover
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            if self._leser is not None:
                await asyncio.gather(self._leser, return_exceptions=True)


# -- thermoctl-Seite ----------------------------------------------------------------------


class Anlage:
    """Zwei simulierte Zonen mit echter Domäne, echter Datenbank, echtem MQTT-Client."""

    def __init__(self, verzeichnis: Path) -> None:
        self.verzeichnis = verzeichnis
        self.uhr = Uhr(START)
        self.einstellungen = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url=f"sqlite:///{verzeichnis / 'anlage.db'}",
            secret_key="s" * 32,  # type: ignore[arg-type]
            mqtt_prefix=PREFIX,
            mqtt_enabled=True,
            mqtt_host="127.0.0.1",
            mqtt_client_id="thermoctl-py",
        )
        self.engine = create_engine_from_settings(self.einstellungen)
        Base.metadata.create_all(self.engine)
        self.factory = session_factory(self.engine)
        self.knoten = Knoten(verzeichnis / "node")
        self.beobachtet: list[tuple[str, str, bool]] = []
        self.zonen: dict[str, int] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._uhr_patch = mock.patch.object(thermoctl_app, "utcnow", self.uhr)
        self._beobachter: aiomqtt.Client | None = None
        self._ha: aiomqtt.Client | None = None
        self.client: MqttClient | None = None

    # -- Aufbau ---------------------------------------------------------------------------

    def datenbank_anlegen(self) -> None:
        with session_scope(self.factory) as session:
            for code, label in ACTOR_SOURCES:
                session.add(ActorSource(code=code, label=label))
            for code, label in OPERATING_MODES:
                session.add(OperatingMode(code=code, label=label))
            for code, label in SENSOR_STATUS:
                session.add(SensorStatus(code=code, label=label))
            session.flush()
            create_settings(session)
            create_mode(session, "komfort", "Komfort")
            create_mode(session, "nacht", "Nacht")
        self.zonen = {
            "A": self._zone_anlegen("Wohnzimmer", temperatur="20.4", heizt=True),
            "B": self._zone_anlegen("Schlafzimmer", temperatur="19.1", heizt=False),
        }

    def _zone_anlegen(self, name: str, *, temperatur: str, heizt: bool) -> int:
        """Zeitplan: 06:00 Komfort (21,0), 22:00 Nacht (18,0), jeden Tag; Frostschutz 16,0."""
        with session_scope(self.factory) as session:
            zone = create_zone(session, name)
            setting = session.get(Setting, 1)
            assert setting is not None
            modi = {m.code: m for m in session.scalars(select(SetpointMode))}
            for modus_id, wert in (
                (setting.frost_protection_mode_id, FROST_C),
                (modi["komfort"].id, TAG_C),
                (modi["nacht"].id, NACHT_C),
            ):
                session.add(
                    ZoneSetpoint(
                        zone_id=zone.id, setpoint_mode_id=modus_id, temperature_c=Decimal(wert)
                    )
                )
            for wochentag in range(1, 8):
                for minute, code in ((6 * 60, "komfort"), (22 * 60, "nacht")):
                    session.add(
                        SchedulePoint(
                            zone_id=zone.id,
                            weekday=wochentag,
                            minute_of_day=minute,
                            setpoint_mode_id=modi[code].id,
                        )
                    )
            state = create_zone_state(session, zone)
            state.temperature_c = Decimal(temperatur)
            state.measured_at = START
            state.sensor_status_id = session.scalars(
                select(SensorStatus.id).where(SensorStatus.code == "ok")
            ).one()
            session.add(
                ShadowDecision(
                    decided_at=START,
                    zone_id=zone.id,
                    setpoint_reason="Simulation",
                    would_heat=heizt,
                    outcome_code="an" if heizt else "aus",
                    reason="Simulation",
                )
            )
            session.flush()
            return zone.id

    async def hochfahren(self) -> None:
        self._uhr_patch.start()
        self.datenbank_anlegen()
        await self.knoten.starten()
        host, port = self.knoten.url.removeprefix("mqtt://").split(":")
        self.einstellungen.mqtt_port = int(port)

        # Beobachter und "Home Assistant" (nur zum Mitlesen und für Direktbefehle): vor der
        # ersten Veröffentlichung verbunden, damit er Zustände ohne retain-Flag sieht.
        self._beobachter = aiomqtt.Client(host, int(port), identifier="beobachter-py")
        await self._beobachter.__aenter__()
        await self._beobachter.subscribe(f"{PREFIX}/zones/+/state/#", qos=1)
        self._tasks.append(asyncio.create_task(self._beobachten()))
        self._ha = aiomqtt.Client(host, int(port), identifier="ha-py")
        await self._ha.__aenter__()

        # Der echte thermoctl-MQTT-Client mit dem echten Befehlsweg.
        app = SimpleNamespace(
            state=SimpleNamespace(
                session_factory=self.factory, publisher=None, bridge_reachable=True
            )
        )
        self.client = MqttClient(
            self.einstellungen,
            lambda topic, payload: _process_mqtt_message(
                app,  # type: ignore[arg-type]
                self.einstellungen,
                topic,
                payload,
            ),
            switching_allowed=False,
            extra_subscriptions=command_subscriptions(PREFIX),
        )
        app.state.publisher = self.client
        self._tasks.append(asyncio.create_task(self.client.run()))
        await self.warten(self._thermoctl_abonniert, "thermoctl abonniert die Befehle nicht")

        zonen = [
            {"id": zone_id, "name": name}
            for zone_id, name in (
                (self.zonen["A"], "Wohnzimmer"),
                (self.zonen["B"], "Schlafzimmer"),
            )
        ]
        await self.knoten.aufruf(
            "start", zones=zonen, prefix=PREFIX, minSetpoint=BRUECKE_MIN, maxSetpoint=BRUECKE_MAX
        )
        await self.warten(self._bruecke_abonniert, "Die Brücke abonniert die Zustände nicht")
        # Die Brücke verwirft retained Nachrichten: frisch veröffentlichen, wie der Zyklus es tut.
        await self.publish_cycle()
        for zone in self.zonen.values():
            await self.matter_zeigt(zone, was="Zone wird nicht erreichbar", reachable=True)

    async def _beobachten(self) -> None:
        assert self._beobachter is not None
        async for nachricht in self._beobachter.messages:
            self.beobachtet.append(
                (str(nachricht.topic), bytes(nachricht.payload).decode(), nachricht.retain)  # type: ignore[arg-type]
            )

    async def _status(self) -> dict[str, Any]:
        status: dict[str, Any] = await self.knoten.aufruf("status")
        return status

    async def _thermoctl_abonniert(self) -> bool:
        clients = (await self._status())["clients"]
        return f"{PREFIX}/zones/+/command/+" in clients.get("thermoctl-py", []) and any(
            id_ == "beobachter-py" for id_ in clients
        )

    async def _bruecke_abonniert(self) -> bool:
        clients = (await self._status())["clients"]
        bruecke = [t for id_, ts in clients.items() if not id_.endswith("-py") for t in ts]
        return len(bruecke) >= 5 * len(self.zonen)

    async def herunterfahren(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for client in (self._beobachter, self._ha):
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.__aexit__(None, None, None)
        await self.knoten.beenden()
        self.engine.dispose()
        self._uhr_patch.stop()

    # -- Warten ---------------------------------------------------------------------------

    async def warten(
        self,
        bedingung: Callable[[], Awaitable[Any] | Any],
        was: str,
        timeout: float = 10.0,
    ) -> Any:
        ende = time.monotonic() + timeout
        while True:
            wert = bedingung()
            if asyncio.iscoroutine(wert):
                wert = await wert
            if wert:
                return wert
            if time.monotonic() > ende:
                raise AssertionError(f"Zeitüberschreitung: {was}")
            await asyncio.sleep(0.05)

    # -- Matter-Controller ----------------------------------------------------------------

    async def matter(self, zone: int) -> dict[str, Any]:
        gelesen: dict[str, Any] = await self.knoten.aufruf("read", zone=zone)
        return gelesen

    async def matter_zeigt(
        self, zone: int, *, was: str, timeout: float = 8.0, **erwartet: Any
    ) -> dict[str, Any]:
        letzter: dict[str, Any] = {}

        async def stimmt() -> bool:
            nonlocal letzter
            letzter = await self.matter(zone)
            return all(letzter[feld] == wert for feld, wert in erwartet.items())

        try:
            await self.warten(stimmt, was, timeout)
        except AssertionError as fehler:
            raise AssertionError(f"{fehler}; erwartet {erwartet}, zuletzt {letzter}") from None
        return letzter

    async def matter_bleibt(self, zone: int, *, sekunden: float = 0.9, **erwartet: Any) -> None:
        """Der Wert gilt auch nach Debounce- und Abgleichzeit der Brücke (200 + 500 ms)."""
        await asyncio.sleep(sekunden)
        gelesen = await self.matter(zone)
        for feld, wert in erwartet.items():
            assert gelesen[feld] == wert, (
                f"{feld}: {gelesen[feld]!r} statt {wert!r} (Matter: {gelesen})"
            )

    async def schreiben(self, zone: int, attribut: str, wert: int) -> dict[str, Any]:
        ergebnis: dict[str, Any] = await self.knoten.aufruf(
            "write", zone=zone, attribute=attribut, value=wert
        )
        return ergebnis

    async def rad(self, zone: int, grad: float) -> None:
        """Ein Radschritt: schreibt occupiedHeatingSetpoint (1/100 Grad), muss angenommen werden."""
        ergebnis = await self.schreiben(zone, "occupiedHeatingSetpoint", round(grad * 100))
        assert ergebnis["accepted"], ergebnis

    async def bruecken_publikationen(self) -> list[dict[str, Any]]:
        publikationen: list[dict[str, Any]] = (await self._status())["bridgePublishes"]
        return publikationen

    async def bruecken_befehle(self, zone: int | None = None) -> list[tuple[str, str]]:
        """(Befehl, Nutzlast) der Brücke, älteste zuerst, optional nur einer Zone."""
        ergebnis = []
        for eintrag in await self.bruecken_publikationen():
            treffer = BEFEHL.match(eintrag["topic"])
            assert treffer, f"Unerlaubtes Topic der Brücke: {eintrag['topic']}"
            if zone is None or int(treffer.group(1)) == zone:
                ergebnis.append((treffer.group(2), eintrag["payload"]))
        return ergebnis

    async def leitung_pruefen(self) -> None:
        """Die Brücke sendet nur die zwei erlaubten Befehle (nie setpoint), QoS 1, ohne retain,
        und abonniert nur die fünf Zustände je Zone."""
        for eintrag in await self.bruecken_publikationen():
            assert BEFEHL.match(eintrag["topic"]), f"Unerlaubtes Topic: {eintrag['topic']}"
            assert not eintrag["topic"].endswith("/command/setpoint")
            assert (eintrag["qos"], eintrag["retain"]) == (1, False), eintrag
        clients = (await self._status())["clients"]
        for id_, themen in clients.items():
            if id_.endswith("-py"):
                continue
            for thema in themen:
                assert ZUSTAND.match(thema), f"Unerlaubtes Abonnement: {thema}"

    # -- thermoctl: Zustand veröffentlichen, Direktbefehle --------------------------------

    async def publish_cycle(self) -> None:
        """Der Zustand aller Zonen über die echte `_send_zone_state` (wie der Regelzyklus)."""
        assert self.client is not None
        with session_scope(self.factory) as session:
            for zone in session.scalars(select(Zone).order_by(Zone.id)):
                await _send_zone_state(session, self.client, zone, PREFIX, self.uhr())

    async def direktbefehl(self, zone: int, befehl: str, nutzlast: str) -> None:
        """Wie Home Assistant: veröffentlicht einen Befehl am Broker (nicht über die Brücke)."""
        assert self._ha is not None
        await self._ha.publish(f"{PREFIX}/zones/{zone}/command/{befehl}", nutzlast, qos=1)

    def zustaende(self, zone: int, schluessel: str, ab: int = 0) -> list[str]:
        """Nutzlasten, die thermoctl für `state/<schluessel>` gesendet hat (ab Index `ab`)."""
        thema = f"{PREFIX}/zones/{zone}/state/{schluessel}"
        return [nutzlast for t, nutzlast, _ in self.beobachtet[ab:] if t == thema]

    # -- Was thermoctl wirklich hält ------------------------------------------------------

    def uebersteuerungen(self, zone: int, *, laufend: bool = False) -> list[ZoneOverride]:
        with session_scope(self.factory) as session:
            zeilen = list(
                session.scalars(
                    select(ZoneOverride)
                    .where(ZoneOverride.zone_id == zone)
                    .order_by(ZoneOverride.id)
                )
            )
            if laufend:
                zeilen = [
                    z
                    for z in zeilen
                    if z.cancelled_at is None
                    and z.starts_at <= self.uhr()
                    and (z.ends_at is None or z.ends_at > self.uhr())
                ]
            session.expunge_all()
            return zeilen

    def modus(self, zone: int) -> str:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            return gefunden.operating_mode.code

    def modus_temperatur(self, zone: int, code: str) -> Decimal:
        with session_scope(self.factory) as session:
            wert = session.scalar(
                select(ZoneSetpoint.temperature_c)
                .join(SetpointMode, SetpointMode.id == ZoneSetpoint.setpoint_mode_id)
                .where(ZoneSetpoint.zone_id == zone, SetpointMode.code == code)
            )
            assert wert is not None
            return wert

    def plan_sollwert(self, zone: int) -> Decimal:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            return resolved_setpoint(session, gefunden, self.uhr()).temperature_c

    def zone_state(self, zone: int) -> tuple[Decimal | None, bool | None]:
        with session_scope(self.factory) as session:
            state = session.get(ZoneState, zone)
            assert state is not None
            heizt = session.scalar(
                select(ShadowDecision.would_heat)
                .where(ShadowDecision.zone_id == zone)
                .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
                .limit(1)
            )
            return state.temperature_c, heizt

    def audit(self) -> list[tuple[str, str]]:
        with session_scope(self.factory) as session:
            zeilen = session.execute(
                select(ActorSource.code, AuditEvent.summary)
                .join(ActorSource, ActorSource.id == AuditEvent.source_id)
                .order_by(AuditEvent.id)
            )
            return [(code, summary) for code, summary in zeilen]

    # -- Eingriffe "von der Oberfläche" (nicht über MQTT, nicht sofort veröffentlicht) -----

    def ui_modus(self, zone: int, code: str) -> None:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            set_operating_mode(session, gefunden, code, actor_id=None, source="web")

    def ui_modus_temperatur(self, zone: int, code: str, grad: str) -> None:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            modus_id = session.scalar(select(SetpointMode.id).where(SetpointMode.code == code))
            assert modus_id is not None
            update_setpoints(
                session, gefunden, {modus_id: Decimal(grad)}, user_id=None, source="web"
            )

    def ui_uebersteuerung_beenden(self, zone: int) -> bool:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            return cancel_override(session, gefunden, now=self.uhr()) is not None

    def ui_abwesenheit(self, zone: int, grad: str, tage: int) -> None:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            start_absence(
                session,
                [gefunden],
                Decimal(grad),
                self.uhr() + timedelta(days=tage),
                now=self.uhr(),
                source="web",
            )

    def laufende_uebersteuerung(self, zone: int) -> ZoneOverride | None:
        with session_scope(self.factory) as session:
            gefunden = session.get(Zone, zone)
            assert gefunden is not None
            zeile = running_override(session, gefunden, self.uhr())
            if zeile is not None:
                session.expunge(zeile)
            return zeile


@pytest.fixture
async def anlage(tmp_path: Path) -> AsyncIterator[Anlage]:
    grund = _node_voraussetzungen()
    if grund is not None:
        pytest.skip(f"Matter-Brücken-Integrationstest übersprungen: {grund}")
    anlage = Anlage(tmp_path)
    try:
        await anlage.hochfahren()
        yield anlage
        # Für jeden Test: auf der Leitung ging nur Erlaubtes hinaus.
        await anlage.leitung_pruefen()
    finally:
        await anlage.herunterfahren()


def _lokal(stunde: int, minute: int = 0) -> datetime:
    """Montag 2026-11-23, lokale Uhrzeit Berlin, als naive UTC-Zeit."""
    return (
        datetime(2026, 11, 23, stunde, minute, tzinfo=BERLIN).astimezone(UTC).replace(tzinfo=None)
    )


# -- (a) Lesen ----------------------------------------------------------------------------


async def test_controller_liest_ist_soll_modus_und_heizbedarf_wie_zone_state(
    anlage: Anlage,
) -> None:
    for kennung, ist, heizt in (("A", "20.4", True), ("B", "19.1", False)):
        zone = anlage.zonen[kennung]
        temperatur, heizt_db = anlage.zone_state(zone)
        assert temperatur == Decimal(ist) and heizt_db is heizt
        gelesen = await anlage.matter(zone)
        assert gelesen["localTemperature"] == int(temperatur * 100)
        assert gelesen["occupiedHeatingSetpoint"] == int(anlage.plan_sollwert(zone) * 100) == 2100
        assert gelesen["systemMode"] == MATTER_HEIZEN  # Betriebsart auto
        assert gelesen["heat"] is heizt_db
        assert gelesen["reachable"] is True
        # Heating-only mit den konfigurierten Grenzen der Brücke.
        assert (gelesen["minHeat"], gelesen["maxHeat"]) == (500, 3000)


async def test_zustandsformat_von_thermoctl_passt_zum_vertrag_der_bruecke(anlage: Anlage) -> None:
    """Zahlenformat auf der Leitung: genau das, was die strengen Parser der Brücke erwarten."""
    zone = anlage.zonen["A"]
    assert anlage.zustaende(zone, "setpoint")[-1] == "21.0"
    assert anlage.zustaende(zone, "current_temperature")[-1] == "20.40"
    assert anlage.zustaende(zone, "operating_mode")[-1] == "auto"
    assert anlage.zustaende(zone, "would_heat")[-1] == "true"
    assert anlage.zustaende(zone, "override_active")[-1] == "false"
    # Der Beobachter war vor der ersten Veröffentlichung verbunden: nichts davon kam als retained
    # Wiedergabe, sondern als frische Nachricht an eine bestehende Abonnentin -- so wie die
    # Brücke sie braucht (sie verwirft retained Nachrichten).
    assert all(not retain for _, _, retain in anlage.beobachtet)


# -- (b) Rad -> genau eine Übersteuerung --------------------------------------------------


async def test_rad_legt_genau_eine_uebersteuerung_an_und_laesst_den_plan_unveraendert(
    anlage: Anlage,
) -> None:
    zone = anlage.zonen["A"]
    await anlage.rad(zone, 22.5)
    await anlage.matter_zeigt(zone, was="22,5 kommt nicht an", occupiedHeatingSetpoint=2250)
    # Länger als Entprellung und Abgleich der Brücke: der Wert kam aus thermoctl zurück,
    # nicht aus dem lokalen Echo des Controllers.
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2250, systemMode=MATTER_HEIZEN)

    zeilen = anlage.uebersteuerungen(zone)
    assert len(zeilen) == 1, "genau eine Zeile, nicht mehrere"
    uebersteuerung = zeilen[0]
    assert uebersteuerung.temperature_c == Decimal("22.5")
    assert uebersteuerung.setpoint_mode_id is None and uebersteuerung.absence_id is None
    assert uebersteuerung.cancelled_at is None
    # Ende = nächste Schaltung des Plans (22:00 Berlin am selben Tag).
    assert uebersteuerung.ends_at == _lokal(22)
    assert uebersteuerung.starts_at < uebersteuerung.ends_at
    # Die Modus-Temperaturen des Plans sind unverändert.
    assert anlage.modus_temperatur(zone, "komfort") == Decimal("21.0")
    assert anlage.modus_temperatur(zone, "nacht") == Decimal("18.0")
    # Audit: Quelle `system` (niemand angemeldet), vorübergehend gesetzt.
    assert (
        "system",
        "Sollwert für Zone 'Wohnzimmer' vorübergehend auf 22,5 °C gesetzt",
    ) in anlage.audit()
    # thermoctl hat die Übersteuerung zurückgemeldet, die Brücke genau einen Befehl gesendet.
    assert anlage.zustaende(zone, "override_active")[-1] == "true"
    assert anlage.zustaende(zone, "setpoint")[-1] == "22.5"
    assert await anlage.bruecken_befehle(zone) == [("temporary_setpoint", "22.5")]
    # Die andere Zone ist unberührt.
    assert anlage.uebersteuerungen(anlage.zonen["B"]) == []


# -- (c) schnelle Radschritte -------------------------------------------------------------


async def test_schnelle_radschritte_ergeben_nach_der_entprellung_eine_uebersteuerung(
    anlage: Anlage,
) -> None:
    zone = anlage.zonen["A"]
    for grad in (
        22.4,
        22.6,
        22.8,
        23.0,
    ):  # 23.0 ist ganzzahlig: die Brücke sendet "23", nicht "23.0"
        await anlage.rad(zone, grad)
    await anlage.matter_zeigt(zone, was="Letzter Radwert fehlt", occupiedHeatingSetpoint=2300)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2300)

    assert await anlage.bruecken_befehle(zone) == [("temporary_setpoint", "23")]
    zeilen = anlage.uebersteuerungen(zone)
    assert [z.temperature_c for z in zeilen] == [Decimal("23.0")]
    assert zeilen[0].cancelled_at is None
    # thermoctl meldet den ganzzahligen Wert im Format, das die Brücke liest ("23.0", nicht "23").
    assert anlage.zustaende(zone, "setpoint")[-1] == "23.0"
    summaries = [s for q, s in anlage.audit() if "vorübergehend" in s]
    assert len(summaries) == 1


async def test_zweites_drehen_ersetzt_die_uebersteuerung_statt_zu_stapeln(anlage: Anlage) -> None:
    """Zwei getrennte Radbewegungen (länger als die Entprellung): zwei Befehle, eine läuft."""
    zone = anlage.zonen["A"]
    await anlage.rad(zone, 22.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2250)
    await anlage.rad(zone, 19.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=1950)

    assert await anlage.bruecken_befehle(zone) == [
        ("temporary_setpoint", "22.5"),
        ("temporary_setpoint", "19.5"),
    ]
    alle = anlage.uebersteuerungen(zone)
    assert len(alle) == 2
    laufend = anlage.uebersteuerungen(zone, laufend=True)
    assert [z.temperature_c for z in laufend] == [Decimal("19.5")]
    assert alle[0].cancelled_at is not None
    # "Übersteuerung beenden" führt zum Zeitplan zurück, nicht zum vorigen Radwert.
    assert anlage.ui_uebersteuerung_beenden(zone)
    assert anlage.plan_sollwert(zone) == Decimal("21.0")


# -- (d) Übersteuerung beenden ------------------------------------------------------------


async def test_uebersteuerung_beenden_zeigt_dem_controller_den_planwert(anlage: Anlage) -> None:
    zone = anlage.zonen["A"]
    await anlage.rad(zone, 22.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2250)

    assert anlage.ui_uebersteuerung_beenden(zone)
    assert anlage.laufende_uebersteuerung(zone) is None
    await anlage.publish_cycle()

    await anlage.matter_zeigt(zone, was="Planwert kommt nicht zurück", occupiedHeatingSetpoint=2100)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2100)
    assert anlage.zustaende(zone, "override_active")[-1] == "false"
    # Die Zeile bleibt als Verlauf stehen, beendet.
    zeilen = anlage.uebersteuerungen(zone)
    assert len(zeilen) == 1 and zeilen[0].cancelled_at is not None


# -- (e) Ende der Übersteuerung am Zeitplan -----------------------------------------------


async def test_ende_der_uebersteuerung_am_zeitplan_bringt_den_planwert_zurueck(
    anlage: Anlage,
) -> None:
    zone = anlage.zonen["A"]
    await anlage.rad(zone, 22.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2250)

    # Zeit vorstellen auf 22:01 Berlin: die Übersteuerung ist abgelaufen, die Nacht gilt (18,0).
    anlage.uhr.vorstellen(_lokal(22, 1) - anlage.uhr())
    assert anlage.laufende_uebersteuerung(zone) is None
    assert anlage.plan_sollwert(zone) == Decimal("18.0")
    await anlage.publish_cycle()

    await anlage.matter_zeigt(zone, was="Nachtwert kommt nicht an", occupiedHeatingSetpoint=1800)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=1800)
    assert anlage.zustaende(zone, "override_active")[-1] == "false"
    # Die Zeile wurde nicht von Hand beendet, sondern lief aus.
    zeilen = anlage.uebersteuerungen(zone)
    assert len(zeilen) == 1 and zeilen[0].cancelled_at is None


# -- (f) Aus / Heizen ---------------------------------------------------------------------


async def test_matter_aus_setzt_betriebsart_off_und_sperrt_das_rad_heizen_setzt_auto(
    anlage: Anlage,
) -> None:
    zone = anlage.zonen["A"]
    # Ausgangslage "manual": Matter liest das als Heizen; "Heizen" muss trotzdem `auto` senden.
    anlage.ui_modus(zone, "manual")
    await anlage.publish_cycle()
    await anlage.matter_zeigt(
        zone, was="manual wird nicht als Heizen gelesen", systemMode=MATTER_HEIZEN
    )

    assert (await anlage.schreiben(zone, "systemMode", MATTER_AUS))["accepted"]
    await anlage.warten(
        lambda: anlage.modus(zone) == "off", "Betriebsart off kommt nicht in thermoctl an"
    )
    # Frostschutz gilt, und der Controller sieht Aus mit dem Frostschutzwert.
    await anlage.matter_zeigt(
        zone, was="Aus erscheint nicht", systemMode=MATTER_AUS, occupiedHeatingSetpoint=1600
    )
    await anlage.matter_bleibt(zone, systemMode=MATTER_AUS, occupiedHeatingSetpoint=1600)
    assert anlage.plan_sollwert(zone) == Decimal("16.0")
    assert ("system", "Betriebsart von 'Wohnzimmer' auf Aus gesetzt") in anlage.audit()

    # Bei Aus wird ein Sollwert-Write abgelehnt (schon von der Brücke), und es entsteht nichts.
    abgelehnt = await anlage.schreiben(zone, "occupiedHeatingSetpoint", 2200)
    assert abgelehnt["accepted"] is False and abgelehnt["code"] == STATUS_INVALID_IN_STATE
    await asyncio.sleep(0.5)
    assert anlage.uebersteuerungen(zone) == []
    assert [c for c, _ in await anlage.bruecken_befehle(zone)] == ["operating_mode"]

    # Heizen -> `auto` (nicht manual).
    assert (await anlage.schreiben(zone, "systemMode", MATTER_HEIZEN))["accepted"]
    await anlage.warten(lambda: anlage.modus(zone) == "auto", "Heizen setzt nicht auf auto")
    await anlage.matter_zeigt(
        zone, was="Heizen erscheint nicht", systemMode=MATTER_HEIZEN, occupiedHeatingSetpoint=2100
    )
    await anlage.matter_bleibt(zone, systemMode=MATTER_HEIZEN, occupiedHeatingSetpoint=2100)
    assert await anlage.bruecken_befehle(zone) == [
        ("operating_mode", "off"),
        ("operating_mode", "auto"),
    ]
    assert ("system", "Betriebsart von 'Wohnzimmer' auf Automatik gesetzt") in anlage.audit()
    assert anlage.uebersteuerungen(zone) == []


async def test_thermoctl_lehnt_den_sollwert_bei_off_ab_auch_wenn_die_bruecke_ihn_schickt(
    anlage: Anlage,
) -> None:
    """Zweite Absicherung: die Brücke kennt "off" noch nicht (Änderung an der Oberfläche, noch nicht
    veröffentlicht) und schickt das Rad durch -- thermoctl lehnt ab und meldet den echten Zustand
    zurück, der Controller springt auf Aus und Frostschutzwert."""
    zone = anlage.zonen["A"]
    anlage.ui_modus(zone, "off")  # nicht veröffentlicht: die Brücke zeigt noch Heizen/21,0
    assert (await anlage.matter(zone))["systemMode"] == MATTER_HEIZEN

    await anlage.rad(zone, 22.0)  # die Brücke lässt es durch
    await anlage.matter_zeigt(
        zone,
        was="Controller springt nicht auf den echten Zustand zurück",
        systemMode=MATTER_AUS,
        occupiedHeatingSetpoint=1600,
    )
    await anlage.matter_bleibt(zone, systemMode=MATTER_AUS, occupiedHeatingSetpoint=1600)
    assert await anlage.bruecken_befehle(zone) == [("temporary_setpoint", "22")]
    assert anlage.uebersteuerungen(zone) == []
    assert anlage.zustaende(zone, "operating_mode")[-1] == "off"
    assert anlage.zustaende(zone, "setpoint")[-1] == "16.0"


# -- (g) Grenzen --------------------------------------------------------------------------


async def test_werte_ausserhalb_der_grenzen_erreichen_thermoctl_nicht(anlage: Anlage) -> None:
    zone = anlage.zonen["A"]
    for wert in (
        3100,
        490,
        2231,
        9900,
    ):  # über Maximum, unter Minimum, kein Zehntelgrad, weit darüber
        ergebnis = await anlage.schreiben(zone, "occupiedHeatingSetpoint", wert)
        assert ergebnis["accepted"] is False and ergebnis["code"] == STATUS_CONSTRAINT_ERROR, (
            wert,
            ergebnis,
        )
    await asyncio.sleep(0.6)
    assert await anlage.bruecken_publikationen() == []
    assert anlage.uebersteuerungen(zone) == []
    await anlage.matter_bleibt(zone, sekunden=0.0, occupiedHeatingSetpoint=2100)


async def test_direktbefehl_ausserhalb_der_grenzen_wird_abgelehnt_zustand_zurueckgemeldet(
    anlage: Anlage, caplog: pytest.LogCaptureFixture
) -> None:
    zone = anlage.zonen["A"]
    vorher = len(anlage.beobachtet)
    with caplog.at_level("WARNING"):
        await anlage.direktbefehl(zone, "temporary_setpoint", "99")
        # Die Rückmeldung nach der Ablehnung: der unveränderte Zustand geht trotzdem hinaus.
        await anlage.warten(
            lambda: anlage.zustaende(zone, "setpoint", vorher),
            "Keine Rückmeldung nach abgelehntem Befehl",
        )
    assert anlage.zustaende(zone, "setpoint", vorher) == ["21.0"]
    assert anlage.zustaende(zone, "override_active", vorher) == ["false"]
    assert anlage.zustaende(zone, "operating_mode", vorher) == ["auto"]
    assert "Befehl abgelehnt" in caplog.text
    assert anlage.uebersteuerungen(zone) == []
    await anlage.matter_bleibt(
        zone, sekunden=0.3, occupiedHeatingSetpoint=2100, systemMode=MATTER_HEIZEN
    )
    # Die andere Zone bekam keine Rückmeldung zu diesem Befehl.
    assert anlage.zustaende(anlage.zonen["B"], "setpoint", vorher) == []


# -- (h) Änderungen in thermoctl erscheinen im Controller ---------------------------------


async def test_aenderungen_an_plan_und_betriebsart_erscheinen_im_controller(anlage: Anlage) -> None:
    zone = anlage.zonen["A"]
    anlage.ui_modus_temperatur(zone, "komfort", "22.0")
    await anlage.publish_cycle()
    await anlage.matter_zeigt(zone, was="Neue Modus-Temperatur fehlt", occupiedHeatingSetpoint=2200)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2200)

    anlage.ui_modus(zone, "off")
    await anlage.publish_cycle()
    await anlage.matter_zeigt(
        zone, was="Aus fehlt", systemMode=MATTER_AUS, occupiedHeatingSetpoint=1600
    )

    anlage.ui_modus(zone, "auto")
    await anlage.publish_cycle()
    await anlage.matter_zeigt(
        zone, was="Auto fehlt", systemMode=MATTER_HEIZEN, occupiedHeatingSetpoint=2200
    )

    # Messwert und Heizbedarf ebenfalls.
    with session_scope(anlage.factory) as session:
        state = session.get(ZoneState, zone)
        assert state is not None
        state.temperature_c = Decimal("21.85")
        session.add(
            ShadowDecision(
                decided_at=anlage.uhr(),
                zone_id=zone,
                setpoint_reason="Simulation",
                would_heat=False,
                outcome_code="aus",
                reason="Simulation",
            )
        )
    await anlage.publish_cycle()
    await anlage.matter_zeigt(zone, was="Neuer Istwert fehlt", localTemperature=2185, heat=False)
    # Nichts davon hat die Brücke zu Befehlen veranlasst.
    assert await anlage.bruecken_publikationen() == []


# -- (i) Abwesenheit ----------------------------------------------------------------------


async def test_abwesenheit_bleibt_beim_rad_unberuehrt(anlage: Anlage) -> None:
    zone = anlage.zonen["A"]
    anlage.ui_abwesenheit(zone, "17.0", tage=2)
    await anlage.publish_cycle()
    await anlage.matter_zeigt(zone, was="Absenkung fehlt", occupiedHeatingSetpoint=1700)

    await anlage.rad(zone, 23.0)
    await anlage.matter_zeigt(zone, was="Radwert fehlt", occupiedHeatingSetpoint=2300)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2300)
    laufend = anlage.uebersteuerungen(zone, laufend=True)
    assert sorted(z.temperature_c for z in laufend) == [Decimal("17.0"), Decimal("23.0")]
    absenkung = [z for z in anlage.uebersteuerungen(zone) if z.absence_id is not None]
    assert len(absenkung) == 1 and absenkung[0].cancelled_at is None
    assert absenkung[0].temperature_c == Decimal("17.0")

    # Das Rad noch einmal: ersetzt nur die Radzeile, die Absenkung bleibt.
    await anlage.rad(zone, 21.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2150)
    absenkung = [z for z in anlage.uebersteuerungen(zone) if z.absence_id is not None]
    assert len(absenkung) == 1 and absenkung[0].cancelled_at is None

    # Nach dem Ende der kurzen Übersteuerung gilt die Absenkung wieder.
    assert anlage.ui_uebersteuerung_beenden(zone)
    await anlage.publish_cycle()
    await anlage.matter_zeigt(zone, was="Absenkung gilt nicht wieder", occupiedHeatingSetpoint=1700)


# -- (j) Leitungsdisziplin ----------------------------------------------------------------


async def test_die_bruecke_sendet_nie_command_setpoint_und_nichts_ausser_den_zwei_befehlen(
    anlage: Anlage,
) -> None:
    zone = anlage.zonen["A"]
    await anlage.rad(zone, 22.5)
    await anlage.matter_bleibt(zone, occupiedHeatingSetpoint=2250)
    assert (await anlage.schreiben(zone, "systemMode", MATTER_AUS))["accepted"]
    await anlage.warten(lambda: anlage.modus(zone) == "off", "off fehlt")
    assert (await anlage.schreiben(zone, "systemMode", MATTER_HEIZEN))["accepted"]
    await anlage.warten(lambda: anlage.modus(zone) == "auto", "auto fehlt")
    await anlage.rad(anlage.zonen["B"], 20.0)
    await anlage.matter_bleibt(anlage.zonen["B"], occupiedHeatingSetpoint=2000)

    publikationen = await anlage.bruecken_publikationen()
    assert {
        BEFEHL.match(p["topic"]).group(2) for p in publikationen if BEFEHL.match(p["topic"])
    } == {  # type: ignore[union-attr]
        "temporary_setpoint",
        "operating_mode",
    }
    assert not any(p["topic"].endswith("/command/setpoint") for p in publikationen)
    assert all((p["qos"], p["retain"]) == (1, False) for p in publikationen)
    await anlage.leitung_pruefen()
    # Abonniert sind genau die fünf Zustände je Zone.
    clients = (await anlage._status())["clients"]
    bruecke = [t for id_, ts in clients.items() if not id_.endswith("-py") for t in ts]
    assert sorted(bruecke) == sorted(
        f"{PREFIX}/zones/{z}/state/{k}"
        for z in anlage.zonen.values()
        for k in (
            "current_temperature",
            "setpoint",
            "operating_mode",
            "would_heat",
            "override_active",
        )
    )


# -- (k) Zwei Zonen -----------------------------------------------------------------------


async def test_zwei_zonen_sind_unabhaengig(anlage: Anlage) -> None:
    a, b = anlage.zonen["A"], anlage.zonen["B"]
    await anlage.rad(a, 22.5)
    await anlage.rad(b, 19.5)
    await anlage.matter_zeigt(a, was="Zone A fehlt", occupiedHeatingSetpoint=2250)
    await anlage.matter_zeigt(b, was="Zone B fehlt", occupiedHeatingSetpoint=1950)
    await anlage.matter_bleibt(a, occupiedHeatingSetpoint=2250)
    await anlage.matter_bleibt(b, sekunden=0.0, occupiedHeatingSetpoint=1950)
    assert [z.temperature_c for z in anlage.uebersteuerungen(a)] == [Decimal("22.5")]
    assert [z.temperature_c for z in anlage.uebersteuerungen(b)] == [Decimal("19.5")]
    assert sorted(await anlage.bruecken_befehle()) == [
        ("temporary_setpoint", "19.5"),
        ("temporary_setpoint", "22.5"),
    ]

    # A beenden: B behält seinen Radwert.
    assert anlage.ui_uebersteuerung_beenden(a)
    await anlage.publish_cycle()
    await anlage.matter_zeigt(
        a, was="A fällt nicht auf den Plan zurück", occupiedHeatingSetpoint=2100
    )
    await anlage.matter_bleibt(b, occupiedHeatingSetpoint=1950)

    # B auf Aus: A bleibt auf Heizen.
    assert (await anlage.schreiben(b, "systemMode", MATTER_AUS))["accepted"]
    await anlage.warten(lambda: anlage.modus(b) == "off", "B steht nicht auf off")
    await anlage.matter_zeigt(b, was="B zeigt nicht Aus", systemMode=MATTER_AUS)
    await anlage.matter_bleibt(a, systemMode=MATTER_HEIZEN, occupiedHeatingSetpoint=2100)
    assert anlage.modus(a) == "auto"
