from datetime import datetime

from sqlalchemy.orm import Session

from tests.hilfen import benutzer_anlegen, quelle
from thermoctl.db.models.operations import AuditEvent


def _eintrag(
    session: Session,
    zusammenfassung: str,
    *,
    zeitpunkt: datetime = datetime(2026, 8, 15, 12),
    quellen_code: str = "web",
    benutzer_id: int | None = None,
    aktion: str = "zone.geaendert",
    objekttyp: str = "zone",
    objekt_id: str | None = "1",
) -> AuditEvent:
    eintrag = AuditEvent(
        occurred_at=zeitpunkt,
        source_id=quelle(session, quellen_code).id,
        actor_user_id=benutzer_id,
        action=aktion,
        object_type=objekttyp,
        object_id=objekt_id,
        summary=zusammenfassung,
    )
    session.add(eintrag)
    session.flush()
    return eintrag


def test_audit_braucht_audit_read(client_als) -> None:
    assert client_als([("zone.read", None)]).get("/audit").status_code == 403
    assert client_als([("audit.read", None)]).get("/audit").status_code == 200


def test_von_filtert_tatsaechlich(client_als, session: Session) -> None:
    _eintrag(session, "zu alt", zeitpunkt=datetime(2026, 8, 1, 23, 59))
    _eintrag(session, "im Zeitraum", zeitpunkt=datetime(2026, 8, 2))
    antwort = client_als([("audit.read", None)]).get("/audit?von=2026-08-02")
    assert "im Zeitraum" in antwort.text
    assert "zu alt" not in antwort.text


def test_bis_filtert_inklusive_des_ganzen_tages(client_als, session: Session) -> None:
    _eintrag(session, "noch enthalten", zeitpunkt=datetime(2026, 8, 2, 23, 59, 59))
    _eintrag(session, "zu neu", zeitpunkt=datetime(2026, 8, 3))
    antwort = client_als([("audit.read", None)]).get("/audit?bis=2026-08-02")
    assert "noch enthalten" in antwort.text
    assert "zu neu" not in antwort.text


def test_benutzer_filtert_tatsaechlich(client_als, session: Session) -> None:
    anna = benutzer_anlegen(session, "anna")
    bert = benutzer_anlegen(session, "bert")
    _eintrag(session, "von Anna", benutzer_id=anna.id)
    _eintrag(session, "von Bert", benutzer_id=bert.id)
    antwort = client_als([("audit.read", None)]).get("/audit?benutzer=anna")
    assert "von Anna" in antwort.text
    assert "von Bert" not in antwort.text


def test_aktion_filtert_tatsaechlich(client_als, session: Session) -> None:
    _eintrag(session, "Anmeldung", aktion="login")
    _eintrag(session, "Zone entfernt", aktion="zone.geloescht")
    antwort = client_als([("audit.read", None)]).get("/audit?aktion=login")
    assert "Anmeldung" in antwort.text
    assert "Zone entfernt" not in antwort.text


def test_objekt_filtert_typ_und_optional_id(client_als, session: Session) -> None:
    _eintrag(session, "Zone eins", objekt_id="1")
    _eintrag(session, "Zone zwei", objekt_id="2")
    _eintrag(session, "Benutzer eins", objekttyp="user", objekt_id="1")
    client = client_als([("audit.read", None)])
    nach_typ = client.get("/audit?objekt=zone")
    assert "Zone eins" in nach_typ.text and "Zone zwei" in nach_typ.text
    assert "Benutzer eins" not in nach_typ.text
    nach_id = client.get("/audit?objekt=zone%3A2")
    assert "Zone zwei" in nach_id.text
    assert "Zone eins" not in nach_id.text


def test_quelle_filtert_tatsaechlich(client_als, session: Session) -> None:
    _eintrag(session, "aus dem Web", quellen_code="web")
    _eintrag(session, "aus der API", quellen_code="api")
    antwort = client_als([("audit.read", None)]).get("/audit?quelle=api")
    assert "aus der API" in antwort.text
    assert "aus dem Web" not in antwort.text


def test_blaetterung_liefert_die_zweite_seite(client_als, session: Session) -> None:
    for nummer in range(51):
        _eintrag(session, f"Eintrag {nummer:02}", zeitpunkt=datetime(2026, 8, 15, 12, nummer))
    antwort = client_als([("audit.read", None)]).get("/audit?seite=2")
    assert "Eintrag 00" in antwort.text
    assert "Eintrag 50" not in antwort.text


def test_bis_vor_von_zeigt_meldung_und_erhaelt_werte(client_als) -> None:
    antwort = client_als([("audit.read", None)]).get(
        "/audit?von=2026-08-20&bis=2026-08-10"
    )
    assert antwort.status_code == 200
    assert "darf nicht vor dem Von-Datum liegen" in antwort.text
    assert 'value="2026-08-20"' in antwort.text
    assert 'value="2026-08-10"' in antwort.text


def test_unlesbare_filterwerte_zeigen_meldungen_statt_422(client_als) -> None:
    antwort = client_als([("audit.read", None)]).get("/audit?von=gestern&seite=zwei")
    assert antwort.status_code == 200
    assert "Bitte ein gültiges Datum eingeben" in antwort.text
    assert "Seitennummer muss eine ganze Zahl sein" in antwort.text


def test_detail_ist_erst_auf_anforderung_sichtbar(client_als, session: Session) -> None:
    eintrag = _eintrag(session, "mit Einzelheiten")
    eintrag.detail = "Eine längere technische Erklärung"
    antwort = client_als([("audit.read", None)]).get("/audit")
    assert "<details" in antwort.text
    assert "Eine längere technische Erklärung" in antwort.text
