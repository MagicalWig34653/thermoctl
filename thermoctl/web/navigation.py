from dataclasses import dataclass
from typing import Literal

from thermoctl.domain.authz import has_permission
from thermoctl.domain.principal import Principal


@dataclass(frozen=True)
class NavigationItem:
    path: str
    label: str
    permission: str
    endpoint: str
    scope: Literal["plant", "any_zone"] = "plant"
    section: Literal["main", "settings"] = "settings"


# This is the single navigation-to-permission contract.  The template renders this
# table instead of repeating permission codes.  Endpoint checks deliberately remain
# security boundaries in the views; the guardian test compares those checks with this
# table, so either side changing alone makes the suite fail instead of silently drifting.
NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    NavigationItem(
        "/zones",
        "Zonen",
        "zone.read",
        "thermoctl.web.zone_views.zone_list_view",
        "any_zone",
        "main",
    ),
    NavigationItem(
        "/devices",
        "Geräte",
        "device.read",
        "thermoctl.web.device_views.device_overview",
        section="main",
    ),
    NavigationItem(
        "/control",
        "Betrieb",
        "zone.read",
        "thermoctl.web.control_views.show_control",
        section="main",
    ),
    NavigationItem(
        "/settings", "Regelvorgaben", "zone.read", "thermoctl.web.control_views.show_settings"
    ),
    NavigationItem("/modes", "Sollwert-Modi", "mode.manage", "thermoctl.web.mode_views.mode_list"),
    NavigationItem(
        "/interfaces",
        "Schnittstellen",
        "setting.manage",
        "thermoctl.web.control_views.show_interfaces",
    ),
    NavigationItem(
        "/controllers",
        "Bediengeräte",
        "device.read",
        "thermoctl.web.controller_views.controllers",
        "any_zone",
    ),
    NavigationItem("/users", "Benutzer", "user.manage", "thermoctl.web.admin_views.user_list"),
    NavigationItem("/groups", "Gruppen", "group.manage", "thermoctl.web.admin_views.group_list"),
    NavigationItem("/tokens", "API-Tokens", "token.self", "thermoctl.web.admin_views.token_list"),
    NavigationItem(
        "/kiosk-tokens",
        "Kiosk-Tokens",
        "token.manage",
        "thermoctl.web.kiosk_admin_views.kiosk_token_list",
    ),
    NavigationItem("/audit", "Protokoll", "audit.read", "thermoctl.web.audit_views.audit_list"),
    NavigationItem(
        "/device-commands",
        "Schaltprotokoll",
        "audit.read",
        "thermoctl.web.device_commands_views.device_command_list",
    ),
)


def visible_navigation(principal: Principal) -> tuple[NavigationItem, ...]:
    """Navigation destinations the principal can actually use.

    A listing which filters itself by zone is useful as soon as one matching zone is
    available.  A plant-wide endpoint stays hidden for a merely zone-scoped grant,
    matching ``has_permission(principal, code)`` at that endpoint.
    """
    return tuple(
        item
        for item in NAVIGATION_ITEMS
        if has_permission(principal, item.permission)
        or (
            item.scope == "any_zone"
            and any(code == item.permission for code, _zone_id in principal.grants)
        )
    )
