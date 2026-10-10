"""Erzeugt die öffentliche Seite (`site/`) für GitHub Pages.

Aufruf aus dem Projektstamm::

    .venv/bin/python -m tools.landingpage_erzeugen            # schreibt site/
    .venv/bin/python -m tools.landingpage_erzeugen --pruefen  # Exit 1 bei Abweichung

Was entsteht:

* ``index.html`` -- die Startseite,
* ``glossar.html`` -- das Glossar, **ausschließlich** aus ``thermoctl/data/glossar.json``
  (derselben Quelle wie die Oberfläche und ``docs/glossar.md``); Anker sind die
  Kennungen der Einträge, also ``glossar.html#<id>``,
* ``404.html``,
* ``stil.css`` und ``glossar.js`` (Quellen: ``tools/landingpage/``),
* ``favicon.svg`` und die Bilder unter ``bilder/`` (Kopien aus der Anwendung bzw. aus
  ``docs/bilder/``).

Zwei Regeln, die ein Test (``tests/test_landingpage.py``) erzwingt, weil die Seite
öffentlich ist: Sie lädt **nichts** von Dritten (kein Webfont, kein CDN, kein Skript,
kein Tracker) und setzt keine Cookies, und jeder Verweis nach außen steht in der
Positivliste ``ERLAUBTE_VERWEISE``. Wer einen neuen Verweis braucht, trägt ihn dort
bewusst ein.

Nur Standardbibliothek plus das Glossar-Modul des Projekts. Die Seite wird nicht von
Hand gepflegt: Ein Test schlägt fehl, wenn ``site/`` nicht zu diesem Skript passt.
"""

import argparse
import html
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from thermoctl.domain.glossary import Glossary, GlossaryError, default_glossary

WURZEL = Path(__file__).resolve().parent.parent
ZIEL = WURZEL / "site"
VORLAGEN = Path(__file__).resolve().parent / "landingpage"

#: Die Adresse, unter der GitHub Pages die Seite ausliefert. Sie steht nur in
#: `<link rel="canonical">` und den Open-Graph-Angaben; eine Anfrage dorthin stellt die
#: Seite selbst nie.
SEITEN_URL = "https://magicalwig34653.github.io/thermoctl/"
#: Pfad-Anteil der Adresse. Nur die 404-Seite braucht ihn: GitHub Pages liefert sie
#: unter jedem unbekannten Pfad aus, relative Verweise würden dort ins Leere zeigen.
BASIS_PFAD = "/thermoctl/"

REPO = "https://github.com/MagicalWig34653/thermoctl"
ADDON_REPO = "https://github.com/MagicalWig34653/thermoctl-addon"
ABBILD = "ghcr.io/magicalwig34653/thermoctl"
HOME_ASSISTANT = "https://www.home-assistant.io/"

#: Jede Adresse, auf die die Seite zeigt oder die in ihr vorkommt. Alles andere lässt den
#: Test scheitern. Eigene Seite, das Repository samt Add-on-Repository, die
#: Container-Registry (nur als Text im Abbildnamen) und die Home-Assistant-Startseite.
ERLAUBTE_VERWEISE: tuple[str, ...] = (
    SEITEN_URL,
    "https://github.com/MagicalWig34653/",
    "https://ghcr.io/magicalwig34653/",
    "ghcr.io/magicalwig34653/",
    HOME_ASSISTANT,
)

#: Ab diesem Verhältnis Höhe zu Breite gilt ein Bild als Telefonaufnahme und wird
#: vollständig, nicht angeschnitten gezeigt (Telefonbilder sind 390 x 844, also 2,16).
HOCHFORMAT = 2.0

DOKU_VERWEIS = re.compile(r"docs/[a-z0-9-]+\.md")


# --------------------------------------------------------------------------------------
# Inhalte der Startseite
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bild:
    """Ein Doku-Screenshot (``docs/bilder/<datei>``, erzeugt aus erfundenen Demodaten)."""

    datei: str
    titel: str
    text: str
    alt: str


#: Die Aufnahmen der Seite. Ausschließlich Bilder aus `docs/bilder/`, die aus den
#: erfundenen Demodaten von `tools/screenshot_seed.py` entstehen. Ein Bild kommt erst
#: hierher, nachdem jemand es angesehen und keine Echtdaten darauf gefunden hat.
HERO_BILD = Bild(
    "anlage-startseite.png",
    "Anlagensicht",
    "Alle Zonen im Blick, mit Übersteuerung und Tagesplan je Raum.",
    "Startseite der Anlagensicht: Freigabestatus, geplanter Urlaub und die ersten Zonen "
    "mit Ist-Wert, Sollwert und Zeitplan-Band. Demodaten.",
)

