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
BATTERY_LOW_PERCENT = Decimal(20)

# Unterhalb dieser Funkgüte wird die Verbindung unzuverlässig. Zigbee2MQTT meldet sie
# als LQI von 0 bis 255; die Grenze ist Erfahrung, kein Standard, und deshalb hier
# benannt statt in einer Bedingung versteckt.
RADIO_WEAK_LQI = 30

# Diese Faehigkeiten bekommen kein eigenes Kaertchen: Ihr Wert steht in derselben Zeile
# schon als Zahl oder als Befund. Ein Kaertchen "Batteriestand" neben "58 %" sagt nichts,
# was die Zahl nicht sagt -- es kostet nur die Aufmerksamkeit, die den zwei auffaelligen
# Geraeten gehoert.
WITHOUT_CHIP = frozenset({"battery", "link_quality", "availability"})


@dataclass(frozen=True)
class Finding:
    """Ein Satz darüber, was an diesem Gerät nicht stimmt."""

    kind: str  # "stumm", "offline", "batterie", "funk", "abgeschaltet"
    text: str


@dataclass(frozen=True)
class DeviceSurvey:
    device_id: int
    name: str
    modell: str | None
    integration: str
    ist_group: bool
    capabilities: list[str]
    zones: list[str]
    last_heard: datetime | None
    battery: Decimal | None
    radio_quality: int | None
    befunde: list[Finding] = field(default_factory=list)
    # Wie viele Faehigkeiten unterdrueckt wurden, weil ihr Wert schon als Zahl dasteht.
    # Ohne diese Zahl liesse sich "meldet nichts" nicht von "meldet nur Batterie und
    # Funk" unterscheiden -- und die Seite behauptete bei jedem Fernbedienungsknopf, er
    # koenne gar nichts.
    quiet_capabilities: int = 0

    @property
    def in_ordnung(self) -> bool:
        return not self.befunde

    @property
    def schwere(self) -> int:
        """Zum Sortieren: Je kleiner, desto dringender.

        Ein stummes Gerät steht vor einer schwachen Batterie -- das eine ist ein Ausfall,
        das andere eine Ankündigung.
        """
        rang = {"offline": 0, "silent": 1, "disabled": 2, "battery": 3, "radio": 4}
        return min((rang.get(b.kind, 9) for b in self.befunde), default=9)


def _age_in_words(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} Minuten"
    if seconds < 86400:
        return f"{int(seconds // 3600)} Stunden"
    days = int(seconds // 86400)
    return f"{days} {'Tag' if days == 1 else 'Tagen'}"


def befunde(
    *,
    active: bool,
    last_heard: datetime | None,
    availability: str | None,
    battery: Decimal | None,
    radio_quality: int | None,
    silent_after_seconds: int,
    now: datetime,
) -> list[Finding]:
    """Was an einem Gerät auffällt. Leer heißt: nichts.

    `stumm_nach_sekunden` kommt aus den globalen Vorgaben — dieselbe Schwelle, nach der
    die Regelung einen Sensor für ausgefallen hält. Eine zweite Zahl allein für diese
    Seite hieße, dass die Geräteliste ein Gerät für gesund hält, das die Regelung schon
    aufgegeben hat.
    """
    gefunden: list[Finding] = []
    if not active:
        gefunden.append(Finding("disabled", "in der Brücke abgeschaltet"))
    if availability is not None and availability.lower() == "offline":
        gefunden.append(Finding("offline", "die Brücke führt es als offline"))
    if last_heard is None:
        gefunden.append(Finding("silent", "hat sich noch nie gemeldet"))
    elif (now - last_heard).total_seconds() > silent_after_seconds:
        age = _age_in_words((now - last_heard).total_seconds())
        gefunden.append(Finding("silent", f"seit {age} still"))
    if battery is not None and battery <= BATTERY_LOW_PERCENT:
        gefunden.append(Finding("battery", f"Batterie bei {battery:.0f} %"))
    if radio_quality is not None and radio_quality < RADIO_WEAK_LQI:
        gefunden.append(Finding("radio", f"schwacher Funk (LQI {radio_quality})"))
    return gefunden
