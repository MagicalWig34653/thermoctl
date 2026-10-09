"""Erzeugt `docs/glossar.md` aus der einzigen Quelle `thermoctl/data/glossar.json`.

Aufruf aus dem Projektstamm::

    .venv/bin/python -m tools.glossar_erzeugen            # schreibt docs/glossar.md
    .venv/bin/python -m tools.glossar_erzeugen --pruefen  # schreibt nichts, Exit 1 bei Abweichung

Als Modul (`-m`), nicht als Skriptdatei: Nur so liegt der Projektstamm auf dem
Suchpfad, und `thermoctl` lässt sich importieren, ohne installiert zu sein.

Die Markdown-Fassung selbst entsteht in `thermoctl.domain.glossary.render_markdown`,
nicht hier: `tests/test_glossary.py` ruft dieselbe Funktion auf und schlägt fehl, wenn
`docs/glossar.md` nicht dazu passt. Dieses Skript ist nur der Weg, die Datei zu
schreiben -- es enthält absichtlich keine eigene Formatierung, die auseinanderlaufen
könnte.
"""

import argparse
import sys
from pathlib import Path

from thermoctl.domain.glossary import GlossaryError, default_glossary, render_markdown

ZIEL = Path(__file__).resolve().parent.parent / "docs" / "glossar.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--pruefen",
        action="store_true",
        help="nichts schreiben; mit Exit-Code 1 beenden, wenn docs/glossar.md veraltet ist",
    )
    arguments = parser.parse_args(argv)

    try:
        text = render_markdown(default_glossary())
    except GlossaryError as exc:
        print(f"Das Glossar ist fehlerhaft: {exc}", file=sys.stderr)
        return 2

    vorhanden = ZIEL.read_text(encoding="utf-8") if ZIEL.exists() else None
    if arguments.pruefen:
        if vorhanden != text:
            print(
                f"{ZIEL.name} ist nicht aktuell. "
                "Neu erzeugen: python -m tools.glossar_erzeugen",
                file=sys.stderr,
            )
            return 1
        print(f"{ZIEL.name} ist aktuell.")
        return 0

    if vorhanden == text:
        print(f"{ZIEL.name} war bereits aktuell.")
        return 0
    ZIEL.write_text(text, encoding="utf-8")
    print(f"{ZIEL.name} geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
