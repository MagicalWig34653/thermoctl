"""Die gesamte Umstellung auf Englisch, in acht Schritten.

Als ein Skript, damit sie von einem sauberen Stand wiederholbar ist. Genau das war beim
ersten Anlauf der Unterschied zwischen "geht schief" und "geht schief und ist nicht mehr
zu reparieren".

Die Schritte sind bewusst getrennt, weil sie verschiedene Dinge anfassen -- Bezeichner,
Vorlagenschluessel, Web-Pfade, MQTT-Topics -- und jeder von ihnen an anderer Stelle
danebengreifen kann.
"""

import ast
import importlib.util
import io
import re
import subprocess
import tokenize
from pathlib import Path

BASIS = Path.home() / ".claude/jobs/db75991a/tmp/umbenennung"


def _laden(name: str):
    spec = importlib.util.spec_from_file_location(name, BASIS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pfade = _laden("pfade")
mqtt = _laden("mqtt")


def tabelle(datei: str) -> dict[str, str]:
    p = BASIS / datei
    if not p.exists():
        return {}
    return dict(
        z.split("\t") for z in p.read_text(encoding="utf-8").splitlines() if "\t" in z
    )


NAMEN = {**tabelle("abbildung.txt"), **tabelle("zu_pruefen.tsv")}
MODULE = {**tabelle("module.tsv"), **tabelle("testdateien.tsv")}
for alt, neu in MODULE.items():
    NAMEN[Path(alt).stem] = Path(neu).stem
ZEICHENKETTEN = tabelle("zeichenketten_global.tsv")

# Zeichenketten, die nur in bestimmten Dateien einen Bezeichner nennen. Ueberall sonst
# tragen dieselben Woerter ein Formularfeld (`name="modus"` im Zeitplanformular) oder
# einen Modus-Code in der Datenbank -- und muessen deutsch bleiben.
DATEI_GEBUNDEN: dict[str, dict[str, str]] = {}
for _zeile in (BASIS / "zeichenketten_datei.tsv").read_text(encoding="utf-8").splitlines():
    if _zeile.count("\t") == 2:
        _datei, _alt, _neu = _zeile.split("\t")
        DATEI_GEBUNDEN.setdefault(_datei, {})[_alt] = _neu

QUELLEN = ("thermoctl", "tests")


def _neu_literal(roh: str, wert: str) -> str:
    """Baut ein Literal neu -- mit demselben Praefix und denselben Anfuehrungen.

    Ohne das wird aus einer rohen Zeichenkette eine gewoehnliche, und Python meldet
    eine ungueltige Escape-Folge. Aufgefallen ist es an einer Regex, die danach
    still nicht mehr passte.
    """
    i = 0
    while i < len(roh) and roh[i].isalpha():
        i += 1
    praefix, rest = roh[:i], roh[i:]
    anf = rest[:3] if rest[:3] in ('"""', "'''") else rest[0]
    return praefix + anf + wert + anf


def _ersetzen(zeilen: list[str], stellen: list[tuple[int, int, int, str]]) -> int:
    """Ersetzt von hinten, damit fruehere Stellen die spaeteren nicht verschieben."""
    for zeile, von, bis, text in sorted(stellen, reverse=True):
        i = zeile - 1
        zeilen[i] = zeilen[i][:von] + text + zeilen[i][bis:]
    return len(stellen)


def schritt_1_bezeichner() -> int:
    """NAME-Token, und nur die. Zeichenketten bleiben unberuehrt."""
    gesamt = 0
    for wurzel in QUELLEN:
        for p in sorted(Path(wurzel).rglob("*.py")):
            quelle = p.read_text(encoding="utf-8")
            zeilen = quelle.splitlines(keepends=True)
            stellen = [
                (t.start[0], t.start[1], t.end[1], NAMEN[t.string])
                for t in tokenize.generate_tokens(io.StringIO(quelle).readline)
                if t.type == tokenize.NAME
                and t.string in NAMEN
                and t.start[0] == t.end[0]
            ]
            if stellen:
                gesamt += _ersetzen(zeilen, stellen)
                p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


def schritt_2_dateien() -> int:
    # macOS legt bei einem `git mv` auf einen bestehenden Namen eine Kopie "… 2.py" an.
    # Aus einem abgebrochenen Lauf bleiben die stehen und werden beim naechsten Lauf
    # mitgeprueft -- mypy zaehlte dadurch plötzlich sechs Dateien mehr.
    for rest in [*Path("thermoctl").rglob("* 2.py"), *Path("tests").rglob("* 2.py")]:
        rest.unlink()
    for alt, neu in MODULE.items():
        if Path(alt).exists():
            subprocess.run(["git", "mv", alt, neu], check=True)
    return len(MODULE)


def schritt_3_all_und_filter() -> int:
    """`__all__` und die Jinja-Filter tragen Namen als Zeichenkette."""
    n = 0
    for datei, paare in {
        "tests/webauthn_device.py": [('"WebAuthnGeraet"', '"WebAuthnDevice"')],
        "thermoctl/db/models/__init__.py": [
            ('"messwert"', '"measurement"'), ('"zustand"', '"state"')],
        "thermoctl/web/__init__.py": [
            ('templates.env.filters["alter"]', 'templates.env.filters["age"]')],
    }.items():
        p = Path(datei)
        t = p.read_text(encoding="utf-8")
        for alt, neu in paare:
            if alt in t:
                t = t.replace(alt, neu)
                n += 1
        p.write_text(t, encoding="utf-8")
    return n


def schritt_4_parametrize() -> int:
    """Die Namen in `parametrize` muessen zu den Parametern der Testfunktion passen."""
    gesamt = 0
    for p in sorted(Path("tests").rglob("*.py")):
        quelle = p.read_text(encoding="utf-8")
        stellen = []
        for k in ast.walk(ast.parse(quelle)):
            if not (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)):
                continue
            if k.func.attr != "parametrize" or not k.args:
                continue
            erst = k.args[0]
            kandidaten = (
                [erst] if isinstance(erst, ast.Constant)
                else list(erst.elts) if isinstance(erst, ast.Tuple | ast.List) else []
            )
            for c in kandidaten:
                if not (isinstance(c, ast.Constant) and isinstance(c.value, str)):
                    continue
                neu = ",".join(NAMEN.get(s.strip(), s.strip()) for s in c.value.split(","))
                if neu != c.value:
                    zeilen = quelle.splitlines(keepends=True)
                    roh = zeilen[c.lineno - 1][c.col_offset : c.end_col_offset]
                    stellen.append(
                        (c.lineno, c.col_offset, c.end_col_offset, _neu_literal(roh, neu)))
        if stellen:
            zeilen = quelle.splitlines(keepends=True)
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


