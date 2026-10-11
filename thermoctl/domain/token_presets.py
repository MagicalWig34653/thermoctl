"""Rechte-Presets für Tokens: Ausgangspunkte, keine eigene Rechtestufe.

Ein Preset ist nur eine Vorauswahl von Rechten, die beim Ausstellen eines Tokens
angehakt wird; danach lässt sich jedes Recht einzeln ändern. Presets stehen deshalb
im Code und nicht in der Datenbank -- es gibt nichts zu pflegen, und eine Änderung
wirkt nie rückwirkend auf bereits ausgestellte Tokens.

Bewusst in **keinem** Preset: `control.arm` (Scharfschalten bewegt ein Ventil und
bleibt eine Einzelentscheidung), `user.manage`, `group.manage`, `token.manage`,
`token.self` (ein Token soll sich nicht selbst weitere Zugänge ausstellen) und
`device.manage` (die Gerätezuordnung bleibt ein eigener Haken; wer bei
`set_sensor_failure_policy` Ersatzversätze setzen will, braucht ihn zusätzlich).
"""

READ_ONLY: frozenset[str] = frozenset({"zone.read", "device.read", "audit.read"})
READ_WRITE: frozenset[str] = READ_ONLY | frozenset({
    "override.create", "override.cancel", "vacation.manage", "zone.manage",
    "schedule.manage", "setting.manage", "control.disarm",
})

PRESETS: dict[str, frozenset[str]] = {"read_only": READ_ONLY, "read_write": READ_WRITE}


def preset_permissions(name: str) -> list[tuple[str, None]]:
    """Die Rechte eines Presets als `(Code, zone_id)`-Paare, anlagenweit, sortiert.

    Raises `KeyError` bei einem unbekannten Preset: ein Tippfehler darf nicht still
    ein Token ohne Rechte ergeben.
    """
    return [(code, None) for code in sorted(PRESETS[name])]
