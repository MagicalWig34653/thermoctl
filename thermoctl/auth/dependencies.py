from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from thermoctl.auth.sessions import COOKIE_NAME, sitzung_aufloesen
from thermoctl.db.models.identity import User
from thermoctl.domain.authz import principal_fuer_benutzer
from thermoctl.domain.principal import Principal

_NICHT_ANGEMELDET = "Nicht angemeldet"


def get_session(request: Request) -> Iterator[Session]:
    """Liefert eine Datenbank-Sitzung fuer die Dauer der Anfrage.

    Wird committet, wenn die Anfrage ohne Fehler durchlaeuft, sonst zurueckgerollt. Der
    Session-Factory sitzt auf ``app.state`` — ``create_app()`` legt sie beim Start an.
    """
    factory = request.app.state.session_factory
    sitzung = factory()
    try:
        yield sitzung
        sitzung.commit()
    except Exception:
        sitzung.rollback()
        raise
    finally:
        sitzung.close()


def aktueller_principal(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Principal:
    """FastAPI-Abhaengigkeit fuer geschuetzte Routen: loest das Sitzungscookie auf.

    Fehlt das Cookie, ist es unbekannt, abgelaufen oder widerrufen, oder ist der
    zugehoerige Benutzer inaktiv, antwortet das einheitlich mit 401 — dieselbe Antwort
    fuer jeden dieser Faelle, aus demselben Grund wie bei der Anmeldung selbst.
    """
    cookie_wert = request.cookies.get(COOKIE_NAME)
    if cookie_wert is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    sitzung = sitzung_aufloesen(session, cookie_wert)
    if sitzung is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    benutzer = session.get(User, sitzung.user_id)
    if benutzer is None or not benutzer.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    return principal_fuer_benutzer(session, benutzer)
