import asyncio
import json
import logging
from urllib.request import Request

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import create_zone, create_zone_state, sensorstatus, source
from thermoctl import app as app_modul
from thermoctl.config import Settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.fault_notice import FaultNotice
from thermoctl.integrations import notification


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _settings(**values: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **values,
    )


NOTICE = FaultNotice(
    schluessel="sensor:1",
    schwere="stoerung",
    titel="Sensorstoerung",
    text="Keine aktuellen Werte.",
)


def test_ohne_webhook_erfolgt_keinerlei_http_aufruf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aufrufe: list[Request] = []
    monkeypatch.setattr(
        notification,
        "urlopen",
        lambda anfrage, timeout: aufrufe.append(anfrage),
    )

    asyncio.run(notification.send(_settings(), NOTICE))

    assert aufrufe == []


def test_webhook_sendet_genau_einmal_erwartete_nutzlast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aufrufe: list[tuple[Request, int]] = []

    def _oeffnen(anfrage: Request, timeout: int) -> _Response:
        aufrufe.append((anfrage, timeout))
        return _Response()

    monkeypatch.setattr(notification, "urlopen", _oeffnen)
    asyncio.run(
        notification.send(
            _settings(notify_webhook="https://example.invalid/meldung"), NOTICE
        )
    )

    assert len(aufrufe) == 1
    anfrage, timeout = aufrufe[0]
    assert timeout == 10
    assert anfrage.full_url == "https://example.invalid/meldung"
    assert anfrage.get_method() == "POST"
    assert json.loads(anfrage.data or b"") == {
        "schluessel": "sensor:1",
        "schwere": "stoerung",
        "titel": "Sensorstoerung",
        "text": "Keine aktuellen Werte.",
    }


def test_fehler_haelt_aufrufer_nicht_an_und_token_bleibt_aus_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    token = "auffaelliges-webhook-geheimnis"
    gesehen: list[str | None] = []

    def _kaputt(anfrage: Request, timeout: int) -> _Response:
        gesehen.append(anfrage.get_header("Authorization"))
        raise OSError("Gegenstelle nicht erreichbar")

    monkeypatch.setattr(notification, "urlopen", _kaputt)
    caplog.set_level(logging.WARNING)

    asyncio.run(
        notification.send(
            _settings(
                notify_webhook="https://example.invalid/meldung",
                notify_webhook_token=token,
            ),
            NOTICE,
        )
    )
    danach = True

    assert danach is True
    assert gesehen == [f"Bearer {token}"]
    assert "konnte nicht" in caplog.text
    assert token not in caplog.text


def test_sensormeldung_bekommt_audit_eintrag_mit_quelle_system(
    session: Session,
) -> None:
    source(session, "system")
    zone = create_zone(session, "Meldezone")
    state = create_zone_state(session, zone)
    vorher = app_modul._sensorzustaende(session)
    state.sensor_status_id = sensorstatus(session, "veraltet").id

    notices = app_modul._sensor_notices(session, vorher)

    entry = session.scalar(select(AuditEvent))
    assert len(notices) == 1
    assert entry is not None
    assert entry.action == "notification.sent"
    assert entry.object_id == f"sensor:{zone.id}"
    assert entry.source_id == source(session, "system").id
