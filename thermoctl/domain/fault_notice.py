"""Pure derivation of fault notices from state transitions."""

from dataclasses import dataclass
from decimal import Decimal

from thermoctl.db.models.operations import Setting

# The three notice kinds a `FaultNotice` can carry -- named, not loose strings, so
# the gate below and every caller (including the interface, which offers a switch
# per kind) refer to the exact same values. Bound one-to-one to the three
# `setting.notify_*` columns; `notice_enabled` below is the only place that mapping
# is spelled out.
NOTICE_KIND_SENSOR_FAULT = "sensor_fault"
NOTICE_KIND_BRIDGE_FAULT = "bridge_fault"
NOTICE_KIND_COMMAND_FAILURE = "command_failure"
# A fourth kind, deliberately not folded into `NOTICE_KIND_SENSOR_FAULT`: a stuck
# reading is not a sensor failure -- `sensor_status` (and therefore `decide()`'s
# frost-protection fallback) is entirely untouched by it, the zone keeps regulating
# on the same value throughout (see `services/ingest.py::advance_zone_state`,
# `sensor_stuck`). Sharing one switch with real sensor failures would mean nobody
# could mute the (usually harmless, sometimes days-long) "unmoving but present"
# case without also muting the "actually gone" one, which is exactly the case
# that must keep reaching the fallback path.
NOTICE_KIND_STUCK_SENSOR = "stuck_sensor"
# A fifth kind: a window left open with cold air outside (`domain.window_alarm`).
# Neither a sensor problem nor a stuck reading -- its own switch for the same
# reason the fourth one got its own.
NOTICE_KIND_WINDOW_ALARM = "window_alarm"

#: Die Meldung, die ein Mensch ausdrücklich ausgelöst hat, nicht eine, die die
#: Regelung aus einem Zustandswechsel abgeleitet hat. Sie geht bewusst **nicht**
#: durch `notice_enabled`: Wer auf "Testen" drückt, will genau diese eine Meldung
#: hinausschicken -- ein Schalter, der sie unterdrückt, würde die Frage
#: beantworten, ob der Schalter steht, statt der, ob der Webhook erreichbar ist.
#: `notice_enabled` wirft für diese Art darum absichtlich weiterhin -- landet eine
#: Testmeldung je am Tor, ist das ein Fehler und soll auffallen, nicht durchrutschen.
NOTICE_KIND_TEST = "test"

#: Eine sechste Art: ein Mieter hat aus seinem Raum heraus ein Problem gemeldet
#: (`domain.problem_report`). Anders als die fünf oben leitet sie die Regelung nicht
#: aus einem Zustandswechsel ab -- ein Mensch hat sie ausgelöst. Trotzdem geht sie
#: durch dasselbe Tor: eine Anlage, deren Betreiber keine Mietermeldungen bekommen
#: will, muss sie abschalten können, ohne dafür den ganzen Webhook abzuräumen. Und
#: anders als die Testmeldung ist sie keine Antwort auf einen Knopf des Betreibers
#: selbst, sondern kommt von jemand anderem -- deshalb ein Schalter und keine
#: Ausnahme.
NOTICE_KIND_TENANT_REPORT = "tenant_report"


@dataclass(frozen=True)
class FaultNotice:
    key: str
    severity: str
    title: str
    text: str
    # Mandatory, not optional: a notice without a kind would silently slip past
    # `notice_enabled` below (a `None` or missing kind has to fail loudly, not
    # default to "always deliver" or "never deliver"). Every producer of a
    # `FaultNotice` in this codebase is required to set it explicitly.
    kind: str


def notice_enabled(kind: str, settings: Setting) -> bool:
    """Whether a notice of this kind should actually be delivered.

    The one gate every dispatch path (webhook, and any future channel) is meant to
    ask before sending -- not Home Assistant, which several notices already reach
    over their own path (`services/publishing.py::send_fault_notice`) regardless of
    this switch; see that module's docstring for why the two are deliberately not
    coupled.
    """
    if kind == NOTICE_KIND_SENSOR_FAULT:
        return settings.notify_sensor_faults
    if kind == NOTICE_KIND_BRIDGE_FAULT:
        return settings.notify_bridge_faults
    if kind == NOTICE_KIND_COMMAND_FAILURE:
        return settings.notify_command_failures
    if kind == NOTICE_KIND_STUCK_SENSOR:
        return settings.notify_stuck_sensor
    if kind == NOTICE_KIND_WINDOW_ALARM:
        return settings.notify_window_alarm
    if kind == NOTICE_KIND_TENANT_REPORT:
        return settings.notify_tenant_reports
    raise ValueError(f"Unbekannte Meldungsart {kind!r}")


