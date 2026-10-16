

from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.web.forms import (
    FormError,
    password_form_error,
)


def test_password_too_short_becomes_a_form_error_on_the_password_field() -> None:
    errors = password_form_error(PasswordTooShort("Mindestens 12 Zeichen."))

    assert errors == FormError("password", "Mindestens 12 Zeichen.")


def test_error_classes_survive_being_raised_through_multiple_layers() -> None:
    """A frozen dataclass used as an exception breaks as soon as Python tries to
    attach a traceback to it.

    This only surfaced when a domain error passed through FastAPI's dependency
    resolution: instead of the expected message, a `FrozenInstanceError` showed
    up. This test pins down that the three error classes withstand that -- it
    turns red as soon as someone adds `frozen=True` back because it "looks
    cleaner".
    """
    import pytest as _pytest

    from thermoctl.domain.modes import DomainError
    from thermoctl.domain.schedule import ScheduleError
    from thermoctl.web.forms import FormError

    def throw_it(cls: type[Exception]) -> None:
        raise cls("feld", "meldung")

    def in_between(cls: type[Exception]) -> None:
        throw_it(cls)

    for cls in (DomainError, ScheduleError, FormError):
        with _pytest.raises(cls) as errors:
            in_between(cls)
        # This is exactly where the frozen version failed: Python sets
        # `__traceback__` on the exception while it is propagating.
        assert errors.value.__traceback__ is not None
        assert errors.value.notice == "meldung"




