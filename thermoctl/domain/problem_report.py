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


def _clean_note(text: str) -> str:
    """Der Freitext, so wie er hinausgehen darf.

    Zeilenumbrüche und Steuerzeichen fallen weg: der Text landet in einer
    HTTP-Nutzlast an ein fremdes System, und was dort eine Zeile beginnt, entscheidet
    nicht der Absender einer Meldung.
    """
    flattened = "".join(" " if character < " " else character for character in text)
    return " ".join(flattened.split())[:NOTE_LIMIT]


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
