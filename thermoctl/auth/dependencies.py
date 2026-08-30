from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from thermoctl.auth.csrf import CSRF_HEADER, check_csrf
from thermoctl.auth.sessions import COOKIE_NAME, resolve_session
from thermoctl.config import get_settings
from thermoctl.db.models.identity import User
from thermoctl.domain.authz import principal_for_user
from thermoctl.domain.principal import Principal

_NICHT_ANGEMELDET = "Nicht angemeldet"


def get_session(request: Request) -> Iterator[Session]:
    """Liefert eine Datenbank-Sitzung fuer die Dauer der Anfrage.

    Wird committet, wenn die Anfrage ohne Fehler durchlaeuft, sonst zurueckgerollt. Der
    Session-Factory sitzt auf ``app.state`` — ``create_app()`` legt sie beim Start an.
    """
    factory = request.app.state.session_factory
    http_session = factory()
    try:
        yield http_session
        http_session.commit()
    except Exception:
        http_session.rollback()
        raise
    finally:
        http_session.close()


def aktueller_principal(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Principal:
    """FastAPI-Abhaengigkeit fuer geschuetzte Routen: loest das Sitzungscookie auf.

    Fehlt das Cookie, ist es unbekannt, abgelaufen oder widerrufen, oder ist der
    zugehoerige Benutzer inaktiv, antwortet das einheitlich mit 401 — dieselbe Antwort
    fuer jeden dieser Faelle, aus demselben Grund wie bei der Anmeldung selbst.
    """
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    http_session = resolve_session(session, cookie_value)
    if http_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    user = session.get(User, http_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_NICHT_ANGEMELDET)

    # Fuer die Kopfleiste, die auf jeder Seite den angemeldeten Namen tragen soll (siehe
    # `_angemeldeter_benutzer` in thermoctl/web/__init__.py). Hier hinterlegt statt in
    # jeder Ansicht: Sonst traegt die Leiste den Namen genau dort, wo eine Ansicht daran
    # gedacht hat.
    request.state.user = user
    return principal_for_user(session, user)


# Sichere Methoden aendern nichts und brauchen deshalb keinen CSRF-Nachweis. Ohne
# diese Ausnahme koennte `csrf_schutz` nicht an einem ganzen Router haengen — jeder
# gewoehnliche Seitenaufruf aus dem Browser schickt das Cookie, aber keinen Header.
_SICHERE_METHODEN = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def csrf_schutz(request: Request) -> None:
    """Gemeinsame Abhaengigkeit fuer jede zustandsaendernde Route der Oberflaeche.

    Traegt die Anfrage ein Sitzungscookie, muss sie den passenden ``X-CSRF-Token``-Header
    mitbringen — auch dann, wenn gar keiner mitgeschickt wurde. Ein Schutz, der sich durch
    Weglassen des Headers umgehen liesse, waere keiner.

    Ohne Sitzungscookie greift der Schutz nicht: Dann gibt es nichts, was ein fremder
    Ursprung unbemerkt mitschicken koennte. Das betrifft die Anmeldung selbst und die
    Einrichtung, die ueber das Einmal-Token abgesichert ist, sowie die REST-API, die
    ausschliesslich Bearer-Tokens auswertet.

    Haengt als ``dependencies=[Depends(csrf_schutz)]`` am Router, nicht an der einzelnen
    Route: Eine spaeter ergaenzte Route ist damit von sich aus geschuetzt, statt dass
    jemand daran denken muss. `tests/test_csrf.py` haelt das nach.
    """
    if request.method in _SICHERE_METHODEN:
        return
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value is None:
        return
    settings = get_settings()
    if not check_csrf(
        request.headers.get(CSRF_HEADER), cookie_value, settings.secret_key.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Ungueltiges CSRF-Token"
        )
