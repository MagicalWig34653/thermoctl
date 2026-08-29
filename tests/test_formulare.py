from fastapi import Request

from thermoctl.auth.passwords import PasswordTooShort
from thermoctl.web import templates
from thermoctl.web.formulare import (
    Formularfehler,
    formular_erneut,
    passwort_formularfehler,
)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/test", "headers": []})


def test_formular_erneut_zeigt_werte_und_feldfehler() -> None:
    antwort = formular_erneut(
        _request(),
        "einrichtung.html",
        {"username": "eingegebener-name"},
        Formularfehler("password", "Das Passwort ist zu kurz."),
    )

    inhalt = antwort.body.decode()
    assert antwort.status_code == 200
    assert 'value="eingegebener-name"' in inhalt
    assert "Das Passwort ist zu kurz." in inhalt
    assert 'class="form-control is-invalid"' in inhalt


def test_formular_erneut_gibt_passwort_nie_zurueck() -> None:
    geheimnis = "dieses-passwort-darf-nicht-zurueck"
    antwort = formular_erneut(
        _request(),
        "einrichtung.html",
        {"username": "lino", "password": geheimnis, "new_password": geheimnis},
    )

    assert geheimnis not in antwort.body.decode()
    assert geheimnis not in antwort.context.values()
    assert geheimnis not in antwort.context["werte"].values()


def test_formularmakros_verknuepfen_beschriftungen_und_felder() -> None:
    vorlage = templates.env.from_string(
        """{% from 'formular.html' import textfeld, zahlenfeld, auswahl, umschalter %}
        {{ textfeld('name', 'Name') }}
        {{ zahlenfeld('temperatur', 'Temperatur') }}
        {{ auswahl('modus', 'Modus', [('tag', 'Tag')]) }}
        {{ umschalter('aktiv', 'Aktiv') }}"""
    )

    inhalt = vorlage.render()
    for feld in ("name", "temperatur", "modus", "aktiv"):
        assert f'for="{feld}"' in inhalt
        assert f'id="{feld}"' in inhalt


def test_loeschbestaetigung_zeigt_abhaengigkeiten() -> None:
    vorlage = templates.env.from_string(
        """{% from 'formular.html' import loeschbestaetigung %}
        {{ loeschbestaetigung('Zone löschen', 'Wirklich löschen?',
                              '4 Schaltpunkte, 2 zugeordnete Geräte', '/zonen/1', '/zonen') }}"""
    )

    inhalt = vorlage.render()
    assert "4 Schaltpunkte, 2 zugeordnete Geräte" in inhalt


def test_password_too_short_wird_formularfehler_am_passwortfeld() -> None:
    fehler = passwort_formularfehler(PasswordTooShort("Mindestens 12 Zeichen."))

    assert fehler == Formularfehler("password", "Mindestens 12 Zeichen.")