EINBLICKE: tuple[Bild, ...] = (
    Bild(
        "anlage-bad-wochenplan.png",
        "Zeitplan einer Zone",
        "Zeiträume im Raster malen oder Schaltpunkte ziehen. Die Vorschau zeigt, was "
        "gerade gilt, auch mit laufender Übersteuerung.",
        "Zeitplan der Zone Bad mit Vorschau für die nächsten 24 Stunden, Werkzeugen zum "
        "Zeitmalen und Wochenraster. Demodaten.",
    ),
    Bild(
        "anlage-schaltprotokoll.png",
        "Schaltprotokoll",
        "Jeder Befehl mit Zeitpunkt, Zone, Gerät, Ergebnis und Begründung, auch im "
        "Trockenlauf.",
        "Schaltprotokoll mit sechs Einträgen aus System, Weboberfläche und REST-API: "
        "ausgeführt, im Trockenlauf unterdrückt oder gescheitert. Demodaten.",
    ),
    Bild(
        "anlage-einstellungen-notbetrieb.png",
        "Notbetrieb bei Sensorausfall",
        "Festtakt, Rückkehrprüfung, Notsollwert und eine optionale Außenkennlinie sind "
        "anlagenweit einstellbar.",
        "Karte „Notbetrieb bei Sensorausfall“ mit Festtakt, Rückkehrprüfung, "
        "Wiederanlaufspanne, Notsollwert und einer Außenkennlinie aus vier Zeilen. "
        "Demodaten.",
    ),
    Bild(
        "wohnung-startseite-mobil.png",
        "Wohnungssicht auf dem Telefon",
        "Bewohner sehen nur ihre eigenen Räume, in Alltagssprache.",
        "Wohnungssicht am Seitenanfang: Hinweis zum Notbetrieb, laufende Abwesenheit und "
        "die erste Raumkarte. Demodaten.",
    ),
    Bild(
        "wohnung-heizzeit-mobil.png",
        "Heizzeit je Raum",
        "Die Heizdauer der letzten 7, 30 oder 90 Tage als Orientierung, kein Energie- "
        "oder Kostenmesser.",
        "Seite Heizzeit der Wohnungssicht mit Auswahl 7, 30 oder 90 Tage und einem "
        "Balkendiagramm je Raum. Demodaten.",
    ),
    Bild(
        "kiosk-dashboard.png",
        "Kiosk für ein Wandtablet",
        "Großformatig, ohne Anmeldung, hinter einem jederzeit widerrufbaren Token.",
        "Kiosk-Dashboard: sechs Zonen nebeneinander mit Ist-Wert und Reglern für den "
        "Sollwert. Demodaten.",
    ),
)


@dataclass(frozen=True)
class Funktion:
    """Eine Karte im Abschnitt „Funktionen“."""

    titel: str
    text: str
    #: Glossar-Kennungen, auf die die Karte verweist (müssen im Glossar existieren).
    begriffe: tuple[str, ...] = ()
    #: Dokumentation im Repository (`docs/<datei>`), auf die die Karte verweist.
    doku: tuple[tuple[str, str], ...] = ()


