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
        raise RuntimeError(f"{_OPTIONEN_SKRIPT} lässt sich nicht laden")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


optionen = _lade_thermoctl_optionen()

#: Settings-Felder, die das Add-on grundsätzlich nicht braucht -- hinter Ingress
#: bedeutungslos oder vom Supervisor automatisch ermittelt (siehe
#: ``BEWUSST_AUSGELASSEN`` in ``docker/thermoctl_optionen.py`` für die Begründung je
#: Feld). Diese Werte werden nicht übertragen -- auch nicht über das freie
#: ``env``-Feld, denn dort wären sie ebenso wirkungslos.
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
    "mcp_token",
    "passkey_rp_id",
    "passkey_rp_name",
    "passkey_origin",
    "env",
]

#: Add-on-Optionen, deren Wert ein YAML-Wahrheitswert sein muss (statt einer
#: Zeichenkette) -- das Gegenstück zu ``optionen._bool`` in der Vorwärtsrichtung.
_BOOL_FELDER = frozenset({"mqtt_enabled", "mqtt_tls"})

#: Dieselbe Rolle für ganzzahlige Optionen.
_INT_FELDER = frozenset({"mqtt_port"})

_WAHR_WERTE = {"1", "true", "yes", "on"}
_FALSCH_WERTE = {"0", "false", "no", "off"}


class UmwandlungsFehler(ValueError):
    """Eine ``.env`` liess sich nicht in Add-on-Optionen übersetzen."""


def _zu_bool(wert: str, variable: str) -> bool:
    normalisiert = wert.strip().lower()
    if normalisiert in _WAHR_WERTE:
        return True
    if normalisiert in _FALSCH_WERTE:
        return False
    raise UmwandlungsFehler(
        f"{variable} ist kein Wahrheitswert (erwartet z. B. 'true'/'false'), "
        "der Wert lautet weder true/false/yes/no/on/off (Groß-/Kleinschreibung "
        "gleichgültig)."
    )


def _zu_int(wert: str, variable: str) -> int:
    try:
        return int(wert.strip())
    except ValueError as error:
        raise UmwandlungsFehler(f"{variable} ist keine ganze Zahl: {wert!r}") from error


