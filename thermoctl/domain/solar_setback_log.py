"""Zustandswechsel der Sonnenabsenkung, abgeleitet aus dem Schattenprotokoll.

Warum es das gibt: Die Absenkung (`domain/solar_setback.py`, angewandt in
`services/shadow_run.py::_with_solar_setback`) senkt den Sollwert einer Zone um bis zu
einige Kelvin. Im Betrieb bedeutete das: Zeitplan 20,5 °C, Ist 19,4 °C, wirksamer Sollwert
18,5 °C -- und fünf Stunden lang keine Heizung. Der Grund stand nur im Fließtext von
`shadow_decision.setpoint_reason`, nirgends als eigener Eintrag. Das Schaltprotokoll
(`domain/device_commands.py::list_commands`) zeigt deshalb jetzt auch diese Wechsel.

**Einträge entstehen bei Zustandswechseln, nicht je Zyklus.** Eine Zeile pro Regelzyklus
wäre dasselbe Rauschen, das `action == "normal"` aus dem Protokoll fernhält. Der Zustand
einer Zone ist `shadow_decision.solar_setback_k` (NULL = keine Absenkung); ein Eintrag
entsteht, wenn er sich gegenüber der vorherigen Zeile derselben Zone ändert:

* `beginn`: vorher keine Absenkung, jetzt eine;
* `aenderung`: vorher und jetzt eine Absenkung, aber ein anderer Betrag;
* `ende`: vorher eine Absenkung, jetzt keine.

Eine Änderung des Zeitplan-Sollwerts bei gleichbleibendem Betrag ist keiner.

**Laufzeit.** `shadow_decision` hat Größenordnung 10^5 bis 10^6 Zeilen, eine pro Zone und
Minute. Das Schaltprotokoll wird bei jedem Öffnen der Seite berechnet und darf davon nicht
spürbar langsamer werden. Deshalb:

1. Ein Bereichszugriff über `ix_shadow_decision_solar_setback` liefert je Zone, wann
   zuerst und zuletzt eine Absenkung stand. Zonen ohne jede Absenkung (der Regelfall: der
   Zonenfaktor ist standardmäßig 0) kosten keinen weiteren Zugriff, und eine Anlage ohne
   die Funktion liest nur einen leeren Indexbereich.
2. Davor kann es keinen Wechsel geben (alle früheren Zeilen haben NULL), danach nur genau
   einen: die erste folgende Zeile ist das `ende`. Nur dieser Bereich wird betrachtet.
3. Darin wird vom Neuesten her in wachsenden Zeitabschnitten gelesen, bis genug Einträge
   für die verlangte Seite beisammen sind. Die Wechsel selbst bestimmt die Datenbank mit
   `LAG()` (SQLite >= 3.25, MariaDB >= 10.2); zurück kommen nur die Wechselzeilen, nicht
   die Minutenzeilen dazwischen.

**Die Vorzeile.** Die erste Zeile eines Abschnitts braucht die Zeile davor, sonst entstünde
ein falscher `beginn` mitten in einer laufenden Absenkung. Sie wird je Zone und Abschnitt
gesondert gelesen und nur zum Vergleich mitgenommen, nie selbst gemeldet.

**Zeilen ohne `scheduled_setpoint_c`** (aus der Zeit vor der Migration, und die synthetische
Rückkehr-Markierung des Notbetriebs) tragen keine Aussage über die Absenkung und werden
übersprungen -- sie wären sonst ein vorgetäuschtes `ende`. Eine Zeile, deren Vorgängerin so
fehlt, gilt als "vorher keine Absenkung".

**Die Einträge sind abgeleitet, nicht gespeichert.** Sie folgen deshalb der Aufbewahrungsfrist
von `shadow_decision` (was dort bereinigt ist, fehlt auch hier) und dem heutigen Zonennamen
(`Zone.name`; eine gelöschte Zone hat keine Einträge mehr, anders als bei den Gerätebefehlen
mit ihrem Namensschnappschuss). Am Anfang der verbleibenden Historie kann eine Absenkung, die
schon lief, als neuer `beginn` erscheinen -- die Vorzeile, die das Gegenteil zeigen würde, ist
bereinigt.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from thermoctl.db.models.state import ShadowDecision
from thermoctl.db.models.zone import Zone
from thermoctl.domain.number_text import difference_text, temperature_text

KIND_BEGIN = "beginn"
KIND_CHANGE = "aenderung"
KIND_END = "ende"

# Erster Leseabschnitt vom Neuesten her; jeder weitere ist doppelt so lang wie der
# vorige. Ein Abschnitt dieser Größe sind bei einer Zeile pro Minute rund 10^4 Zeilen.
_FIRST_SPAN = timedelta(days=7)
# Die Obergrenzen sind halboffen (`< hi`), `to_at` des Aufrufers aber einschließlich;
# eine Mikrosekunde überbrückt das, ohne datenbankspezifische Funktionen.
_EPSILON = timedelta(microseconds=1)


@dataclass(frozen=True)
class SetbackTransition:
    """Ein Wechsel der Sonnenabsenkung einer Zone, mit den Zahlen für den Text."""

    decision_id: int
    decided_at: datetime
    zone_name: str
    kind: str
    previous_k: Decimal | None
    setback_k: Decimal | None
    scheduled_c: Decimal | None
    effective_c: Decimal | None

    @property
    def text(self) -> str:
        """Der Protokolltext. Zahlen über `number_text`, wie in den Regelbegründungen."""
        if self.kind == KIND_END:
            if self.effective_c is None:  # pragma: no cover - setpoint_c is never NULL here
                return "Sonnenabsenkung beendet."
            return f"Sonnenabsenkung beendet: wirksam wieder {temperature_text(self.effective_c)}"
        assert self.setback_k is not None
        amount = difference_text(-self.setback_k, places=1)
        situation = _situation(self.scheduled_c, self.effective_c)
        if self.kind == KIND_CHANGE:
            assert self.previous_k is not None
            before = difference_text(-self.previous_k, places=1)
            return f"Sonnenabsenkung geändert auf {amount} (vorher {before}){situation}"
        return f"Sonnenabsenkung {amount}{situation}"


def _situation(scheduled_c: Decimal | None, effective_c: Decimal | None) -> str:
    if scheduled_c is None or effective_c is None:  # pragma: no cover - set whenever k is
        return ""
    return f": Zeitplan {temperature_text(scheduled_c)}, wirksam {temperature_text(effective_c)}"


def setback_transitions(
    session: Session,
    *,
    zone_name: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    fetch: int,
) -> list[SetbackTransition]:
    """Die neuesten `fetch` Wechsel, neueste zuerst; Grenzen als naive UTC, einschließlich.

    Rechte prüft der Aufrufer (`audit.read`), wie bei den übrigen Quellen des
    Schaltprotokolls. `zone_name` trifft nur noch bestehende Zonen: `shadow_decision` hängt
    per CASCADE an der Zone und überlebt ihre Löschung nicht.
    """
    zone_query = select(Zone.id, Zone.name)
    if zone_name:
        # Auch der Anzeigename trifft: `device_command.zone_name` und damit die Zonenauswahl
        # des Schaltprotokolls tragen ihn (`setback_zone_names`); wer dort eine Zone wählt,
        # soll ihre Absenkungen ebenfalls sehen. Der Eintrag selbst nennt weiter `Zone.name`.
        zone_query = zone_query.where(or_(Zone.name == zone_name, Zone.display_name == zone_name))
    names = {zone_id: name for zone_id, name in session.execute(zone_query)}
    if not names:
        return []

    bounds = session.execute(
        select(
            ShadowDecision.zone_id,
            func.min(ShadowDecision.decided_at),
            func.max(ShadowDecision.decided_at),
        )
        .where(ShadowDecision.solar_setback_k.is_not(None), ShadowDecision.zone_id.in_(names))
        .group_by(ShadowDecision.zone_id)
    ).all()

    found: list[SetbackTransition] = []
    for zone_id, first_at, last_at in bounds:
        found.extend(
            _zone_transitions(
                session,
                zone_id=zone_id,
                zone_name=names[zone_id],
                first_at=first_at,
                last_at=last_at,
                from_at=from_at,
                to_at=to_at,
                fetch=fetch,
            )
        )
    found.sort(key=lambda row: (row.decided_at, row.decision_id), reverse=True)
    return found[:fetch]


def setback_zone_names(session: Session) -> list[str]:
    """Anzeigenamen der bestehenden Zonen, die je eine Sonnenabsenkung hatten.

    Für die Zonenauswahl des Schaltprotokolls: Eine Zone, die nur Absenkungseinträge und
    keinen Gerätebefehl hat, wäre dort sonst nicht wählbar. Ein einziger Zugriff auf
    `ix_shadow_decision_solar_setback` (nur Zeilen mit Wert, die Spalte ist fast überall
    NULL), derselbe Bereich, den `setback_transitions` ohnehin liest; kein Scan der
    Minutenzeilen. Wie dort gilt: gelöschte Zonen fehlen, ihre Zeilen sind per CASCADE weg.
    """
    # Verbund mit Gruppierung statt `IN (Unterabfrage)`/`EXISTS`: Nur diese Form bringt den
    # Planer dazu, den Index `ix_shadow_decision_solar_setback` (Bereich `k > NULL`, nur
    # die wenigen Zeilen mit Wert) zu lesen; die anderen Formen scannen in SQLite den
    # Zonenindex über alle Minutenzeilen.
    return list(
        session.scalars(
            select(Zone.display_name)
            .join(ShadowDecision, ShadowDecision.zone_id == Zone.id)
            .where(ShadowDecision.solar_setback_k.is_not(None))
            .group_by(Zone.id, Zone.display_name)
            .order_by(Zone.display_name)
        )
    )


def _zone_transitions(
    session: Session,
    *,
    zone_id: int,
    zone_name: str,
    first_at: datetime,
    last_at: datetime,
    from_at: datetime | None,
    to_at: datetime | None,
    fetch: int,
) -> list[SetbackTransition]:
    """Die neuesten Wechsel einer Zone, gelesen in wachsenden Abschnitten vom Neuesten her."""
    end_at = session.scalar(
        select(func.min(ShadowDecision.decided_at)).where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.decided_at > last_at,
            ShadowDecision.scheduled_setpoint_c.is_not(None),
        )
    )
    # Das `ende` ist die erste Zeile nach der letzten mit Absenkung; gibt es sie noch
    # nicht, steht die Absenkung noch.
    upper = (end_at if end_at is not None else last_at) + _EPSILON
    if to_at is not None:
        upper = min(upper, to_at + _EPSILON)
    floor = first_at if from_at is None else max(first_at, from_at)

    found: list[SetbackTransition] = []
    span = _FIRST_SPAN
    while upper > floor and len(found) < fetch:
        lower = max(floor, upper - span)
        found.extend(_chunk(session, zone_id, zone_name, first_at, lower, upper))
        upper = lower
        span *= 2
    found.sort(key=lambda row: (row.decided_at, row.decision_id), reverse=True)
    return found[:fetch]


def _chunk(
    session: Session,
    zone_id: int,
    zone_name: str,
    first_at: datetime,
    lower: datetime,
    upper: datetime,
) -> Sequence[SetbackTransition]:
    """Die Wechsel mit `lower <= decided_at < upper` -- die Vorzeile liegt davor.

    Bei `lower == first_at` braucht es keine Vorzeile: alles davor hat NULL, die erste
    Zeile mit Absenkung ist ohnehin ein `beginn`.
    """
    start = lower
    if lower > first_at:
        seed_at = session.scalar(
            select(ShadowDecision.decided_at)
            .where(
                ShadowDecision.zone_id == zone_id,
                ShadowDecision.decided_at >= first_at,
                ShadowDecision.decided_at < lower,
                ShadowDecision.scheduled_setpoint_c.is_not(None),
            )
            .order_by(ShadowDecision.decided_at.desc(), ShadowDecision.id.desc())
            .limit(1)
        )
        if seed_at is not None:
            start = seed_at

    order = (ShadowDecision.decided_at, ShadowDecision.id)
    inner = (
        select(
            ShadowDecision.id.label("id"),
            ShadowDecision.decided_at.label("decided_at"),
            ShadowDecision.solar_setback_k.label("k"),
            ShadowDecision.scheduled_setpoint_c.label("scheduled_c"),
            ShadowDecision.setpoint_c.label("effective_c"),
            func.lag(ShadowDecision.solar_setback_k)
            .over(partition_by=ShadowDecision.zone_id, order_by=order)
            .label("prev_k"),
            func.row_number()
            .over(partition_by=ShadowDecision.zone_id, order_by=order)
            .label("rn"),
        )
        .where(
            ShadowDecision.zone_id == zone_id,
            ShadowDecision.decided_at >= start,
            ShadowDecision.decided_at < upper,
            # Zeilen ohne diese Angabe sagen nichts über die Absenkung, siehe Moduldoku.
            ShadowDecision.scheduled_setpoint_c.is_not(None),
        )
        .subquery()
    )
    # Die Vorzeile (`decided_at < lower`) wird nur zum Vergleich gelesen und fällt hier
    # heraus. Die allererste Zeile ohne Vorzeile zählt als "vorher keine Absenkung".
    rows = session.execute(
        select(inner).where(
            inner.c.decided_at >= lower,
            or_(
                and_(inner.c.rn > 1, inner.c.k.is_distinct_from(inner.c.prev_k)),
                and_(inner.c.rn == 1, inner.c.k.is_not(None)),
            ),
        )
    ).all()
    return [
        SetbackTransition(
            decision_id=row.id,
            decided_at=row.decided_at,
            zone_name=zone_name,
            kind=_kind(row.prev_k, row.k),
            previous_k=row.prev_k,
            setback_k=row.k,
            scheduled_c=row.scheduled_c,
            effective_c=row.effective_c,
        )
        for row in rows
    ]


def _kind(previous_k: Decimal | None, k: Decimal | None) -> str:
    if k is None:
        return KIND_END
    return KIND_BEGIN if previous_k is None else KIND_CHANGE
