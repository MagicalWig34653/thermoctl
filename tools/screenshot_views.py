"""Screenshot inventory shared by documentation checks and browser tooling.

Die Ansichten der Svelte-Oberfläche (`/app/...`). Alle Pfade sind Browserpfade ohne
Präfix; Platzhalter in geschweiften Klammern füllt `tools/screenshot_seed.py`.
"""

from dataclasses import dataclass

ZONE_SLUGS = ("wohnzimmer", "kueche", "bad", "schlafzimmer", "kinderzimmer", "buero")


@dataclass(frozen=True)
class View:
    name: str
    path: str
    profile: str = "anlage"
    mobile: bool = False
    viewport: tuple[int, int] = (1280, 900)
    # Kioskansicht: "auto", "panel" oder "tafel" (`?ansicht=` der Adresse).
    kiosk_mode: str | None = None
    # Raum, dessen Kachel in der Panel-Ansicht angetippt wird, bevor aufgenommen wird.
    kiosk_detail_zone: str | None = None
    # Whether this view's primary capture, respectively its mobile (390px)
    # capture, is one of the images actually embedded in the documentation. Only
    # `documented` views are written by `--doku`; `documented_mobile` only applies
    # when `mobile` is also set, since otherwise there is no mobile capture at all.
    documented: bool = False
    documented_mobile: bool = False
    # Beschriftung einer Schaltfläche oder eines Reiters, der vor der Aufnahme angeklickt wird.
    click: str | None = None
    # Zweite Aufnahme derselben Seite, eine Bildschirmhöhe weiter unten.
    mobile_followup: bool = False
    desktop_followup: bool = False

    @property
    def stem(self) -> str:
        return f"{self.profile}-{self.name}"


VIEWS: tuple[View, ...] = (
    View("einrichtung", "/app/setup", "oeffentlich", documented=True),
    View("anmeldung", "/app/login", "oeffentlich", documented=True),
    # Verwaltungssicht
    View("startseite", "/app/", documented=True, desktop_followup=True),
    View("zonen", "/app/uebersicht/raeume", documented=True),
    View("bad-raum", "/app/raeume/{zone_bad}", documented=True),
    View("bad-wochenplan", "/app/zeit/wochenplaene?zone={zone_bad}", documented=True),
    View("verlauf", "/app/auswertung/verlauf", documented=True),
    View("schaltprotokoll", "/app/auswertung/ereignisse", click="Schaltprotokoll", documented=True),
    View("geraete", "/app/geraete/liste", documented=True),
    View("geraetekarte", "/app/geraete/karte", documented=True),
    View("betrieb", "/app/system/betrieb", documented=True, desktop_followup=True),
    View("einstellungen-notbetrieb", "/app/heizung/notbetrieb", documented=True),
    View("kiosk-token", "/app/personen/kiosk", documented=True),
    View("benutzer", "/app/personen/rechte", documented=True),
    View("einstellungen", "/app/heizung/regelvorgaben", documented=True),
    View("meldungen", "/app/system/meldungen", documented=True),
    View("relaisverschleiss", "/app/auswertung/verschleiss", documented=True),
    View("urlaub", "/app/zeit/anlagenurlaub", documented=True),
    # Wohnungssicht (Desktop und Telefon); Anlagenurlaub ist für Bewohner nicht sichtbar
    View(
        "startseite",
        "/app/",
        "wohnung",
        mobile=True,
        documented_mobile=True,
        mobile_followup=True,
    ),
    View(
        "wohnzimmer-wochenplan",
        "/app/zeit/wochenplaene?zone={zone_wohnzimmer}",
        "wohnung",
        mobile=True,
        documented=True,
        documented_mobile=True,
        desktop_followup=True,
        mobile_followup=True,
    ),
    View(
        "verlauf",
        "/app/auswertung/verlauf",
        "wohnung",
        mobile=True,
        documented=True,
        documented_mobile=True,
    ),
    View(
        "konto",
        "/app/konto",
        "wohnung",
        mobile=True,
        documented=True,
        documented_mobile=True,
        mobile_followup=True,
    ),
    # Kiosk
    View("dashboard", "/kiosk/{plaintext}", "kiosk", documented=True),
    View(
        "panel-uebersicht",
        "/kiosk/{plaintext}",
        "kiosk",
        viewport=(480, 480),
        kiosk_mode="panel",
        documented=True,
    ),
    View(
        "panel-detail",
        "/kiosk/{plaintext}",
        "kiosk",
        viewport=(480, 480),
        kiosk_mode="panel",
        kiosk_detail_zone="zone_wohnzimmer",
        documented=True,
    ),
)
