#!/bin/sh
set -e

# THERMOCTL_ENTRYPOINT_UNPRIVILEGED markiert, dass dieser ganze Block -- Optionsdatei,
# Ingress-Pfad -- in diesem Start schon einmal lief. Gesetzt wird sie weiter unten, kurz
# bevor sich das Skript per `exec setpriv ... "$0"` selbst als unprivilegierter Benutzer
# neu startet (siehe dort). Ohne diese Markierung würde der Neustart den ganzen Block
# ein zweites Mal ausführen -- jetzt ohne die Rechte, die zum Lesen von options.json
# nötig waren, und der zweite Leseversuch schlüge fehl, obwohl der erste (als root)
# bereits erfolgreich war und die Werte schon exportiert sind (sie überleben den
# Neustart unverändert, `setpriv` gibt die Umgebung unverändert weiter).
if [ -z "${THERMOCTL_ENTRYPOINT_UNPRIVILEGED:-}" ]; then
    # Home-Assistant-Add-on-Betrieb: der Supervisor legt die Nutzereinstellungen als
    # /data/options.json ab. Ohne diese Datei (gewöhnlicher docker-compose-Betrieb)
    # ändert sich hier nichts. THERMOCTL_ADDON_OPTIONS_FILE/_SCRIPT sind nur für Tests da.
    optionsdatei="${THERMOCTL_ADDON_OPTIONS_FILE:-/data/options.json}"
    optionsskript="${THERMOCTL_ADDON_OPTIONS_SCRIPT:-/usr/local/bin/thermoctl_optionen.py}"
    if [ -f "$optionsdatei" ]; then
        # In eine Variable einlesen statt direkt in eval, damit ein Fehlschlag des
        # Python-Skripts unter `set -e` auch wirklich abbricht -- ein Fehlschlag
        # innerhalb von eval "$(...)" selbst würde sonst verschluckt. Kein `set -x`
        # in diesem Block und keine Ausgabe der Werte: Zugangsdaten dürfen nicht
        # ins Log geraten (Grundsatz 2).
        exporte="$(THERMOCTL_ADDON_OPTIONS_FILE="$optionsdatei" python3 "$optionsskript")"
        eval "$exporte"
    fi

    # Unter Ingress vergibt der Supervisor den Pfadpräfix selbst und teilt ihn nur über
    # seine eigene API mit -- ein Betreiber kann ihn nicht im Voraus kennen und also nicht
    # in options.json eintragen. Das Skript erkennt den gewöhnlichen docker-compose-Betrieb
    # selbst (kein SUPERVISOR_TOKEN) und gibt dann nichts aus; dieser Aufruf läuft also
    # immer, ohne die Datei-Existenzprüfung oben. Gleiche Begründung für die
    # Zwischenvariable wie beim Optionsskript: ein echter Fehlschlag muss unter `set -e`
    # abbrechen können, statt in einem leeren eval zu verschwinden.
    ingressskript="${THERMOCTL_INGRESS_SCRIPT:-/usr/local/bin/thermoctl_ingress.py}"
    ingress_exporte="$(python3 "$ingressskript")"
    if [ -n "$ingress_exporte" ]; then
        eval "$ingress_exporte"
    fi
fi

# Der Supervisor legt /data/options.json selbst an -- root:root, nur für root lesbar --
# und der Container muss sie lesen können, bevor irgendetwas anderes läuft. Deshalb
# startet das Abbild als root (siehe Dockerfile: kein `USER` mehr gesetzt) und hat sie
# oben bereits gelesen. Ab hier braucht niemand mehr erhöhte Rechte, und der Dienst
# selbst soll -- wie bisher -- unprivilegiert laufen: das ist eine bewusste Eigenschaft
# dieses Abbilds (siehe CLAUDE.md), keine Nachlässigkeit, die dieser Umstieg aufgeben
# darf. `setpriv` ist Teil von util-linux und im Basisabbild schon vorhanden -- kein
# zusätzliches Paket dafür.
#
# Der gewöhnliche docker-compose-Betrieb ist von diesem Block nicht betroffen: startet
# ein Operator den Container explizit mit `user:` (etwa dem bisherigen Standard, uid
# 10001), ist `id -u` hier nie `0`, und der Block läuft nie -- unverändertes Verhalten.
# Ohne `user:`-Angabe startet der Container jetzt zwar kurz als root statt direkt als
# `thermoctl`, faellt aber noch vor `alembic` auf denselben unprivilegierten Benutzer
# zurück wie bisher; der laufende Dienst selbst ist in beiden Fällen unverändert
# unprivilegiert. Die zweite Bedingung ist dieselbe Markierung wie oben -- verhindert,
# dass ein zweiter Durchlauf (falls `id -u` danach aus irgendeinem Grund wieder `0`
# meldet) sich selbst erneut re-execen würde.
datenverzeichnis="${THERMOCTL_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ] && [ -z "${THERMOCTL_ENTRYPOINT_UNPRIVILEGED:-}" ]; then
    chown thermoctl:thermoctl "$datenverzeichnis"
    export THERMOCTL_ENTRYPOINT_UNPRIVILEGED=1
    exec setpriv --reuid=thermoctl --regid=thermoctl --init-groups "$0"
fi

alembic upgrade head
exec thermoctl
