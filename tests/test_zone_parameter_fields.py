"""Feldlisten der Zonenparameter: festgeschrieben und aus `PARAMETERS` abgeleitet."""
from thermoctl.domain.zone_settings import BY_NAME
from thermoctl.web.shared.zone_parameters import FELDER, GANZZAHLEN, PI_FELDER

ERBENDE_FELDER = (
    "hysteresis_k",
    "min_on_seconds",
    "min_off_seconds",
    "sensor_timeout_seconds",
    "temperature_offset_k",
    "window_resume_delay_seconds",
    "solar_setback_max_k",
)


def test_felderlisten_unveraendert() -> None:
    assert FELDER == (
        *ERBENDE_FELDER,
        "valve_protection_interval_days",
        "valve_protection_duration_minutes",
    )
    assert PI_FELDER == (
        "pi_gain_per_k",
        "pi_integral_time_minutes",
        "pi_min_on_seconds",
        "pi_min_off_seconds",
    )
    assert GANZZAHLEN == frozenset(
        {
            "min_on_seconds",
            "min_off_seconds",
            "sensor_timeout_seconds",
            "window_resume_delay_seconds",
            "valve_protection_interval_days",
            "valve_protection_duration_minutes",
            "pi_integral_time_minutes",
            "pi_min_on_seconds",
            "pi_min_off_seconds",
        }
    )


def test_jeder_parameter_hat_eine_gruppe() -> None:
    from thermoctl.domain.zone_settings import PARAMETERS
    from thermoctl.web.shared.zone_parameters import ERBENDE_FELDER as ABGELEITET

    assert ABGELEITET == ERBENDE_FELDER
    schalter = {"valve_protection_enabled", "pi_enabled"}
    assert {p.name for p in PARAMETERS if not p.group} == set()
    assert {p.name for p in PARAMETERS} == {*FELDER, *PI_FELDER, *schalter}


def test_felder_stimmen_mit_beschreibung_ueberein() -> None:
    alle = (*FELDER, *PI_FELDER)
    assert len(alle) == len(set(alle))
    assert all(name in BY_NAME for name in alle)
    assert {name for name in alle if BY_NAME[name].integral} == GANZZAHLEN
