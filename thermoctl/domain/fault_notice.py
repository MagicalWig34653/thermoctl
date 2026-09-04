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
            title=f"Sensorstoerung in {zone_name}",
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


def bridge_notice(
    reachable_before: bool | None, reachable_after: bool
) -> FaultNotice | None:
    """Reports failure and recovery of the Zigbee2MQTT bridge, each exactly once."""
    if not reachable_after and reachable_before is not False:
        return FaultNotice(
            key="zigbee2mqtt:bruecke",
            severity="stoerung",
            title="Zigbee2MQTT-Bruecke nicht erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist ausgefallen.",
            kind=NOTICE_KIND_BRIDGE_FAULT,
        )
    if reachable_after and reachable_before is False:
        return FaultNotice(
            key="zigbee2mqtt:bruecke",
            severity="entwarnung",
            title="Zigbee2MQTT-Bruecke wieder erreichbar",
            text="Die Verbindung zur Zigbee2MQTT-Bruecke ist wiederhergestellt.",
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
            text=f"Schaltbefehle an {device_name} werden wieder erfolgreich ausgefuehrt.",
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