def sensor_notice(
    key: str,
    zone_name: str,
    before: str | None,
    after: str,
    frost_protection_c: Decimal,
) -> FaultNotice | None:
    """Reports only entry into a sensor fault and its all-clear."""
    if (
        before is not None
        and after in {"veraltet", "keine_quelle"}
        and before != after
    ):
        reason = (
            "Der Temperaturwert ist veraltet. Die Zone regelt die Heizung bis auf Weiteres "
            f"gegen den Frostschutz-Sollwert von {frost_protection_c} °C."
            if after == "veraltet"
            else "Der Zone ist keine Temperaturquelle zugeordnet. Ohne Temperaturwert "
            "kann sie die Heizung nicht gegen den Frostschutz-Sollwert von "
            f"{frost_protection_c} °C regeln."
        )
        return FaultNotice(
            key=key,
            severity="stoerung",
            title=f"Sensorstörung in {zone_name}",
            text=reason,
            kind=NOTICE_KIND_SENSOR_FAULT,
        )
    if after == "ok" and before in {"veraltet", "keine_quelle"}:
        return FaultNotice(
            key=key,
            severity="entwarnung",
            title=f"Sensor in {zone_name} wieder in Ordnung",
            text=(
                "Die Temperaturquelle liefert wieder aktuelle Werte. "
                "Die Zone regelt die Heizung wieder normal."
            ),
            kind=NOTICE_KIND_SENSOR_FAULT,
        )
    return None


def stuck_sensor_notice(
    key: str, zone_name: str, before: bool | None, after: bool
) -> FaultNotice | None:
    """Reports only entry into, and recovery from, a suspiciously unmoving reading.

    The same "only the transition" shape as `sensor_notice` above, and the same
    `key` convention (`f"sensor:{zone.id}"`) -- the two can never fire in the same
    cycle for the same zone (`sensor_stuck` is only ever computed while
    `sensor_status` reads `ok`, see `services/ingest.py::advance_zone_state`), so
    sharing the key, and therefore the one Home Assistant entity it maps to
    (`fault_notice_discovery`), never overwrites one notice with the other.

    Deliberately **not** a fault in tone: `severity="stoerung"` still drives the
    Home Assistant indicator and the audit trail the same way a real fault would
    (the project owner's own words called it a "Störungsmeldung"), but the text
    must make unmistakable that nothing about the regulation itself has changed --
    otherwise whoever reads it goes looking for a stopped heater that isn't there.

    `before=None` (nothing tracked yet for this zone in this process -- the usual
    case right after a restart, or an installation upgraded straight into years of
    matching history already sitting in `measurement`) counts as "not known to be
    stuck", the same convention `bridge_notice` and `command_failure_notice` use for
    their own `before=None` -- not `sensor_notice`'s, which suppresses a first
    observation entirely. A zone already stuck the first time this process computes
    it still raises the alert; it does not wait for a second cycle to notice.
    """
    if after and before is not True:
        return FaultNotice(
            key=key,
            severity="stoerung",
            title=f"Messwert in {zone_name} bewegt sich nicht mehr",
            text=(
                "Der Temperaturwert hat sich über die eingestellte Dauer nicht "
                "verändert. Das kann ein hängender Sensor sein oder ein tatsächlich "
                "sehr stabiler Raum — die Zone regelt unverändert mit diesem Wert "
                "weiter, es findet kein Wechsel in den Frostschutz statt."
            ),
            kind=NOTICE_KIND_STUCK_SENSOR,
        )
    if not after and before is True:
        return FaultNotice(
            key=key,
            severity="entwarnung",
            title=f"Messwert in {zone_name} bewegt sich wieder",
            text=(
                "Der Temperaturwert verändert sich wieder — kein Hinweis mehr auf "
                "einen festhängenden Sensor."
            ),
            kind=NOTICE_KIND_STUCK_SENSOR,
        )
    return None


def window_alarm_notice(
    key: str, zone_name: str, before: bool | None, after: bool | None
) -> FaultNotice | None:
    """Reports only entry into, and recovery from, a forgotten open window.

    `after=None` (the outdoor reading is currently unknown --
    `domain.window_alarm.window_alarm_state`) never produces a notice, in either
    direction: an unknown state is not "no alarm", so it must not be read as an
    all-clear if an alarm was active a moment ago, and it obviously is not itself
    a new alarm. Whatever `before` was, nothing fires while `after` is `None` --
    the same reasoning `window_alarm_state`'s own docstring gives for why `None`
    must never collapse into `False` upstream of this function either.

    `before=None` counts as "not known to be alarming" (the usual case right
    after a restart), the same convention every other transition notice in this
    module uses for its own `before=None`.
    """
    if after is None:
        return None
    if after and before is not True:
        return FaultNotice(
            key=key,
            severity="stoerung",
            title=f"Fenster in {zone_name} vergessen offen",
            text=(
                "Ein Fenster steht seit Längerem offen, während es draußen kalt "
                "genug ist, um den Raum in Richtung Frostschutz auskühlen zu "
                "lassen."
            ),
            kind=NOTICE_KIND_WINDOW_ALARM,
        )
    if not after and before is True:
        return FaultNotice(
            key=key,
            severity="entwarnung",
            title=f"Fenster in {zone_name} nicht mehr auffällig",
            text=(
                "Entweder ist das Fenster wieder zu, oder die Außentemperatur "
                "liegt wieder über der eingestellten Schwelle."
            ),
            kind=NOTICE_KIND_WINDOW_ALARM,
        )
    return None


