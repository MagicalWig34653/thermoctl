"""Welche Oberfläche ein Principal bekommt -- nicht, was er darf.

Zwei Begriffe, die auseinandergehalten werden müssen, weil sie beim Lesen leicht
zusammenfallen:

* **UI-Profil** (hier) beantwortet *welche Weboberfläche wird gerendert*: die
  Anlagensicht mit Betrieb, Geräten, Protokollen und Rechteverwaltung, oder die
  Wohnungssicht mit Räumen, Zeitplan und Heizzeit.
* **Grants** (``domain.authz``) beantworten *was tatsächlich erlaubt ist*.

Das Profil ist ausdrücklich **keine** Berechtigung und ersetzt keine Prüfung. Ein
Admin-Profil ohne ``user.manage`` kommt weiterhin nicht an die Benutzerverwaltung,
und ein Mieter-Profil mit ``zone.read`` auf einer Zone sieht weiterhin genau diese
eine Zone. Der Profil-Wächter (``web.guards``) liegt *vor* den Rechteprüfungen der
Endpunkte, nicht an ihrer Stelle.

Das Profil hängt an der Gruppe, nicht am Benutzer: thermoctl ordnet Benutzer schon
heute Gruppen zu, und die Gruppe ist auch der Ort, an dem die Rechte hängen. Ein
zweiter Zuordnungsweg nur für die Oberfläche wäre eine zweite Wahrheit darüber, wer
wozu gehört.
"""

from enum import Enum


class WebUiProfile(Enum):
    """Die zwei Oberflächen. Als Enum und nicht als Zeichenkette im Vergleich, damit
    ein Tippfehler beim Vergleich auffällt statt still ``False`` zu ergeben."""

    ADMIN = "admin"
    TENANT = "tenant"


#: Der Wert, den eine Gruppe ohne ausdrückliche Wahl trägt. Bewusst ``admin``: eine
#: bestehende Installation, die aktualisiert wird, hat ihre Gruppen nie als Mieter
#: gekennzeichnet -- sie stillschweigend in die eingeschränkte Oberfläche zu
#: verschieben würde beim Upgrade Zugänge nehmen, die vorher da waren. Wer eine
#: Mietergruppe will, setzt sie ausdrücklich (Gruppenverwaltung).
DEFAULT_PROFILE = WebUiProfile.ADMIN


def parse_profile(value: str | None) -> WebUiProfile:
    """Der gespeicherte Spaltenwert als Enum; alles Unbekannte fällt auf Admin zurück.

    Ein unbekannter Wert kann nur aus einer von Hand veränderten Datenbank oder aus
    einer künftigen, hier noch nicht bekannten Fassung stammen. Beides ist kein Grund,
    die Anmeldung mit einem Fehler abzuweisen -- der Rückfall ist derselbe wie beim
    Upgrade und nimmt niemandem etwas weg, weil die Rechte davon unberührt bleiben.
    """
    try:
        return WebUiProfile(value)
    except ValueError:
        return DEFAULT_PROFILE


def combined_profile(profiles: list[WebUiProfile]) -> WebUiProfile:
    """Das Profil eines Benutzers aus den Profilen seiner Gruppen.

    Ein Benutzer kann in mehreren Gruppen sein. Mieter ist er nur, wenn er in
    mindestens einer Gruppe ist und **alle** davon Mietergruppen sind. Eine einzige
    Admingruppe genügt für die Anlagensicht -- andersherum wäre die Zuordnung zu einer
    Mietergruppe ein Weg, jemandem die Admin-Oberfläche wieder zu nehmen, obwohl seine
    Rechte unverändert weiterreichen. Ohne jede Gruppe (und damit ohne jedes Recht)
    bleibt es beim Vorgabewert; zu sehen gibt es dort ohnehin nichts.
    """
    if not profiles:
        return DEFAULT_PROFILE
    if all(profile is WebUiProfile.TENANT for profile in profiles):
        return WebUiProfile.TENANT
    return WebUiProfile.ADMIN
