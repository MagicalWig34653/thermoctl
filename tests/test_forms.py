from fastapi import Request

from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.web import templates
from thermoctl.web.forms import (
    FormError,
    form_again,
    password_form_error,
)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/test", "headers": []})


def test_form_again_shows_values_and_field_errors() -> None:
    response = form_again(
        _request(),
        "setup.html",
        {"username": "eingegebener-name"},
        FormError("password", "Das Passwort ist zu kurz."),
    )

    content = response.body.decode()
    assert response.status_code == 200
    assert 'value="eingegebener-name"' in content
    assert "Das Passwort ist zu kurz." in content
    assert 'class="form-control is-invalid"' in content


def test_form_again_never_returns_the_password() -> None:
    secret = "dieses-passwort-darf-nicht-zurueck"
    response = form_again(
        _request(),
        "setup.html",
        {"username": "lino", "password": secret, "new_password": secret},
    )

    assert secret not in response.body.decode()
    assert secret not in response.context.values()
    assert secret not in response.context["values"].values()


def test_form_macros_link_labels_and_fields() -> None:
    template = templates.env.from_string(
        """{% from 'form.html' import textfeld, zahlenfeld, auswahl, umschalter %}
        {{ textfeld('name', 'Name') }}
        {{ zahlenfeld('temperatur', 'Temperatur') }}
        {{ auswahl('modus', 'Modus', [('tag', 'Tag')]) }}
        {{ umschalter('aktiv', 'Aktiv') }}"""
    )

    content = template.render()
    for field in ("name", "temperatur", "modus", "aktiv"):
        assert f'for="{field}"' in content
        assert f'id="{field}"' in content


def test_delete_confirmation_shows_dependencies() -> None:
    template = templates.env.from_string(
        """{% from 'form.html' import loeschbestaetigung %}
        {{ loeschbestaetigung('Zone löschen', 'Wirklich löschen?',
                              '4 Schaltpunkte, 2 zugeordnete Geräte', '/zonen/1', '/zonen') }}"""
    )

    content = template.render()
    assert "4 Schaltpunkte, 2 zugeordnete Geräte" in content


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
