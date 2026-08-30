from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import Request, Response, status

from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.web import templates

_PASSWORD_FIELDS = frozenset(
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
class FormError(Exception):
    """Eine Eingabe, die der Benutzer korrigieren kann -- kein Fehler des Dienstes."""

    feld: str
    notice: str


def password_form_error(
    errors: PasswordTooShort, feld: str = "password"
) -> FormError:
    """Ordnet ein zu kurzes Passwort dem Passwortfeld des jeweiligen Formulars zu."""
    return FormError(feld=feld, notice=str(errors))


def form_again(
    request: Request,
    vorlage: str,
    values: Mapping[str, object],
    errors: FormError | None = None,
    **weitere: object,
) -> Response:
    """Zeigt korrigierbare Eingaben erneut, ohne Passwortwerte zurueckzugeben."""
    safe_values = {
        name: value for name, value in values.items() if name not in _PASSWORD_FIELDS
    }
    field_errors = {errors.feld: errors.notice} if errors is not None else {}
    return templates.TemplateResponse(
        request,
        vorlage,
        {**weitere, **safe_values, "values": safe_values, "errors": field_errors},
        status_code=status.HTTP_200_OK,
    )