def datenbank_optionen_aus_url(datenbank_url: str) -> dict[str, str | int]:
    """Zerlegt ``THERMOCTL_DATABASE_URL`` in die fünf flachen ``database_*``-Optionen.

    Die Gegenrichtung von ``_database_url`` in ``docker/thermoctl_optionen.py``: dort
    werden die Optionen zu einer Verbindungszeichenfolge *zusammengesetzt*, hier wird
    sie wieder *auseinandergenommen*. Das ist keine mechanische Umkehrung eines
    einfachen Nachschlagens (wie bei den übrigen Feldern) und bekommt deshalb eine
    eigene Funktion.

    Erkannt werden genau die beiden Formen, die die Vorwärtsrichtung erzeugen kann:
    ``sqlite:///...`` und ``mysql+pymysql://...`` (MariaDB). Alles andere ist ein
    Fehler mit einer verständlichen Meldung -- kein stilles Verwerfen, wie im Auftrag
    verlangt. Die Fehlermeldungen enthalten bewusst nie die rohe URL: Sie kann ein
    Passwort tragen, und eine Fehlermeldung ist ein Ort, an dem das nicht landen darf.
    """
    try:
        url = make_url(datenbank_url)
    except Exception as error:  # noqa: BLE001 -- Fehlerursache bewusst nicht durchgereicht
        raise UmwandlungsFehler(
            "THERMOCTL_DATABASE_URL lässt sich nicht als Verbindungszeichenfolge lesen "
            "(erwartet wird 'sqlite:///...' oder 'mysql+pymysql://...')."
        ) from error

    if url.drivername == "sqlite":
        pfad = url.database or ""
        if pfad != "/data/thermoctl.db":
            print(
                "env_nach_addon: THERMOCTL_DATABASE_URL verweist auf "
                f"{pfad!r}; das Add-on legt SQLite immer unter /data/thermoctl.db an. "
                "Die vorhandene Datenbankdatei muss beim Umstieg dorthin übertragen "
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
        assert url.host and url.username and url.password and url.database  # für mypy
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


def auskommentierte_datenbank_url(env_text: str) -> str | None:
    """Sucht in der rohen ``.env``-Datei nach einer *auskommentierten*
    ``THERMOCTL_DATABASE_URL``-Zuweisung und gibt deren Wert zurück, wenn eine da ist.

    ``optionen._parse_env_field`` verwirft Kommentarzeilen bewusst -- richtig für den
    Normalfall, aber ein Betreiber, der zwischen SQLite und MariaDB wechselt,
    kommentiert typischerweise die eine Zeile aus statt sie zu löschen, genau um sich
    die Alternative aufzubewahren:

        THERMOCTL_DATABASE_URL=sqlite:///./data/thermoctl.db
        #THERMOCTL_DATABASE_URL=mysql+pymysql://nutzer:pw@host:3306/db

    Erkannt wird eine beliebige Anzahl führender ``#`` (mit oder ohne Leerzeichen
    danach), der Rest der Zeile wird wie eine aktive Zeile behandelt -- inklusive
    ``export`` und Anführungszeichen -- indem er derselben ``_parse_env_field``
    vorgelegt wird, die auch aktive Zeilen liest. So entsteht keine zweite,
    abweichende Parsing-Logik für denselben Zeilenaufbau.

    Stehen mehrere solche Kommentarzeilen in der Datei, gilt die letzte -- dieselbe
    Regel, die ``_parse_env_field`` für mehrfach zugewiesene aktive Variablen schon
    anwendet.
    """
    entkommentierte_zeilen = []
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        ohne_raute = line.lstrip("#").strip()
        if ohne_raute == line:
            continue  # keine Kommentarzeile
        entkommentierte_zeilen.append(ohne_raute)
    geparst: dict[str, str] = optionen._parse_env_field(  # noqa: SLF001
        "\n".join(entkommentierte_zeilen)
    )
    return geparst.get("THERMOCTL_DATABASE_URL")


def _datenbank_optionen(
    wert: str, kommentierte_mariadb_url: str | None
) -> tuple[dict[str, str | int], list[str]]:
    """Wandelt ``THERMOCTL_DATABASE_URL`` in Add-on-Optionen um -- und lässt dabei
    eine auskommentierte MariaDB-Alternative gegen eine aktive SQLite-URL gewinnen.

    Der Anlass: Eine ``.env``, die tatsächlich gegen MariaDB läuft, kann trotzdem
    ``THERMOCTL_DATABASE_URL=sqlite:///...`` als Ueberbleibsel einer frühen
    Entwicklungsumgebung enthalten, während die echten Zugangsdaten daneben
    auskommentiert liegen. Wird eine solche Zeile gefunden und lässt sie sich
    vollständig lesen, gewinnt sie -- SQLite samt seiner Pfad-Warnung faellt dann
    weg. Lässt sie sich nicht vollständig lesen, ist das ein Hinweis, kein stilles
    Zurückfallen: SQLite wird trotzdem übertragen, aber mit einer zusätzlichen
    Zeile, die sagt, warum die auskommentierte Alternative nicht gezogen wurde.
    """
    hinweise: list[str] = []

    ist_sqlite = False
    try:
        ist_sqlite = make_url(wert).drivername == "sqlite"
    except Exception:  # noqa: BLE001 -- wird gleich unten noch einmal richtig gemeldet
        ist_sqlite = False

    kommentierter_treiber: str | None = None
    if kommentierte_mariadb_url is not None:
        try:
            kommentierter_treiber = make_url(kommentierte_mariadb_url).drivername
        except Exception:  # noqa: BLE001 -- unparsbare Kommentarzeile ist kein Fall hier
            kommentierter_treiber = None

    if ist_sqlite and kommentierter_treiber == "mysql+pymysql":
        assert kommentierte_mariadb_url is not None  # für mypy, siehe oben
        try:
            mariadb_optionen = datenbank_optionen_aus_url(kommentierte_mariadb_url)
        except UmwandlungsFehler as fehler:
            hinweise.append(
                "THERMOCTL_DATABASE_URL: SQLite ist aktiv, daneben liegt eine "
                f"auskommentierte MariaDB-Zeile, die aber unvollständig ist ({fehler}) "
                "-- SQLite wird übertragen."
            )
        else:
            hinweise.append(
                "THERMOCTL_DATABASE_URL: SQLite ist aktiv, aber eine auskommentierte "
                "MariaDB-Verbindung liegt daneben -- diese wird verwendet, SQLite "
                "verworfen."
            )
            return mariadb_optionen, hinweise

    ergebnis = datenbank_optionen_aus_url(wert)
    if ergebnis.get("database_type") == "sqlite":
        hinweise.append(
            "THERMOCTL_DATABASE_URL: SQLite wird übertragen. Ist im Add-on bereits "
            "eine andere Datenbank eingetragen (typischerweise MariaDB) und soll das "
            "so bleiben, das Datenbankfeld stattdessen ganz weglassen: "
            "--ohne-datenbank."
        )
    return ergebnis, hinweise


#: Umkehrung von ``ABGEBILDETE_FELDER`` (minus ``database_url``, das eine eigene
#: Funktion bekommt): jede ``THERMOCTL_*``-Umgebungsvariable, die eine dedizierte
#: Add-on-Option hat, auf den Settings-Feldnamen -- der zugleich der Optionsname ist,
#: außer bei den Datenbankfeldern. Der Zusammenhang zwischen Feldname und
#: Variablenname ("secret_key" <-> "THERMOCTL_SECRET_KEY") ist derselbe, den
#: ``docker/thermoctl_optionen.py`` in ``translate()`` schon voraussetzt -- dort ist
#: er nur nie als Zeichenkette aufgeschrieben, weil die Vorwärtsrichtung ihn nicht
#: braucht.
_VARIABLE_ZU_FELD: dict[str, str] = {
    f"THERMOCTL_{feld.upper()}": feld
    for feld in optionen.ABGEBILDETE_FELDER
    if feld != "database_url"
}

#: Settings-Felder, die zwar keine dedizierte Add-on-Option haben, aber trotzdem
#: übertragen werden sollen -- über das freie ``env``-Feld. Das ist
#: ``BEWUSST_AUSGELASSEN`` minus der Felder, die das Add-on grundsätzlich nicht
#: braucht (``_ADDON_BRAUCHT_NICHT``): jene sind ohne Wirkung und werden nicht einmal
#: dorthin übertragen.
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


def addon_optionen(
    env_werte: dict[str, str],
    *,
    ohne_datenbank: bool = False,
    kommentierte_mariadb_url: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Baut die Add-on-Optionen aus geparsten ``.env``-Werten.

    Gibt die Optionen (in Schema-Reihenfolge sortiert von der Aufruferin) und eine
    Liste von Hinweiszeilen für die Fehlerausgabe zurück -- letztere nennen nur
    Variablennamen und Gründe, nie Werte, damit auch die Diagnose keine Zugangsdaten
    preisgibt.

    ``ohne_datenbank`` lässt alle ``database_*``-Felder in der Ausgabe komplett weg
    -- für den Fall, dass im Add-on bereits eine Datenbank eingetragen ist und die
    ``.env`` unangetastet bleiben soll (siehe CLI-Hilfetext in ``main``).
    ``kommentierte_mariadb_url`` ist das Ergebnis von ``auskommentierte_datenbank_url``
    auf dem rohen ``.env``-Text und gewinnt gegen eine aktive SQLite-URL, siehe
    ``_datenbank_optionen``.
    """
    optionswerte: dict[str, Any] = {}
    env_feld_zeilen: list[str] = []
    hinweise: list[str] = []

    for name, wert in env_werte.items():
        if name == "THERMOCTL_DATABASE_URL":
            if ohne_datenbank:
                hinweise.append(
                    f"{name}: übersprungen -- --ohne-datenbank angegeben, die im "
                    "Add-on bereits eingetragene Datenbank bleibt unangetastet."
                )
                continue
            db_optionen, db_hinweise = _datenbank_optionen(wert, kommentierte_mariadb_url)
            optionswerte.update(db_optionen)
            hinweise.extend(db_hinweise)
            continue
        if name in _UEBERSPRUNGEN:
            hinweise.append(
                f"{name}: übersprungen -- {_UEBERSPRUNGEN[name]}"
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
                "ins freie `env`-Feld übernommen."
            )
            continue
        if name.startswith("THERMOCTL_"):
            env_feld_zeilen.append(f"{name}={wert}")
            hinweise.append(
                f"{name}: keiner bekannten Einstellung zugeordnet -- ins freie "
                "`env`-Feld übernommen."
            )
            continue
        hinweise.append(f"{name}: keine THERMOCTL_*-Variable -- ignoriert.")

    if env_feld_zeilen:
        optionswerte["env"] = "\n".join(env_feld_zeilen)

    return optionswerte, hinweise


def _yaml_zeichenkette(wert: str) -> str:
    """Zitiert eine Zeichenkette als YAML-Doppelquote-Skalar.

    Doppelt zitierte YAML-Skalare kennen Escape-Sequenzen für alles, was in einem
    Wert aus der ``.env`` vorkommen kann -- Anführungszeichen, Doppelpunkt, `#`,
    Zeilenumbruch (das mehrzeilige ``env``-Feld) -- und sind deshalb die einzige Form,
    die ohne Fallunterscheidung für jeden Wert richtig ist.
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


_OHNE_DATENBANK_FLAG = "--ohne-datenbank"


def _aufruf_text(programmname: str) -> str:
    """Der Hilfetext zum Aufruf -- auch das, was bei falschen Argumenten erscheint.

    Nennt zuerst den Fall, der für einen Add-on-Betrieb mit eigener Datenbank der
    übliche ist: Die Datenbank steht schon im Add-on (typischerweise MariaDB) und
    soll dort unangetastet bleiben, auch wenn die ``.env`` etwas anderes sagt --
    z. B. eine ``sqlite:///...``-Zeile aus der lokalen Entwicklungsumgebung.
    """
    return (
        f"Aufruf: {programmname} [{_OHNE_DATENBANK_FLAG}] <.env-Datei>\n"
        "\n"
        f"  {_OHNE_DATENBANK_FLAG}\n"
        "      Lässt alle database_*-Felder in der Ausgabe komplett weg.\n"
        "      Der übliche Grund: Im Add-on ist bereits eine Datenbank eingetragen\n"
        "      (typischerweise MariaDB) und soll so bleiben -- die .env enthält dann\n"
        "      z. B. nur noch eine sqlite:///...-Zeile als Ueberbleibsel der lokalen\n"
        "      Entwicklungsumgebung. Ohne diesen Schalter würde die erzeugte YAML\n"
        "      die im Add-on eingetragene Datenbank beim Einfügen überschreiben."
    )


def main(argv: list[str]) -> int:
    programmname = argv[0] if argv else "env_nach_addon.py"
    ohne_datenbank = False
    pfade: list[str] = []
    unbekannte_flags: list[str] = []
    for arg in argv[1:]:
        if arg == _OHNE_DATENBANK_FLAG:
            ohne_datenbank = True
        elif arg.startswith("-"):
            unbekannte_flags.append(arg)
        else:
            pfade.append(arg)

    if unbekannte_flags or len(pfade) != 1:
        print(_aufruf_text(programmname), file=sys.stderr)
        return 2

    env_pfad = Path(pfade[0])
    try:
        text = env_pfad.read_text()
    except OSError as error:
        print(f"env_nach_addon: {env_pfad} nicht lesbar: {error}", file=sys.stderr)
        return 1

    env_werte = optionen._parse_env_field(text)  # noqa: SLF001 -- bewusst dieselbe Funktion
    kommentierte_mariadb_url = auskommentierte_datenbank_url(text)

    try:
        optionswerte, hinweise = addon_optionen(
            env_werte,
            ohne_datenbank=ohne_datenbank,
            kommentierte_mariadb_url=kommentierte_mariadb_url,
        )
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
        "env_nach_addon: Die Ausgabe enthält Zugangsdaten aus der eingelesenen "
        f"{env_pfad} im Klartext. In Home Assistant unter Add-on -> Konfiguration -> "
        "YAML bearbeiten einfügen; wer sie in eine Datei umleitet (`> datei.yaml`), "
        "behandelt diese Datei mit derselben Sorgfalt wie die `.env` selbst -- nicht "
        "committen, nicht liegen lassen.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
