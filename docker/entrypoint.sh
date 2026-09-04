#!/bin/sh
set -e

# Home-Assistant-Add-on-Betrieb: der Supervisor legt die Nutzereinstellungen als
# /data/options.json ab. Ohne diese Datei (gewoehnlicher docker-compose-Betrieb)
# aendert sich hier nichts. THERMOCTL_ADDON_OPTIONS_FILE/_SCRIPT sind nur fuer Tests da.
optionsdatei="${THERMOCTL_ADDON_OPTIONS_FILE:-/data/options.json}"
optionsskript="${THERMOCTL_ADDON_OPTIONS_SCRIPT:-/usr/local/bin/thermoctl_optionen.py}"
if [ -f "$optionsdatei" ]; then
    # In eine Variable einlesen statt direkt in eval, damit ein Fehlschlag des
    # Python-Skripts unter `set -e` auch wirklich abbricht -- ein Fehlschlag
    # innerhalb von eval "$(...)" selbst wuerde sonst verschluckt. Kein `set -x`
    # in diesem Block und keine Ausgabe der Werte: Zugangsdaten duerfen nicht
    # ins Log geraten (Grundsatz 2).
    exporte="$(THERMOCTL_ADDON_OPTIONS_FILE="$optionsdatei" python3 "$optionsskript")"
    eval "$exporte"
fi

alembic upgrade head
exec thermoctl
