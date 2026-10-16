"""Wochenplan: signierte Rueckgaengig-Marken -- geteilt von den /ui-api-Endpunkten."""
import base64
import hashlib
import hmac
import json
from collections.abc import Sequence

from thermoctl.config import get_settings
from thermoctl.domain.schedule import ScheduleGesture, ScheduleSnapshot


def undo_token(
    zone_id: int,
    gesture: ScheduleGesture,
    history: Sequence[ScheduleSnapshot] = (),
) -> str:
    """`history` lists the plans before `before`, most recent first (multi-step undo of
    the JSON interface); the HTML views never pass it."""
    before, after, revision = gesture
    content: dict[str, object] = {
        "zone_id": zone_id,
        "before": before,
        "after": after,
        "revision": revision,
    }
    if history:
        content["history"] = list(history)
    payload = json.dumps(content, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(
        get_settings().secret_key.get_secret_value().encode(),
        ("schedule-gesture-undo:" + encoded).encode(), hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def undo_payload(token: str) -> dict[str, object]:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(
            get_settings().secret_key.get_secret_value().encode(),
            ("schedule-gesture-undo:" + encoded).encode(), hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return payload
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid undo token") from exc
