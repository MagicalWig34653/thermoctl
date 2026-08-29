import asyncio
import json
import logging
from urllib.request import Request

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.hilfen import quelle, sensorstatus, zone_anlegen, zonenzustand_anlegen
from thermoctl import app as app_modul
from thermoctl.config import Settings
from thermoctl.db.models.operations import AuditEvent
from thermoctl.domain.stoerungsmeldung import Stoerungsmeldung
from thermoctl.integrations import benachrichtigung


class _Antwort:
    def __enter__(self) -> _Antwort:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _settings(**werte: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="s" * 32,
        **werte,
    )


MELDUNG = Stoerungsmeldung(
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
        benachrichtigung,
        "urlopen",
        lambda anfrage, timeout: aufrufe.append(anfrage),
    )

    asyncio.run(benachrichtigung.senden(_settings(), MELDUNG))

    assert aufrufe == []


def test_webhook_sendet_genau_einmal_erwartete_nutzlast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aufrufe: list[tuple[Request, int]] = []

    def _oeffnen(anfrage: Request, timeout: int) -> _Antwort:
        aufrufe.append((anfrage, timeout))
        return _Antwort()

    monkeypatch.setattr(benachrichtigung, "urlopen", _oeffnen)
    asyncio.run(
        benachrichtigung.senden(
            _settings(notify_webhook="https://example.invalid/meldung"), MELDUNG
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

    def _kaputt(anfrage: Request, timeout: int) -> _Antwort:
        gesehen.append(anfrage.get_header("Authorization"))
        raise OSError("Gegenstelle nicht erreichbar")

    monkeypatch.setattr(benachrichtigung, "urlopen", _kaputt)
    caplog.set_level(logging.WARNING)

    asyncio.run(
        benachrichtigung.senden(
            _settings(
                notify_webhook="https://example.invalid/meldung",
                notify_webhook_token=token,
            ),
            MELDUNG,
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
    quelle(session, "system")
    zone = zone_anlegen(session, "Meldezone")
    zustand = zonenzustand_anlegen(session, zone)
    vorher = app_modul._sensorzustaende(session)
    zustand.sensor_status_id = sensorstatus(session, "veraltet").id

    meldungen = app_modul._sensormeldungen(session, vorher)

    eintrag = session.scalar(select(AuditEvent))
    assert len(meldungen) == 1
    assert eintrag is not None
    assert eintrag.action == "notification.sent"
    assert eintrag.object_id == f"sensor:{zone.id}"
    assert eintrag.source_id == quelle(session, "system").id
