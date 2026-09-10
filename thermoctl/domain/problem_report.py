"""Ein Mieter meldet ein Problem aus seinem Raum -- was dabei mitgeht und was nicht.

Der Bericht geht über die vorhandene, vom Betreiber konfigurierte Meldekette hinaus
(`integrations/notification.py`, derselbe Webhook wie jede Störungsmeldung). Es gibt
hier keinen zweiten Zustellweg und keine zweite Infrastruktur.

**Was der Bericht enthält** -- abschließend, nicht beispielhaft:

* den Anzeigenamen **des gemeldeten Raums**,
* die gewählte Problemart aus `REPORT_KINDS`,
* den optionalen Freitexthinweis des Mieters, gekürzt und von Steuerzeichen befreit,
* den Zeitpunkt der Meldung,
* den letzten Messwert dieses Raums samt seinem Alter,
* den aufgelösten Sollwert samt Begründung und den laufenden Modus,
* den Anzeigenamen des meldenden Benutzers.

**Was er ausdrücklich nicht enthält:** Zugangsdaten jeder Art, Brokeradressen,
Webhook-Ziele, MQTT-Themen, Gerätebezeichner -- und nichts, gar nichts, aus einer
anderen Zone. Die Aufzählung steht hier und nicht in der Weboberfläche, weil sie
eine Eigenschaft der Meldung ist und nicht eine des Formulars; ein Test in
`tests/test_problem_report.py` legt eine zweite Zone mit auffälligem Namen an und
prüft, dass er im Text nicht vorkommt.
"""

import unicodedata
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.identity import User
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.state import ZoneState
from thermoctl.db.models.zone import SetpointMode, Zone
from thermoctl.domain.fault_notice import NOTICE_KIND_TENANT_REPORT, FaultNotice
from thermoctl.domain.modes import DomainError
from thermoctl.domain.schedule import resolved_setpoint
from thermoctl.domain.time import age_in_words, local_time

#: Die vier Fälle, die ein Mieter ohne technische Kenntnis auseinanderhalten kann.
#: Als geschlossene Liste und nicht als Freitextfeld: ein Betreiber, der zehn
#: Meldungen bekommt, soll sie sortieren können, und der Mieter soll nicht raten
#: müssen, was er hinschreiben soll.
REPORT_KINDS: tuple[tuple[str, str], ...] = (
    ("not_warm", "Raum wird nicht warm"),
    ("wrong_temperature", "Temperatur wirkt falsch"),
    ("too_warm", "Raum zu warm"),
    ("other", "Anderes Problem"),
)

#: Wie viel vom Freitext mitgeht. Zweihundert Zeichen sind reichlich für „seit heute
#: Morgen“ und zu wenig, um die Nutzlast an ein fremdes System zu einem eigenen
#: Problem zu machen. Der Rest wird abgeschnitten, nicht abgelehnt -- eine Meldung
#: an der Zeichenzahl scheitern zu lassen wäre die schlechtere Antwort.
NOTE_LIMIT = 200


#: Unicode-Kategorien, die kein sichtbares Zeichen sind: Steuerzeichen, Formatzeichen
#: (darunter die Richtungsumschalter **und** der Zusammenfüger U+200D), Surrogate und
#: Privatgebrauch.
_UNSICHTBAR = frozenset({"Cc", "Cf", "Cs", "Co"})

#: Kombinierende Zeichen -- sie gehören zum Zeichen davor und dürfen bei einer
#: Kürzung nicht von ihm getrennt werden.
_KOMBINIEREND = frozenset({"Mn", "Mc", "Me"})


