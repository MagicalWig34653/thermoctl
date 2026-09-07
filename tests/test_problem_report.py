"""„Problem melden" -- was hinausgeht, und wer es auslösen darf.

Zwei Zusicherungen tragen diese Datei:

1. **Ein reines Leserecht löst keine Meldung nach außen aus.** `zone.read` genügt
   ausdrücklich nicht; es braucht `report.create` für genau diese Zone. Wer nur
   lesen darf, könnte sonst den Webhook des Betreibers bedienen.
2. **Im Text steht nichts, was nicht hineingehört.** Kein Zugang, kein Gerät, keine
   Brokeradresse -- und nichts aus einer anderen Zone.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.helpers import (
    create_mode,
    create_settings,
    create_zone,
    create_zone_state,
    source,
)
from thermoctl.auth.csrf import csrf_token
from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings
from thermoctl.db.models.operations import AuditEvent, Setting
from thermoctl.db.models.zone import Zone, ZoneSetpoint
from thermoctl.domain.modes import DomainError
from thermoctl.domain.problem_report import NOTE_LIMIT, REPORT_KINDS, build_report

Client = Callable[[list[tuple[str, int | None]]], TestClient]


@pytest.fixture(autouse=True)
def _audit_source(session: Session) -> None:
    source(session, "web")


def _csrf(client: TestClient) -> dict[str, str]:
    secret = client.cookies[COOKIE_NAME]
    return {"X-CSRF-Token": csrf_token(secret, get_settings().secret_key.get_secret_value())}


def _zones(session: Session) -> tuple[Zone, Zone]:
    create_settings(session)
    mine = create_zone(session, "bad")
    mine.display_name = "Bad"
    other = create_zone(session, "fremd")
    other.display_name = "Nachbars Wohnzimmer"
    mode = create_mode(session, "tag", "Komfort")
    for zone in (mine, other):
        session.add(
            ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=mode.id,
                         temperature_c=Decimal("21.0"))
        )
        create_zone_state(session, zone)
    session.flush()
    return mine, other


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Fängt den Zustellversuch ab. Geprüft wird, **ob** und **was** hinausginge --
    nicht der Webhook selbst, den `tests/test_notification.py` schon prüft."""
    captured: list[Any] = []

    async def _deliver(_factory: Any, _settings: Any, notice: Any) -> None:
        captured.append(notice)

    monkeypatch.setattr(
        "thermoctl.web.report_views.notification.deliver", _deliver
    )
    # Über die Umgebungsvariable, nicht über das Feld: `get_settings()` baut eine
    # zwischengespeicherte `Settings`-Instanz aus der Umgebung, und ein gesetztes
    # Klassenattribut käme dort nie an.
    monkeypatch.setenv("THERMOCTL_NOTIFY_WEBHOOK", "https://example.invalid/hook")
    get_settings.cache_clear()
    yield captured
    get_settings.cache_clear()


# -- Der Inhalt ---------------------------------------------------------------


def test_the_report_names_the_room_the_problem_and_the_state(session: Session) -> None:
    mine, _other = _zones(session)
    notice = build_report(
        session, mine, "not_warm", "seit heute Morgen",
        now=datetime(2026, 1, 5, 8, 0), user=None,
    )
    assert "Bad" in notice.title
    assert "Raum wird nicht warm" in notice.title
    for expected in ("Raum:", "Problem:", "Zeitpunkt:", "Letzter Messwert:",
                     "Sollwert:", "Modus:", "seit heute Morgen"):
        assert expected in notice.text, expected


def test_the_report_carries_nothing_from_another_room(session: Session) -> None:
    """Der Test, der die Aufzählung im Docstring des Moduls prüfbar macht."""
    mine, other = _zones(session)
    notice = build_report(
        session, mine, "too_warm", "", now=datetime(2026, 1, 5, 8, 0), user=None
    )
    assert other.display_name not in notice.text
    assert other.name not in notice.text


def test_the_report_carries_no_technical_plumbing(session: Session) -> None:
    mine, _other = _zones(session)
    notice = build_report(
        session, mine, "other", "", now=datetime(2026, 1, 5, 8, 0), user=None
    )
    for forbidden in ("mqtt", "broker", "webhook", "token", "passwort", "zigbee"):
        assert forbidden not in notice.text.lower(), forbidden