AUSDRUCK = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.DOTALL)
WORT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _in_jinja(text: str) -> tuple[str, int]:
    """Bezeichner innerhalb eines Jinja-Ausdrucks, ausserhalb von Zeichenketten."""
    treffer, ergebnis, i = 0, [], 0
    while i < len(text):
        z = text[i]
        if z in "'\"":
            j = text.find(z, i + 1)
            if j == -1:
                ergebnis.append(text[i:]); break
            ergebnis.append(text[i : j + 1]); i = j + 1
            continue
        m = WORT.match(text, i)
        if m:
            wort = m.group()
            ergebnis.append(NAMEN.get(wort, wort))
            treffer += wort in NAMEN
            i = m.end()
            continue
        ergebnis.append(z); i += 1
    return "".join(ergebnis), treffer


def schritt_5_vorlagen() -> int:
    gesamt = 0
    for p in sorted(Path("thermoctl/web/templates").rglob("*.html")):
        teile = AUSDRUCK.split(p.read_text(encoding="utf-8"))
        treffer = 0
        for k, teil in enumerate(teile):
            if AUSDRUCK.fullmatch(teil):
                teile[k], n = _in_jinja(teil)
                treffer += n
        if treffer:
            p.write_text("".join(teile), encoding="utf-8")
            gesamt += treffer
    return gesamt