FUNKTIONEN: tuple[Funktion, ...] = (
    Funktion(
        "Zeitpläne und Sollwert-Modi",
        "Wochenpläne bestehen aus echten Schaltpunkten je Zone. Komfort, Tag, Nacht und "
        "Frostschutz sind vorgegeben; eigene Modi lassen sich anlegen.",
        ("zeitplan", "sollwert-modus"),
    ),
    Funktion(
        "Regelung mit Hysterese",
        "Entscheidungen mit Hysterese und Mindestschaltdauer. Je Zone gibt es optional "
        "eine PI-Regelung, ausdrücklich als Beta und aus als Vorgabe.",
        ("hysterese", "pi-regelung"),
    ),
    Funktion(
        "Urlaub und Abwesenheit",
        "Die Verwaltung setzt einen anlagenweiten Urlaub. Bewohner setzen für ihre "
        "eigenen Räume eine Abwesenheit bis zu einem Rückkehrdatum.",
        ("urlaub", "abwesenheit"),
    ),
    Funktion(
        "Sonnenabsenkung",
        "Optional: eine Absenkung des Sollwerts, wenn die Sonnenprognose sie erlaubt. "
        "Beginn, Änderung und Ende stehen im Schaltprotokoll.",
        ("sonnenabsenkung",),
    ),
    Funktion(
        "Notbetrieb bei Sensorausfall",
        "Fällt der Messwert einer Zone aus, kann ein Notbetrieb übernehmen, etwa mit "
        "Ersatzquelle, festem Takt oder Notsollwert. Er ist ab Werk eingeschaltet und "
        "je Zone einstellbar.",
        ("notbetrieb", "ersatzquelle"),
    ),
    Funktion(
        "Schaltprotokoll und Regelentscheidungen",
        "Für jede Zone wird festgehalten, was geschaltet würde und warum. Das "
        "Schaltprotokoll zeigt Befehle mit Ergebnis und Begründung.",
        ("schaltprotokoll", "regelentscheidung"),
    ),
    Funktion(
        "Temperaturverlauf je Zone",
        "Im Reiter Zonendaten zeigt ein Diagramm für 24 Stunden, 3 oder 7 Tage Ist-"
        "Temperatur, Zeitplan-Soll, wirksamen Soll, die Sonnenabsenkung als Band und die "
        "Heizanforderung, auch als Vollbild. Es zeichnet nur, was protokollierte Zeilen "
        "belegen; Lücken und unbekannte Zeiträume bleiben sichtbar.",
        ("sonnenabsenkung", "schaltprotokoll"),
        (("Bedienungsanleitung", "bedienung.md"),),
    ),
    Funktion(
        "Statistik und Meldungen",
        "Heizzeit und Relaisverschleiß je Gerät und Tag. Störungen wie ein "
        "ausgefallener Sensor lassen sich einzeln per Webhook melden.",
        ("heizzeit", "relaisverschleiss", "meldungen"),
    ),
    Funktion(
        "Zwei Oberflächen",
        "Die Anlagensicht für Verwaltung und Technik: Zonen, Geräte, Betrieb, "
        "Protokolle, Rechte. Die Wohnungssicht für Bewohner: nur die eigenen Räume, "
        "mit Temperatur, Wochenplan und Heizzeit.",
        ("oberflaechen",),
        (("Bedienungsanleitung", "bedienung.md"),),
    ),
    Funktion(
        "Kiosk für Wandtablets",
        "Ein großformatiges Dashboard ohne Anmeldung, geöffnet über ein eigenes Token, "
        "das sich jederzeit widerrufen lässt.",
        ("kiosk",),
    ),
    Funktion(
        "Anmeldung, Rechte und Passkeys",
        "Authentifizierung ist verpflichtend. Benutzer, Gruppen und Rechte je Raum; "
        "Passkeys als Anmeldung ohne Passwort; API-Tokens mit begrenzten Rechten.",
        ("gruppen-und-rechte", "passkey", "api-token"),
    ),
    Funktion(
        "REST-API und MCP-Server",
        "Dieselben Funktionen wie in der Weboberfläche lassen sich über eine "
        "REST-Schnittstelle und einen MCP-Server ansprechen.",
        (),
        (("REST-Schnittstelle", "api.md"), ("MCP-Server", "mcp.md")),
    ),
    Funktion(
        "Home Assistant, MQTT und Zigbee2MQTT",
        "Sensoren werden über Zigbee2MQTT eingelesen. Die Anbindung an Home Assistant "
        "per MQTT-Discovery ist optional und keine Voraussetzung.",
        ("home-assistant", "zigbee2mqtt"),
        (("MQTT", "mqtt.md"),),
    ),
)

HINWEISE: tuple[str, ...] = (
    "Der Trockenlauf ist die Vorgabe: Die Regelung entscheidet und protokolliert, ohne "
    "Befehle an Geräte zu senden. Erst nach ausdrücklichem Scharfschalten und einem "
    "Neustart wird wirklich gesendet.",
    "thermoctl macht keine Zusagen über Heizverhalten, Komfort oder Verbrauch. Auch die "
    "PI-Regelung verspricht keinen Verbrauchsvorteil.",
    "Die Software steuert eine echte Heizung. Wer sie einsetzt, prüft ihre Entscheidungen "
    "zuerst im Trockenlauf; die Lizenz schließt jede Gewährleistung aus.",
    "Der Zugriff verlangt immer eine Anmeldung. Wer die Oberfläche über das Netz "
    "erreichbar macht, stellt einen Reverse-Proxy mit TLS davor und aktiviert "
    "THERMOCTL_SECURE_COOKIES.",
)

DOCKER_SCHRITT_1 = f"""git clone {REPO}.git
cd thermoctl
cp docker/compose.beispiel.yml compose.yml
cp .env.example .env"""

DOCKER_SCHRITT_2 = """python3 -c "import secrets; print(secrets.token_urlsafe(48))\""""

DOCKER_SCHRITT_3 = """docker compose up -d
docker compose logs thermoctl"""


