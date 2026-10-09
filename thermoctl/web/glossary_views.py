"""Das Glossar der Fachbegriffe -- eine reine Leseseite.

Wie `/account/help` braucht die Seite kein Recht und liest nichts aus der Datenbank: Sie
zeigt Erklärtext, keine Anlagendaten, und kann deshalb auch nichts über fremde Räume
verraten. Anmelden muss sich trotzdem jeder (Grundsatz 4) -- das erledigt
`current_principal`, genau wie bei den anderen Leseseiten.

Bewusst **kein** Profil-Wächter (`web/guards.py`): Das Glossar gehört beiden
Oberflächen. Die Wohnungssicht bekommt dieselben Erklärungen, in ihrer eigenen Hülle.

Die Inhalte stehen allein in `thermoctl/data/glossar.json`
(`thermoctl/domain/glossary.py`); diese Datei reicht sie nur an die Vorlage weiter.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from thermoctl.auth.dependencies import csrf_protection, current_principal
from thermoctl.domain.glossary import default_glossary
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile
from thermoctl.web import templates

router = APIRouter(dependencies=[Depends(csrf_protection)], include_in_schema=False)


@router.get("/glossar")
async def show_glossary(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> Response:
    """Alle Begriffe alphabetisch, mit Sprungmarken und Suchfeld."""
    glossary = default_glossary()
    return templates.TemplateResponse(
        request,
        "glossar.html",
        {
            "shell": (
                "base_tenant.html"
                if principal.ui_profile is WebUiProfile.TENANT
                else "base_admin.html"
            ),
            "groups": glossary.grouped(),
            "available_letters": frozenset(glossary.letters()),
            "terms": {entry.id: entry.term for entry in glossary.entries},
            "entry_count": len(glossary.entries),
        },
    )
