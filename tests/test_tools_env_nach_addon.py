"""Tests for ``tools/env_nach_addon.py``.

This script runs ``docker/thermoctl_optionen.py`` backwards: it reads a ``.env`` and
produces the Home Assistant add-on configuration YAML that, fed back through
``thermoctl_optionen.translate()`` plus its own ``_parse_env_field()``, must yield the
same ``THERMOCTL_*`` variables the ``.env`` started with. That round trip
(``test_round_trip_*``) is the load-bearing test in this file: it is what keeps the
two directions from drifting apart the way this project's mapping has drifted three
times before (see both scripts' docstrings).

Loaded by file path, same reason and same technique as
``tests/test_docker_addon_options.py``: it lives outside the ``thermoctl`` package and
is called with a bare ``python3``.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "env_nach_addon.py"
OPTIONEN_SCRIPT = ROOT / "docker" / "thermoctl_optionen.py"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


werkzeug = _load_module(SCRIPT, "env_nach_addon")
optionen = _load_module(OPTIONEN_SCRIPT, "thermoctl_optionen_fuer_rueckrichtung")


# --- Eine .env mit jeder dokumentierten Einstellung, fuer den Rundlauf-Test ---------

_VOLLSTAENDIGE_ENV_MARIADB = """
# Kommentar, wird ignoriert
export THERMOCTL_DATABASE_URL=mysql+pymysql://thermoctl_nutzer:sch-wer-PW123@db.example.org:3307/thermoctl_db
THERMOCTL_SECRET_KEY=x123456789012345678901234567890123456789y

THERMOCTL_BIND_HOST=0.0.0.0
THERMOCTL_BIND_PORT=8000
THERMOCTL_ROOT_PATH=/pfad
THERMOCTL_LOG_LEVEL=DEBUG
THERMOCTL_LOG_FORMAT=plain
THERMOCTL_SECURE_COOKIES=true

THERMOCTL_MQTT_ENABLED=true
THERMOCTL_MQTT_HOST=192.168.0.10
THERMOCTL_MQTT_PORT=8883
THERMOCTL_MQTT_TLS=true
THERMOCTL_MQTT_CA_CERT=/ssl/mqtt-ca.pem
THERMOCTL_MQTT_USERNAME=thermoctl
THERMOCTL_MQTT_PASSWORD=mqtt-geheimnis
THERMOCTL_MQTT_CLIENT_ID=heizung-keller
THERMOCTL_MQTT_BASE_TOPIC=zigbee2mqtt
THERMOCTL_MQTT_PREFIX=heizung

THERMOCTL_MEROSS_EMAIL=person@example.org
THERMOCTL_MEROSS_PASSWORD=meross-geheimnis
THERMOCTL_MEROSS_API_BASE=https://iotx-us.meross.com

THERMOCTL_NOTIFY_WEBHOOK=https://example.org/hooks/heizung
THERMOCTL_NOTIFY_WEBHOOK_TOKEN=webhook-token

THERMOCTL_MCP_TOKEN=mcp-token

