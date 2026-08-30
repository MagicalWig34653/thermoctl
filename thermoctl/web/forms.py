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


# Deliberately NOT `frozen=True`: Python attaches its traceback to an exception when
# it's raised, and a frozen dataclass refuses exactly that. The bug only surfaces once
# the exception is passed deep enough -- in our case through FastAPI's dependency
# resolution -- and then shows up as a `FrozenInstanceError` instead of the bug you're
# actually looking for.
@dataclass
class FormError(Exception):
    """An input the user can correct -- not a fault of the service."""

    field: str
    notice: str


def password_form_error(
    errors: PasswordTooShort, field: str = "password"
) -> FormError:
    """Attributes a too-short password to the password field of the given form."""
    return FormError(field=field, notice=str(errors))


def form_again(
    request: Request,
    template: str,
    values: Mapping[str, object],
    errors: FormError | None = None,
    **more_items: object,
) -> Response:
    """Shows correctable input again, without echoing back password values."""
    safe_values = {
        name: value for name, value in values.items() if name not in _PASSWORD_FIELDS
    }
    field_errors = {errors.field: errors.notice} if errors is not None else {}
    return templates.TemplateResponse(
        request,
        template,
        {**more_items, **safe_values, "values": safe_values, "errors": field_errors},
        status_code=status.HTTP_200_OK,
    )
