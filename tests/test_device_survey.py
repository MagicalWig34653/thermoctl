"""The thresholds after which a device stands out.

They live in the domain so they exist in exactly one place -- and that is why
they are checked individually here rather than only through the page: a page
that happens to show the right thing because two bugs cancel out is green and
still wrong.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from thermoctl.domain.device_survey import (
    BATTERY_LOW_PERCENT,
    RADIO_WEAK_LQI,
    DeviceSurvey,
    Finding,
    findings,
)

NOW = datetime(2026, 8, 30, 12, 0)


def _findings(**deviation) -> list[Finding]:
    arguments = {
        "active": True,
        "last_heard": NOW,
        "availability": "online",
        "battery": Decimal(80),
        "radio_quality": 120,
        "silent_after_seconds": 900,
        "now": NOW,
    }
    arguments.update(deviation)
    return findings(**arguments)


def test_a_healthy_device_has_nothing_to_report() -> None:
    assert _findings() == []


def test_disabled_offline_silent_battery_and_radio_are_each_detected_individually() -> None:
    kinds = {
        "disabled": _findings(active=False),
        "offline": _findings(availability="OFFLINE"),
        "silent": _findings(last_heard=None),
        "battery": _findings(battery=BATTERY_LOW_PERCENT),
        "radio": _findings(radio_quality=RADIO_WEAK_LQI - 1),
    }
    for kind, found in kinds.items():
        assert [b.kind for b in found] == [kind], kind
        assert found[0].text, "a finding without text helps no one"
    # The counter-check for each threshold: just below it, nothing is flagged.
    assert _findings(battery=BATTERY_LOW_PERCENT + 1) == []
    assert _findings(radio_quality=RADIO_WEAK_LQI) == []
    assert _findings(availability=None) == []


def test_silent_only_after_the_configured_grace_period_and_with_age_in_plain_text() -> None:
    """The grace period is the same one after which the control logic gives up on a sensor."""
    assert _findings(last_heard=NOW - timedelta(seconds=900)) == []
    for elapsed, word in (
        (timedelta(minutes=30), "seit 30 Minuten still"),
        (timedelta(hours=5), "seit 5 Stunden still"),
        (timedelta(days=1), "seit 1 Tag still"),
        (timedelta(days=3), "seit 3 Tagen still"),
    ):
        found = _findings(last_heard=NOW - elapsed)
        assert [b.text for b in found] == [word]


def test_a_never_reconciled_meross_device_is_not_told_it_never_reported() -> None:
    """A Meross socket never sends anything on its own -- it is only ever confirmed

    online by our hourly cloud reconciliation. Wording it the Zigbee2MQTT way ("hat
    sich noch nie gemeldet") claims a reporting channel that plain does not exist for
    this integration, and is simply false when the reconciliation just hasn't run
    yet, as opposed to the device being broken.
    """
    found = _findings(last_heard=None, last_heard_kind="abgeglichen")
    assert [b.text for b in found] == ["wurde noch nie als online abgeglichen"]
    assert "gemeldet" not in found[0].text


def test_a_stale_meross_reconciliation_is_worded_as_a_reconciliation_not_a_silence() -> None:
    """The same elapsed time reads differently depending on what `last_heard` means.

    For Zigbee2MQTT it is "seit X still" -- the device itself has gone quiet. For
    Meross it must not claim that, because the device may be perfectly reachable and
    simply hasn't been reconciled -- or the cloud has stopped naming it. Both text and
    finding stay distinct from the Zigbee2MQTT wording for the identical inputs.
    """
    zigbee = _findings(last_heard=NOW - timedelta(hours=5))
    meross = _findings(last_heard=NOW - timedelta(hours=5), last_heard_kind="abgeglichen")
    assert [b.text for b in zigbee] == ["seit 5 Stunden still"]
    assert [b.text for b in meross] == ["seit 5 Stunden nicht mehr als online bestätigt"]
    assert zigbee[0].text != meross[0].text
    # Both remain "silent" findings for severity ranking -- the reader still needs
    # to see it ranked as urgently as an offline Zigbee2MQTT device.
    assert zigbee[0].kind == meross[0].kind == "silent"


def test_severity_ranks_failure_before_early_warning() -> None:
    """A silent device ranks ahead of a weak battery.

    Without this ordering, the half-empty cell would show above and the
    failed sensor below it -- the page would then point to the wrong thing.
    """

    def build_survey(*findings: Finding) -> DeviceSurvey:
        return DeviceSurvey(
            device_id=1,
            name="x",
            model=None,
            integration="Zigbee2MQTT",
            ist_group=False,
            capabilities=[],
            zones=[],
            last_heard=None,
            battery=None,
            radio_quality=None,
            findings=list(findings),
        )

    offline = build_survey(Finding("offline", "o"))
    battery = build_survey(Finding("battery", "b"))
    healthy = build_survey()
    assert offline.severity < battery.severity < healthy.severity
    assert healthy.is_fine and not offline.is_fine
    # Multiple findings: the most urgent one determines the ranking.
    mixed = build_survey(Finding("battery", "b"), Finding("offline", "o"))
    assert mixed.severity == offline.severity
    # An unknown kind lands at the back instead of tripping things up.
    assert build_survey(Finding("novel", "?")).severity > battery.severity
