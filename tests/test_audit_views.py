from datetime import datetime

from sqlalchemy.orm import Session

from tests.helpers import create_user, source
from thermoctl.db.models.operations import AuditEvent


def _entry(
    session: Session,
    zusammenfassung: str,
    *,
    moment: datetime = datetime(2026, 8, 15, 12),
    source_codes: str = "web",
    user_id: int | None = None,
    aktion: str = "zone.geaendert",
    objekttyp: str = "zone",
    objekt_id: str | None = "1",
) -> AuditEvent:
    entry = AuditEvent(
        occurred_at=moment,
        source_id=source(session, source_codes).id,
        actor_user_id=user_id,
        action=aktion,
        object_type=objekttyp,
        object_id=objekt_id,
        summary=zusammenfassung,
    )
    session.add(entry)
    session.flush()
    return entry


def test_audit_braucht_audit_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/audit").status_code == 403
    assert client_als([("audit.read", None)]).get("/audit").status_code == 200


def test_von_filtert_tatsaechlich(client_als, session: Session) -> None:
    _entry(session, "zu alt", moment=datetime(2026, 8, 1, 23, 59))
    _entry(session, "im Zeitraum", moment=datetime(2026, 8, 2))
    response = client_als([("audit.read", None)]).get("/audit?from_date=2026-08-02")
    assert "im Zeitraum" in response.text
    assert "zu alt" not in response.text


def test_bis_filtert_inklusive_des_ganzen_tages(client_als, session: Session) -> None:
    _entry(session, "noch enthalten", moment=datetime(2026, 8, 2, 23, 59, 59))
    _entry(session, "zu neu", moment=datetime(2026, 8, 3))
    response = client_als([("audit.read", None)]).get("/audit?to_date=2026-08-02")
    assert "noch enthalten" in response.text
    assert "zu neu" not in response.text


def test_benutzer_filtert_tatsaechlich(client_als, session: Session) -> None:
    anna = create_user(session, "anna")
    bert = create_user(session, "bert")
    _entry(session, "von Anna", user_id=anna.id)
    _entry(session, "von Bert", user_id=bert.id)
    response = client_als([("audit.read", None)]).get("/audit?user=anna")
    assert "von Anna" in response.text
    assert "von Bert" not in response.text


def test_aktion_filtert_tatsaechlich(client_als, session: Session) -> None:
    _entry(session, "Anmeldung", aktion="login")
    _entry(session, "Zone entfernt", aktion="zone.geloescht")
    response = client_als([("audit.read", None)]).get("/audit?action_code=login")
    assert "Anmeldung" in response.text
    assert "Zone entfernt" not in response.text


def test_objekt_filtert_typ_und_optional_id(client_als, session: Session) -> None:
    _entry(session, "Zone eins", objekt_id="1")
    _entry(session, "Zone zwei", objekt_id="2")
    _entry(session, "Benutzer eins", objekttyp="user", objekt_id="1")
    client = client_als([("audit.read", None)])
    nach_typ = client.get("/audit?object=zone")
    assert "Zone eins" in nach_typ.text and "Zone zwei" in nach_typ.text
    assert "Benutzer eins" not in nach_typ.text
    nach_id = client.get("/audit?object=zone%3A2")
    assert "Zone zwei" in nach_id.text
    assert "Zone eins" not in nach_id.text


def test_quelle_filtert_tatsaechlich(client_als, session: Session) -> None:
    _entry(session, "aus dem Web", source_codes="web")
    _entry(session, "aus der API", source_codes="api")
    response = client_als([("audit.read", None)]).get("/audit?source=api")
    assert "aus der API" in response.text
    assert "aus dem Web" not in response.text


def test_blaetterung_liefert_die_zweite_seite(client_als, session: Session) -> None:
    for nummer in range(51):
        _entry(session, f"Eintrag {nummer:02}", moment=datetime(2026, 8, 15, 12, nummer))
    response = client_als([("audit.read", None)]).get("/audit?page=2")
    assert "Eintrag 00" in response.text
    assert "Eintrag 50" not in response.text


def test_bis_vor_von_zeigt_meldung_und_erhaelt_werte(client_als) -> None:
    response = client_als([("audit.read", None)]).get(
        "/audit?from_date=2026-08-20&to_date=2026-08-10"
    )
    assert response.status_code == 200
    assert "darf nicht vor dem Von-Datum liegen" in response.text
    assert 'value="2026-08-20"' in response.text
    assert 'value="2026-08-10"' in response.text


def test_unlesbare_filterwerte_zeigen_meldungen_statt_422(client_als) -> None:
    response = client_als([("audit.read", None)]).get("/audit?from_date=gestern&page=zwei")
    assert response.status_code == 200
    assert "Bitte ein gültiges Datum eingeben" in response.text
    assert "Seitennummer muss eine ganze Zahl sein" in response.text


def test_detail_ist_erst_auf_anforderung_sichtbar(client_als, session: Session) -> None:
    entry = _entry(session, "mit Einzelheiten")
    entry.detail = "Eine längere technische Erklärung"
    response = client_als([("audit.read", None)]).get("/audit")
    assert "<details" in response.text
    assert "Eine längere technische Erklärung" in response.text
