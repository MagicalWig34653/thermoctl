"""Kiosk: Konstanten, die Alt-Views und kiosk_api gemeinsam benutzen."""
from decimal import Decimal

# The same step used by the start page's thermostat (`daily_views.py`) -- one click
# should be one perceptible change, not a fraction of a degree nobody notices and not
# a jump so coarse that reaching a target takes forever.
THERMOSTAT_STEP = Decimal("0.5")
