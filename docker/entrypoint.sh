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

# Unter Ingress vergibt der Supervisor den Pfadpraefix selbst und teilt ihn nur ueber
# seine eigene API mit -- ein Betreiber kann ihn nicht im Voraus kennen und also nicht
# in options.json eintragen. Das Skript erkennt den gewoehnlichen docker-compose-Betrieb
# selbst (kein SUPERVISOR_TOKEN) und gibt dann nichts aus; dieser Aufruf laeuft also
# immer, ohne die Datei-Existenzpruefung oben. Gleiche Begruendung fuer die
# Zwischenvariable wie beim Optionsskript: ein echter Fehlschlag muss unter `set -e`
# abbrechen koennen, statt in einem leeren eval zu verschwinden.
ingressskript="${THERMOCTL_INGRESS_SCRIPT:-/usr/local/bin/thermoctl_ingress.py}"
ingress_exporte="$(python3 "$ingressskript")"
if [ -n "$ingress_exporte" ]; then
    eval "$ingress_exporte"
fi

alembic upgrade head
exec thermoctl
