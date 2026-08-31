import json
import logging
import sys

import pytest

from thermoctl.config import Settings
from thermoctl.logging import (
    JsonFormatter,
    KioskPathFilter,
    MaskingFilter,
    configure_logging,
    mask,
    request_id_var,
)


def test_output_is_valid_json() -> None:
    record = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "hallo"
    assert data["level"] == "INFO"
    assert "timestamp" in data


def test_the_request_id_ends_up_in_the_record() -> None:
    marker = request_id_var.set("abc123")
    try:
        record = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
        data = json.loads(JsonFormatter().format(record))
        assert data["request_id"] == "abc123"
    finally:
        request_id_var.reset(marker)


def test_extra_fields_are_carried_over() -> None:
    record = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    record.zone = "Wohnzimmer"  # type: ignore[attr-defined]
    data = json.loads(JsonFormatter().format(record))
    assert data["zone"] == "Wohnzimmer"


def test_masking_applies_to_known_keys() -> None:
    raw = {"username": "lino", "password": "geheim", "token": "tctl_x_y"}
    assert mask(raw) == {"username": "lino", "password": "***", "token": "***"}


def test_masking_works_nested_and_in_lists() -> None:
    raw = {"aussen": {"secret_key": "s"}, "liste": [{"cookie": "c"}]}
    result = mask(raw)
    assert result == {"aussen": {"secret_key": "***"}, "liste": [{"cookie": "***"}]}


def test_masking_is_case_insensitive() -> None:
    assert mask({"Authorization": "Bearer x"}) == {"Authorization": "***"}


def test_masked_fields_do_not_appear_in_the_output() -> None:
    record = logging.LogRecord("t", logging.INFO, "p", 1, "start", None, None)
    record.config = {"secret_key": "streng-geheim"}  # type: ignore[attr-defined]
    text = JsonFormatter().format(record)
    assert "streng-geheim" not in text
    assert "***" in text


def test_masking_recognizes_compound_keys() -> None:
    raw = {
        "mqtt_password": "geheim1",
        "client_secret": "geheim2",
        "refresh_token": "geheim3",
        "broker_password": "geheim4",
        "access_token": "geheim5",
    }
    result = mask(raw)
    assert result == {
        "mqtt_password": "***",
        "client_secret": "***",
        "refresh_token": "***",
        "broker_password": "***",
        "access_token": "***",
    }


def test_username_is_not_masked() -> None:
    # "username" contains no core term (password/secret/token/...) and is not
    # itself a secret — it may stay readable.
    assert mask({"username": "lino", "mqtt_username": "lino"}) == {
        "username": "lino",
        "mqtt_username": "lino",
    }


def test_masking_also_works_in_text_format(capsys: object) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        secret_key="a" * 32,
        log_format="text",
    )
    configure_logging(settings)
    logger = logging.getLogger("test.textformat")
    logger.info("verbindung", extra={"mqtt_password": "streng-geheim"})

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "streng-geheim" not in output
    assert "***" in output


def test_the_filter_masks_a_top_level_extra_field_in_isolation() -> None:
    # Tests the filter on its own, without the formatter: the filter must
    # decide based on the field name, not on the (bare) value. A top-level
    # extra field like "mqtt_password" carries exactly the information
    # mask() needs as its attribute name -- which the filter does not pass
    # along so far.
    record = logging.LogRecord("t", logging.INFO, "p", 1, "verbindung", None, None)
    record.mqtt_password = "streng-geheim"  # type: ignore[attr-defined]
    MaskingFilter().filter(record)
    assert record.mqtt_password == "***"  # type: ignore[attr-defined]


def test_the_filter_masks_nested_extra_fields_in_isolation() -> None:
    record = logging.LogRecord("t", logging.INFO, "p", 1, "verbindung", None, None)
    record.config = {"secret_key": "streng-geheim"}  # type: ignore[attr-defined]
    MaskingFilter().filter(record)
    assert record.config == {"secret_key": "***"}  # type: ignore[attr-defined]


def test_masking_recognizes_camelcase_compound_keys() -> None:
    raw = {"mqttPassword": "geheim1", "refreshToken": "geheim2"}
    assert mask(raw) == {"mqttPassword": "***", "refreshToken": "***"}


def test_masking_rejects_substring_matches() -> None:
    # Segment-wise exact check instead of substring search: these names
    # contain "token"/"password"/"secret" only as part of a longer word
    # within a segment, not as a segment of their own, and are not secrets.
    raw = {
        "tokenizer": "x",
        "passwordless_supported": True,
        "secretary_name": "Kim",
        "username": "lino",
    }
    assert mask(raw) == raw


def test_masking_keeps_its_deliberate_over_masking() -> None:
    # Trade-off: when in doubt, better to redact one harmless field too many
    # than one secret too few. "token_count" and "cookie_policy" have a
    # segment ("token"/"cookie") that exactly matches a core term --
    # they are deliberately masked too, even though they are not secrets
    # themselves.
    raw = {"token_count": 3, "cookie_policy": "strict"}
    assert mask(raw) == {"token_count": "***", "cookie_policy": "***"}