class _Kontexte(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stellen: list[ast.Dict] = []

    def visit_Call(self, k: ast.Call) -> None:
        ziel = k.func
        name = ziel.attr if isinstance(ziel, ast.Attribute) else getattr(ziel, "id", "")
        if name == "TemplateResponse":
            for a in [*k.args, *(s.value for s in k.keywords)]:
                if isinstance(a, ast.Dict):
                    self.stellen.append(a)
        self.generic_visit(k)

    def visit_Return(self, k: ast.Return) -> None:
        if isinstance(k.value, ast.Dict):
            self.stellen.append(k.value)
        self.generic_visit(k)


def schritt_6_kontextschluessel() -> int:
    """Die Schluessel eines Vorlagenkontexts sind Bezeichner, keine Daten.

    Dieselbe Zeichenkette in `formular.get("wochentag")` ist dagegen ein Formularfeld
    und bleibt -- im HTML steht `name="wochentag"`.
    """
    gesamt = 0
    for p in sorted(Path("thermoctl/web").glob("*.py")):
        quelle = p.read_text(encoding="utf-8")
        sucher = _Kontexte()
        sucher.visit(ast.parse(quelle))
        zeilen = quelle.splitlines(keepends=True)
        stellen = []
        for d in sucher.stellen:
            for s in d.keys:
                if isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value in NAMEN:
                    roh = zeilen[s.lineno - 1][s.col_offset : s.end_col_offset]
                    stellen.append((s.lineno, s.col_offset, s.end_col_offset,
                                    _neu_literal(roh, NAMEN[s.value])))
        if stellen:
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


def _fstring_beginnt_mit(quelle: str) -> dict[int, bool]:
    """Markiert **jedes** Stueck einer f-Zeichenkette, die mit einem Schraegstrich anfaengt.

    Nur eine solche ist ein Web-Pfad. `f"{praefix}/zonen/1"` faengt mit einer Klammer an
    und ist ein MQTT-Topic. Wichtig ist, dass die Markierung fuer die ganze Zeichenkette
    gilt und nicht nur fuer ihr erstes Stueck: In `f"/zones/{id}/loeschen"` steht der
    zweite Teil hinter der Klammer, und ein Anlauf, der nur das erste Stueck ansah, liess
    dreiunddreissig Endpunkte auf Deutsch stehen.
    """
    ergebnis: dict[int, bool] = {}
    tiefe = 0
    ist_pfad = False
    erstes = True
    stuecke: list[int] = []
    for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
        if t.type == tokenize.FSTRING_START:
            tiefe += 1
            if tiefe == 1:
                ist_pfad, erstes, stuecke = False, True, []
        elif t.type == tokenize.FSTRING_MIDDLE and tiefe:
            schluessel = t.start[0] * 10000 + t.start[1]
            stuecke.append(schluessel)
            if erstes:
                ist_pfad = t.string.startswith("/")
                erstes = False
        elif t.type == tokenize.FSTRING_END and tiefe:
            tiefe -= 1
            if tiefe == 0:
                for s in stuecke:
                    ergebnis[s] = ist_pfad
    return ergebnis


def schritt_7_pfade() -> int:
    """Web-Pfade -- nur Zeichenketten, die als Ganzes mit `/` anfangen."""
    gesamt = 0
    for wurzel in QUELLEN:
        for p in sorted(Path(wurzel).rglob("*.py")):
            quelle = p.read_text(encoding="utf-8")
            beginnt = _fstring_beginnt_mit(quelle)
            zeilen = quelle.splitlines(keepends=True)
            stellen = []
            for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
                if t.start[0] != t.end[0]:
                    continue
                if t.type == tokenize.STRING:
                    try:
                        wert = ast.literal_eval(t.string)
                    except (ValueError, SyntaxError):
                        continue
                    if not isinstance(wert, str) or not wert.startswith("/"):
                        continue
                    neu, n = pfade.in_text(wert)
                    if n:
                        stellen.append(
                            (t.start[0], t.start[1], t.end[1], _neu_literal(t.string, neu)))
                elif t.type == tokenize.FSTRING_MIDDLE:
                    if not beginnt.get(t.start[0] * 10000 + t.start[1]):
                        continue
                    neu, n = pfade.in_text(t.string)
                    if n:
                        stellen.append((t.start[0], t.start[1], t.end[1], neu))
            if stellen:
                gesamt += _ersetzen(zeilen, stellen)
                p.write_text("".join(zeilen), encoding="utf-8")

    ATTRIBUT = re.compile(r'(href|action|hx-get|hx-post|hx-put|hx-delete|src)="([^"]*)"')
    for p in sorted(Path("thermoctl/web/templates").rglob("*.html")):
        quelle = p.read_text(encoding="utf-8")
        n = 0

        def ersetzen(t: re.Match[str]) -> str:
            nonlocal n
            neu, k = pfade.in_text(t.group(2))
            n += k
            return f'{t.group(1)}="{neu}"'

        neu = ATTRIBUT.sub(ersetzen, quelle)
        if n:
            p.write_text(neu, encoding="utf-8")
            gesamt += n

    ZK = re.compile(r'"(/[^"]*)"|\'(/[^\']*)\'')
    for p in sorted(Path("thermoctl/web/static").glob("*.js")):
        quelle = p.read_text(encoding="utf-8")
        n = 0

        def js(t: re.Match[str]) -> str:
            nonlocal n
            roh = t.group(1) if t.group(1) is not None else t.group(2)
            anf = '"' if t.group(1) is not None else "'"
            neu, k = pfade.in_text(roh)
            n += k
            return f"{anf}{neu}{anf}"

        neu = ZK.sub(js, quelle)
        if n:
            p.write_text(neu, encoding="utf-8")
            gesamt += n
    return gesamt


def schritt_8_topics_und_namen() -> int:
    """MQTT-Topics und die Zeichenketten, die einen Bezeichner nennen."""
    gesamt = 0
    for wurzel in QUELLEN:
        for p in sorted(Path(wurzel).rglob("*.py")):
            quelle = p.read_text(encoding="utf-8")
            zeilen = quelle.splitlines(keepends=True)
            # Der Dateiname von *vor* der Umbenennung: Schritt 2 hat sie schon bewegt.
            alt_name = next((a for a, n in MODULE.items() if n == str(p)), str(p))
            ortsgebunden = DATEI_GEBUNDEN.get(alt_name, {})
            stellen = []
            for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
                if t.start[0] != t.end[0]:
                    continue
                if t.type == tokenize.STRING:
                    try:
                        wert = ast.literal_eval(t.string)
                    except (ValueError, SyntaxError):
                        continue
                    if not isinstance(wert, str):
                        continue
                    neu = ZEICHENKETTEN.get(wert)
                    if neu is None:
                        neu = ortsgebunden.get(wert)
                    if neu is None and "/" in wert and not wert.startswith("/"):
                        kandidat = mqtt.topic_uebersetzen(wert)
                        neu = kandidat if kandidat != wert else None
                    if neu is not None:
                        stellen.append(
                            (t.start[0], t.start[1], t.end[1], _neu_literal(t.string, neu)))
                elif t.type == tokenize.FSTRING_MIDDLE and "/" in t.string:
                    neu = mqtt.topic_uebersetzen(t.string)
                    if neu != t.string:
                        stellen.append((t.start[0], t.start[1], t.end[1], neu))
            if stellen:
                gesamt += _ersetzen(zeilen, stellen)
                p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


BEFUNDARTEN = {
    "abgeschaltet": "disabled", "stumm": "silent", "batterie": "battery", "funk": "radio",
}
BEFUND_DATEIEN = (
    "thermoctl/domain/geraeteschau.py", "tests/test_geraeteschau.py",
)


def schritt_8e_befundarten() -> int:
    """`Finding.kind` traegt einen internen Code, kein sichtbares Wort."""
    gesamt = 0
    for datei in BEFUND_DATEIEN:
        alt = datei
        neu_name = MODULE.get(alt, alt)
        p = Path(neu_name)
        if not p.exists():
            continue
        quelle = p.read_text(encoding="utf-8")
        zeilen = quelle.splitlines(keepends=True)
        stellen = []
        for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
            if t.type != tokenize.STRING or t.start[0] != t.end[0]:
                continue
            try:
                wert = ast.literal_eval(t.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(wert, str) and wert in BEFUNDARTEN:
                stellen.append((t.start[0], t.start[1], t.end[1],
                                _neu_literal(t.string, BEFUNDARTEN[wert])))
        if stellen:
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")
    # Die Vorlage bildet die Art auf eine Marke ab.
    v = Path("thermoctl/web/templates/geraete.html")
    if v.exists():
        q = v.read_text(encoding="utf-8")
        for a, b in BEFUNDARTEN.items():
            q = q.replace(f'"{a}":', f'"{b}":')
        v.write_text(q, encoding="utf-8")
        gesamt += len(BEFUNDARTEN)
    return gesamt


ARTEN = {
    "sollwert": "setpoint", "betriebsart": "operating_mode", "modus": "mode",
    "letzte_schaltung": "last_switch", "naechste_schaltung": "next_switch",
}
ARTEN_DATEIEN = (
    "thermoctl/integrations/mqtt/commands.py",
    "thermoctl/integrations/mqtt/publication.py",
    "thermoctl/app.py",
    "tests/test_commands.py",
    "tests/test_publication.py",
    "tests/test_publishing.py",
)


def schritt_8b_befehlsarten() -> int:
    """Die Befehls- und Zustandsarten stehen als nackte Zeichenkette da.

    Sie sind Topic-Segmente (`.../befehl/sollwert`) und muessen sich mit den Topics
    aendern -- der Schritt davor sieht sie nicht, weil kein Schraegstrich darin steht.
    """
    gesamt = 0
    for datei in ARTEN_DATEIEN:
        p = Path(datei)
        if not p.exists():
            continue
        quelle = p.read_text(encoding="utf-8")
        zeilen = quelle.splitlines(keepends=True)
        stellen = []
        for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
            if t.type != tokenize.STRING or t.start[0] != t.end[0]:
                continue
            try:
                wert = ast.literal_eval(t.string)
            except (ValueError, SyntaxError):
                continue
            if isinstance(wert, str) and wert in ARTEN:
                stellen.append(
                    (t.start[0], t.start[1], t.end[1], _neu_literal(t.string, ARTEN[wert])))
        if stellen:
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


FELDATTRIBUT = re.compile(r'\b(name|id|for|aria-labelledby)="([A-Za-z_][A-Za-z0-9_]*)"')


def schritt_8c_formularfelder() -> int:
    """Formularfelder heissen in der Vorlage, in der Ansicht und im Fehlerschluessel gleich.

    Sie sind die Schnittstelle zwischen Vorlage und Ansicht, also Code -- und wenn sich
    eine der drei Seiten aendert, muessen die anderen mit. Der Vorlagenschritt hat
    `errors.uhrzeit` zu `errors.time_of_day` gemacht, waehrend die Ansicht weiter
    `formular.get("uhrzeit")` las; die Fehlermeldung erschien dann nirgends.

    In den Ansichten unter `thermoctl/web/` wird deshalb **jede** Zeichenkette
    uebersetzt, die einen Bezeichner nennt: Dort sind das Formularfelder und
    Kontextschluessel. Die Domaene bleibt unberuehrt -- da tragen dieselben Woerter
    Codes aus der Datenbank.
    """
    felder = tabelle("formularfelder.tsv")
    gesamt = 0
    for p in sorted(Path("thermoctl/web").glob("*.py")):
        quelle = p.read_text(encoding="utf-8")
        zeilen = quelle.splitlines(keepends=True)
        stellen = []
        for t in tokenize.generate_tokens(io.StringIO(quelle).readline):
            if t.type != tokenize.STRING or t.start[0] != t.end[0]:
                continue
            try:
                wert = ast.literal_eval(t.string)
            except (ValueError, SyntaxError):
                continue
            if not isinstance(wert, str) or wert not in felder:
                continue
            stellen.append(
                (t.start[0], t.start[1], t.end[1], _neu_literal(t.string, felder[wert])))
        if stellen:
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")

    for p in sorted(Path("thermoctl/web/templates").rglob("*.html")):
        quelle = p.read_text(encoding="utf-8")
        n = 0

        def ersetzen(m: re.Match[str]) -> str:
            nonlocal n
            if m.group(2) not in felder:
                return m.group()
            n += 1
            return f'{m.group(1)}="{felder[m.group(2)]}"'

        neu = FELDATTRIBUT.sub(ersetzen, quelle)
        if n:
            p.write_text(neu, encoding="utf-8")
            gesamt += n
    return gesamt


def schritt_8d_testformulare() -> int:
    """Die Tests schicken Formulardaten -- mit denselben Feldnamen wie die Vorlage.

    `data={"uhrzeit": "06:00"}` ist dieselbe Schnittstelle wie `name="uhrzeit"` im HTML
    und `formular.get("uhrzeit")` in der Ansicht. Aendert sich eine der drei Seiten,
    muessen die anderen mit; `json=` bleibt unberuehrt, das geht an die REST-Schnittstelle
    und ist ohnehin englisch.
    """
    felder = tabelle("formularfelder.tsv")
    gesamt = 0
    for p in sorted(Path("tests").rglob("*.py")):
        quelle = p.read_text(encoding="utf-8")
        zeilen = quelle.splitlines(keepends=True)
        stellen = []
        for k in ast.walk(ast.parse(quelle)):
            if not isinstance(k, ast.Call):
                continue
            for s in k.keywords:
                if s.arg != "data" or not isinstance(s.value, ast.Dict):
                    continue
                for c in s.value.keys:
                    if (isinstance(c, ast.Constant) and isinstance(c.value, str)
                            and c.value in felder):
                        roh = zeilen[c.lineno - 1][c.col_offset : c.end_col_offset]
                        stellen.append((c.lineno, c.col_offset, c.end_col_offset,
                                        _neu_literal(roh, felder[c.value])))
        if stellen:
            gesamt += _ersetzen(zeilen, stellen)
            p.write_text("".join(zeilen), encoding="utf-8")
    return gesamt


NACHARBEIT = [
    # `[a-z]+` traf `operating_mode` nicht mehr: Die Befehlsart hat jetzt einen
    # Unterstrich. Das Muster war vorher richtig, weil `sollwert` keinen hatte.
    ("thermoctl/integrations/mqtt/commands.py",
     "(?P<art>[a-z]+)", "(?P<art>[a-z_]+)"),
]


def schritt_9b_nacharbeit() -> int:
    n = 0
    for datei, alt, neu in NACHARBEIT:
        p = Path(datei)
        if not p.exists():
            continue
        q = p.read_text(encoding="utf-8")
        if alt in q:
            p.write_text(q.replace(alt, neu), encoding="utf-8")
            n += 1
    return n


def schritt_9_verschattung() -> int:
    """Eine modulweite Fixture verdeckte die gleichnamige aus `conftest.py`.

    `einstellungen` wurde zu `settings` -- und `settings` gibt es in `conftest.py` schon,
    mit `scope="session"`. pytest meldet das als ScopeMismatch, nicht als Namenskonflikt,
    und deshalb faellt es erst beim Ausfuehren auf.
    """
    p = Path("tests/test_passkey.py")
    quelle = p.read_text(encoding="utf-8")
    zeilen = quelle.splitlines(keepends=True)
    stellen = [
        (t.start[0], t.start[1], t.end[1], "passkey_settings")
        for t in tokenize.generate_tokens(io.StringIO(quelle).readline)
        if t.type == tokenize.NAME and t.string == "settings"
    ]
    _ersetzen(zeilen, stellen)
    p.write_text("".join(zeilen), encoding="utf-8")
    return len(stellen)


UMBRUECHE = [
    ("tests/test_api_configuration.py",
     '        client.put(f"/api/v1/zones/{zone.id}/setpoints", headers=without_permission, json=daten).status_code',
     '        client.put(\n            f"/api/v1/zones/{zone.id}/setpoints", headers=without_permission, json=daten\n        ).status_code'),
    ("tests/test_api_configuration.py",
     '        client.post(f"/api/v1/zones/{zone.id}/schedule", headers=without_permission, json=daten).status_code',
     '        client.post(\n            f"/api/v1/zones/{zone.id}/schedule", headers=without_permission, json=daten\n        ).status_code'),
    ("tests/test_device_model.py",
     '                zone_id=zone.id, device_id=create_device(session, name).id, device_role_id=actuator.id',
     '                zone_id=zone.id,\n                device_id=create_device(session, name).id,\n                device_role_id=actuator.id,'),
    ("tests/test_identity_model.py",
     '    session.add(GroupPermission(access_group_id=group.id, permission_id=read_only.id, zone_id=zone.id))',
     '    session.add(\n        GroupPermission(access_group_id=group.id, permission_id=read_only.id, zone_id=zone.id)\n    )'),
    ("tests/test_plant_diagram.py",
     '    assert picture.temperature_source is not None and picture.temperature_source.name == sensor.display_name',
     '    assert picture.temperature_source is not None\n    assert picture.temperature_source.name == sensor.display_name'),
    ("tests/test_startup_behaviour.py",
     '        offene = list(http_session.scalars(select(SetupToken).where(SetupToken.consumed_at.is_(None))))',
     '        offene = list(\n            http_session.scalars(select(SetupToken).where(SetupToken.consumed_at.is_(None)))\n        )'),
    ("thermoctl/domain/plant_diagram.py",
     '                temperature_source=_picture(source, capabilities, codes, TEMPERATURE_SOURCE) if source else None,',
     '                temperature_source=(\n                    _picture(source, capabilities, codes, TEMPERATURE_SOURCE) if source else None\n                ),'),
]


def schritt_10_umbrueche() -> int:
    """Zeilen, die durch die laengeren englischen Namen zu lang geworden sind."""
    n = 0
    for datei, alt, neu in UMBRUECHE:
        p = Path(datei)
        t = p.read_text(encoding="utf-8")
        if alt in t:
            p.write_text(t.replace(alt, neu), encoding="utf-8")
            n += 1
    return n


if __name__ == "__main__":
    for name, f in [
        ("Bezeichner", schritt_1_bezeichner),
        ("Dateien", schritt_2_dateien),
        ("__all__ und Filter", schritt_3_all_und_filter),
        ("parametrize", schritt_4_parametrize),
        ("Vorlagen", schritt_5_vorlagen),
        ("Kontextschluessel", schritt_6_kontextschluessel),
        ("Web-Pfade", schritt_7_pfade),
        ("Topics und Namen", schritt_8_topics_und_namen),
        ("Befehlsarten", schritt_8b_befehlsarten),
        ("Befundarten", schritt_8e_befundarten),
        ("Formularfelder", schritt_8c_formularfelder),
        ("Testformulare", schritt_8d_testformulare),
        ("Nacharbeit", schritt_9b_nacharbeit),
        ("Verschattung", schritt_9_verschattung),
        ("Umbrueche", schritt_10_umbrueche),
    ]:
        print(f"{name}: {f()}")
