#!/bin/sh
set -e

# THERMOCTL_ENTRYPOINT_UNPRIVILEGED markiert, dass dieser ganze Block -- Optionsdatei,
# Ingress-Pfad -- in diesem Start schon einmal lief. Gesetzt wird sie weiter unten, kurz
# bevor sich das Skript per `exec setpriv ... "$0"` selbst als unprivilegierter Benutzer
# neu startet (siehe dort). Ohne diese Markierung wuerde der Neustart den ganzen Block
# ein zweites Mal ausfuehren -- jetzt ohne die Rechte, die zum Lesen von options.json
# noetig waren, und der zweite Leseversuch schluege fehl, obwohl der erste (als root)
# bereits erfolgreich war und die Werte schon exportiert sind (sie ueberleben den
# Neustart unveraendert, `setpriv` gibt die Umgebung unveraendert weiter).
if [ -z "${THERMOCTL_ENTRYPOINT_UNPRIVILEGED:-}" ]; then
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
fi

# Der Supervisor legt /data/options.json selbst an -- root:root, nur fuer root lesbar --
# und der Container muss sie lesen koennen, bevor irgendetwas anderes laeuft. Deshalb
# startet das Abbild als root (siehe Dockerfile: kein `USER` mehr gesetzt) und hat sie
# oben bereits gelesen. Ab hier braucht niemand mehr erhoehte Rechte, und der Dienst
# selbst soll -- wie bisher -- unprivilegiert laufen: das ist eine bewusste Eigenschaft
# dieses Abbilds (siehe CLAUDE.md), keine Nachlaessigkeit, die dieser Umstieg aufgeben
# darf. `setpriv` ist Teil von util-linux und im Basisabbild schon vorhanden -- kein
# zusaetzliches Paket dafuer.
#
# Der gewoehnliche docker-compose-Betrieb ist von diesem Block nicht betroffen: startet
# ein Operator den Container explizit mit `user:` (etwa dem bisherigen Standard, uid
# 10001), ist `id -u` hier nie `0`, und der Block laeuft nie -- unveraendertes Verhalten.
# Ohne `user:`-Angabe startet der Container jetzt zwar kurz als root statt direkt als
# `thermoctl`, faellt aber noch vor `alembic` auf denselben unprivilegierten Benutzer
# zurueck wie bisher; der laufende Dienst selbst ist in beiden Faellen unveraendert
# unprivilegiert. Die zweite Bedingung ist dieselbe Markierung wie oben -- verhindert,
# dass ein zweiter Durchlauf (falls `id -u` danach aus irgendeinem Grund wieder `0`
# meldet) sich selbst erneut re-execen wuerde.
datenverzeichnis="${THERMOCTL_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ] && [ -z "${THERMOCTL_ENTRYPOINT_UNPRIVILEGED:-}" ]; then
    chown thermoctl:thermoctl "$datenverzeichnis"
    export THERMOCTL_ENTRYPOINT_UNPRIVILEGED=1
    exec setpriv --reuid=thermoctl --regid=thermoctl --init-groups "$0"
fi

alembic upgrade head
exec thermoctl
