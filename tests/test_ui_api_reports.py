"""Auswertung unter `/ui-api`: Verlauf (JSON, CSV), Heizzeit, Verschleiß, Ereignisse, Rücknahme."""

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import (
    create_device,
    create_device_command,
    create_settings,
    create_user,
    create_zone,
    source,
)
from thermoctl import audit
from thermoctl.auth.csrf import CSRF_HEADER, csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain import audit_query, history_report
from thermoctl.domain import change_log as cl
from thermoctl.domain.authz import Forbidden
from thermoctl.domain.change_log import Actor
from thermoctl.domain.schedule import ScheduleError
from thermoctl.domain.sensor_failure_policy import PolicyError
from thermoctl.domain.time import local_time
from thermoctl.domain.zone_settings import ParameterOutOfRange
from thermoctl.web import ui_api_reports

Als = Callable[[list[tuple[str, int | None]]], TestClient]


def _csrf(client: TestClient) -> dict[str, str]:
    http_session = client.cookies.get(COOKIE_NAME)
    assert http_session is not None
    return {CSRF_HEADER: csrf_token(http_session, get_settings().secret_key.get_secret_value())}


def _fehler(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def _now() -> datetime:
    return utcnow().replace(second=0, microsecond=0)


def _row(
    zone: Zone,
    at: datetime,
    *,
    temp: str | None = "20.0",
    effective: str | None = "21.0",
    scheduled: str | None = "21.0",
    heat: bool = False,
    solar: str | None = None,
) -> ShadowDecision:
    return ShadowDecision(
        decided_at=at,
        zone_id=zone.id,
        setpoint_reason="Zeitplan",
        would_heat=heat,
        outcome_code="aus",
        reason="Test",
        temperature_c=Decimal(temp) if temp is not None else None,
        setpoint_c=Decimal(effective) if effective is not None else None,
        scheduled_setpoint_c=Decimal(scheduled) if scheduled is not None else None,
        solar_setback_k=Decimal(solar) if solar is not None else None,
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _params(zone: Zone, start: datetime, end: datetime, **extra: object) -> dict[str, object]:
    return {"zone_id": zone.id, "from": _iso(start), "to": _iso(end), **extra}


# -- Verlauf: Zugriff ---------------------------------------------------------------------


def test_history_and_csv_without_session_are_401(client: TestClient) -> None:
    for path in ("/ui-api/history", "/ui-api/history.csv"):
        response = client.get(path, params={"zone_id": 1, "from": "2026-01-01", "to": "2026-01-02"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Nicht angemeldet"}


@pytest.mark.parametrize("path", ["/ui-api/history", "/ui-api/history.csv"])
def test_history_foreign_and_unknown_zone_are_the_same_404(
    client_als: Als, session: Session, path: str
) -> None:
    create_settings(session)
    mine = create_zone(session, "meine")
    foreign = create_zone(session, "fremde")
    end = _now()
    client = client_als([("zone.read", mine.id)])
    start = end - timedelta(hours=1)
    unknown = _fehler(
        client.get(path, params={"zone_id": 99999, "from": _iso(start), "to": _iso(end)}),
        404,
        "not_found",
    )
    other = _fehler(client.get(path, params=_params(foreign, start, end)), 404, "not_found")
    assert unknown == other


def test_history_without_any_read_right_is_404_not_an_existence_hint(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "z")
    end = _now()
    client = client_als([("audit.read", None)])
    _fehler(
        client.get("/ui-api/history", params=_params(zone, end - timedelta(hours=1), end)),
        404,
        "not_found",
    )


def test_history_tenant_profile_may_read_own_zone(tenant_client: Als, session: Session) -> None:
    zone = create_zone(session, "wohnung")
    end = _now()
    response = tenant_client([("zone.read", zone.id)]).get(
        "/ui-api/history", params=_params(zone, end - timedelta(hours=1), end)
    )
    assert response.status_code == 200


# -- Verlauf: Daten, Lücken -------------------------------------------------------------


def test_history_gaps_stay_gaps_and_values_are_exact(client_als: Als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "verlauf")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(hours=1)  # 60 Fächer à 60 s
    # Minuten 0..4 Messwerte, dann 30 Minuten nichts, dann Minute 35..36.
    for minute, temp in enumerate(["19.0", "20.0", "21.0", "20.5", "19.5"]):
        session.add(
            _row(zone, start + timedelta(minutes=minute), temp=temp, heat=minute < 2, solar="1.5")
        )
    session.add(
        _row(
            zone,
            start + timedelta(minutes=35),
            temp=None,
            effective="22.0",
            scheduled=None,
            solar=None,
        )
    )
    session.add(
        _row(zone, start + timedelta(minutes=36), temp="18.25", effective=None, scheduled="20.0")
    )
    session.flush()
    client = client_als([("zone.read", zone.id)])

    body = client.get("/ui-api/history", params=_params(zone, start, end)).json()

    assert body["zone_id"] == zone.id and body["zone_name"] == zone.display_name
    assert body["timezone"] == "Europe/Berlin"
    assert body["units"] == {"temperature": "°C", "setback": "K", "heat": "%"}
    assert body["resolution_seconds"] == 60
    assert body["rows"] == 7
    assert body["limits"]["gap_threshold_seconds"] == 600
    buckets = body["buckets"]
    assert len(buckets) == 60
    first = buckets[0]
    assert first["start"] == _iso(start.replace(tzinfo=UTC))
    assert first["rows"] == 1 and first["measurements"] == 1
    assert first["temperature_c"] == "19.0"
    assert first["heat_percent"] == 100 and first["solar_setback_k"] == "1.5"
    assert first["solar_setback_min_k"] == "1.5"
    assert first["effective_min_c"] == "21.0" and first["scheduled_max_c"] == "21.0"
    # Lücke: jedes Fach dazwischen ist ausdrücklich leer -- nichts interpoliert.
    for index in range(5, 35):
        empty = buckets[index]
        assert empty["rows"] == 0 and empty["measurements"] == 0
        assert empty["temperature_c"] is None and empty["temperature_min_c"] is None
        assert empty["scheduled_min_c"] is None and empty["effective_max_c"] is None
        assert empty["heat_percent"] is None and empty["solar_setback_k"] is None
        assert empty["solar_setback_min_k"] is None
    # Zeile ohne Messwert: belegt, aber ohne Temperatur; unbekannte Absenkung bleibt null.
    sensor_out = buckets[35]
    assert sensor_out["rows"] == 1 and sensor_out["measurements"] == 0
    assert sensor_out["temperature_c"] is None
    assert sensor_out["effective_min_c"] == "22.0" and sensor_out["scheduled_min_c"] is None
    assert sensor_out["solar_setback_k"] is None and sensor_out["heat_percent"] == 0
    assert sensor_out["solar_setback_min_k"] is None
    # Sollwert fehlt in dieser Zeile: null, nicht der Vorgänger.
    assert buckets[36]["effective_min_c"] is None and buckets[36]["scheduled_min_c"] == "20.0"
    assert buckets[36]["temperature_c"] == "18.25"
    # Lücken als Spannen: 4 min -> 35 min und 36 min -> Fensterende.
    assert body["gaps"] == [
        {
            "start": _iso((start + timedelta(minutes=4)).replace(tzinfo=UTC)),
            "end": _iso((start + timedelta(minutes=35)).replace(tzinfo=UTC)),
        },
        {
            "start": _iso((start + timedelta(minutes=36)).replace(tzinfo=UTC)),
            "end": _iso(end.replace(tzinfo=UTC)),
        },
    ]


def test_history_bucket_mean_min_max_and_setpoint_changes(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "fach")
    end = _now() - timedelta(hours=2)
    start = end - timedelta(hours=2)
    # Auflösung 10 min: vier Zeilen in einem Fach, Sollwert wechselt darin.
    for minute, (temp, eff) in enumerate(
        [("20.0", "21.0"), ("21.0", "21.0"), ("22.0", "17.0"), ("19.0", "17.0")]
    ):
        session.add(
            _row(
                zone, start + timedelta(minutes=minute), temp=temp, effective=eff, heat=minute == 0
            )
        )
    session.flush()
    body = (
        client_als([("zone.read", zone.id)])
        .get("/ui-api/history", params=_params(zone, start, end, resolution=600))
        .json()
    )
    assert body["resolution_seconds"] == 600
    bucket = body["buckets"][0]
    assert bucket["temperature_c"] == "20.5"
    assert (bucket["temperature_min_c"], bucket["temperature_max_c"]) == ("19.0", "22.0")
    # Kein Mittel über den Sprung: beide Werte stehen da.
    assert (bucket["effective_min_c"], bucket["effective_max_c"]) == ("17.0", "21.0")
    assert bucket["heat_percent"] == 25


def test_history_says_whether_the_plant_is_armed(client_als: Als, session: Session) -> None:
    """Im Trockenlauf zeigt der Verlauf Entscheidungen („hätte“), keine Aufzeichnung der Räume."""
    zone = create_zone(session, "trocken")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(minutes=5)
    session.add(_row(zone, start))
    session.flush()
    client = client_als([("zone.read", zone.id)])
    params = _params(zone, start, end)
    # Ohne Einrichtungszeile: nicht scharf.
    assert session.get(Setting, 1) is None
    assert client.get("/ui-api/history", params=params).json()["control_armed"] is False
    row = create_settings(session)
    assert client.get("/ui-api/history", params=params).json()["control_armed"] is False
    row.control_armed = True
    session.flush()
    assert client.get("/ui-api/history", params=params).json()["control_armed"] is True


def test_history_bucket_labels_a_changing_setback_with_its_range(
    client_als: Als, session: Session
) -> None:
    """Wechselt die Absenkung im Fach, liefert es Kleinst- und Höchstwert, nie nur Letzteren."""
    zone = create_zone(session, "spanne")
    end = _now() - timedelta(hours=2)
    start = end - timedelta(hours=2)
    # Auflösung 10 min: Absenkung 0,5 / 2,0 / „keine Absenkung“ (Zeile mit Plansoll, ohne
    # Absenkung = 0 K) / 3,0 in einem Fach.
    for minute, solar in enumerate(["0.5", "2.0", None, "3.0"]):
        session.add(_row(zone, start + timedelta(minutes=minute), solar=solar))
    # Zweites Fach: gleichbleibende Absenkung -- Minimum und Maximum fallen zusammen.
    for minute in (10, 11):
        session.add(_row(zone, start + timedelta(minutes=minute), solar="1.0"))
    # Drittes Fach: Zeile aus der Zeit vor der Spalte (kein Plansoll, keine Absenkung) --
    # unbekannt, nicht null Kelvin.
    session.add(_row(zone, start + timedelta(minutes=20), scheduled=None, solar=None))
    session.flush()
    body = (
        client_als([("zone.read", zone.id)])
        .get("/ui-api/history", params=_params(zone, start, end, resolution=600))
        .json()
    )
    first, second, third = body["buckets"][:3]
    assert (first["solar_setback_min_k"], first["solar_setback_k"]) == ("0.0", "3.0")
    assert (second["solar_setback_min_k"], second["solar_setback_k"]) == ("1.0", "1.0")
    assert third["solar_setback_min_k"] is None and third["solar_setback_k"] is None


def test_history_row_exactly_at_end_belongs_to_last_bucket(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "ende")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(minutes=10)
    session.add(_row(zone, end))
    session.flush()
    body = (
        client_als([("zone.read", zone.id)])
        .get("/ui-api/history", params=_params(zone, start, end))
        .json()
    )
    assert len(body["buckets"]) == 10 and body["buckets"][-1]["rows"] == 1


def test_history_future_is_not_a_gap_and_empty_past_is(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "zukunft")
    now = _now()
    session.add(_row(zone, now - timedelta(minutes=1)))
    session.flush()
    client = client_als([("zone.read", zone.id)])
    body = client.get(
        "/ui-api/history",
        params=_params(zone, now - timedelta(minutes=5), now + timedelta(hours=2)),
    ).json()
    assert body["gaps"] == []
    empty = client.get(
        "/ui-api/history",
        params=_params(zone, now - timedelta(days=3), now - timedelta(days=2)),
    ).json()
    assert empty["rows"] == 0 and len(empty["gaps"]) == 1
    assert all(b["rows"] == 0 and b["temperature_c"] is None for b in empty["buckets"])


def test_history_time_zone_offsets_are_converted_and_naive_means_utc(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "offset")
    base = _now() - timedelta(hours=5)
    client = client_als([("zone.read", zone.id)])
    aware = client.get(
        "/ui-api/history",
        params={
            "zone_id": zone.id,
            "from": f"{base.isoformat()}+02:00",
            "to": f"{(base + timedelta(hours=3)).isoformat()}+02:00",
        },
    ).json()
    assert aware["start"] == (base - timedelta(hours=2)).replace(tzinfo=UTC).isoformat()
    naive = client.get(
        "/ui-api/history", params=_params(zone, base, base + timedelta(hours=1))
    ).json()
    assert naive["start"] == base.replace(tzinfo=UTC).isoformat()


# -- Verlauf: Grenzen ---------------------------------------------------------------------


def test_history_input_limits(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "grenzen")
    now = _now()
    client = client_als([("zone.read", zone.id)])

    def get(**kw: object) -> Any:
        return client.get("/ui-api/history", params={"zone_id": zone.id, **kw})

    _fehler(get(**{"from": _iso(now), "to": _iso(now)}), 422, "validation")
    too_long = get(**{"from": _iso(now - timedelta(days=31, seconds=1)), "to": _iso(now)})
    assert _fehler(too_long, 422, "validation")["field"] == "to"
    assert (
        _fehler(get(**{"from": "gestern", "to": _iso(now)}), 422, "validation")["field"] == "from"
    )
    assert (
        _fehler(get(**{"from": _iso(now - timedelta(hours=1)), "to": "morgen"}), 422, "validation")[
            "field"
        ]
        == "to"
    )
    assert (
        _fehler(
            get(**{"from": _iso(now - timedelta(hours=1)), "to": _iso(now), "resolution": 0}),
            422,
            "validation",
        )["field"]
        == "resolution"
    )
    # Pflichtangaben fehlen / falscher Typ: einheitliche Fehlerform.
    assert client.get("/ui-api/history", params={"zone_id": zone.id}).status_code == 422
    assert client.get("/ui-api/history", params={"zone_id": "x"}).json()["code"] == "validation"


def test_history_31_days_is_allowed_with_bucket_cap(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "monat")
    now = _now()
    client = client_als([("zone.read", zone.id)])
    body = client.get("/ui-api/history", params=_params(zone, now - timedelta(days=31), now)).json()
    assert body["resolution_seconds"] == 3720  # 31 d / 720 Fächer, aufgerundet
    assert len(body["buckets"]) == 720
    # Eine feinere Auflösung wird angehoben, eine gröbere gilt.
    finer = client.get(
        "/ui-api/history", params=_params(zone, now - timedelta(days=31), now, resolution=60)
    ).json()
    assert finer["resolution_seconds"] == 3720
    coarser = client.get(
        "/ui-api/history", params=_params(zone, now - timedelta(days=31), now, resolution=86400)
    ).json()
    assert coarser["resolution_seconds"] == 86400 and len(coarser["buckets"]) == 31


def test_history_too_many_rows_is_an_error_not_a_silent_cut(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    zone = create_zone(session, "voll")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(hours=1)
    for minute in range(6):
        session.add(_row(zone, start + timedelta(minutes=minute)))
    session.flush()
    monkeypatch.setattr(history_report, "MAX_ROWS", 5)
    client = client_als([("zone.read", zone.id)])
    body = _fehler(
        client.get("/ui-api/history", params=_params(zone, start, end)), 422, "validation"
    )
    assert body["field"] == "to"
    monkeypatch.setattr(history_report, "MAX_ROWS", 6)
    assert client.get("/ui-api/history", params=_params(zone, start, end)).json()["rows"] == 6


def test_history_many_samples_stay_bounded(client_als: Als, session: Session) -> None:
    zone = create_zone(session, "dicht")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(days=2)
    session.bulk_save_objects(
        [_row(zone, start + timedelta(minutes=m)) for m in range(2 * 24 * 60)]
    )
    session.flush()
    body = (
        client_als([("zone.read", zone.id)])
        .get("/ui-api/history", params=_params(zone, start, end))
        .json()
    )
    assert body["rows"] == 2 * 24 * 60
    assert len(body["buckets"]) <= 720
    assert body["resolution_seconds"] == 240
    assert body["gaps"] == []


def test_resolve_resolution_rules() -> None:
    day = timedelta(days=1)
    assert history_report.resolve_resolution(timedelta(minutes=10), None) == 60
    assert history_report.resolve_resolution(day, None) == 120
    assert history_report.resolve_resolution(day, 30) == 120
    assert history_report.resolve_resolution(day, 7200) == 7200
    with pytest.raises(history_report.ReportRangeError):
        history_report.check_window(_now(), _now())


# -- CSV ----------------------------------------------------------------------------------


def test_history_csv_has_the_same_data_and_guards_formulas(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "=HYPERLINK(1)")
    end = _now() - timedelta(hours=1)
    start = end - timedelta(minutes=5)
    session.add(_row(zone, start, temp="-1.50", effective="-3.0", scheduled="-3.0"))
    session.flush()
    client = client_als([("zone.read", zone.id)])
    params = _params(zone, start, end)

    response = client.get("/ui-api/history.csv", params=params)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"] == f'attachment; filename="verlauf-{zone.id}.csv"'
    )
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:3] == ["zone", "bucket_start_utc", "rows"]
    assert rows[0][-2:] == ["solar_setback_min_k", "solar_setback_k"]
    assert len(rows) == 1 + 5
    data = dict(zip(rows[0], rows[1], strict=True))
    assert zone.display_name.startswith("=")
    assert data["zone"] == "'" + zone.display_name  # Text geschützt
    assert data["temperature_c"] == "-1.5"  # Zahl bleibt Zahl
    assert data["rows"] == "1" and data["heat_percent"] == "0"
    empty = dict(zip(rows[0], rows[2], strict=True))
    assert empty["rows"] == "0" and empty["temperature_c"] == ""
    # Dieselben Zahlen wie das JSON.
    json_body = client.get("/ui-api/history", params=params).json()
    assert [r[1] for r in rows[1:]] == [b["start"] for b in json_body["buckets"]]


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_csv_cell_neutralises_every_formula_start(text: str) -> None:
    assert ui_api_reports.csv_cell(text) == "'" + text


def test_csv_cell_leaves_ordinary_text_alone() -> None:
    assert ui_api_reports.csv_cell("Wohnzimmer") == "Wohnzimmer"
    assert ui_api_reports.csv_cell("") == ""


# -- Heizzeit -----------------------------------------------------------------------------


def test_heating_time_access(client_als: Als, session: Session) -> None:
    create_settings(session)
    mine = create_zone(session, "mein")
    foreign = create_zone(session, "fremd")
    # 403: kein zone.read irgendwo
    _fehler(client_als([("audit.read", None)]).get("/ui-api/heating-time"), 403, "forbidden")
    client = client_als([("zone.read", mine.id)])
    # 404: fremde Zone wie unbekannte
    a = _fehler(
        client.get("/ui-api/heating-time", params={"zone_id": foreign.id}), 404, "not_found"
    )
    b = _fehler(client.get("/ui-api/heating-time", params={"zone_id": 99999}), 404, "not_found")
    assert a == b
    _fehler(client.get("/ui-api/heating-time", params={"period": "5"}), 422, "validation")
    body = client.get("/ui-api/heating-time").json()
    assert [z["zone_id"] for z in body["zones"]] == [mine.id]


def test_heating_time_without_session_is_401(client: TestClient) -> None:
    assert client.get("/ui-api/heating-time").status_code == 401


def test_heating_time_counts_seconds_and_marks_dry_run(client_als: Als, session: Session) -> None:
    settings = create_settings(session)
    zone = create_zone(session, "heiz")
    now = _now()
    for minutes_ago, heat in [(4, True), (3, True), (2, False), (1, False)]:
        session.add(_row(zone, now - timedelta(minutes=minutes_ago), heat=heat))
    session.flush()
    client = client_als([("zone.read", zone.id)])

    body = client.get("/ui-api/heating-time", params={"period": "7"}).json()

    assert body["timezone"] == "Europe/Berlin" and body["period"] == "7"
    assert body["control_armed"] is False
    (entry,) = body["zones"]
    assert entry["zone_name"] == zone.display_name
    assert entry["seconds_total"] == 120
    assert len(entry["days"]) in {7, 8}
    assert sum(d["seconds"] for d in entry["days"]) == 120
    settings.control_armed = True
    session.flush()
    assert client.get("/ui-api/heating-time").json()["control_armed"] is True


def test_heating_time_and_relay_wear_tolerate_missing_settings(
    client_als: Als, session: Session
) -> None:
    zone = create_zone(session, "ohne")
    assert session.get(Setting, 1) is None
    client = client_als([("zone.read", zone.id), ("audit.read", None)])
    heating = client.get("/ui-api/heating-time").json()
    assert heating["timezone"] == "UTC" and heating["control_armed"] is False
    wear = client.get("/ui-api/relay-wear").json()
    assert wear["timezone"] == "UTC" and wear["assumed_lifetime_operations"] == 500_000


# -- Relaisverschleiß ---------------------------------------------------------------------


def test_relay_wear_access(client_als: Als, tenant_client: Als, session: Session) -> None:
    create_settings(session)
    zone = create_zone(session, "relais")
    foreign = create_zone(session, "fremd")
    _fehler(
        tenant_client([("audit.read", None), ("zone.read", zone.id)]).get("/ui-api/relay-wear"),
        403,
        "wrong_profile",
    )
    _fehler(client_als([("zone.read", zone.id)]).get("/ui-api/relay-wear"), 403, "forbidden")
    # audit.read nur zonenbezogen reicht nicht (anlagenweites Protokoll)
    _fehler(
        client_als([("audit.read", zone.id), ("zone.read", zone.id)]).get("/ui-api/relay-wear"),
        403,
        "forbidden",
    )
    client = client_als([("audit.read", None), ("zone.read", zone.id)])
    _fehler(client.get("/ui-api/relay-wear", params={"zone_id": foreign.id}), 404, "not_found")
    _fehler(client.get("/ui-api/relay-wear", params={"period": "x"}), 422, "validation")
    assert client.get("/ui-api/relay-wear").json()["devices"] == []


def test_relay_wear_without_session_is_401(client: TestClient) -> None:
    assert client.get("/ui-api/relay-wear").status_code == 401


def test_relay_wear_counts_confirmed_transitions_for_visible_zones_only(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    zone = create_zone(session, "relais")
    hidden = create_zone(session, "versteckt")
    relay = create_device(session, "relais-1")
    hidden_relay = create_device(session, "relais-2")
    now = _now()
    for minutes_ago, state in [(30, "ON"), (20, "OFF"), (10, "ON")]:
        for target, device in ((zone, relay), (hidden, hidden_relay)):
            create_device_command(
                session,
                target,
                device,
                at=now - timedelta(minutes=minutes_ago),
                command="switch",
                payload=f'{{"state": "{state}"}}',
            )
    client = client_als([("audit.read", None), ("zone.read", zone.id)])

    body = client.get("/ui-api/relay-wear", params={"period": "7"}).json()

    (device,) = body["devices"]
    assert device["zone_id"] == zone.id and device["zone_name"] == zone.display_name
    assert device["device_name"] == "relais-1"
    assert device["operations_total"] == 2
    assert device["annual_projection"] > 0
    assert device["wear_level"] == "normal"
    assert device["lifetime_years"] is not None
    assert sum(d["operations"] for d in device["days"]) == 2
    assert body["assumed_lifetime_operations"] == 500_000


# -- Ereignisse ---------------------------------------------------------------------------


def _events(session: Session, count: int = 3) -> None:
    source(session, "web")
    source(session, "api")
    for index in range(count):
        audit.record(
            session,
            source="web" if index % 2 == 0 else "api",
            action="zone.updated" if index % 2 == 0 else "token.issued",
            object_type="zone",
            object_id=str(index),
            summary=f"Ereignis {index}",
            detail=f"Detail {index}",
        )
    session.flush()


def test_audit_access(client_als: Als, tenant_client: Als, client: TestClient) -> None:
    assert client.get("/ui-api/audit").status_code == 401
    _fehler(tenant_client([("audit.read", None)]).get("/ui-api/audit"), 403, "wrong_profile")
    _fehler(client_als([("zone.read", None)]).get("/ui-api/audit"), 403, "forbidden")


def test_audit_lists_filters_and_names_the_actor(client_als: Als, session: Session) -> None:
    create_settings(session)
    _events(session, 4)
    owner = create_user(session, "besitzer")
    token = ApiToken(user_id=owner.id, name="mein-token", token_hash="h" * 64, prefix="abc")
    session.add(token)
    session.flush()
    audit.record(
        session,
        source="api",
        action="token.used",
        object_type="token",
        summary="mit Token",
        token_id=token.id,
    )
    session.flush()
    client = client_als([("audit.read", None)])

    body = client.get("/ui-api/audit").json()
    assert body["page"] == 1 and body["page_size"] == 50 and body["has_more"] is False
    assert body["timezone"] == "Europe/Berlin"
    assert len(body["entries"]) == 5
    newest = body["entries"][0]
    assert newest["summary"] == "mit Token" and newest["actor"] == "mein-token"
    assert newest["source"] == {"code": "api", "label": "REST-API"}
    assert newest["occurred_at"].endswith("+00:00")
    anonymous = body["entries"][1]
    assert anonymous["actor"] is None and anonymous["detail"] == "Detail 3"
    assert {s["code"] for s in body["sources"]} >= {"web", "api"}
    assert body["actions"] == sorted({"zone.updated", "token.issued", "token.used"})

    def summaries(**params: str) -> list[str]:
        return [e["summary"] for e in client.get("/ui-api/audit", params=params).json()["entries"]]

    assert summaries(action_code="zone.updated") == ["Ereignis 2", "Ereignis 0"]
    assert summaries(source="web") == ["Ereignis 2", "Ereignis 0"]
    assert summaries(object="zone:1") == ["Ereignis 1"]
    assert len(summaries(object="zone")) == 4
    assert summaries(user="mein-token") == ["mit Token"]
    today = local_time(utcnow(), "Europe/Berlin").date().isoformat()
    assert len(summaries(from_date=today, to_date=today)) == 5
    assert summaries(from_date="2999-01-01") == []
    assert summaries(to_date="2000-01-01") == []


def test_audit_pagination_and_validation(client_als: Als, session: Session) -> None:
    create_settings(session)
    _events(session, 51)
    client = client_als([("audit.read", None)])
    first = client.get("/ui-api/audit").json()
    assert first["has_more"] is True and len(first["entries"]) == 50
    second = client.get("/ui-api/audit", params={"page": "2"}).json()
    assert second["has_more"] is False and [e["summary"] for e in second["entries"]] == [
        "Ereignis 0"
    ]
    assert client.get("/ui-api/audit", params={"page": "-4"}).json()["page"] == 1
    assert (
        _fehler(client.get("/ui-api/audit", params={"page": "x"}), 422, "validation")["field"]
        == "page"
    )
    assert (
        _fehler(client.get("/ui-api/audit", params={"from_date": "kaputt"}), 422, "validation")[
            "field"
        ]
        == "from_date"
    )
    assert (
        _fehler(
            client.get(
                "/ui-api/audit", params={"from_date": "2026-02-02", "to_date": "2026-02-01"}
            ),
            422,
            "validation",
        )["field"]
        == "to_date"
    )


def _record(session: Session, summary: str, **extra: Any) -> None:
    source(session, "web")
    audit.record(
        session,
        source="web",
        action=extra.pop("action", "zone.updated"),
        object_type=extra.pop("object_type", "zone"),
        summary=summary,
        **extra,
    )
    session.flush()


def test_audit_free_text_searches_summary_detail_and_object(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    _record(session, "Sollwert Küche", object_id="7")
    _record(session, "Anderes", detail="Grund: FROSTSCHUTZ aktiv", object_id="8")
    _record(session, "Drittes", object_type="gadget", object_id="kessel-9")
    client = client_als([("audit.read", None)])

    def found(q: str) -> list[str]:
        response = client.get("/ui-api/audit", params={"q": q})
        return [e["summary"] for e in response.json()["entries"]]

    assert found("küche") == ["Sollwert Küche"]
    assert found("frostschutz") == ["Anderes"]
    assert found("kessel") == ["Drittes"]
    assert found("gadget") == ["Drittes"]
    assert found("  küche  ") == ["Sollwert Küche"]
    assert found("gibtesnicht") == []
    assert len(found("")) == 3


def test_audit_free_text_wildcards_are_literal(client_als: Als, session: Session) -> None:
    create_settings(session)
    _record(session, "100% sicher")
    _record(session, "a_b")
    _record(session, "axb")
    _record(session, "pfad\\datei")
    client = client_als([("audit.read", None)])

    def found(q: str) -> list[str]:
        response = client.get("/ui-api/audit", params={"q": q})
        return [e["summary"] for e in response.json()["entries"]]

    assert found("%") == ["100% sicher"]
    assert found("_") == ["a_b"]
    assert found("a_b") == ["a_b"]
    assert found("\\") == ["pfad\\datei"]
    assert found("/") == []


def test_audit_free_text_length_is_limited(client_als: Als, session: Session) -> None:
    create_settings(session)
    client = client_als([("audit.read", None)])
    assert client.get("/ui-api/audit", params={"q": "x" * 100}).status_code == 200
    assert _fehler(client.get("/ui-api/audit", params={"q": "x" * 101}), 422, "validation")[
        "field"
    ] == "q"
    assert _fehler(client.get("/ui-api/audit.csv", params={"q": "x" * 101}), 422, "validation")[
        "field"
    ] == "q"


def test_audit_room_filter_and_room_list_show_only_visible_rooms(
    client_als: Als, session: Session
) -> None:
    create_settings(session)
    sichtbar = create_zone(session, "Sichtbar")
    versteckt = create_zone(session, "Versteckt")
    _record(session, "im sichtbaren", object_id=str(sichtbar.id))
    _record(session, "im versteckten", object_id=str(versteckt.id))
    client = client_als([("audit.read", None), ("zone.read", sichtbar.id)])

    body = client.get("/ui-api/audit", params={"object": f"zone:{sichtbar.id}"}).json()
    assert [e["summary"] for e in body["entries"]] == ["im sichtbaren"]
    assert body["zones"] == [{"id": sichtbar.id, "name": "Sichtbar"}]


def test_audit_csv_exports_the_filtered_entries(client_als: Als, session: Session) -> None:
    create_settings(session)
    _record(session, "=HYPERLINK(1)", detail="+cmd", object_id="1")
    _record(session, "Normal, mit \"Komma\"", detail="zeile1\nzeile2", object_id="2")
    _record(session, "Fremd", action="token.issued", object_type="token")
    client = client_als([("audit.read", None)])

    response = client.get("/ui-api/audit.csv", params={"action_code": "zone.updated"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="ereignisse.csv"'
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "time_utc", "source", "actor", "action", "object_type", "object_id", "summary", "detail",
    ]
    assert len(rows) == 3
    assert rows[1][6:] == ['Normal, mit "Komma"', "zeile1\nzeile2"]
    assert rows[2][6:] == ["'=HYPERLINK(1)", "'+cmd"]
    assert rows[2][1:3] == ["web", ""]

    text_only = client.get("/ui-api/audit.csv", params={"q": "fremd"}).text
    assert "Fremd" in text_only and "HYPERLINK" not in text_only


def test_audit_csv_names_token_actor_and_has_a_row_limit(
    client_als: Als, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_settings(session)
    owner = create_user(session, "besitzer")
    token = ApiToken(user_id=owner.id, name="-token", token_hash="h" * 64, prefix="abc")
    session.add(token)
    session.flush()
    source(session, "api")
    for index in range(3):
        audit.record(
            session,
            source="api",
            action="a",
            object_type="o",
            summary=f"E{index}",
            token_id=token.id,
        )
    session.flush()
    client = client_als([("audit.read", None)])
    rows = list(csv.reader(io.StringIO(client.get("/ui-api/audit.csv").text)))
    assert [r[2] for r in rows[1:]] == ["'-token"] * 3

    monkeypatch.setattr(ui_api_reports, "MAX_EXPORT_ROWS", 2)
    monkeypatch.setattr(audit_query, "MAX_EXPORT_ROWS", 2)
    rows = list(csv.reader(io.StringIO(client.get("/ui-api/audit.csv").text)))
    assert [r[6] for r in rows[1:3]] == ["E2", "E1"]
    assert len(rows) == 4 and rows[3][0].startswith("Hinweis: auf 2 Zeilen gekürzt")


def test_audit_csv_access_and_validation(
    client_als: Als, tenant_client: Als, client: TestClient, session: Session
) -> None:
    assert client.get("/ui-api/audit.csv").status_code == 401
    _fehler(tenant_client([("audit.read", None)]).get("/ui-api/audit.csv"), 403, "wrong_profile")
    _fehler(client_als([("zone.read", None)]).get("/ui-api/audit.csv"), 403, "forbidden")
    create_settings(session)
    admin = client_als([("audit.read", None)])
    _fehler(admin.get("/ui-api/audit.csv", params={"from_date": "x"}), 422, "validation")


# -- Änderungsprotokoll und Rücknahme -----------------------------------------------------


def _change(session: Session, name: str = "aend") -> tuple[int, Zone]:
    create_settings(session)
    source(session, "web")
    zone = create_zone(session, name)
    entry = cl.set_parameter(
        session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.7")
    )
    assert entry.change_id is not None
    return entry.change_id, zone


def test_changes_access(client_als: Als, tenant_client: Als, client: TestClient) -> None:
    for method, path in [
        ("GET", "/ui-api/changes"),
        ("GET", "/ui-api/changes/1"),
        ("POST", "/ui-api/changes/1/revert"),
    ]:
        assert client.request(method, path).status_code == 401
    for path in ("/ui-api/changes", "/ui-api/changes/1"):
        _fehler(tenant_client([("audit.read", None)]).get(path), 403, "wrong_profile")
        _fehler(client_als([("zone.read", None)]).get(path), 403, "forbidden")
    tenant = tenant_client([("audit.read", None)])
    _fehler(
        tenant.post("/ui-api/changes/1/revert", json={}, headers=_csrf(tenant)),
        403,
        "wrong_profile",
    )
    plain = client_als([("zone.read", None)])
    _fehler(plain.post("/ui-api/changes/1/revert", json={}, headers=_csrf(plain)), 403, "forbidden")


def test_changes_list_and_detail(client_als: Als, session: Session) -> None:
    change_id, zone = _change(session)
    cl.set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.9"))
    client = client_als([("audit.read", None)])

    body = client.get("/ui-api/changes").json()
    assert body["page"] == 1 and body["has_more"] is False
    assert len(body["entries"]) == 2
    older = body["entries"][1]
    assert older["id"] == change_id and older["zone_id"] == zone.id
    assert older["change_type"] == "control_parameter" and older["revertible"] is True
    assert older["fields"] == [{"field": "hysteresis_k", "before": None, "after": "0.7"}]
    assert older["occurred_at"].endswith("+00:00") and older["reverted_at"] is None
    assert "control_parameter" in body["change_types"]
    assert {"code": "web", "label": "Weboberfläche"} in body["sources"]

    detail = client.get(f"/ui-api/changes/{change_id}").json()
    assert detail["id"] == change_id and detail["summary"] == older["summary"]
    _fehler(client.get("/ui-api/changes/999999"), 404, "not_found")

    def ids(**params: object) -> list[int]:
        return [e["id"] for e in client.get("/ui-api/changes", params=params).json()["entries"]]

    assert len(ids(zone_id=zone.id)) == 2 and ids(zone_id=999999) == []
    assert len(ids(change_type="control_parameter", source="web")) == 2
    assert ids(change_type="override") == []
    assert ids(hide_reverted="true") == ids()
    assert ids(from_date="2999-01-01") == [] and ids(to_date="2000-01-01") == []
    assert len(ids(from_date="2000-01-01", to_date="2999-01-01")) == 2


def test_changes_pagination_and_validation(client_als: Als, session: Session) -> None:
    _change_id, zone = _change(session)
    for index in range(50):
        cl.set_parameter(
            session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal(f"0.{index % 8 + 1}")
        )
    client = client_als([("audit.read", None)])
    first = client.get("/ui-api/changes").json()
    assert first["has_more"] is True and len(first["entries"]) == 50
    assert len(client.get("/ui-api/changes", params={"page": "2"}).json()["entries"]) >= 1
    for params, field in [
        ({"page": "x"}, "page"),
        ({"from_date": "x"}, "from_date"),
        ({"from_date": "2026-02-02", "to_date": "2026-02-01"}, "to_date"),
        ({"change_type": "gibtsnicht"}, "change_type"),
        ({"source": "gibtsnicht"}, "source"),
    ]:
        assert (
            _fehler(client.get("/ui-api/changes", params=params), 422, "validation")["field"]
            == field
        )
    assert client.get("/ui-api/changes", params={"page": "0"}).json()["page"] == 1


def test_revert_restores_the_value_and_cannot_repeat(client_als: Als, session: Session) -> None:
    change_id, zone = _change(session)
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])

    response = client.post(f"/ui-api/changes/{change_id}/revert", json={}, headers=_csrf(client))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "reverted"
    assert body["change"]["reverts_id"] == change_id
    assert body["change"]["summary"].startswith("Rücknahme")
    session.refresh(zone)
    assert zone.hysteresis_k is None
    again = client.post(f"/ui-api/changes/{change_id}/revert", json={}, headers=_csrf(client))
    _fehler(again, 409, "already_reverted")
    # Ein Rücknahme-Eintrag lässt sich nicht erneut zurücknehmen.
    _fehler(
        client.post(
            f"/ui-api/changes/{body['change']['id']}/revert", json={}, headers=_csrf(client)
        ),
        409,
        "not_revertible",
    )
    _fehler(
        client.post("/ui-api/changes/999999/revert", json={}, headers=_csrf(client)),
        404,
        "not_found",
    )


def test_revert_needs_the_right_of_the_original_change(client_als: Als, session: Session) -> None:
    change_id, zone = _change(session)
    client = client_als([("audit.read", None), ("zone.read", zone.id)])
    _fehler(
        client.post(f"/ui-api/changes/{change_id}/revert", json={}, headers=_csrf(client)),
        403,
        "forbidden",
    )
    session.refresh(zone)
    assert zone.hysteresis_k == Decimal("0.7")


def test_revert_conflict_needs_the_confirmation_token(client_als: Als, session: Session) -> None:
    change_id, zone = _change(session)
    cl.set_parameter(session, Actor(None, None, "web"), zone, "hysteresis_k", Decimal("0.9"))
    client = client_als([("audit.read", None), ("zone.manage", zone.id)])
    url = f"/ui-api/changes/{change_id}/revert"

    conflict = _fehler(client.post(url, json={}, headers=_csrf(client)), 409, "conflict")

    assert conflict["conflicts"] == [{"field": "hysteresis_k", "current": "0.9", "expected": "0.7"}]
    token = conflict["force_token"]
    assert isinstance(token, str) and len(token) == 64
    session.refresh(zone)
    assert zone.hysteresis_k == Decimal("0.9")
    # Erzwingen ohne oder mit falschem Wert: 403, nichts geschrieben.
    _fehler(
        client.post(url, json={"force": True}, headers=_csrf(client)), 403, "force_token_invalid"
    )
    _fehler(
        client.post(url, json={"force": True, "force_token": "0" * 64}, headers=_csrf(client)),
        403,
        "force_token_invalid",
    )
    session.refresh(zone)
    assert zone.hysteresis_k == Decimal("0.9")
    forced = client.post(url, json={"force": True, "force_token": token}, headers=_csrf(client))
    assert forced.status_code == 200, forced.text
    assert forced.json()["change"]["forced"] is True
    session.refresh(zone)
    assert zone.hysteresis_k is None


def test_revert_of_an_effect_that_has_already_ended_is_marked_not_failed(
    client_als: Als, session: Session
) -> None:
    from tests.helpers import zone_with_schedule

    source(session, "web")
    zone = zone_with_schedule(session, "beendet", [(1, 420, "tag", Decimal("21.0"))])
    actor = Actor(None, None, "web")
    created = cl.create_override(session, actor, zone, Decimal("22"), None)
    cl.cancel_override(session, actor, zone)
    client = client_als(
        [("audit.read", None), ("override.create", zone.id), ("override.cancel", zone.id)]
    )
    url = f"/ui-api/changes/{created.change_id}/revert"

    response = client.post(url, json={}, headers=_csrf(client))

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "already_effective"
    assert response.json()["change"]["reverted_at"] is not None
    _fehler(client.post(url, json={}, headers=_csrf(client)), 409, "already_reverted")


@pytest.mark.parametrize(
    ("raised", "status", "code"),
    [
        (cl.ChangeNotPossible("weg"), 409, "not_possible"),
        (cl.ChangeNotRevertible("nein"), 409, "not_revertible"),
        (ParameterOutOfRange("zu groß"), 422, "revert_invalid"),
        (ScheduleError("minute", "Ungültige Minute."), 422, "revert_invalid"),
        (PolicyError("enabled", "Richtlinie unzulässig."), 422, "revert_invalid"),
        (Forbidden("Recht fehlt"), 403, "forbidden"),
    ],
)
def test_revert_domain_errors_get_the_uniform_error_form(
    client_als: Als,
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    status: int,
    code: str,
) -> None:
    def failing(*_args: object, **_kwargs: object) -> None:
        raise raised

    monkeypatch.setattr(ui_api_reports, "revert_change", failing)
    client = client_als([("audit.read", None)])
    body = _fehler(
        client.post("/ui-api/changes/1/revert", json={}, headers=_csrf(client)), status, code
    )
    if isinstance(raised, ScheduleError | PolicyError):
        assert body["detail"] == raised.notice and body["field"] == raised.field


def test_revert_without_csrf_header_is_refused(client_als: Als) -> None:
    client = client_als([("audit.read", None)])
    assert client.post("/ui-api/changes/1/revert", json={}).status_code == 403


def test_unknown_input_is_validation_error_not_500(client_als: Als) -> None:
    client = client_als([("audit.read", None)])
    response = client.post(
        "/ui-api/changes/x/revert", json={"force": "vielleicht"}, headers=_csrf(client)
    )
    assert response.status_code == 422 and response.json()["code"] == "validation"