# --------------------------------------------------------------------------------------
# Bausteine
# --------------------------------------------------------------------------------------


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _pre(beschriftung: str, code: str) -> str:
    """Ein Codeblock; lange Zeilen brechen um, statt die Seite zu verbreitern."""
    return f'<pre aria-label="{_e(beschriftung)}"><code>{_e(code)}</code></pre>'


def _doku_url(datei: str) -> str:
    return f"{REPO}/blob/main/docs/{datei}"


def _glossar_url(kennung: str) -> str:
    return f"glossar.html#{kennung}"


def png_groesse(daten: bytes) -> tuple[int, int]:
    """Breite und Höhe aus dem PNG-Kopf, ohne Bildbibliothek."""
    if daten[:8] != b"\x89PNG\r\n\x1a\n" or daten[12:16] != b"IHDR":
        raise ValueError("Keine gültige PNG-Datei.")
    breite, hoehe = struct.unpack(">II", daten[16:24])
    return int(breite), int(hoehe)


def _kopf(
    *,
    titel: str,
    beschreibung: str,
    pfad: str,
    praefix: str = "",
    robots: str | None = None,
    og_bild: Bild | None = None,
) -> str:
    """Der `<head>`: nur eigene Dateien, keine Verweise auf Dritte außer dem Kanon-Link."""
    kanonisch = f"{SEITEN_URL}{pfad}"
    zeilen = [
        "<!doctype html>",
        '<html lang="de">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(titel)}</title>",
        f'<meta name="description" content="{_e(beschreibung)}">',
        '<meta name="color-scheme" content="light dark">',
        '<meta name="theme-color" content="#f4f6f8" media="(prefers-color-scheme: light)">',
        '<meta name="theme-color" content="#0f1419" media="(prefers-color-scheme: dark)">',
    ]
    if robots:
        zeilen.append(f'<meta name="robots" content="{_e(robots)}">')
    else:
        zeilen.append(f'<link rel="canonical" href="{_e(kanonisch)}">')
    zeilen += [
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="de_DE">',
        '<meta property="og:site_name" content="thermoctl">',
        f'<meta property="og:title" content="{_e(titel)}">',
        f'<meta property="og:description" content="{_e(beschreibung)}">',
        f'<meta property="og:url" content="{_e(kanonisch)}">',
    ]
    if og_bild is not None:
        zeilen += [
            f'<meta property="og:image" content="{_e(SEITEN_URL + "bilder/" + og_bild.datei)}">',
            f'<meta property="og:image:alt" content="{_e(og_bild.alt)}">',
        ]
    zeilen += [
        f'<link rel="icon" href="{praefix}favicon.svg" type="image/svg+xml">',
        f'<link rel="stylesheet" href="{praefix}stil.css">',
        "</head>",
        "<body>",
    ]
    return "\n".join(zeilen)


def _seitenkopf(aktuell: str, praefix: str = "") -> str:
    def eintrag(ziel: str, text: str, kennung: str) -> str:
        marke = ' aria-current="page"' if kennung == aktuell else ""
        return f'<li><a href="{praefix}{ziel}"{marke}>{text}</a></li>'

    punkte = [
        eintrag("index.html#funktionen", "Funktionen", "funktionen"),
        eintrag("index.html#einblicke", "Einblicke", "einblicke"),
        eintrag("index.html#installation", "Installation", "installation"),
        eintrag("glossar.html", "Glossar", "glossar"),
        f'<li><a href="{REPO}">Quelltext</a></li>',
    ]
    return "\n".join(
        [
            '<a class="skip" href="#inhalt">Zum Inhalt springen</a>',
            '<header class="site-header">',
            '<div class="wrap">',
            f'<a class="brand" href="{praefix}index.html">'
            f'<img src="{praefix}favicon.svg" width="32" height="32" alt="">thermoctl</a>',
            '<nav class="site-nav" aria-label="Hauptnavigation">',
            "<ul>",
            *punkte,
            "</ul>",
            "</nav>",
            "</div>",
            "</header>",
        ]
    )


