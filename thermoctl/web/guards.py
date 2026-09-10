"""Der Profil-Wächter für die HTML-Routen.

Er beantwortet genau eine Frage: *gehört diese Seite überhaupt in die Oberfläche,
die dieser Principal bekommt?* Er beantwortet **nicht**, ob die Aktion erlaubt ist --
das tun weiterhin die Rechteprüfungen in den Endpunkten selbst, unverändert und
zusätzlich. Ein Wächter, der eine Prüfung ersetzt, hätte genau den Fehler, gegen den
Grundsatz "Rechte werden im Endpunkt geprüft" seit der Sicherheitsdurchsicht steht.

Warum das nicht über die Navigation reicht: eine ausgeblendete Verknüpfung ist kein
Riegel. Wer ``/settings`` von Hand eintippt, landete sonst in der Anlagensicht --
und selbst wenn dort jede Änderung an einer fehlenden Berechtigung scheitert, stünde
auf der Seite bereits, was es an der Anlage gibt, samt fremder Zonennamen.

Als Router-Dependency gehängt, nicht je Route: eine später hinzugefügte Route ist
damit automatisch geschützt, statt darauf zu bauen, dass jemand daran denkt.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from thermoctl.auth.dependencies import current_principal
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile

_WRONG_PROFILE = "Diese Seite gehört nicht zu Ihrer Oberfläche."


def require_web_ui_profile(
    profile: WebUiProfile,
) -> Callable[[Request, Principal], None]:
    """Baut eine Dependency, die nur das angegebene UI-Profil durchlässt.

    Antwortet mit 403 -- derselbe Status, mit dem das Projekt eine fehlende
    Berechtigung beantwortet (`app.py::forbidden_handler`). Bewusst kein 404: die
    Adresse existiert, sie gehört nur zu einer anderen Oberfläche, und ein 404 würde
    demselben Benutzer nach einem Gruppenwechsel eine Seite als "gibt es nicht"
    melden, die es sehr wohl gibt.
    """

    def guard(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> None:
        if principal.ui_profile is not profile:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _WRONG_PROFILE)
        # Damit die Vorlagen wissen, welche Hülle sie erben sollen, ohne dass jede
        # einzelne View den Wert von Hand in ihren Kontext legt.
        request.state.ui_profile = principal.ui_profile

    return guard


#: Die beiden fertigen Wächter. Als Konstanten und nicht je Router neu gebaut, damit
#: `tests/test_navigation.py` sie an den Routern wiedererkennen kann.
admin_ui_only = require_web_ui_profile(WebUiProfile.ADMIN)
tenant_ui_only = require_web_ui_profile(WebUiProfile.TENANT)
