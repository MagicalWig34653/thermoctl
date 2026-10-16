from dataclasses import dataclass

from thermoctl.auth.passwords import PasswordTooShort


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
