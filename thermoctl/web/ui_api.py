"""Same-origin-JSON-Vertrag für die Svelte-Oberfläche (`/ui-api/`).

Eigener Router und nicht `/api/v1`: Jener ist ausschließlich Bearer-Token-
authentifiziert und bleibt es. Hier gilt die Browser-Sitzung (Cookie) samt
CSRF-Prüfung bei jeder Änderung; jede Antwort wird aus dem Principal des
Aufrufs berechnet. Navigation ist Darstellung -- jeder spätere Endpunkt dieses
Routers prüft Rechte erneut.

Fremde Räume erscheinen nirgends: Der Bootstrap nennt nur Zonen, die der
Principal mit `zone.read` sehen darf (`visible_zones`).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from thermoctl.auth.dependencies import csrf_protection, current_principal, get_session
from thermoctl.domain.authz import visible_zones
from thermoctl.domain.principal import Principal
from thermoctl.web.navigation import visible_navigation

router = APIRouter(
    prefix="/ui-api", dependencies=[Depends(csrf_protection)], include_in_schema=False
)


class NavItemOut(BaseModel):
    path: str
    label: str
    section: str


class ZoneOut(BaseModel):
    id: int
    name: str


class BootstrapOut(BaseModel):
    user_id: int
    ui_profile: str
    navigation: list[NavItemOut]
    zones: list[ZoneOut]


@router.post("/csrf-probe")
def csrf_probe() -> dict[str, bool]:
    """Wirkungsprobe der Router-weiten CSRF-Prüfung; ändert nichts."""
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap(
    principal: Annotated[Principal, Depends(current_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> BootstrapOut:
    """Wer ist angemeldet, was sieht diese Person. Ohne Sitzung 401 (JSON, keine
    Weiterleitung), damit die Oberfläche selbst zur Anmeldung führen kann."""
    return BootstrapOut(
        user_id=principal.user_id,
        ui_profile=principal.ui_profile.value,
        navigation=[
            NavItemOut(path=i.path, label=i.label, section=i.section)
            for i in visible_navigation(principal)
        ],
        zones=[
            ZoneOut(id=z.id, name=z.display_name)
            for z in visible_zones(session, principal, "zone.read")
        ],
    )
