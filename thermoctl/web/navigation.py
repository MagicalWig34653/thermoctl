from dataclasses import dataclass
from typing import Literal

from thermoctl.domain.authz import has_permission
from thermoctl.domain.principal import Principal
from thermoctl.domain.ui_profile import WebUiProfile


@dataclass(frozen=True)
class NavigationItem:
    path: str
    label: str
    # `None` ausschließlich für einen Bereich, der kein Recht braucht, weil er zum
    # eigenen Konto gehört (`/account` -- jeder Angemeldete kommt an sein eigenes
    # Konto, unabhängig von jedem Grant). Jeder andere Eintrag trägt weiterhin ein
    # echtes Recht.
    permission: str | None
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


# This is the single navigation-to-permission contract.  `/ui-api/bootstrap` hands the
# visible part of this table to the Svelte app instead of repeating permission codes.
# Endpoint checks deliberately remain security boundaries in the `/ui-api` handlers named
# by `endpoint`; the guardian test compares those checks with this table, so either side
# changing alone makes the suite fail instead of silently drifting.
NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    # -- Hauptbereich: was im täglichen Betrieb gebraucht wird -------------------
    NavigationItem(
        "/zones",
        "Zonen",
        "zone.read",
        "thermoctl.web.ui_api_rooms.list_rooms",
        "any_zone",
        "main",
        in_bottom_navigation=True,
    ),
    NavigationItem(
        "/devices",
        "Geräte",
        "device.read",
        "thermoctl.web.ui_api_devices.show_devices",
        section="main",
        in_bottom_navigation=True,
    ),
    NavigationItem(
        "/control",
        "Betrieb",
        "zone.read",
        "thermoctl.web.ui_api_operations.show_operation",
        section="main",
    ),
    # -- Analyse -----------------------------------------------------------------
    NavigationItem(
        "/statistics",
        "Heizstatistik",
        "zone.read",
        "thermoctl.web.ui_api_reports.show_heating_time",
        section="analysis",
    ),
    NavigationItem(
        "/zones/history",
        "Zonenverläufe",
        "zone.manage",
        "thermoctl.web.ui_api_reports.show_history",
        "any_zone",
        "analysis",
    ),
    NavigationItem(
        "/zones/data-flow",
        "Datenfluss",
        "zone.manage",
        "thermoctl.web.ui_api_devices.show_data_flow",
        "any_zone",
        "analysis",
    ),
    NavigationItem(
        "/relay-wear",
        "Relaisverschleiß",
        "audit.read",
        "thermoctl.web.ui_api_reports.show_relay_wear",
        section="analysis",
    ),
    NavigationItem(
        "/audit",
        "Protokoll",
        "audit.read",
        "thermoctl.web.ui_api_reports.show_audit",
        section="analysis",
    ),
    NavigationItem(
        "/device-commands",
        "Schaltprotokoll",
        "audit.read",
        "thermoctl.web.ui_api_devices.show_device_commands",
        section="analysis",
    ),
    NavigationItem(
        "/changes",
        "Änderungsprotokoll",
        "audit.read",
        "thermoctl.web.ui_api_reports.show_changes",
        section="analysis",
    ),
    # -- System ------------------------------------------------------------------
    NavigationItem(
        "/settings",
        "Regelvorgaben",
        "zone.read",
        "thermoctl.web.ui_api_operations.show_settings",
        section="system",
    ),
    NavigationItem(
        "/modes",
        "Sollwert-Modi",
        "mode.manage",
        "thermoctl.web.ui_api_schedule.list_modes",
        section="system",
    ),
    NavigationItem(
        "/vacation",
        "Urlaub",
        "zone.read",
        "thermoctl.web.ui_api_schedule.show_vacation",
        section="system",
    ),
    NavigationItem(
        "/interfaces",
        "Schnittstellen",
        "setting.manage",
        "thermoctl.web.ui_api_operations.show_interfaces",
        section="system",
    ),
    NavigationItem(
        "/controllers",
        "Bediengeräte",
        "device.read",
        "thermoctl.web.ui_api_devices.show_controllers",
        "any_zone",
        "system",
    ),
    NavigationItem(
        "/devices/map",
        "Zuordnungskarte",
        "device.read",
        "thermoctl.web.ui_api_devices.show_map",
        "any_zone",
        "system",
    ),
    # -- Zugänge -----------------------------------------------------------------
    NavigationItem(
        "/users",
        "Benutzer",
        "user.manage",
        "thermoctl.web.ui_api_access.list_users",
        section="access",
    ),
    NavigationItem(
        "/groups",
        "Gruppen",
        "group.manage",
        "thermoctl.web.ui_api_access.list_groups",
        section="access",
    ),
    NavigationItem(
        "/tokens",
        "API-Tokens",
        "token.self",
        "thermoctl.web.ui_api_access.list_tokens",
        section="access",
    ),
    NavigationItem(
        "/kiosk-tokens",
        "Kiosk-Tokens",
        "token.manage",
        "thermoctl.web.ui_api_access.list_kiosk_tokens",
        section="access",
    ),
    # -- Mieteroberfläche ----------------------------------------------------------
    NavigationItem(
        "/schedule",
        "Zeitplan",
        "zone.read",
        "thermoctl.web.ui_api_schedule.show_schedule",
        "any_zone",
        "tenant",
        profile=WebUiProfile.TENANT,
    ),
    NavigationItem(
        "/heating-time",
        "Heizzeit",
        "zone.read",
        "thermoctl.web.ui_api_reports.show_heating_time",
        "any_zone",
        "tenant",
        profile=WebUiProfile.TENANT,
    ),
    NavigationItem(
        "/temperature-history",
        "Verlauf",
        "zone.read",
        "thermoctl.web.ui_api_reports.show_history",
        "any_zone",
        "tenant",
        profile=WebUiProfile.TENANT,
    ),
    NavigationItem(
        "/account",
        "Mehr",
        # Kein Recht: `/account` ist der eigene, persönliche Bereich -- jeder
        # Angemeldete kommt daran, unabhängig von jedem Grant (siehe
        # `/ui-api/account`). Der Wächtertest unten
        # überspringt genau diesen Eintrag deshalb bewusst.
        None,
        "thermoctl.web.ui_api_access.show_account",
        section="tenant",
        profile=WebUiProfile.TENANT,
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
            item.permission is None
            or has_permission(principal, item.permission)
            or (
                item.scope == "any_zone"
                and any(code == item.permission for code, _zone_id in principal.grants)
            )
        )
    )
