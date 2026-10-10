"""Screenshot inventory shared by documentation checks and browser tooling."""

from dataclasses import dataclass, replace

ZONE_SLUGS = ("wohnzimmer", "kueche", "bad", "schlafzimmer", "kinderzimmer", "buero")


@dataclass(frozen=True)
class View:
    name: str
    path: str
    profile: str = "anlage"
    mobile: bool = False
    open_details: bool = False
    route: str | None = None
    viewport: tuple[int, int] = (1280, 900)
    kiosk_mode: str | None = None
    kiosk_detail_zone: str | None = None
    # Whether this view's primary capture, respectively its mobile (390px)
    # capture, is one of the images actually embedded in the documentation. Only
    # `documented` views are written by `--doku`; `documented_mobile` only applies
    # when `mobile` is also set, since otherwise there is no mobile capture at all.
    documented: bool = False
    documented_mobile: bool = False
    # Optional second phone viewport, scrolled to this element below the header.
    mobile_followup: str | None = None
    # Optional second desktop viewport for relevant content below the fold.
    desktop_followup: str | None = None
    # Optional element selector: the primary capture is then cropped to this element
    # (full page, so a section taller than the viewport is still complete) instead of
    # showing the top of the page.
    section: str | None = None

    @property
    def stem(self) -> str:
        return f"{self.profile}-{self.name}"


# The single inventory, including parameterized per-zone views and preparations.
_VIEWS_BEFORE_DOCUMENTATION_FLAGS = (
    View("einrichtung", "/setup", "oeffentlich"),
    View("anmeldung", "/login", "oeffentlich"),
    View("startseite", "/", mobile=True),
    *(
        View(name, path)
        for name, path in (
            ("zonen", "/zones"),
            ("zone-neu", "/zones/new"),
            ("geraete", "/devices"),
            ("anlage", "/plant"),
            ("bediengeraete", "/controllers"),
            ("schaltprotokoll", "/device-commands"),
            ("passkeys", "/passkeys"),
            ("kiosk-token", "/kiosk-tokens"),
            ("urlaub", "/vacation"),
            ("konto", "/account"),
            ("hilfe", "/account/help"),
            ("audit", "/audit"),
            ("benutzer", "/users"),
            ("gruppen", "/groups"),
            ("api-token", "/tokens"),
            ("sollwertmodi", "/modes"),
            ("sollwertmodus-neu", "/modes/new"),
            ("sollwertmodus", "/modes/{mode_id}"),
            ("sollwertmodus-loeschen", "/modes/{mode_id}/delete"),
            ("betrieb", "/control"),
            ("einstellungen", "/settings"),
            ("schnittstellen", "/interfaces"),
            ("statistik", "/statistics"),
            ("relaisverschleiss", "/relay-wear"),
            ("glossar", "/glossar"),
        )
    ),
    View(
        "einstellungen-notbetrieb",
        "/settings",
        section="form[action$='/settings/sensor-failure']",
        route="/settings",
    ),
    View(
        "bad-parameter-notbetrieb",
        "/zones/{zone_bad}/parameters",
        section=".border-top:has(#sensor_failure_enabled)",
        route="/zones/{zone_id}/parameters",
    ),
    *(
        View(
            f"{slug}-{name}",
            path.replace("{zone_id}", "{zone_" + slug + "}").replace(
                "{point_id}", "{point_" + slug + "}"
            ),
            route=path,
        )
        for slug in ZONE_SLUGS
        for name, path in (
            ("details", "/zones/{zone_id}"),
            ("loeschen", "/zones/{zone_id}/delete"),
            ("parameter", "/zones/{zone_id}/parameters"),
            ("geraete", "/zones/{zone_id}/devices"),
            ("wochenplan", "/zones/{zone_id}/schedule"),
            ("schaltpunkt-loeschen", "/zones/{zone_id}/schedule/points/{point_id}/delete"),
            ("wochenplan-uebernehmen", "/zones/{zone_id}/schedule/adopt"),
            ("sollwerte", "/zones/{zone_id}/setpoints"),
        )
    ),
    *(
        View(name, path, "wohnung", mobile=True, open_details=details)
        for name, path, details in (
            ("startseite", "/", False),
            ("abwesenheit", "/", True),
            ("wochenplan", "/schedule", True),
            ("heizzeit", "/heating-time", False),
            ("konto", "/account", False),
            ("hilfe", "/account/help", False),
            ("glossar", "/glossar", False),
            ("passkeys", "/passkeys", False),
        )
    ),
    *(
        View(
            f"{slug}-wochenplan",
            "/schedule?zone={zone_" + slug + "}",
            "wohnung",
            mobile=True,
            open_details=True,
            route="/schedule",
        )
        for slug in ZONE_SLUGS[:4]
    ),
    View("dashboard", "/kiosk/{plaintext}", "kiosk", mobile=True),
    View(
        "panel-uebersicht", "/kiosk/{plaintext}", "kiosk",
        viewport=(480, 480), kiosk_mode="panel",
    ),
    View(
        "panel-detail", "/kiosk/{plaintext}", "kiosk",
        viewport=(480, 480), kiosk_mode="panel", kiosk_detail_zone="zone_wohnzimmer",
    ),
    View(
        "tafel", "/kiosk/{plaintext}", "kiosk",
        viewport=(480, 480), kiosk_mode="tafel",
    ),
)