def test_the_free_text_is_shortened_and_flattened(session: Session) -> None:
    """Der Text landet in einer HTTP-Nutzlast an ein fremdes System. Was dort eine
    Zeile beginnt, entscheidet nicht der Absender einer Meldung."""
    mine, _other = _zones(session)
    notice = build_report(
        session, mine, "other", "erste Zeile\nzweite\tZeile " + "x" * 500,
        now=datetime(2026, 1, 5, 8, 0), user=None,
    )
    hint = notice.text.split("Hinweis: ", 1)[1]
    assert "\n" not in hint
    assert "\t" not in hint
    assert len(hint) <= NOTE_LIMIT


def test_an_unknown_problem_kind_is_a_correctable_input(session: Session) -> None:
    mine, _other = _zones(session)
    with pytest.raises(DomainError) as caught:
        build_report(
            session, mine, "haus-brennt", "", now=datetime(2026, 1, 5, 8, 0), user=None
        )
    assert "Problemart" in caught.value.notice


def test_a_room_without_a_reading_says_so_instead_of_inventing_one(
    session: Session
) -> None:
    create_settings(session)
    bare = create_zone(session, "kammer")
    session.flush()
    notice = build_report(
        session, bare, "other", "", now=datetime(2026, 1, 5, 8, 0), user=None
    )
    assert "kein Messwert vorhanden" in notice.text


# -- Das Recht ----------------------------------------------------------------


def test_a_read_only_tenant_cannot_send_anything(
    tenant_client: Client, session: Session, sent: list[Any]
) -> None:
    """Die wichtigste Zusicherung dieser Datei."""
    mine, _other = _zones(session)
    client = tenant_client([("zone.read", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report", data={"kind": "not_warm"}, headers=_csrf(client)
    )
    assert response.status_code == 404
    assert sent == []
    assert not list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "report.created"))
    )


def test_a_foreign_room_cannot_be_reported(
    tenant_client: Client, session: Session, sent: list[Any]
) -> None:
    mine, other = _zones(session)
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{other.id}/report", data={"kind": "not_warm"}, headers=_csrf(client)
    )
    assert response.status_code == 404
    assert sent == []


def test_the_form_is_only_offered_where_the_permission_exists(
    tenant_client: Client, session: Session
) -> None:
    mine, _other = _zones(session)
    without = tenant_client([("zone.read", mine.id)]).get("/").text
    assert "Problem melden" not in without


def test_with_the_permission_the_report_goes_out_and_into_the_audit(
    tenant_client: Client, session: Session, sent: list[Any]
) -> None:
    mine, _other = _zones(session)
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report",
        data={"kind": "not_warm", "note": "seit gestern"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "report_notice" in response.headers["location"]
    assert len(sent) == 1
    assert "Bad" in sent[0].title
    entry = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "report.created")
    ).one()
    assert entry.object_id == str(mine.id)
    assert "seit gestern" in (entry.detail or "")


def test_an_unknown_kind_is_reported_back_without_sending(
    tenant_client: Client, session: Session, sent: list[Any]
) -> None:
    mine, _other = _zones(session)
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report", data={"kind": "unsinn"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "report_errors" in response.headers["location"]
    assert sent == []
    assert not list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "report.created"))
    )


# -- Die Zustellwege ----------------------------------------------------------


def test_with_the_switch_off_nothing_goes_out_but_the_audit_still_has_it(
    tenant_client: Client, session: Session, sent: list[Any]
) -> None:
    """Keine Attrappe -- aber auch kein stiller Erfolg: der Mieter erfährt, dass die
    Meldung aufgenommen, aber nicht weitergeleitet wurde."""
    mine, _other = _zones(session)
    row = session.get(Setting, 1)
    assert row is not None
    row.notify_tenant_reports = False
    session.flush()
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report", data={"kind": "not_warm"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "nicht+eingerichtet" in response.headers["location"].replace("%20", "+")
    assert sent == []
    assert list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "report.created"))
    )


def test_without_a_configured_webhook_the_answer_is_honest(
    tenant_client: Client, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, _other = _zones(session)
    get_settings.cache_clear()
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report", data={"kind": "not_warm"},
        headers=_csrf(client), follow_redirects=False,
    )
    assert response.status_code == 303
    assert "nicht+eingerichtet" in response.headers["location"].replace("%20", "+")
    assert list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "report.created"))
    )