THERMOCTL_PASSKEY_RP_ID=heizung.example.org
THERMOCTL_PASSKEY_RP_NAME=Heizung Zuhause
THERMOCTL_PASSKEY_ORIGIN=https://heizung.example.org
"""


def _quelle_thermoctl_variablen(env_text: str) -> dict[str, str]:
    """Alle ``THERMOCTL_*``-Zuweisungen aus einem ``.env``-Text, ueber dieselbe
    Parsing-Funktion, die auch das zu pruefende Skript benutzt."""
    return {
        name: wert
        for name, wert in optionen._parse_env_field(env_text).items()
        if name.startswith("THERMOCTL_")
    }


def _rundlauf(env_text: str) -> dict[str, str]:
    """Fuehrt den vollen Rundlauf aus: .env-Text -> addon_optionen() -> YAML ->
    zurueckgelesen -> thermoctl_optionen.translate() + _parse_env_field() auf dessen
    ``env``-Feld. Gibt die am Ende wiederhergestellten THERMOCTL_*-Variablen zurueck."""
    env_werte = optionen._parse_env_field(env_text)
    optionswerte, _hinweise = werkzeug.addon_optionen(env_werte)
    yaml_text = werkzeug.als_yaml(optionswerte)

    # Die Ausgabe muss gueltiges YAML sein -- das ist Teil dessen, was hier geprueft wird.
    zurueckgelesen = yaml.safe_load(yaml_text)
    assert zurueckgelesen == optionswerte

    ergebnis = dict(optionen.translate(zurueckgelesen))
    env_feld = zurueckgelesen.get("env", "")
    ergebnis.update(optionen._parse_env_field(env_feld))
    return ergebnis


class TestRundlauf:
    """Der im Auftrag verlangte Beleg: was hinten herauskommt, entspricht dem, was
    vorne hineinging -- fuer jede dokumentierte Einstellung."""

    def test_jede_dedizierte_einstellung_kommt_unveraendert_zurueck(self) -> None:
        quelle = _quelle_thermoctl_variablen(_VOLLSTAENDIGE_ENV_MARIADB)
        ergebnis = _rundlauf(_VOLLSTAENDIGE_ENV_MARIADB)

        # Bewusst uebersprungene Felder (Auftrag: bind-Adresse, Port, secure_cookies,
        # root_path) tauchen im Ergebnis zu Recht nicht mehr auf -- fuer sie gilt der
        # Rundlauf nicht, sie werden nirgendwo hin uebertragen.
        uebersprungen = {
            "THERMOCTL_BIND_HOST",
            "THERMOCTL_BIND_PORT",
            "THERMOCTL_ROOT_PATH",
            "THERMOCTL_SECURE_COOKIES",
        }
        erwartet = {k: v for k, v in quelle.items() if k not in uebersprungen}

        assert ergebnis == erwartet

    def test_sqlite_kommt_unveraendert_zurueck(self) -> None:
        env_text = (
            "THERMOCTL_DATABASE_URL=sqlite:////data/thermoctl.db\n"
            "THERMOCTL_SECRET_KEY=" + "s" * 32 + "\n"
        )
        ergebnis = _rundlauf(env_text)
        assert ergebnis["THERMOCTL_DATABASE_URL"] == "sqlite:////data/thermoctl.db"
        assert ergebnis["THERMOCTL_SECRET_KEY"] == "s" * 32

    def test_werte_mit_sonderzeichen_ueberleben_den_rundlauf(self) -> None:
        # `.env`-Werte werden nicht escaped -- nur ein aeusseres, passendes
        # Anfuehrungszeichenpaar faellt weg (siehe `_parse_env_field`). Der Wert
        # bleibt also unquotiert, wie ihn ein Betreiber tatsaechlich eintragen wuerde.
        tricky = 'geheimnis mit "Anfuehrung", : Doppelpunkt, # Raute und \\ Backslash'
        env_text = (
            "THERMOCTL_DATABASE_URL=sqlite:////data/thermoctl.db\n"
            f"THERMOCTL_SECRET_KEY={tricky}\n"
        )
        ergebnis = _rundlauf(env_text)
        assert ergebnis["THERMOCTL_SECRET_KEY"] == tricky

    def test_ein_wert_mit_zeilenumbruch_ueberlebt_den_rundlauf(self) -> None:
        """Kein Feld in der .env selbst darf einen echten Zeilenumbruch enthalten
        (das waere schon fuer _parse_env_field zwei Zeilen) -- wohl aber ein Wert, der
        ueber das freie env-Feld eingespeist wird, denn das env-Feld selbst ist
        mehrzeilig. Der YAML-Quoter muss also auch das richtig zitieren."""
        optionswerte = {"env": "THERMOCTL_MEROSS_API_BASE=eine\nzweite Zeile"}
        yaml_text = werkzeug.als_yaml(optionswerte)
        zurueckgelesen = yaml.safe_load(yaml_text)
        assert zurueckgelesen == optionswerte


# --- addon_optionen(): Verhalten im Einzelnen ---------------------------------------


class TestAddonOptionen:
    def test_unbekannte_thermoctl_variable_landet_im_env_feld(self) -> None:
        optionswerte, hinweise = werkzeug.addon_optionen(
            {"THERMOCTL_NAGELNEU": "wert"}
        )
        assert optionswerte["env"] == "THERMOCTL_NAGELNEU=wert"
        assert any("THERMOCTL_NAGELNEU" in zeile for zeile in hinweise)

    def test_nicht_thermoctl_variable_wird_ignoriert_aber_erwaehnt(self) -> None:
        optionswerte, hinweise = werkzeug.addon_optionen({"SONSTWAS": "wert"})
        assert optionswerte == {}
        assert any("SONSTWAS" in zeile and "ignoriert" in zeile for zeile in hinweise)

    def test_uebersprungene_variable_landet_nirgendwo(self) -> None:
        optionswerte, hinweise = werkzeug.addon_optionen(
            {"THERMOCTL_BIND_HOST": "0.0.0.0"}  # noqa: S104 -- Testwert, kein echter Bind
        )
        assert optionswerte == {}
        assert "env" not in optionswerte
        assert any("uebersprungen" in zeile for zeile in hinweise)

    def test_meross_api_base_landet_im_env_feld_nicht_als_eigene_option(self) -> None:
        optionswerte, hinweise = werkzeug.addon_optionen(
            {"THERMOCTL_MEROSS_API_BASE": "https://iotx-us.meross.com"}
        )
        assert "meross_api_base" not in optionswerte
        assert optionswerte["env"] == "THERMOCTL_MEROSS_API_BASE=https://iotx-us.meross.com"
        assert any("THERMOCTL_MEROSS_API_BASE" in zeile for zeile in hinweise)

    def test_mqtt_enabled_true_wird_zu_python_bool(self) -> None:
        optionswerte, _ = werkzeug.addon_optionen({"THERMOCTL_MQTT_ENABLED": "true"})
        assert optionswerte["mqtt_enabled"] is True

    def test_mqtt_enabled_false_wird_zu_python_bool(self) -> None:
        optionswerte, _ = werkzeug.addon_optionen({"THERMOCTL_MQTT_ENABLED": "false"})
        assert optionswerte["mqtt_enabled"] is False

    @pytest.mark.parametrize("wert", ["1", "yes", "on", "TRUE", "True"])
    def test_weitere_wahr_schreibweisen_werden_erkannt(self, wert: str) -> None:
        optionswerte, _ = werkzeug.addon_optionen({"THERMOCTL_MQTT_TLS": wert})
        assert optionswerte["mqtt_tls"] is True

    def test_ungueltiger_wahrheitswert_ist_ein_fehler(self) -> None:
        with pytest.raises(werkzeug.UmwandlungsFehler, match="Wahrheitswert"):
            werkzeug.addon_optionen({"THERMOCTL_MQTT_ENABLED": "vielleicht"})

    def test_mqtt_port_wird_zu_python_int(self) -> None:
        optionswerte, _ = werkzeug.addon_optionen({"THERMOCTL_MQTT_PORT": "8883"})
        assert optionswerte["mqtt_port"] == 8883
        assert isinstance(optionswerte["mqtt_port"], int)

    def test_ungueltiger_mqtt_port_ist_ein_fehler(self) -> None:
        with pytest.raises(werkzeug.UmwandlungsFehler, match="ganze Zahl"):
            werkzeug.addon_optionen({"THERMOCTL_MQTT_PORT": "nicht-numerisch"})

    def test_leere_env_werte_ergeben_leere_optionen(self) -> None:
        optionswerte, hinweise = werkzeug.addon_optionen({})
        assert optionswerte == {}
        assert hinweise == []


# --- datenbank_optionen_aus_url() ----------------------------------------------------


class TestDatenbankUrl:
    def test_sqlite_unter_dem_erwarteten_pfad(self, capsys: pytest.CaptureFixture[str]) -> None:
        ergebnis = werkzeug.datenbank_optionen_aus_url("sqlite:////data/thermoctl.db")
        assert ergebnis == {"database_type": "sqlite"}
        assert capsys.readouterr().err == ""

    def test_sqlite_mit_abweichendem_pfad_warnt_auf_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ergebnis = werkzeug.datenbank_optionen_aus_url("sqlite:///./daten/thermoctl.db")
        assert ergebnis == {"database_type": "sqlite"}
        stderr = capsys.readouterr().err
        assert "/data/thermoctl.db" in stderr

    def test_vollstaendige_mariadb_url(self) -> None:
        ergebnis = werkzeug.datenbank_optionen_aus_url(
            "mysql+pymysql://nutzer:passwort@host.example.org:3307/db_name"
        )
        assert ergebnis == {
            "database_type": "mariadb",
            "database_host": "host.example.org",
            "database_port": 3307,
            "database_user": "nutzer",
            "database_password": "passwort",
            "database_name": "db_name",
        }

    def test_mariadb_url_ohne_port_bekommt_den_vorgabewert(self) -> None:
        ergebnis = werkzeug.datenbank_optionen_aus_url(
            "mysql+pymysql://nutzer:passwort@host.example.org/db_name"
        )
        assert ergebnis["database_port"] == 3306

    def test_mariadb_url_ohne_passwort_ist_ein_fehler(self) -> None:
        with pytest.raises(werkzeug.UmwandlungsFehler, match="Passwort"):
            werkzeug.datenbank_optionen_aus_url(
                "mysql+pymysql://nutzer@host.example.org/db_name"
            )

    def test_unerkanntes_schema_ist_ein_fehler(self) -> None:
        with pytest.raises(werkzeug.UmwandlungsFehler, match="wird nicht erkannt"):
            werkzeug.datenbank_optionen_aus_url("postgresql://nutzer:pw@host/db")

    def test_kaputte_url_ist_ein_fehler_ohne_die_url_im_text(self) -> None:
        kaputte_url = "://///nicht-geheim-aber-kaputt"
        with pytest.raises(werkzeug.UmwandlungsFehler) as fehler:
            werkzeug.datenbank_optionen_aus_url(kaputte_url)
        assert kaputte_url not in str(fehler.value)

    def test_fehlermeldung_der_unerkannten_url_enthaelt_kein_passwort(self) -> None:
        """Auch bei einem *erkannten*, aber fehlerhaften Schema darf ein Passwort in
        der URL nie in der Fehlermeldung landen -- hier absichtlich mit angehaengtem
        Passwort in einer sonst falschen URL."""
        with pytest.raises(werkzeug.UmwandlungsFehler) as fehler:
            werkzeug.datenbank_optionen_aus_url(
                "postgresql://nutzer:s3hr-geheim@host/db"
            )
        assert "s3hr-geheim" not in str(fehler.value)


# --- YAML-Ausgabe ---------------------------------------------------------------------


class TestYamlAusgabe:
    def test_leere_optionen_ergeben_leere_ausgabe(self) -> None:
        assert werkzeug.als_yaml({}) == "\n"

    def test_reihenfolge_folgt_dem_schema_nicht_der_uebergabe(self) -> None:
        optionswerte = {"mqtt_prefix": "heizung", "secret_key": "s" * 32, "log_level": "DEBUG"}
        text = werkzeug.als_yaml(optionswerte)
        positionen = [text.index(f"{name}:") for name in ("secret_key", "log_level", "mqtt_prefix")]
        assert positionen == sorted(positionen)

    def test_bool_und_int_werden_unquotiert_ausgegeben(self) -> None:
        text = werkzeug.als_yaml({"mqtt_enabled": True, "mqtt_port": 1883})
        assert "mqtt_enabled: true\n" in text
        assert "mqtt_port: 1883\n" in text


# --- main() / CLI ----------------------------------------------------------------------


class TestHauptprogramm:
    def test_ohne_argument_meldet_den_aufruf_auf_stderr(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            ergebnis = werkzeug.main(["env_nach_addon.py"])
        assert ergebnis == 2
        assert "Aufruf" in stderr.getvalue()

    def test_fehlende_datei_meldet_sich_auf_stderr(self, tmp_path: Path) -> None:
        fehlend = tmp_path / "nicht-da.env"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            ergebnis = werkzeug.main(["env_nach_addon.py", str(fehlend)])
        assert ergebnis == 1
        assert "nicht lesbar" in stderr.getvalue()

    def test_erfolgreicher_lauf_gibt_nur_yaml_auf_stdout(self, tmp_path: Path) -> None:
        env_datei = tmp_path / ".env"
        env_datei.write_text(
            "THERMOCTL_DATABASE_URL=sqlite:////data/thermoctl.db\n"
            "THERMOCTL_SECRET_KEY=" + "s" * 32 + "\n"
            "THERMOCTL_MEROSS_API_BASE=https://iotx-us.meross.com\n"
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            ergebnis = werkzeug.main(["env_nach_addon.py", str(env_datei)])
        assert ergebnis == 0

        ausgabe = yaml.safe_load(stdout.getvalue())
        assert ausgabe["database_type"] == "sqlite"
        assert ausgabe["secret_key"] == "s" * 32
        assert ausgabe["env"] == "THERMOCTL_MEROSS_API_BASE=https://iotx-us.meross.com"

        # Kein Wert (Geheimnis) landet auf stderr -- nur Variablennamen und Gruende.
        stderr_text = stderr.getvalue()
        assert "s" * 32 not in stderr_text
        assert "THERMOCTL_MEROSS_API_BASE" in stderr_text
        assert "YAML bearbeiten" in stderr_text

    def test_ungueltige_datenbank_url_bricht_mit_fehlermeldung_ab(
        self, tmp_path: Path
    ) -> None:
        env_datei = tmp_path / ".env"
        env_datei.write_text(
            "THERMOCTL_DATABASE_URL=postgresql://nutzer:pw@host/db\n"
            "THERMOCTL_SECRET_KEY=" + "s" * 32 + "\n"
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            ergebnis = werkzeug.main(["env_nach_addon.py", str(env_datei)])
        assert ergebnis == 1
        assert stdout.getvalue() == ""
        assert "wird nicht erkannt" in stderr.getvalue()

    def test_als_unterprozess_aufgerufen_gibt_gueltiges_yaml_aus(self, tmp_path: Path) -> None:
        """Die Probe fuers echte Kommandozeilen-Verhalten aus dem Auftrag:
        ``python3 tools/env_nach_addon.py .env`` (bzw. mit Umleitung nach stdout)."""
        env_datei = tmp_path / ".env"
        env_datei.write_text(
            "THERMOCTL_DATABASE_URL=sqlite:////data/thermoctl.db\n"
            "THERMOCTL_SECRET_KEY=" + "t" * 32 + "\n"
        )
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT), str(env_datei)],
            capture_output=True,
            text=True,
            check=True,
        )
        ausgabe = yaml.safe_load(result.stdout)
        assert ausgabe == {"database_type": "sqlite", "secret_key": "t" * 32}
        assert "YAML bearbeiten" in result.stderr
