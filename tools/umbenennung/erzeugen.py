"""Erzeugt die Umbenennungstabelle aus der Stammtabelle und meldet, was offen bleibt."""
import ast, importlib.util, re, sys
from pathlib import Path

BASIS = Path.home() / ".claude/jobs/db75991a/tmp/umbenennung"
spec = importlib.util.spec_from_file_location("staemme", BASIS / "staemme.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def uebersetzen(name: str) -> str | None:
    if name in s.FREMD or name in s.UNVERAENDERT:
        return None
    if name in s.KLASSEN:
        return s.KLASSEN[name]
    if name in s.EINZELN:
        return s.EINZELN[name]
    if name[:1].isupper() and "_" not in name:
        return None  # CamelCase ausschliesslich ueber KLASSEN
    gross = name.isupper()
    rest = name.lower()
    for alt, neu in s.TEIL:
        rest = rest.replace(alt, neu)
    for alt, neu in sorted(s.STAMM, key=lambda x: -len(x[0])):
        rest = rest.replace(alt, neu)
    for alt, neu in s.NACHBESSERN:
        rest = rest.replace(alt, neu)
    # Verb nach vorne: `device_swap` -> `swap_device`, aber nur wenn davor etwas steht
    # und der Name nicht mit Unterstrich beginnt (private Helfer bleiben, wie sie sind).
    teile = rest.split("_")
    if len(teile) > 1 and teile[-1] in s.VORANSTELLEN and not rest.startswith("_"):
        rest = "_".join([teile[-1], *teile[:-1]])
    if rest == name.lower():
        return None
    return rest.upper() if gross else rest


DEUTSCH = re.compile(
    r"(zonen|geraet|zeitplan|sollwert|betriebsart|uebersteuer|modus|modi|befehl|zustand|"
    r"nutzlast|einstellung|benutzer|gruppe|recht|sitzung|anmeld|pruef|aender|loesch|"
    r"anleg|speicher|veroeffentlich|trockenlauf|scharf|quelle|faehigkeit|wochentag|"
    r"uhrzeit|messquelle|aktor|fenster|schnittstelle|beobacht|belegung|taste|bedien|"
    r"klartext|nachricht|antwort|formular|fehler|anbindung|zuordnung|beschrift|bezeichn|"
    r"ergebnis|eintrag|werkzeug|zyklus|schatten|erreichbar|verfuegbar)",
    re.IGNORECASE,
)

namen: set[str] = set()
for wurzel in ("thermoctl", "tests"):
    for p in sorted(Path(wurzel).rglob("*.py")):
        for k in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(k, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                namen.add(k.name)
            elif isinstance(k, ast.arg):
                namen.add(k.arg)
            elif isinstance(k, ast.Name):
                namen.add(k.id)
            elif isinstance(k, ast.Attribute):
                namen.add(k.attr)

abbildung: dict[str, str] = {}
offen: list[tuple[str, str]] = []
for n in sorted(namen):
    if n.startswith("test_"):
        continue
    neu = uebersetzen(n)
    if neu is None:
        if DEUTSCH.search(n):
            offen.append((n, "—"))
        continue
    if DEUTSCH.search(neu):
        offen.append((n, neu))
    else:
        abbildung[n] = neu

(BASIS / "abbildung.txt").write_text(
    "\n".join(f"{a}\t{b}" for a, b in sorted(abbildung.items())), encoding="utf-8"
)
print(f"abgebildet: {len(abbildung)}   offen: {len(offen)}")
for a, b in offen:
    print(f"   {a}  ->  {b}")
