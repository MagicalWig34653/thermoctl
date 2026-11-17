"""Gemeinsame Zeitgrenze fuer das Lesen von Messwerten.

Ein gespeicherter Messwert mit Zeitstempel weit in der Zukunft wuerde bei jeder
Abfrage "neuester Wert" alle spaeteren, korrekten Werte verdraengen. Beim Empfang
wird das schon verhindert (`reading._measured_at`); diese Klausel schuetzt zusaetzlich
die Lesestellen vor Altbestand.
"""

from datetime import datetime

from sqlalchemy import ColumnElement

from thermoctl.db.models.measurement import Measurement
from thermoctl.domain.fault import FUTURE_TOLERANCE


def not_in_future(now: datetime) -> ColumnElement[bool]:
    """Nur Messwerte, deren Zeitstempel hoechstens die Toleranz nach `now` liegt."""
    return Measurement.measured_at <= now + FUTURE_TOLERANCE
