"""Der Zustand der Geräte — was in Ordnung ist und was nicht.

Die Geräteseite zeigte bis hierher eine Tabelle mit neun gleich gewichteten Spalten:
Anzeigename, Anbindung, Modell, Fähigkeiten, letzte Nachricht, Batterie, Funkgüte,
Erreichbarkeit, Zone. Alles stand da, nichts stach heraus, und die meisten Zellen waren
ein Gedankenstrich.

Die Frage, mit der jemand auf diese Seite kommt, ist aber fast immer dieselbe: **Stimmt
mit meiner Hardware etwas nicht?** Ein Sensor, der seit zwei Tagen schweigt, eine Batterie
bei sieben Prozent, ein Gerät, das die Brücke als offline führt. Dieses Modul beantwortet
genau das — und zwar in der Domäne, damit die Schwellen einmal dastehen und nicht in einer
Vorlage.

Was *wo* hängt, beantwortet dagegen das Anlagenbild. Die beiden Seiten teilen sich die
Geräte, aber nicht die Frage.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Ab hier gilt eine Batterie als schwach. Nicht als leer: Zigbee-Geräte melden lange
# Zeit 100 Prozent und fallen dann schnell; zwanzig Prozent lassen noch Tage, um eine
# Zelle zu besorgen.
BATTERIE_SCHWACH_PROZENT = Decimal(20)

# Unterhalb dieser Funkgüte wird die Verbindung unzuverlässig. Zigbee2MQTT meldet sie
# als LQI von 0 bis 255; die Grenze ist Erfahrung, kein Standard, und deshalb hier
# benannt statt in einer Bedingung versteckt.
FUNK_SCHWACH_LQI = 30

# Diese Faehigkeiten bekommen kein eigenes Kaertchen: Ihr Wert steht in derselben Zeile
# schon als Zahl oder als Befund. Ein Kaertchen "Batteriestand" neben "58 %" sagt nichts,
# was die Zahl nicht sagt -- es kostet nur die Aufmerksamkeit, die den zwei auffaelligen
# Geraeten gehoert.
OHNE_KAERTCHEN = frozenset({"battery", "link_quality", "availability"})


@dataclass(frozen=True)
class Befund:
    """Ein Satz darüber, was an diesem Gerät nicht stimmt."""

    art: str  # "stumm", "offline", "batterie", "funk", "abgeschaltet"
    text: str


@dataclass(frozen=True)
class Geraeteschau:
    geraet_id: int
    name: str
    modell: str | None
    anbindung: str
    ist_gruppe: bool
    faehigkeiten: list[str]
    zonen: list[str]
    zuletzt_gehoert: datetime | None
    batterie: Decimal | None
    funkguete: int | None
    befunde: list[Befund] = field(default_factory=list)
    # Wie viele Faehigkeiten unterdrueckt wurden, weil ihr Wert schon als Zahl dasteht.
    # Ohne diese Zahl liesse sich "meldet nichts" nicht von "meldet nur Batterie und
    # Funk" unterscheiden -- und die Seite behauptete bei jedem Fernbedienungsknopf, er
    # koenne gar nichts.
    stille_faehigkeiten: int = 0

    @property
    def in_ordnung(self) -> bool:
        return not self.befunde

    @property
    def schwere(self) -> int:
        """Zum Sortieren: Je kleiner, desto dringender.

        Ein stummes Gerät steht vor einer schwachen Batterie -- das eine ist ein Ausfall,
        das andere eine Ankündigung.
        """
        rang = {"offline": 0, "stumm": 1, "abgeschaltet": 2, "batterie": 3, "funk": 4}
        return min((rang.get(b.art, 9) for b in self.befunde), default=9)


def _alter_in_worten(sekunden: float) -> str:
    if sekunden < 3600:
        return f"{int(sekunden // 60)} Minuten"
    if sekunden < 86400:
        return f"{int(sekunden // 3600)} Stunden"
    tage = int(sekunden // 86400)
    return f"{tage} {'Tag' if tage == 1 else 'Tagen'}"


def befunde(
    *,
    aktiv: bool,
    zuletzt_gehoert: datetime | None,
    erreichbarkeit: str | None,
    batterie: Decimal | None,
    funkguete: int | None,
    stumm_nach_sekunden: int,
    jetzt: datetime,
) -> list[Befund]:
    """Was an einem Gerät auffällt. Leer heißt: nichts.

    `stumm_nach_sekunden` kommt aus den globalen Vorgaben — dieselbe Schwelle, nach der
    die Regelung einen Sensor für ausgefallen hält. Eine zweite Zahl allein für diese
    Seite hieße, dass die Geräteliste ein Gerät für gesund hält, das die Regelung schon
    aufgegeben hat.
    """
    gefunden: list[Befund] = []
    if not aktiv:
        gefunden.append(Befund("abgeschaltet", "in der Brücke abgeschaltet"))
    if erreichbarkeit is not None and erreichbarkeit.lower() == "offline":
        gefunden.append(Befund("offline", "die Brücke führt es als offline"))
    if zuletzt_gehoert is None:
        gefunden.append(Befund("stumm", "hat sich noch nie gemeldet"))
    elif (jetzt - zuletzt_gehoert).total_seconds() > stumm_nach_sekunden:
        alter = _alter_in_worten((jetzt - zuletzt_gehoert).total_seconds())
        gefunden.append(Befund("stumm", f"seit {alter} still"))
    if batterie is not None and batterie <= BATTERIE_SCHWACH_PROZENT:
        gefunden.append(Befund("batterie", f"Batterie bei {batterie:.0f} %"))
    if funkguete is not None and funkguete < FUNK_SCHWACH_LQI:
        gefunden.append(Befund("funk", f"schwacher Funk (LQI {funkguete})"))
    return gefunden
