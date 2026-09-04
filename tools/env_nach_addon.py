#!/usr/bin/env python3
"""Translates a ``.env`` file into Home Assistant add-on configuration YAML.

The counterpart to ``docker/thermoctl_optionen.py``, which reads the add-on's flat
options and produces ``THERMOCTL_*`` environment variables. This script runs that
translation backwards: it reads a ``.env`` and produces the YAML block an operator
pastes into *Add-on -> Konfiguration -> YAML bearbeiten* when switching from
``docker compose`` to the add-on.

The mapping between the two shapes must not exist twice -- two separately maintained
tables drift apart, and that is exactly the mistake this project has already made
three times over (see ``docker/thermoctl_optionen.py``'s own docstring and the guard
test in ``tests/test_docker_addon_options.py``). So this script imports
``ABGEBILDETE_FELDER``, ``BEWUSST_AUSGELASSEN`` and ``_parse_env_field`` from
``docker/thermoctl_optionen.py`` and inverts them, instead of listing the fields
again. Where the forward direction is not a plain per-field lookup -- the database
URL is *assembled* from five options, so this direction has to *take it apart* --
that inversion lives in its own, named, documented function
(``datenbank_optionen_aus_url``) rather than being folded into the generic loop.

Standalone, standard library only -- run with a plain ``python3``, no installed
package required. Deliberately does not depend on PyYAML: the add-on configuration
here is a flat mapping of scalars plus one multi-line string (the free-form ``env``
field), and a hand-written scalar quoter for that shape is far less exposure than
declaring a YAML library dependency for one screen of output.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy.engine import make_url

_HIER = Path(__file__).resolve().parent
_OPTIONEN_SKRIPT = _HIER.parent / "docker" / "thermoctl_optionen.py"


def _lade_thermoctl_optionen() -> ModuleType:
    """Loads ``docker/thermoctl_optionen.py`` by file path.

    It sits under ``docker/``, not inside the ``thermoctl`` package (the entrypoint
    calls it with a bare ``python3`` before the package is even installed), so it is
    not importable by module name -- same reason ``tests/test_docker_addon_options.py``
    loads it this way.
    """
    spec = importlib.util.spec_from_file_location("thermoctl_optionen", _OPTIONEN_SKRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover -- setup-fehler, kein Laufzeitfall
        raise RuntimeError(f"{_OPTIONEN_SKRIPT} laesst sich nicht laden")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


optionen = _lade_thermoctl_optionen()

#: Settings-Felder, die das Add-on grundsaetzlich nicht braucht -- hinter Ingress
#: bedeutungslos oder vom Supervisor automatisch ermittelt (siehe
#: ``BEWUSST_AUSGELASSEN`` in ``docker/thermoctl_optionen.py`` fuer die Begruendung je
#: Feld). Diese Werte werden nicht uebertragen -- auch nicht ueber das freie
#: ``env``-Feld, denn dort waeren sie ebenso wirkungslos.
_ADDON_BRAUCHT_NICHT = frozenset({"bind_host", "bind_port", "secure_cookies", "root_path"})

#: Reihenfolge, in der die Optionen in der erzeugten YAML-Datei erscheinen -- die
#: Reihenfolge des (gedachten) Add-on-Schemas, nicht die der eingelesenen ``.env``.
_SCHEMA_REIHENFOLGE = [
    "database_type",
    "database_host",
    "database_port",
    "database_user",
    "database_password",
    "database_name",
    "secret_key",
    "log_level",
    "log_format",
    "mqtt_enabled",
    "mqtt_host",
    "mqtt_port",
    "mqtt_tls",
    "mqtt_ca_cert",
    "mqtt_username",
    "mqtt_password",
    "mqtt_client_id",
    "mqtt_base_topic",
    "mqtt_prefix",
    "meross_email",
    "meross_password",
    "notify_webhook",
    "notify_webhook_token",
    "env",
]

#: Add-on-Optionen, deren Wert ein YAML-Wahrheitswert sein muss (statt einer
#: Zeichenkette) -- das Gegenstueck zu ``optionen._bool`` in der Vorwaertsrichtung.
_BOOL_FELDER = frozenset({"mqtt_enabled", "mqtt_tls"})

#: Dieselbe Rolle fuer ganzzahlige Optionen.
_INT_FELDER = frozenset({"mqtt_port"})

_WAHR_WERTE = {"1", "true", "yes", "on"}
_FALSCH_WERTE = {"0", "false", "no", "off"}


class UmwandlungsFehler(ValueError):
    """Eine ``.env`` liess sich nicht in Add-on-Optionen uebersetzen."""


def _zu_bool(wert: str, variable: str) -> bool:
    normalisiert = wert.strip().lower()
    if normalisiert in _WAHR_WERTE:
        return True
    if normalisiert in _FALSCH_WERTE:
        return False
    raise UmwandlungsFehler(
        f"{variable} ist kein Wahrheitswert (erwartet z. B. 'true'/'false'), "
        "der Wert lautet weder true/false/yes/no/on/off (Gross-/Kleinschreibung "
        "gleichgueltig)."
    )


def _zu_int(wert: str, variable: str) -> int:
    try:
        return int(wert.strip())
    except ValueError as error:
        raise UmwandlungsFehler(f"{variable} ist keine ganze Zahl: {wert!r}") from error


def datenbank_optionen_aus_url(datenbank_url: str) -> dict[str, str | int]:
    """Zerlegt ``THERMOCTL_DATABASE_URL`` in die fuenf flachen ``database_*``-Optionen.

    Die Gegenrichtung von ``_database_url`` in ``docker/thermoctl_optionen.py``: dort
    werden die Optionen zu einer Verbindungszeichenfolge *zusammengesetzt*, hier wird
    sie wieder *auseinandergenommen*. Das ist keine mechanische Umkehrung eines
    einfachen Nachschlagens (wie bei den uebrigen Feldern) und bekommt deshalb eine
    eigene Funktion.

    Erkannt werden genau die beiden Formen, die die Vorwaertsrichtung erzeugen kann:
    ``sqlite:///...`` und ``mysql+pymysql://...`` (MariaDB). Alles andere ist ein
    Fehler mit einer verstaendlichen Meldung -- kein stilles Verwerfen, wie im Auftrag
    verlangt. Die Fehlermeldungen enthalten bewusst nie die rohe URL: Sie kann ein
    Passwort tragen, und eine Fehlermeldung ist ein Ort, an dem das nicht landen darf.
    """
    try:
        url = make_url(datenbank_url)
    except Exception as error:  # noqa: BLE001 -- Fehlerursache bewusst nicht durchgereicht
        raise UmwandlungsFehler(
            "THERMOCTL_DATABASE_URL laesst sich nicht als Verbindungszeichenfolge lesen "
            "(erwartet wird 'sqlite:///...' oder 'mysql+pymysql://...')."
        ) from error

    if url.drivername == "sqlite":
        pfad = url.database or ""
        if pfad != "/data/thermoctl.db":
            print(
                "env_nach_addon: THERMOCTL_DATABASE_URL verweist auf "
                f"{pfad!r}; das Add-on legt SQLite immer unter /data/thermoctl.db an. "
                "Die vorhandene Datenbankdatei muss beim Umstieg dorthin uebertragen "
                "werden, sonst startet das Add-on mit einer leeren Datenbank.",
                file=sys.stderr,
            )
        return {"database_type": "sqlite"}

    if url.drivername == "mysql+pymysql":
        fehlende_felder = [
            name
            for name, wert in (
                ("Host", url.host),
                ("Benutzername", url.username),
                ("Passwort", url.password),
                ("Datenbankname", url.database),
            )
            if not wert
        ]
        if fehlende_felder:
            raise UmwandlungsFehler(
                "THERMOCTL_DATABASE_URL (MariaDB) fehlen Angaben: "
                + ", ".join(fehlende_felder)
            )
        assert url.host and url.username and url.password and url.database  # fuer mypy
        return {
            "database_type": "mariadb",
            "database_host": url.host,
            "database_port": url.port or 3306,
            "database_user": url.username,
            "database_password": url.password,
            "database_name": url.database,
        }

    raise UmwandlungsFehler(
        "THERMOCTL_DATABASE_URL wird nicht erkannt: erwartet wird 'sqlite:///...' oder "
        f"'mysql+pymysql://...', das Schema hier lautet {url.drivername!r}."
    )


#: Umkehrung von ``ABGEBILDETE_FELDER`` (minus ``database_url``, das eine eigene
#: Funktion bekommt): jede ``THERMOCTL_*``-Umgebungsvariable, die eine dedizierte
#: Add-on-Option hat, auf den Settings-Feldnamen -- der zugleich der Optionsname ist,
#: ausser bei den Datenbankfeldern. Der Zusammenhang zwischen Feldname und
#: Variablenname ("secret_key" <-> "THERMOCTL_SECRET_KEY") ist derselbe, den
#: ``docker/thermoctl_optionen.py`` in ``translate()`` schon voraussetzt -- dort ist
#: er nur nie als Zeichenkette aufgeschrieben, weil die Vorwaertsrichtung ihn nicht
#: braucht.
_VARIABLE_ZU_FELD: dict[str, str] = {
    f"THERMOCTL_{feld.upper()}": feld
    for feld in optionen.ABGEBILDETE_FELDER
    if feld != "database_url"
}

#: Settings-Felder, die zwar keine dedizierte Add-on-Option haben, aber trotzdem
#: uebertragen werden sollen -- ueber das freie ``env``-Feld. Das ist
#: ``BEWUSST_AUSGELASSEN`` minus der Felder, die das Add-on grundsaetzlich nicht
#: braucht (``_ADDON_BRAUCHT_NICHT``): jene sind ohne Wirkung und werden nicht einmal
#: dorthin uebertragen.
_UEBER_ENV_FELD: dict[str, str] = {
    f"THERMOCTL_{feld.upper()}": grund
    for feld, grund in optionen.BEWUSST_AUSGELASSEN.items()
    if feld not in _ADDON_BRAUCHT_NICHT
}

_UEBERSPRUNGEN: dict[str, str] = {
    f"THERMOCTL_{feld.upper()}": grund
    for feld, grund in optionen.BEWUSST_AUSGELASSEN.items()
    if feld in _ADDON_BRAUCHT_NICHT
}


def addon_optionen(env_werte: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Baut die Add-on-Optionen aus geparsten ``.env``-Werten.

    Gibt die Optionen (in Schema-Reihenfolge sortiert von der Aufruferin) und eine
    Liste von Hinweiszeilen fuer die Fehlerausgabe zurueck -- letztere nennen nur
    Variablennamen und Gruende, nie Werte, damit auch die Diagnose keine Zugangsdaten
    preisgibt.
    """
    optionswerte: dict[str, Any] = {}
    env_feld_zeilen: list[str] = []
    hinweise: list[str] = []

    for name, wert in env_werte.items():
        if name == "THERMOCTL_DATABASE_URL":
            optionswerte.update(datenbank_optionen_aus_url(wert))
            continue
        if name in _UEBERSPRUNGEN:
            hinweise.append(
                f"{name}: uebersprungen -- {_UEBERSPRUNGEN[name]}"
            )
            continue
        if name in _VARIABLE_ZU_FELD:
            feld = _VARIABLE_ZU_FELD[name]
            if feld in _BOOL_FELDER:
                optionswerte[feld] = _zu_bool(wert, name)
            elif feld in _INT_FELDER:
                optionswerte[feld] = _zu_int(wert, name)
            else:
                optionswerte[feld] = wert
            continue
        if name in _UEBER_ENV_FELD:
            env_feld_zeilen.append(f"{name}={wert}")
            hinweise.append(
                f"{name}: keine eigene Add-on-Option ({_UEBER_ENV_FELD[name]}) -- "
                "ins freie `env`-Feld uebernommen."
            )
            continue
        if name.startswith("THERMOCTL_"):
            env_feld_zeilen.append(f"{name}={wert}")
            hinweise.append(
                f"{name}: keiner bekannten Einstellung zugeordnet -- ins freie "
                "`env`-Feld uebernommen."
            )
            continue
        hinweise.append(f"{name}: keine THERMOCTL_*-Variable -- ignoriert.")

    if env_feld_zeilen:
        optionswerte["env"] = "\n".join(env_feld_zeilen)

    return optionswerte, hinweise


