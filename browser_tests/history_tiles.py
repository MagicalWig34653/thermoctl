"""Gemeinsamer Helfer der Verlaufsseiten: alle Kacheln laden lassen.

Die Kacheln der Verlaufsseiten laden erst, wenn sie ins Bild kommen (`hx-trigger="revealed"`), und
wegen `hx-sync` nacheinander. Ein Test muss deshalb zum jeweils nächsten Platzhalter rollen, bis
keiner mehr da ist.

Der Live-Server ist für die ganze Browser-Sitzung geteilt: Im Gesamtlauf stehen auf der
Anlagenübersicht die Zonen aller früheren Tests (170 bis 190 und mehr), und jede Kachel braucht
ihren Schritt. Früher lief die Schleife über höchstens 200 Schritte zu 150 ms -- das reichte bei
wenigen Zonen und scheiterte im Gesamtlauf zufällig, je nach Auslastung des Rechners: Dann blieben
Platzhalter ungeladen, und die Wartezeit danach lief ins Leere. Die Schranke ist deshalb
**Zeit statt Schritte**, mit Abbruch bei ausbleibendem Fortschritt: Solange nach jeweils
`STALL_SECONDS` mindestens ein Platzhalter weniger da ist, geht es weiter. Ein wirklich hängender
Platzhalter (etwa eine Kachel, die mit 500 antwortet) hält den Test nicht stundenlang auf, sondern
scheitert mit der Zahl der übrig gebliebenen Platzhalter.
"""

from __future__ import annotations

from playwright.sync_api import Page

BUDGET_SECONDS = 600
STALL_SECONDS = 60
STEP_MILLISECONDS = 100

_SCROLL_UNTIL_LOADED = """
async ({budget, stall, step}) => {
    const started = performance.now();
    let best = Infinity;
    let lastProgress = started;
    for (;;) {
        const placeholders = document.querySelectorAll('.tc-history-placeholder');
        const now = performance.now();
        if (placeholders.length === 0) return {remaining: 0, reason: 'loaded'};
        if (placeholders.length < best) { best = placeholders.length; lastProgress = now; }
        if (now - lastProgress > stall) return {remaining: placeholders.length, reason: 'stalled'};
        if (now - started > budget) return {remaining: placeholders.length, reason: 'budget'};
        placeholders[0].scrollIntoView({block: 'center'});
        await new Promise(resolve => setTimeout(resolve, step));
    }
}
"""


def scroll_until_loaded(
    page: Page,
    *,
    budget_seconds: int = BUDGET_SECONDS,
    stall_seconds: int = STALL_SECONDS,
) -> None:
    """Rollt zum jeweils nächsten Platzhalter, bis keiner mehr da ist.

    Kacheln tauschen sich beim Laden aus, ein fester Satz Fundstellen wäre danach veraltet; die
    Schleife sucht deshalb in jedem Schritt neu. Scheitert mit der Zahl der übrigen Platzhalter,
    wenn `stall_seconds` lang kein einziger dazukam oder `budget_seconds` überschritten sind.
    """
    started: list[str] = []
    finished: list[tuple[str, int | str]] = []

    def is_tile(url: str) -> bool:
        return "/tile" in url or "tile=1" in url

    page.on("request", lambda r: started.append(r.url) if is_tile(r.url) else None)
    page.on("response", lambda r: finished.append((r.url, r.status)) if is_tile(r.url) else None)
    page.on(
        "requestfailed",
        lambda r: finished.append((r.url, f"abgebrochen: {r.failure}")) if is_tile(r.url) else None,
    )
    outcome = page.evaluate(
        _SCROLL_UNTIL_LOADED,
        {
            "budget": budget_seconds * 1000,
            "stall": stall_seconds * 1000,
            "step": STEP_MILLISECONDS,
        },
    )
    assert outcome["remaining"] == 0, (
        f"{outcome['remaining']} Verlaufskacheln blieben ungeladen ({outcome['reason']}); "
        f"{len(started)} Kachel-Anfragen gestellt, {len(finished)} beantwortet; "
        f"nicht mit 200 beantwortet: {[f for f in finished if f[1] != 200][:5]}; "
        f"zuletzt: {finished[-3:]}"
    )
