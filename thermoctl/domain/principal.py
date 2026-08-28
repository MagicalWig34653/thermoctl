from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Wer handelt — Benutzer oder Token — samt seinem effektiven Rechteumfang.

    Die Adapter (HTMX, REST, spaeter MCP) bekommen nur diesen Typ zu sehen und muessen
    nicht wissen, womit sie es zu tun haben.

    `grants` enthaelt Paare (berechtigungs_code, zone_id). `zone_id = None` heisst
    anlagenweit.
    """

    user_id: int
    token_id: int | None
    grants: frozenset[tuple[str, int | None]]