def _yaml_zeichenkette(wert: str) -> str:
    """Zitiert eine Zeichenkette als YAML-Doppelquote-Skalar.

    Doppelt zitierte YAML-Skalare kennen Escape-Sequenzen fuer alles, was in einem
    Wert aus der ``.env`` vorkommen kann -- Anfuehrungszeichen, Doppelpunkt, `#`,
    Zeilenumbruch (das mehrzeilige ``env``-Feld) -- und sind deshalb die einzige Form,
    die ohne Fallunterscheidung fuer jeden Wert richtig ist.
    """
    escaped = (
        wert.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _yaml_wert(wert: Any) -> str:
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, int):
        return str(wert)
    return _yaml_zeichenkette(str(wert))


def als_yaml(optionswerte: dict[str, Any]) -> str:
    """Gibt die Optionen als YAML aus, in Schema- statt in Einlesereihenfolge."""
    zeilen = [
        f"{name}: {_yaml_wert(optionswerte[name])}"
        for name in _SCHEMA_REIHENFOLGE
        if name in optionswerte
    ]
    return "\n".join(zeilen) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"Aufruf: {argv[0]} <.env-Datei>", file=sys.stderr)
        return 2

    env_pfad = Path(argv[1])
    try:
        text = env_pfad.read_text()
    except OSError as error:
        print(f"env_nach_addon: {env_pfad} nicht lesbar: {error}", file=sys.stderr)
        return 1

    env_werte = optionen._parse_env_field(text)  # noqa: SLF001 -- bewusst dieselbe Funktion

    try:
        optionswerte, hinweise = addon_optionen(env_werte)
    except UmwandlungsFehler as error:
        print(f"env_nach_addon: {error}", file=sys.stderr)
        return 1

    print(als_yaml(optionswerte), end="")

    if hinweise:
        print(file=sys.stderr)
        print("env_nach_addon -- Hinweise:", file=sys.stderr)
        for hinweis in hinweise:
            print(f"  {hinweis}", file=sys.stderr)

    print(file=sys.stderr)
    print(
        "env_nach_addon: Die Ausgabe enthaelt Zugangsdaten aus der eingelesenen "
        f"{env_pfad} im Klartext. In Home Assistant unter Add-on -> Konfiguration -> "
        "YAML bearbeiten einfuegen; wer sie in eine Datei umleitet (`> datei.yaml`), "
        "behandelt diese Datei mit derselben Sorgfalt wie die `.env` selbst -- nicht "
        "committen, nicht liegen lassen.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