def test_a_secret_in_the_message_text_remains_visible() -> None:
    # Deliberate, documented limitation: masking only applies to structured
    # extra fields (extra=...), not to the already-formatted message text.
    # `log.info("passwort=%s", secret)` produces plaintext at logging time
    # that can no longer be masked retroactively. This test pins that down
    # deliberately -- it is not a bug to be "fixed".
    record = logging.LogRecord(
        "t", logging.INFO, "p", 1, "passwort=streng-geheim", None, None
    )
    text = JsonFormatter().format(record)
    assert "streng-geheim" in text


def test_an_exception_ends_up_in_the_json_log() -> None:
    """Without the stack trace, an exception in the log is just a message with no location."""
    import json
    import logging as py_logging

    from thermoctl.logging import JsonFormatter

    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        record = py_logging.LogRecord(
            "test", py_logging.ERROR, __file__, 1, "gescheitert", None, sys.exc_info()
        )
    output = json.loads(JsonFormatter().format(record))
    assert "exception" in output
    assert "ValueError" in output["exception"]


def test_a_text_log_line_does_not_repeat_its_own_message() -> None:
    """Every text log line ended with a copy of itself.

    `super().format()` adds `message` and `asctime` to the LogRecord
    afterward. Neither was in `_STANDARDFELDER`, which is built at import
    time from a fresh record — so the formatter took them for extra fields
    and appended them: "thermoctl startet | database=… message=thermoctl
    startet asctime=…". The fields are now collected before formatting.
    """
    import logging as py_logging

    from thermoctl.logging import TextFormatter

    record = py_logging.LogRecord("test", py_logging.INFO, __file__, 1, "schlicht", None, None)
    assert TextFormatter().format(record) == "schlicht"

    with_extra = py_logging.LogRecord("test", py_logging.INFO, __file__, 1, "gemeldet", None, None)
    with_extra.zone = "wohnzimmer"  # type: ignore[attr-defined]
    output = TextFormatter("%(asctime)s %(message)s").format(with_extra)
    assert output.endswith("gemeldet | zone=wohnzimmer")
    assert "message=" not in output and "asctime=" not in output


def test_the_text_format_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`THERMOCTL_LOG_FORMAT=text` is the choice for humans, json the one for machines."""
    import logging as py_logging

    from thermoctl.config import Settings
    from thermoctl.logging import JsonFormatter, configure_logging

    for format_choice, expected_json in (("text", False), ("json", True)):
        settings = Settings(
            _env_file=None, database_url="sqlite://", secret_key="s" * 32,
            log_format=format_choice,
        )
        configure_logging(settings)
        handler = py_logging.getLogger().handlers[0]
        assert isinstance(handler.formatter, JsonFormatter) is expected_json, format_choice


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped the way uvicorn's access logger builds one.

    That shape is the whole problem: uvicorn does not pass the request path as a
    structured extra field but as one of `record.args`, formatted into the message
    later. `MaskingFilter` only ever looks at extra fields and therefore cannot see it.
    """
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "p",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", path, "1.1", 200),
        None,
    )


def test_a_kiosk_token_never_reaches_the_access_log() -> None:
    """The kiosk token travels in the URL once -- and would be logged on every visit.

    `/kiosk/{token}` carries a live, if revocable, credential in the request line
    itself. Principle 2 forbids that in the log, and this is the one channel the
    masking filter above cannot reach.

    Checked through `getMessage()`, not on the arguments: what matters is what a
    handler would actually write out.
    """
    record = _access_record("/kiosk/tctl_abc_geheimespasswort")
    assert KioskPathFilter().filter(record) is True
    assert "geheimespasswort" not in record.getMessage()
    assert "/kiosk/***" in record.getMessage()


def test_the_filter_leaves_every_other_path_alone() -> None:
    """A filter that rewrote more than the one path would make the log useless."""
    record = _access_record("/zones/1/schedule")
    KioskPathFilter().filter(record)
    assert "/zones/1/schedule" in record.getMessage()


def test_a_record_that_is_not_an_access_line_passes_through_untouched() -> None:
    """Other loggers write records with no args at all -- the filter must not choke."""
    record = logging.LogRecord("t", logging.INFO, "p", 1, "eine Meldung", None, None)
    assert KioskPathFilter().filter(record) is True
    assert record.getMessage() == "eine Meldung"


def test_the_access_logger_carries_the_filter_after_configuration() -> None:
    """The filter sits on the logger, not on a handler.

    `Logger.filter()` runs before a record reaches any handler -- root's included --
    which is where the token has to be gone. Hung on a handler instead, anything that
    ships records past that handler would still see it.
    """
    settings = Settings(database_url="sqlite://", secret_key="s" * 40)
    configure_logging(settings)
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, KioskPathFilter) for f in access.filters)
    # Configured twice -- every TestClient builds a fresh app -- must not pile up.
    configure_logging(settings)
    assert sum(isinstance(f, KioskPathFilter) for f in access.filters) == 1
