"""Der Betriebszustand der Anlage: scharf oder Trockenlauf, und die globalen Vorgaben.

Zwei Dinge stehen hier, die vorher nirgends bedienbar waren:

* **Scharfschalten.** `setting.control_armed` war bisher ein Feld, das niemand setzen
  konnte -- der Riegel des Trockenlaufs, aber ohne Schluessel. Wer die Anlage in Betrieb
  nehmen will, musste ihn in der Datenbank umlegen. Das ist jetzt eine Bedienhandlung
  mit eigenem Recht, eigenem Audit-Eintrag und einer Begruendung, die mitgeschrieben wird.
* **Die globalen Vorgaben**, von denen jede Zone erbt, die nichts eigenes gesetzt hat.

Beides liegt in der Domaene und nicht in der Weboberflaeche, damit REST und MCP dieselbe
Pruefung benutzen und nicht jeweils eine eigene, leicht andere.

**Der zweite Riegel bleibt unberuehrt.** `MqttClient(schalten_erlaubt=...)` wird beim Bau
des Clients gesetzt und nicht von hier. Scharfschalten allein laesst also noch nichts
senden -- es hebt nur den Riegel, den die Datenbank haelt. Das ist Absicht: Ein einzelner
falscher Klick soll die Heizung nicht in Bewegung setzen koennen.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from thermoctl import audit
from thermoctl.db.models.operations import Setting


@dataclass
class Steuerungsfehler(Exception):
    """Eine abzuweisende Eingabe, kein Fehler des Dienstes.

    Nicht eingefroren: Python haengt einer Ausnahme beim Werfen ihren Traceback an, und
    eine eingefrorene Dataclass verweigert das.
    """

    feld: str
    meldung: str

    def __str__(self) -> str:  # pragma: no cover - Anzeige, nicht Logik
        return self.meldung


# Untergrenzen, nicht Geschmack: Ein Regelzyklus von null Sekunden ist eine Endlosschleife,
# eine Mindestschaltdauer von null Sekunden ist genau der Defekt des Altsystems (Takten am
# Sollwert), und eine Hysterese von null Kelvin ebenso. Obergrenzen halten Zahlen fern, die
# nur durch einen Tippfehler entstehen koennen.
GRENZEN: dict[str, tuple[Decimal, Decimal]] = {
    "polling_interval_seconds": (Decimal(5), Decimal(3600)),
    "shadow_interval_seconds": (Decimal(5), Decimal(3600)),
    "default_hysteresis_k": (Decimal("0.1"), Decimal("5.0")),
    "default_min_on_seconds": (Decimal(30), Decimal(7200)),
    "default_min_off_seconds": (Decimal(30), Decimal(7200)),
    "default_sensor_timeout_seconds": (Decimal(60), Decimal(86400)),
    "default_window_resume_delay_seconds": (Decimal(0), Decimal(3600)),
    "measurement_retention_days": (Decimal(1), Decimal(3650)),
    "session_lifetime_seconds": (Decimal(300), Decimal(31536000)),
}

BESCHRIFTUNG: dict[str, str] = {
    "polling_interval_seconds": "Abfrageintervall (Sekunden)",
    "shadow_interval_seconds": "Regelzyklus (Sekunden)",
    "default_hysteresis_k": "Hysterese (K)",
    "default_min_on_seconds": "Mindest-Einschaltdauer (Sekunden)",
    "default_min_off_seconds": "Mindest-Ausschaltdauer (Sekunden)",
    "default_sensor_timeout_seconds": "Sensor gilt als ausgefallen nach (Sekunden)",
    "default_window_resume_delay_seconds": "Nachlauf nach Fensterschluss (Sekunden)",
    "measurement_retention_days": "Messwerte aufbewahren (Tage)",
    "session_lifetime_seconds": "Sitzungsdauer (Sekunden)",
}

# Welche Felder ganzzahlig sind. Der Rest ist eine Dezimalzahl.
GANZZAHLIG = frozenset(GRENZEN) - {"default_hysteresis_k"}


def einstellungen(session: Session) -> Setting:
    zeile = session.get(Setting, 1)
    if zeile is None:  # pragma: no cover - nur bei unvollstaendiger Einrichtung
        raise Steuerungsfehler("", "Die Einrichtung ist unvollständig.")
    return zeile


def zahl_pruefen(feld: str, eingabe: str) -> Decimal:
    """Prueft einen einzelnen Wert gegen seine Grenzen.

    Eigene Funktion, weil dieselbe Pruefung aus der Oberflaeche, aus REST und aus MCP
    kommt -- und weil eine Grenze, die an drei Stellen steht, an drei Stellen abweicht.
    """
    text = eingabe.strip().replace(",", ".")
    if not text:
        raise Steuerungsfehler(feld, "Bitte einen Wert angeben.")
    try:
        wert = Decimal(text)
    except InvalidOperation as exc:
        raise Steuerungsfehler(feld, "Bitte eine Zahl angeben.") from exc
    if feld in GANZZAHLIG and wert != wert.to_integral_value():
        raise Steuerungsfehler(feld, "Bitte eine ganze Zahl angeben.")
    unten, oben = GRENZEN[feld]
    if not (unten <= wert <= oben):
        raise Steuerungsfehler(
            feld, f"Bitte einen Wert zwischen {_kurz(unten)} und {_kurz(oben)} angeben."
        )
    return wert


def _kurz(wert: Decimal) -> str:
    ganz = wert.to_integral_value()
    return str(int(ganz)) if wert == ganz else str(wert)


def einstellungen_speichern(
    session: Session,
    werte: dict[str, str],
    zeitzone: str,
    *,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> None:
    """Uebernimmt die globalen Vorgaben, nachdem **alle** Werte geprueft sind.

    Erst pruefen, dann schreiben: Sonst bliebe bei einer abgelehnten Eingabe im dritten
    Feld eine halb uebernommene Einstellung stehen -- derselbe Fehler, der die
    Einrichtung schon einmal halb angelegt hinterlassen hat.
    """
    if not zeitzone.strip():
        raise Steuerungsfehler("timezone", "Bitte eine Zeitzone angeben.")
    geprueft = {feld: zahl_pruefen(feld, werte.get(feld, "")) for feld in GRENZEN}

    zeile = einstellungen(session)
    zeile.timezone = zeitzone.strip()
    for feld, wert in geprueft.items():
        setattr(zeile, feld, wert if feld not in GANZZAHLIG else int(wert))
    audit.record(
        session,
        source=quelle,
        action="update",
        object_type="setting",
        object_id="1",
        summary="Globale Regelvorgaben geändert",
        user_id=user_id,
        token_id=token_id,
    )


def scharf_schalten(
    session: Session,
    scharf: bool,
    *,
    begruendung: str,
    user_id: int | None,
    token_id: int | None = None,
    quelle: str = "web",
) -> bool:
    """Legt den Riegel um, den die Datenbank haelt. Gibt zurueck, ob sich etwas aenderte.

    Die Begruendung ist Pflicht beim Scharfschalten und freiwillig beim Zurueckdrehen:
    Wer die Anlage aus der Hand gibt, soll spaeter nachlesen koennen, worauf er sich
    dabei verlassen hat. Zurueck in den Trockenlauf soll dagegen nie an einer Formalie
    scheitern -- das ist der Weg, den jemand in Eile geht.
    """
    if scharf and not begruendung.strip():
        raise Steuerungsfehler(
            "begruendung", "Bitte kurz festhalten, worauf sich das Scharfschalten stützt."
        )

    zeile = einstellungen(session)
    if zeile.control_armed == scharf:
        return False
    zeile.control_armed = scharf
    audit.record(
        session,
        source=quelle,
        action="arm" if scharf else "disarm",
        object_type="setting",
        object_id="1",
        summary=(
            "Regelung scharf geschaltet — ab jetzt wird wirklich geschaltet"
            if scharf
            else "Regelung in den Trockenlauf zurückgenommen"
        ),
        detail=begruendung.strip() or None,
        user_id=user_id,
        token_id=token_id,
    )
    return True
