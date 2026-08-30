from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Who is acting — user or token — together with their effective permission scope.

    The adapters (HTMX, REST, later MCP) only ever see this type and do not need to
    know what they are actually dealing with.

    `grants` holds pairs of (permission code, zone_id). `zone_id = None` means
    plant-wide.
    """

    user_id: int
    token_id: int | None
    grants: frozenset[tuple[str, int | None]]