def _clean_note(text: str) -> str:
    """Der Freitext, so wie er hinausgehen darf.

    Zeilenumbrüche und Steuerzeichen fallen weg: der Text landet in einer
    HTTP-Nutzlast an ein fremdes System, und was dort eine Zeile beginnt, entscheidet
    nicht der Absender einer Meldung.

    Geprüft wird über die Unicode-Kategorie, nicht über `character < " "`. Letzteres
    erwischt nur die C0-Steuerzeichen und ließ genau die durch, auf die es ankommt:
    die Richtungsumschalter (U+202E und Verwandte) können die Anzeige einer Meldung
    im Posteingang des Betreibers umdrehen oder Teile davon umsortieren, und die
    C1-Zeichen ab U+0080 gelten in manchen Anzeigen ebenfalls als Steuerzeichen.
    Derselbe Filter entfernt auch den Zusammenfüger U+200D -- ein aus mehreren
    Teilen bestehendes Emoji zerfällt dabei in seine Teile. Das ist in einem
    Meldungstext das kleinere Übel gegenüber einer Zeichenfolge, deren Anzeige
    niemand vorhersagen kann.

    Gekürzt wird an einer **Zeichengrenze**: fiele der Schnitt zwischen einen
    Buchstaben und den Akzent, der zu ihm gehört, stünde am Ende ein anderer
    Buchstabe als der getippte. Dann fällt der Buchstabe mit weg.
    """
    flattened = "".join(
        " " if unicodedata.category(character) in _UNSICHTBAR else character
        for character in text
    )
    collapsed = " ".join(flattened.split())
    if len(collapsed) <= NOTE_LIMIT:
        return collapsed
    cut = collapsed[:NOTE_LIMIT]
    # Hängt hinter dem Schnitt noch ein kombinierendes Zeichen, war der Schnitt
    # mitten in einem Zeichen -- dann muss auch dessen Basis weg. Danach kann
    # dasselbe für das nun letzte Zeichen gelten (mehrere Akzente auf einer Basis).
    while cut and unicodedata.category(collapsed[len(cut) : len(cut) + 1]) in _KOMBINIEREND:
        cut = cut[:-1]
        while cut and unicodedata.category(cut[-1]) in _KOMBINIEREND:
            cut = cut[:-1]
    return cut.rstrip()


def build_report(
    session: Session,
    zone: Zone,
    kind_code: str,
    note: str,
    *,
    now: datetime,
    user: User | None,
) -> FaultNotice:
    """Baut die Meldung. Schreibt nichts und verschickt nichts -- das tut der Aufrufer.

    Getrennt gehalten, damit der Inhalt für sich prüfbar bleibt: ein Test, der den
    Text gegen einen Webhook prüfen müsste, prüfte am Ende den Webhook.
    """
    labels = dict(REPORT_KINDS)
    if kind_code not in labels:
        raise DomainError("kind", "Bitte eine Problemart auswählen.")

    settings = session.get(Setting, 1)
    timezone_name = settings.timezone if settings is not None else "UTC"
    state = session.scalars(
        select(ZoneState).where(ZoneState.zone_id == zone.id)
    ).first()
    setpoint = resolved_setpoint(session, zone, now)
    mode_name = (
        session.scalar(
            select(SetpointMode.name).where(SetpointMode.id == setpoint.mode_id)
        )
        if setpoint.mode_id is not None
        else None
    )

    measured = (
        f"{state.temperature_c} °C ({age_in_words(state.measured_at, now)})"
        if state is not None and state.temperature_c is not None
        else "kein Messwert vorhanden"
    )
    lines = [
        f"Raum: {zone.display_name}",
        f"Problem: {labels[kind_code]}",
        f"Gemeldet von: {user.display_name if user is not None else 'unbekannt'}",
        f"Zeitpunkt: {local_time(now, timezone_name).strftime('%d.%m.%Y %H:%M')}",
        f"Letzter Messwert: {measured}",
        f"Sollwert: {setpoint.temperature_c} °C ({setpoint.reason})",
        f"Modus: {mode_name or 'keiner'}",
    ]
    cleaned = _clean_note(note)
    if cleaned:
        lines.append(f"Hinweis: {cleaned}")

    return FaultNotice(
        kind=NOTICE_KIND_TENANT_REPORT,
        # Je Zone und Art ein Schlüssel, wie bei den Störungsmeldungen: ein
        # empfangendes System kann damit zusammenfassen, statt jede Meldung als
        # neuen Vorfall zu führen.
        key=f"tenant-report:{zone.id}:{kind_code}",
        severity="stoerung",
        title=f"{zone.display_name}: {labels[kind_code]}",
        text="\n".join(lines),
    )