def test_a_failing_webhook_does_not_lose_the_report(
    tenant_client: Client, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Audit-Eintrag entsteht **vor** dem Zustellversuch.

    Gescheitert wird hier auf der Ebene, auf der es real scheitert -- im
    Netzzugriff, nicht in `deliver` selbst, das seine Fehler bewusst nicht
    weiterreicht. Der Mieter bekommt trotzdem eine Antwort, und der Betreiber
    findet die Meldung im Protokoll.
    """
    mine, _other = _zones(session)

    async def _no_route(*_args: Any, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, "Verbindung abgelehnt"

    monkeypatch.setattr(
        "thermoctl.integrations.notification._attempt_delivery", _no_route
    )
    monkeypatch.setenv("THERMOCTL_NOTIFY_WEBHOOK", "https://example.invalid/hook")
    get_settings.cache_clear()
    client = tenant_client([("zone.read", mine.id), ("report.create", mine.id)])
    response = client.post(
        f"/zones/{mine.id}/report", data={"kind": "not_warm"},
        headers=_csrf(client), follow_redirects=False,
    )
    get_settings.cache_clear()
    assert response.status_code == 303
    assert list(
        session.scalars(select(AuditEvent).where(AuditEvent.action == "report.created"))
    )


def test_every_offered_kind_is_accepted_by_the_domain(session: Session) -> None:
    """Die Vorlage bietet genau die Arten an, die die Domäne kennt -- geprüft gegen
    `REPORT_KINDS`, damit eine neue Art nicht in nur einer der beiden landet."""
    mine, _other = _zones(session)
    for code, _label in REPORT_KINDS:
        notice = build_report(
            session, mine, code, "", now=datetime(2026, 1, 5, 8, 0), user=None
        )
        assert notice.kind == "tenant_report"


def test_the_free_text_loses_every_invisible_control_character(session: Session) -> None:
    """Nicht nur Zeilenumbrüche.

    Die Richtungsumschalter (U+202E und Verwandte) können die Anzeige einer Meldung
    im Posteingang des Betreibers umdrehen oder Teile davon umsortieren -- eine
    Meldung, die auf den ersten Blick etwas anderes sagt als das, was jemand
    geschrieben hat. Ein Filter auf `character < " "` erwischte sie nicht.
    """
    mine, _other = _zones(session)
    boesartig = (
        "harmlos\u202egerdeht \u2066isoliert\u2069 \u0081c1 \u200dfuge"
    )
    notice = build_report(
        session, mine, "other", boesartig, now=datetime(2026, 1, 5, 8, 0), user=None
    )
    hint = notice.text.split("Hinweis: ", 1)[1]
    for codepoint in ("\u202e", "\u2066", "\u2069", "\u0081", "\u200d"):
        assert codepoint not in hint, hex(ord(codepoint))
    # Der lesbare Text bleibt erhalten -- gefiltert wird das Unsichtbare, nicht
    # der Inhalt.
    assert "harmlos" in hint
    assert "isoliert" in hint


def test_the_free_text_is_cut_at_a_character_not_inside_one(session: Session) -> None:
    """Ein Schnitt zwischen Buchstabe und Akzent hinterlässt einen anderen
    Buchstaben als den getippten. Dann fällt der Buchstabe mit weg."""
    mine, _other = _zones(session)
    notice = build_report(
        session, mine, "other", "x" * (NOTE_LIMIT - 1) + "e\u0301",
        now=datetime(2026, 1, 5, 8, 0), user=None,
    )
    hint = notice.text.split("Hinweis: ", 1)[1]
    assert hint == "x" * (NOTE_LIMIT - 1)
    assert not hint.endswith("e")


def test_several_accents_on_one_letter_are_dropped_together(session: Session) -> None:
    """Ein Buchstabe kann mehrere kombinierende Zeichen tragen.

    Fällt der Schnitt zwischen sie, muss die ganze Zeichengruppe weg -- sonst bliebe
    ein Buchstabe mit der halben Anzahl Akzente stehen, also wieder ein anderes
    Zeichen als das getippte.
    """
    mine, _other = _zones(session)
    marken = "\u0301\u0304\u0308"          # Akut, Makron, Trema
    notice = build_report(
        session, mine, "other", "x" * (NOTE_LIMIT - 3) + "e" + marken,
        now=datetime(2026, 1, 5, 8, 0), user=None,
    )
    hint = notice.text.split("Hinweis: ", 1)[1]
    assert hint == "x" * (NOTE_LIMIT - 3)
    for marke in marken:
        assert marke not in hint