def _fuss(praefix: str = "") -> str:
    return "\n".join(
        [
            '<footer class="site-footer">',
            '<div class="wrap">',
            '<div class="footer-cols">',
            "<div>",
            "<h2>Lizenz</h2>",
            "<p>thermoctl steht unter der "
            f'<a href="{REPO}/blob/main/LICENSE">GNU Affero General Public License, '
            "Version 3</a> (AGPL-3.0-only). Wer den Code verändert und weitergibt, muss den "
            "veränderten Quelltext mitgeben. Wer thermoctl anderen über das Netz zugänglich "
            "macht, muss ihnen ebenfalls den Quelltext anbieten, einschließlich eigener "
            "Änderungen.</p>",
            "</div>",
            "<div>",
            "<h2>Verweise</h2>",
            "<ul>",
            f'<li><a href="{REPO}">Quelltext auf GitHub</a></li>',
            f'<li><a href="{ADDON_REPO}">Home-Assistant-Add-on</a></li>',
            f'<li><a href="{REPO}/tree/main/docs">Dokumentation</a></li>',
            f'<li><a href="{REPO}/blob/main/CHANGELOG.md">Änderungen</a></li>',
            f'<li><a href="{praefix}glossar.html">Glossar</a></li>',
            "</ul>",
            "</div>",
            "<div>",
            "<h2>Diese Seite</h2>",
            "<p>Sie setzt keine Cookies, bindet keine Schriften, Skripte oder Bilder von "
            "Dritten ein und speichert nichts im Browser. Ausgeliefert wird sie von "
            "GitHub Pages.</p>",
            "</div>",
            "</div>",
            "</div>",
            "</footer>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _bild(bild: Bild, groesse: tuple[int, int], *, zuschnitt: bool, praefix: str = "") -> str:
    breite, hoehe = groesse
    if breite > 0 and hoehe / breite > HOCHFORMAT:
        klasse = "shot portrait"
    else:
        klasse = "shot crop" if zuschnitt else "shot"
    pfad = f"{praefix}bilder/{bild.datei}"
    return "\n".join(
        [
            f'<figure class="{klasse}">',
            f'<a href="{pfad}">'
            f'<img src="{pfad}" width="{breite}" height="{hoehe}" '
            f'alt="{_e(bild.alt)}" loading="{"eager" if not zuschnitt else "lazy"}"></a>',
            f"<figcaption><strong>{_e(bild.titel)}</strong>{_e(bild.text)}</figcaption>",
            "</figure>",
        ]
    )


def _karte(funktion: Funktion, glossar: Glossary) -> str:
    verweise: list[str] = []
    for kennung in funktion.begriffe:
        verweise.append(
            f'<a href="{_glossar_url(kennung)}">{_e(glossar.get(kennung).term)}</a>'
        )
    for text, datei in funktion.doku:
        verweise.append(f'<a href="{_doku_url(datei)}">{_e(text)}</a>')
    mehr = ""
    if verweise:
        mehr = f'\n<p class="more">Mehr: {", ".join(verweise)}</p>'
    return (
        f'<li class="card">\n<h3>{_e(funktion.titel)}</h3>\n'
        f"<p>{_e(funktion.text)}</p>{mehr}\n</li>"
    )


# --------------------------------------------------------------------------------------
# Seiten
# --------------------------------------------------------------------------------------


def render_index(glossar: Glossary, groessen: dict[str, tuple[int, int]]) -> str:
    beschreibung = (
        "thermoctl ist eine eigenständige, selbst gehostete Heizungssteuerung: "
        "sensorbasierte Raumregelung mit Zeitplänen, Weboberfläche, REST-API und "
        "MCP-Server. Läuft als Docker-Container oder als Home-Assistant-Add-on."
    )
    kopf = _kopf(
        titel="thermoctl – Heizungssteuerung zum Selbsthosten",
        beschreibung=beschreibung,
        pfad="",
        og_bild=HERO_BILD,
    )
    karten = "\n".join(_karte(funktion, glossar) for funktion in FUNKTIONEN)
    einblicke = "\n".join(
        _bild(bild, groessen[bild.datei], zuschnitt=True) for bild in EINBLICKE
    )
    hinweise = "\n".join(f"<li>{_e(text)}</li>" for text in HINWEISE)
    anker_doku = _doku_url("inbetriebnahme-schattenbetrieb.md")
    umstieg = _doku_url(
        "self-hosting.md#6b-umstieg-von-docker-compose-auf-das-home-assistant-add-on"
    )
    haupt = f"""<main id="inhalt">
<section class="hero" aria-labelledby="titel">
<div class="wrap">
<div class="hero-text">
<p class="eyebrow">Heizungssteuerung zum Selbsthosten</p>
<h1 id="titel">Räume regeln nach Sensorwert und Zeitplan.</h1>
<p class="lead">thermoctl ist eine eigenständige, selbst gehostete Heizungssteuerung.
Konfiguriert wird im Browser; zusätzlich gibt es eine REST-Schnittstelle und einen
MCP-Server. Home Assistant lässt sich per MQTT anbinden, ist aber keine Voraussetzung.</p>
<ul class="badges" aria-label="Eckdaten">
<li>Open Source, AGPL-3.0</li>
<li>Docker oder Home-Assistant-Add-on</li>
<li>SQLite oder MariaDB</li>
</ul>
<div class="actions">
<a class="button" href="#installation">Installieren</a>
<a class="button secondary" href="{REPO}">Quelltext auf GitHub</a>
<a class="button secondary" href="glossar.html">Glossar</a>
</div>
</div>
{_bild(HERO_BILD, groessen[HERO_BILD.datei], zuschnitt=False)}
</div>
</section>

<section id="funktionen" class="alt" aria-labelledby="funktionen-titel">
<div class="wrap">
<div class="section-head">
<p class="eyebrow">Funktionen</p>
<h2 id="funktionen-titel">Was thermoctl kann</h2>
<p>Die Liste folgt der Dokumentation im Repository. Hinter jedem Fachbegriff steht
eine Erklärung im <a href="glossar.html">Glossar</a>.</p>
</div>
<ul class="grid">
{karten}
</ul>
</div>
</section>

<section id="einblicke" aria-labelledby="einblicke-titel">
<div class="wrap">
<div class="section-head">
<p class="eyebrow">Einblicke</p>
<h2 id="einblicke-titel">So sieht es aus</h2>
<p>Alle Bilder entstehen aus erfundenen Demodaten, nicht aus einer echten Anlage. Ein
Klick öffnet das Bild in voller Größe.</p>
</div>
<div class="grid">
{einblicke}
</div>
</div>
</section>

<section id="installation" class="alt" aria-labelledby="installation-titel">
<div class="wrap">
<div class="section-head">
<p class="eyebrow">Installation</p>
<h2 id="installation-titel">Zwei Wege</h2>
<p>thermoctl läuft als eigener Docker-Container oder als
<a href="{HOME_ASSISTANT}">Home-Assistant</a>-Add-on. Voraussetzung ist bei Docker eine
SQLite- oder MariaDB-Datenbank; SQLite braucht keinen eigenen Dienst.</p>
</div>
<div class="two">
<div class="panel">
<h3>Docker mit Compose</h3>
<p class="note">Das fertige Abbild <code>{ABBILD}</code> gibt es für
<code>linux/amd64</code> und <code>linux/arm64</code>.</p>
<ol>
<li>Quelltext holen und das Beispiel kopieren:
{_pre("Befehle: Quelltext holen", DOCKER_SCHRITT_1)}</li>
<li>In <code>.env</code> mindestens <code>THERMOCTL_DATABASE_URL</code> setzen, etwa
<code>sqlite:////data/thermoctl.db</code>, und <code>THERMOCTL_SECRET_KEY</code> mit
einem zufälligen Schlüssel von mindestens 32 Zeichen:
{_pre("Befehl: Schlüssel erzeugen", DOCKER_SCHRITT_2)}</li>
<li>Starten und das Log ansehen:
{_pre("Befehle: starten", DOCKER_SCHRITT_3)}</li>
<li>Im Log steht beim ersten Start ein einmalig verwendbares Einrichtungs-Token.
Damit wird die Einrichtung unter <code>/setup</code> abgeschlossen. Das Token ist wie
ein Passwort zu behandeln.</li>
</ol>
<p class="note">Die Beispieldatei bindet den Port nur an die Loopback-Adresse
(<code>127.0.0.1:8000</code>). Alle Werte stehen in der <code>.env</code>, nie im
Quelltext; Zugangsdaten gehören nicht ins Repository.</p>
</div>
<div class="panel">
<h3>Home-Assistant-Add-on</h3>
<p class="note">Das Add-on liegt in einem eigenen Repository und zeigt auf eine feste
Version des Abbilds.</p>
<ol>
<li>In Home Assistant: <em>Einstellungen</em>, <em>Add-ons</em>, <em>Add-on-Store</em>.</li>
<li>Über das Menü oben rechts <em>Repositories</em> öffnen und diese Adresse hinzufügen:
{_pre("Adresse des Add-on-Repositorys", ADDON_REPO)}</li>
<li>thermoctl installieren, konfigurieren und starten. Die Einbindung läuft über
Ingress.</li>
</ol>
<p class="note">Wer bisher mit <code>docker compose</code> betreibt, findet den
Umstiegsweg in der
<a href="{umstieg}">Anleitung zum Selbst-Hosten</a>.</p>
</div>
</div>
<p class="after">Danach: den
<a href="{anker_doku}">Schattenbetrieb in Gang setzen</a>, später
<a href="{_doku_url("scharfschalten.md")}">scharf schalten</a>. Alles Weitere steht in
der <a href="{REPO}/tree/main/docs">Dokumentation</a>.</p>
</div>
</section>

<section id="hinweise" aria-labelledby="hinweise-titel">
<div class="wrap">
<div class="notice">
<h2 id="hinweise-titel">Gut zu wissen</h2>
<ul>
{hinweise}
</ul>
</div>
</div>
</section>
</main>"""
    return "\n".join([kopf, _seitenkopf("start"), haupt, _fuss()])


def _verlinke_doku(text: str) -> str:
    """Escaped den Text und macht aus `docs/<name>.md` einen Verweis ins Repository."""
    escaped = _e(text)
    return DOKU_VERWEIS.sub(
        lambda treffer: f'<a href="{REPO}/blob/main/{treffer.group(0)}">'
        f"<code>{treffer.group(0)}</code></a>",
        escaped,
    )


def render_glossar(glossar: Glossary) -> str:
    anzahl = len(glossar.entries)
    kopf = _kopf(
        titel="Glossar – thermoctl",
        beschreibung=(
            f"Die Fachbegriffe von thermoctl in einfachen Worten: {anzahl} Begriffe von "
            "Abwesenheit bis Zonen, alphabetisch und durchsuchbar."
        ),
        pfad="glossar.html",
    )
    vorhanden = set(glossar.letters())
    buchstaben = []
    for buchstabe in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if buchstabe in vorhanden:
            buchstaben.append(
                f'<li><a href="#buchstabe-{buchstabe.lower()}" data-buchstabe="{buchstabe}" '
                f'aria-label="Begriffe mit {buchstabe}">{buchstabe}</a></li>'
            )
        else:
            buchstaben.append(f'<li><span aria-hidden="true">{buchstabe}</span></li>')
    gruppen: list[str] = []
    for buchstabe, eintraege in glossar.grouped():
        teile = [
            f'<section class="group" data-gruppe="{buchstabe}" '
            f'aria-labelledby="buchstabe-{buchstabe.lower()}">',
            f'<h2 id="buchstabe-{buchstabe.lower()}">{buchstabe}</h2>',
        ]
        for eintrag in eintraege:
            teile += [
                f'<article class="entry" id="{_e(eintrag.id)}" '
                f'data-suche="{_e(eintrag.search_text)}">',
                f'<h3>{_e(eintrag.term)} '
                f'<a class="perma" href="#{_e(eintrag.id)}" '
                f'aria-label="Link zum Begriff {_e(eintrag.term)}">#</a></h3>',
                f'<p class="area">{_e(eintrag.area)}</p>',
                f'<p class="short">{_e(eintrag.short)}</p>',
                f"<p>{_verlinke_doku(eintrag.explanation)}</p>",
            ]
            if eintrag.synonyms:
                teile.append(f'<p class="small">Auch: {_e(", ".join(eintrag.synonyms))}</p>')
            if eintrag.see_also:
                ziele = ", ".join(
                    f'<a href="#{_e(ziel)}">{_e(glossar.get(ziel).term)}</a>'
                    for ziel in eintrag.see_also
                )
                teile.append(f'<p class="small">Siehe auch: {ziele}</p>')
            teile.append("</article>")
        teile.append("</section>")
        gruppen.append("\n".join(teile))
    haupt = f"""<main id="inhalt">
<div class="wrap">
<div class="page-head">
<p class="eyebrow">Glossar</p>
<h1>Fachbegriffe in einfachen Worten</h1>
<p>{anzahl} Begriffe, alphabetisch. Dieselben Erklärungen stehen in der Oberfläche von
thermoctl unter <code>/glossar</code>; die Quelle ist die Datei
<a href="{REPO}/blob/main/thermoctl/data/glossar.json"><code>thermoctl/data/glossar.json</code></a>.
Die Texte erklären Begriffe und nennen, wo etwas eingestellt wird; sie sichern kein
Verhalten zu.</p>
<div class="search" id="glossar-suchbox" hidden>
<label for="glossar-suche">Begriff suchen</label>
<input type="search" id="glossar-suche" autocomplete="off"
placeholder="zum Beispiel „Hysterese“" aria-controls="glossar-liste">
<p id="glossar-status" role="status"></p>
</div>
<nav aria-label="Sprungmarken von A bis Z">
<ul class="letters">
{chr(10).join(buchstaben)}
</ul>
</nav>
</div>
<p class="empty" id="glossar-leer" role="status" hidden>Kein Begriff passt zur Suche.</p>
<div class="glossary" id="glossar-liste">
{chr(10).join(gruppen)}
</div>
</div>
<script src="glossar.js"></script>
</main>"""
    return "\n".join([kopf, _seitenkopf("glossar"), haupt, _fuss()])


def render_404() -> str:
    kopf = _kopf(
        titel="Seite nicht gefunden – thermoctl",
        beschreibung=(
            "Diese Seite gibt es nicht. Die Startseite von thermoctl führt zu Funktionen, "
            "Installation und Glossar."
        ),
        pfad="404.html",
        praefix=BASIS_PFAD,
        robots="noindex",
    )
    haupt = f"""<main id="inhalt">
<div class="wrap">
<div class="page-head">
<p class="eyebrow">404</p>
<h1>Diese Seite gibt es nicht</h1>
<p>Vielleicht hilft der Weg zurück zur <a href="{BASIS_PFAD}index.html">Startseite</a> oder
ins <a href="{BASIS_PFAD}glossar.html">Glossar</a>.</p>
</div>
</div>
</main>"""
    return "\n".join([kopf, _seitenkopf("404", BASIS_PFAD), haupt, _fuss(BASIS_PFAD)])


# --------------------------------------------------------------------------------------
# Gesamtausgabe
# --------------------------------------------------------------------------------------


def erzeuge(glossar: Glossary | None = None, wurzel: Path = WURZEL) -> dict[str, bytes]:
    """Alle Dateien der Seite, relativ zu `site/`, vollständig und deterministisch."""
    glossar = glossar if glossar is not None else default_glossary()
    bilder_quelle = wurzel / "docs" / "bilder"
    dateien: dict[str, bytes] = {}
    groessen: dict[str, tuple[int, int]] = {}
    for bild in (HERO_BILD, *EINBLICKE):
        daten = (bilder_quelle / bild.datei).read_bytes()
        groessen[bild.datei] = png_groesse(daten)
        dateien[f"bilder/{bild.datei}"] = daten
    dateien["index.html"] = render_index(glossar, groessen).encode("utf-8")
    dateien["glossar.html"] = render_glossar(glossar).encode("utf-8")
    dateien["404.html"] = render_404().encode("utf-8")
    dateien["stil.css"] = (VORLAGEN / "stil.css").read_bytes()
    dateien["glossar.js"] = (VORLAGEN / "glossar.js").read_bytes()
    dateien["favicon.svg"] = (wurzel / "thermoctl/web/static/favicon.svg").read_bytes()
    return dateien


def abweichungen(dateien: dict[str, bytes], ziel: Path = ZIEL) -> list[str]:
    """Was in `ziel` fehlt, abweicht oder überzählig ist; leer, wenn alles passt."""
    probleme: list[str] = []
    for name, daten in sorted(dateien.items()):
        pfad = ziel / name
        if not pfad.is_file():
            probleme.append(f"fehlt: {name}")
        elif pfad.read_bytes() != daten:
            probleme.append(f"weicht ab: {name}")
    if ziel.is_dir():
        vorhanden = {
            str(pfad.relative_to(ziel)) for pfad in ziel.rglob("*") if pfad.is_file()
        }
        for name in sorted(vorhanden - set(dateien)):
            probleme.append(f"überzählig: {name}")
    return probleme


def schreibe(dateien: dict[str, bytes], ziel: Path = ZIEL) -> None:
    """Schreibt die Dateien nach `ziel` und entfernt dort Dateien, die nicht mehr gehören."""
    for name, daten in dateien.items():
        pfad = ziel / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        if not pfad.is_file() or pfad.read_bytes() != daten:
            pfad.write_bytes(daten)
    if ziel.is_dir():
        for pfad in sorted(ziel.rglob("*"), reverse=True):
            if pfad.is_file() and str(pfad.relative_to(ziel)) not in dateien:
                pfad.unlink()
            elif pfad.is_dir() and not any(pfad.iterdir()):
                pfad.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--pruefen",
        action="store_true",
        help="nichts schreiben; mit Exit-Code 1 beenden, wenn site/ veraltet ist",
    )
    argumente = parser.parse_args(argv)
    try:
        dateien = erzeuge()
    except GlossaryError as exc:
        print(f"Das Glossar ist fehlerhaft: {exc}", file=sys.stderr)
        return 2
    if argumente.pruefen:
        probleme = abweichungen(dateien, ZIEL)
        if probleme:
            print(
                "site/ ist nicht aktuell: "
                + "; ".join(probleme)
                + ". Neu erzeugen: python -m tools.landingpage_erzeugen",
                file=sys.stderr,
            )
            return 1
        print("site/ ist aktuell.")
        return 0
    schreibe(dateien, ZIEL)
    print(f"site/ geschrieben ({len(dateien)} Dateien).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
