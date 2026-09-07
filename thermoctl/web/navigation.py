from dataclasses import dataclass
from typing import Literal

from thermoctl.domain.authz import has_permission
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile


@dataclass(frozen=True)
class NavigationItem:
    path: str
    label: str
    permission: str
    endpoint: str
    scope: Literal["plant", "any_zone"] = "plant"
    # Der Abschnitt der Admin-Seitenleiste, in dem der Eintrag steht. "main" ist der
    # tägliche Betrieb, "analysis" die Auswertungen, "system" die Konfiguration,
    # "access" die Zugänge -- die vier Blöcke der Admin-Demo. Für Mietereinträge
    # bedeutungslos, deren Navigation hat nur eine Ebene.
    section: Literal["main", "analysis", "system", "access", "tenant"] = "system"
    # Für welche Oberfläche der Eintrag gilt. Ausdrücklich nur Navigation: dass eine
    # Mieteroberfläche `/settings` nicht anzeigt, ist Darstellung -- dass sie sie
    # nicht öffnen kann, entscheidet der Wächter am Router (`web/guards.py`), und
    # ob sie dort etwas darf, entscheidet weiterhin die Rechteprüfung im Endpunkt.
    profile: WebUiProfile = WebUiProfile.ADMIN
    # Auf kleinen Bildschirmen zeigt die Admin-Oberfläche nur eine reduzierte
    # Leiste (Übersicht, Zonen, Geräte, Mehr) -- alles Übrige liegt unter "Mehr".
    in_bottom_navigation: bool = False


# This is the single navigation-to-permission contract.  The template renders this
# table instead of repeating permission codes.  Endpoint checks deliberately remain
# security boundaries in the views; the guardian test compares those checks with this
# table, so either side changing alone makes the suite fail instead of silently drifting.
NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    # -- Hauptbereich: was im täglichen Betrieb gebraucht wird -------------------
    NavigationItem(
        "/zones",
        "Zonen",
        "zone.read",
        "thermoctl.web.zone_views.zone_list_view",
        "any_zone",
        "main",
        in_bottom_navigation=True,
    ),
    NavigationItem(
        "/devices",
        "Geräte",
        "device.read",
        "thermoctl.web.device_views.device_overview",
        section="main",
        in_bottom_navigation=True,
    ),
    NavigationItem(
        "/control",
        "Betrieb",
        "zone.read",
        "thermoctl.web.control_views.show_control",
        section="main",
    ),
    # -- Analyse -----------------------------------------------------------------
    NavigationItem(
        "/statistics",
        "Heizstatistik",
        "zone.read",
        "thermoctl.web.control_views.show_statistics",
        section="analysis",
    ),
    NavigationItem(
        "/relay-wear",
        "Relaisverschleiß",
        "audit.read",
        "thermoctl.web.control_views.show_relay_wear",
        section="analysis",
    ),
    NavigationItem(
        "/audit",
        "Protokoll",
        "audit.read",
        "thermoctl.web.audit_views.audit_list",
        section="analysis",
    ),
    NavigationItem(
        "/device-commands",
        "Schaltprotokoll",
        "audit.read",
        "thermoctl.web.device_commands_views.device_command_list",
        section="analysis",
    ),
    # -- System ------------------------------------------------------------------
    NavigationItem(
        "/settings",
        "Regelvorgaben",
        "zone.read",
        "thermoctl.web.control_views.show_settings",
        section="system",
    ),
    NavigationItem(
        "/modes",
        "Sollwert-Modi",
        "mode.manage",
        "thermoctl.web.mode_views.mode_list",
        section="system",
    ),
    NavigationItem(
        "/vacation",
        "Urlaub",
        "zone.read",
        "thermoctl.web.vacation_views.show_vacation",
        section="system",
    ),
    NavigationItem(
        "/interfaces",
        "Schnittstellen",
        "setting.manage",
        "thermoctl.web.control_views.show_interfaces",
        section="system",
    ),
    NavigationItem(
        "/controllers",
        "Bediengeräte",
        "device.read",
        "thermoctl.web.controller_views.controllers",
        "any_zone",
        "system",
    ),
    # -- Zugänge -----------------------------------------------------------------
    NavigationItem(
        "/users",
        "Benutzer",
        "user.manage",
        "thermoctl.web.admin_views.user_list",
        section="access",
    ),
    NavigationItem(
        "/groups",
        "Gruppen",
        "group.manage",
        "thermoctl.web.admin_views.group_list",
        section="access",
    ),
    NavigationItem(
        "/tokens",
        "API-Tokens",
        "token.self",
        "thermoctl.web.admin_views.token_list",
        section="access",
    ),
    NavigationItem(
        "/kiosk-tokens",
        "Kiosk-Tokens",
        "token.manage",
        "thermoctl.web.kiosk_admin_views.kiosk_token_list",
        section="access",
    ),
)


def visible_navigation(principal: Principal) -> tuple[NavigationItem, ...]:
    """Navigation destinations the principal can actually use.

    A listing which filters itself by zone is useful as soon as one matching zone is
    available.  A plant-wide endpoint stays hidden for a merely zone-scoped grant,
    matching ``has_permission(principal, code)`` at that endpoint.

    Zusätzlich gefiltert nach dem UI-Profil: ein Mieter sieht keine Anlageneinträge,
    ein Administrator keine Mietereinträge. Das bleibt reine Darstellung -- ob eine
    Adresse geöffnet werden kann, entscheidet der Wächter am Router, ob dort etwas
    erlaubt ist, weiterhin der Endpunkt.
    """
    return tuple(
        item
        for item in NAVIGATION_ITEMS
        if item.profile is principal.ui_profile
        and (
            has_permission(principal, item.permission)
            or (
                item.scope == "any_zone"
                and any(code == item.permission for code, _zone_id in principal.grants)
            )
        )
    )
