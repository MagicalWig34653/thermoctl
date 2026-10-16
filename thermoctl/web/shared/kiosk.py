"""Kiosk: Konstanten, die kiosk_api benutzt."""
from decimal import Decimal

# The same step the room page's thermostat uses (`ui_api_rooms.py`) -- one click
# should be one perceptible change, not a fraction of a degree nobody notices and not
# a jump so coarse that reaching a target takes forever.
THERMOSTAT_STEP = Decimal("0.5")
