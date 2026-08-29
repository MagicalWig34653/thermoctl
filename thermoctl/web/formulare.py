from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import Request, Response, status

from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.web import templates

_PASSWORTFELDER = frozenset(
    {
        "password",
        "passwort",
        "current_password",
        "new_password",
        "password_confirmation",
        "passwort_bestaetigung",
    }
)


# Bewusst NICHT `frozen=True`: Python haengt einer Ausnahme beim Werfen ihren
# Traceback an, und eine eingefrorene Dataclass verweigert genau das. Der Fehler
# faellt erst auf, wenn die Ausnahme tief genug durchgereicht wird — bei uns durch
# die Abhaengigkeitsaufloesung von FastAPI — und aeussert sich dann als
# `FrozenInstanceError` statt als der Fehler, den man sucht.
@dataclass
class Formularfehler(Exception):
    """Eine Eingabe, die der Benutzer korrigieren kann -- kein Fehler des Dienstes."""

    feld: str
    meldung: str


def passwort_formularfehler(
    fehler: PasswordTooShort, feld: str = "password"
) -> Formularfehler:
    """Ordnet ein zu kurzes Passwort dem Passwortfeld des jeweiligen Formulars zu."""
    return Formularfehler(feld=feld, meldung=str(fehler))


def formular_erneut(
    request: Request,
    vorlage: str,
    werte: Mapping[str, object],
    fehler: Formularfehler | None = None,
    **weitere: object,
) -> Response:
    """Zeigt korrigierbare Eingaben erneut, ohne Passwortwerte zurueckzugeben."""
    sichere_werte = {
        name: wert for name, wert in werte.items() if name not in _PASSWORTFELDER
    }
    feldfehler = {fehler.feld: fehler.meldung} if fehler is not None else {}
    return templates.TemplateResponse(
        request,
        vorlage,
        {**weitere, **sichere_werte, "werte": sichere_werte, "fehler": feldfehler},
        status_code=status.HTTP_200_OK,
    )