# Which generated PNGs are actually embedded in the documentation, keyed by
# `<profile>-<name>` (i.e. `View.stem`, not the on-disk file name -- the `-mobil`
# suffix is expressed through `documented_mobile` instead, since one view can have a
# documented desktop capture, a documented mobile capture, both, or neither).
_DOCUMENTED: dict[str, tuple[bool, bool]] = {
    "anlage-startseite": (True, False),
    "anlage-zonen": (True, False),
    "anlage-bad-wochenplan": (True, False),
    "anlage-bad-sollwerte": (True, False),
    "anlage-geraete": (True, False),
    "anlage-betrieb": (True, False),
    "anlage-schaltprotokoll": (True, False),
    "anlage-benutzer": (True, False),
    "anlage-gruppen": (True, False),
    "anlage-kiosk-token": (True, False),
    "anlage-einstellungen": (True, False),
    "anlage-einstellungen-notbetrieb": (True, False),
    "anlage-bad-parameter-notbetrieb": (True, False),
    "anlage-relaisverschleiss": (True, False),
    "anlage-urlaub": (True, False),
    "wohnung-startseite": (False, True),
    "wohnung-abwesenheit": (True, True),
    "wohnung-wohnzimmer-wochenplan": (True, True),
    "wohnung-heizzeit": (True, True),
    "wohnung-konto": (True, True),
    "kiosk-dashboard": (True, False),
    "kiosk-panel-uebersicht": (True, False),
    "kiosk-panel-detail": (True, False),
    "oeffentlich-anmeldung": (True, False),
    "oeffentlich-einrichtung": (True, False),
}


def _with_documentation_flags(view: View) -> View:
    flags = _DOCUMENTED.get(view.stem)
    if flags is None:
        return view
    documented, documented_mobile = flags
    followups = {
        "wohnung-startseite": ".tc-room:has(.tc-room-name:text-is('Schlafzimmer'))",
        "wohnung-wohnzimmer-wochenplan": ".tc-panel:has(.tc-dayrow)",
        "wohnung-konto": ".tc-panel:has(a[href$='/account/help'])",
    }
    desktop_followups = {
        "anlage-bad-wochenplan": "h2:text-is('Schaltpunkt anlegen')",
        "anlage-benutzer": "h2:text-is('Benutzer anlegen')",
        "anlage-betrieb": ".tc-panel:has(.tc-zone-operation)",
        "anlage-einstellungen": "form[action$='/settings/notifications']",
        "anlage-geraete": "h2:text-is('Der Rest meldet sich')",
        "anlage-startseite": ".tc-zone:has(.tc-zone-name:text-is('Kinderzimmer'))",
        "anlage-gruppen": "article:has(h2:text-is('Gruppe anlegen'))",
        "wohnung-abwesenheit": ".tc-room:has(.tc-room-name:text-is('Wohnzimmer'))",
        "wohnung-wohnzimmer-wochenplan": ".tc-dayrow:last-child",
        "wohnung-heizzeit": ".tc-panel:has(h2:text-is('Schlafzimmer'))",
    }
    return replace(
        view,
        documented=documented,
        documented_mobile=documented_mobile,
        mobile_followup=followups.get(view.stem),
        desktop_followup=desktop_followups.get(view.stem),
    )


VIEWS = tuple(_with_documentation_flags(view) for view in _VIEWS_BEFORE_DOCUMENTATION_FLAGS)
assert {stem for stem in _DOCUMENTED if stem not in {v.stem for v in VIEWS}} == set(), (
    "documented-Kennzeichen für eine Ansicht vergeben, die es nicht (mehr) gibt"
)

EXCLUDED_ROUTES = {
    "/kiosk": "Ziel der Token-Weiterleitung; wird über /kiosk/{plaintext} aufgenommen.",
}
