"""What the audit trail refuses to record."""

import pytest
from sqlalchemy.orm import Session

from thermoctl import audit


def test_an_unknown_source_is_refused_rather_than_recorded(session: Session) -> None:
    """The source comes from the adapter, so a typo is a real possibility.

    Recording it anyway would put the entry in the log under a source nobody filters
    for -- an audit trail that quietly loses entries is worse than one that fails
    loudly at the moment the mistake is made.
    """
    with pytest.raises(ValueError, match="Unbekannte Audit-Quelle"):
        audit.record(
            session, source="gibtesnicht", action="update", object_type="zone",
            object_id="1", summary="egal",
        )