def bridge_notice(
    reachable_before: bool | None, reachable_after: bool
) -> FaultNotice | None:
    """Reports failure and recovery of the Zigbee2MQTT bridge, each exactly once."""
    if not reachable_after and reachable_before is not False:
        return FaultNotice(
            key="zigbee2mqtt:brücke",
            severity="stoerung",
            title="Zigbee2MQTT-Brücke nicht erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Brücke ist ausgefallen.",
            kind=NOTICE_KIND_BRIDGE_FAULT,
        )
    if reachable_after and reachable_before is False:
        return FaultNotice(
            key="zigbee2mqtt:brücke",
            severity="entwarnung",
            title="Zigbee2MQTT-Brücke wieder erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Brücke ist wiederhergestellt.",
            kind=NOTICE_KIND_BRIDGE_FAULT,
        )
    return None


def command_failure_notice(
    key: str,
    device_name: str,
    before_failed: bool | None,
    after_failed: bool,
) -> FaultNotice | None:
    """Reports only the transition into, and recovery from, a failing switching
    attempt for one device -- the same "only the transition" shape as
    `sensor_notice` and `bridge_notice` above, not one notice per control cycle.

    `before_failed=None` (nothing tracked yet for this device in this process --
    the usual case right after a restart) counts as "not known to be failing", the
    same convention `bridge_notice` uses for `reachable_before=None`: a device that
    is already failing the first time this process attempts it still raises the
    alert, it does not wait for a second attempt to notice.
    """
    if after_failed and before_failed is not True:
        return FaultNotice(
            key=key,
            severity="stoerung",
            title=f"Schaltbefehl an {device_name} gescheitert",
            text=(
                f"Ein Schaltbefehl an {device_name} ist fehlgeschlagen. Jeder "
                "weitere Regelzyklus versucht es erneut, bis er wieder durchgeht."
            ),
            kind=NOTICE_KIND_COMMAND_FAILURE,
        )
    if not after_failed and before_failed is True:
        return FaultNotice(
            key=key,
            severity="entwarnung",
            title=f"Schaltbefehl an {device_name} geht wieder durch",
            text=f"Schaltbefehle an {device_name} werden wieder erfolgreich ausgeführt.",
            kind=NOTICE_KIND_COMMAND_FAILURE,
        )
    return None


# The two audit-log action codes a dispatched `FaultNotice` can be recorded under.
# Named here, not spelled out at each call site, so the wording used in
# `thermoctl.audit` and the decision behind it (`notice_enabled` above) cannot
# drift apart -- see `notification_audit_action` below for why that would matter.
AUDIT_ACTION_NOTIFICATION_SENT = "notification.sent"
AUDIT_ACTION_NOTIFICATION_SUPPRESSED = "notification.suppressed"


def notification_audit_action(kind: str, settings: Setting | None) -> str:
    """The audit-log action code for a notice about to be handed to `notice_enabled`.

    The audit trail exists to be believed. Before this function existed, every
    dispatched `FaultNotice` was recorded with the same `"notification.sent"`
    action regardless of whether `notice_enabled` above actually let it through --
    so a notice kind switched off still left a trail claiming it was sent, which
    is exactly the kind of untrue user-visible claim this project has already
    paid for finding four times over (see `tests/test_user_visible_effect_texts.py`).
    Whoever later looks for why a fault notified nobody would find "sent" in the
    log and look for the bug in the wrong place.

    Deliberately still writes an entry either way: that the fault itself occurred
    belongs in the audit trail regardless of whether anyone was told about it --
    only the action code says which of the two happened. `settings=None` (before
    setup finishes, no `setting` row yet) counts as "sent", the same fail-open
    default every caller of `notice_enabled` already uses.

    This says nothing about whether a webhook attempt that *was* made actually
    reached its destination -- that is a separate question, answered by
    `setting.notify_last_ok`/`notify_last_error`
    (`integrations/notification.py::deliver`), not by the audit trail. The audit
    log records that this service dispatched a notification attempt, not that
    the network round-trip behind it succeeded; conflating the two here would
    make the audit trail depend on network timing it cannot observe at the
    moment the fault itself is recorded.
    """
    delivered = settings is None or notice_enabled(kind, settings)
    return AUDIT_ACTION_NOTIFICATION_SENT if delivered else AUDIT_ACTION_NOTIFICATION_SUPPRESSED
