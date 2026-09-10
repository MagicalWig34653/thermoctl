from dataclasses import dataclass

from thermoctl.domain.ui_profile import DEFAULT_PROFILE, WebUiProfile


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
    # Welche Weboberfläche dieser Principal bekommt (siehe `domain.ui_profile`).
    # Steht hier und nicht neben dem Benutzer, weil die Adapter genau diesen Typ
    # sehen und sonst ein zweites Mal nachschlagen müssten, wer da gerade handelt.
    #
    # Der Vorgabewert ist ADMIN und nicht TENANT: das Profil ist keine Berechtigung
    # -- ein Principal ohne ausdrückliches Profil (REST-Token, Kiosk, Testaufbau)
    # darf die Anlagensicht sehen, kommt aber nur an das, wofür `grants` reichen.
    # Ein Vorgabewert TENANT würde umgekehrt bestehende Zugänge stillschweigend
    # verengen und dabei nichts absichern.
    ui_profile: WebUiProfile = DEFAULT_PROFILE
