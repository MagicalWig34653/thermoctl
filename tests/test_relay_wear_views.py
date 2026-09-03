"""The relay-wear page protects the command log and turns counts into guidance."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tests.helpers import create_settings, create_zone
from thermoctl.domain.statistics import RelayDayValue, RelayDeviceStatistics


def test_relay_wear_requires_audit_read_and_a_visible_zone(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "nur-meine-zone")

    assert client_als([("zone.read", zone.id)]).get("/relay-wear").status_code == 403
    response = client_als([("audit.read", None)]).get("/relay-wear")

    assert response.status_code == 200
    assert "Keine für Sie sichtbaren Zonen" in response.text


def test_relay_wear_empty_period_is_explicit(client_als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "leise-zone")

    response = client_als([("audit.read", None), ("zone.read", zone.id)]).get(
        "/relay-wear?period=unbekannt"
    )

    assert response.status_code == 200
    assert "keine Schaltaktoren protokolliert" in response.text
    assert 'href="/relay-wear?period=7"' in response.text
    assert 'aria-current="page"' in response.text


def test_relay_wear_highlights_actionable_levels_and_the_visible_assumption(
    client_als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    visible = create_zone(session, "sichtbare-zone")
    hidden = create_zone(session, "geheime-zone")
    days = [date(2026, 8, 27) + timedelta(days=offset) for offset in range(7)]

    def statistics_for_visible_zones(
        _session: Session,
        zone_ids: list[int],
        _start_at: datetime,
        _until: datetime,
        *,
        timezone_name: str | None = None,
        assumed_lifetime_operations: int = 500_000,
    ) -> list[RelayDeviceStatistics]:
        assert zone_ids == [visible.id]
        assert timezone_name == "Europe/Berlin"
        # The default -- the view is expected to pass the setting's value through,
        # not to invent its own.
        assert assumed_lifetime_operations == 500_000
        return [
            RelayDeviceStatistics(
                visible.id,
                "Normalrelais",
                [RelayDayValue(day, 10) for day in days],
                assumed_lifetime_operations=assumed_lifetime_operations,
            ),
            RelayDeviceStatistics(
                visible.id,
                "Warnrelais",
                [RelayDayValue(day, 800) for day in days],
                assumed_lifetime_operations=assumed_lifetime_operations,
            ),
            RelayDeviceStatistics(
                visible.id,
                "Gefahrrelais",
                [RelayDayValue(day, 1500) for day in days],
                assumed_lifetime_operations=assumed_lifetime_operations,
            ),
        ]

    monkeypatch.setattr(
        "thermoctl.web.control_views.relay_operations", statistics_for_visible_zones
    )
    monkeypatch.setattr(
        "thermoctl.web.control_views.utcnow", lambda: datetime(2026, 9, 2, 12, 0)
    )

    response = client_als(
        [("audit.read", None), ("zone.read", visible.id)]
    ).get("/relay-wear")

    assert response.status_code == 200
    assert visible.display_name in response.text
    assert hidden.display_name not in response.text
    assert response.text.index("Gefahrrelais") < response.text.index("Warnrelais")
    assert "547.500 / Jahr" in response.text
    assert "mehr als eine angenommene Lebensdauer pro Jahr" in response.text
    assert "Erhöht: mehr als die Hälfte" in response.text
    assert "Unter der Warnschwelle von 50 %" in response.text
    assert "Annahme, keine Meross-Herstellerangabe" in response.text
    assert "500.000 elektrische Betätigungen" in response.text


def test_relay_wear_reflects_a_changed_assumption(
    client_als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same switching pattern is judged differently once the installation's
    own assumption is changed -- the page must read the setting, not a constant."""
    row = create_settings(session)
    row.assumed_relay_lifetime_operations = 100_000
    session.flush()
    visible = create_zone(session, "geaenderte-annahme")
    days = [date(2026, 8, 27) + timedelta(days=offset) for offset in range(7)]

    def statistics_at_the_installations_assumption(
        _session: Session,
        zone_ids: list[int],
        _start_at: datetime,
        _until: datetime,
        *,
        timezone_name: str | None = None,
        assumed_lifetime_operations: int = 500_000,
    ) -> list[RelayDeviceStatistics]:
        return [
            RelayDeviceStatistics(
                visible.id,
                "Relais",
                [RelayDayValue(day, 150) for day in days],
                assumed_lifetime_operations=assumed_lifetime_operations,
            )
        ]

    monkeypatch.setattr(
        "thermoctl.web.control_views.relay_operations",
        statistics_at_the_installations_assumption,
    )
    monkeypatch.setattr(
        "thermoctl.web.control_views.utcnow", lambda: datetime(2026, 9, 2, 12, 0)
    )

    response = client_als(
        [("audit.read", None), ("zone.read", visible.id)]
    ).get("/relay-wear")

    assert response.status_code == 200
    assert "100.000 elektrische Betätigungen" in response.text
    assert "Erhöht: mehr als die Hälfte" in response.text
