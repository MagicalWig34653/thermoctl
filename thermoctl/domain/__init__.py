"""Domain logic: device-independent rules, used equally by all adapters.

This package must not import anything from ``thermoctl.web``, ``thermoctl.api`` or
``fastapi`` (Principle 6: domain logic does not belong in adapters).
"""
