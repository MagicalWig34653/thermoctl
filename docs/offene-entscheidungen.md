# Selbst getroffene Entscheidungen

Entscheidungen, die sonst eine Rückfrage an den Projektinhaber gewesen wären. Der Auftrag
für die autonome Arbeit lautet: die naheliegendste treffen, hier mit Begründung und
verworfenen Alternativen festhalten, weiterarbeiten. Wer eine davon anders will, ändert sie
— sie sind dokumentiert, nicht in Stein.

---

## 2026-08-29 — CSRF-Schutz hängt am Router, nicht an der Route

**Entschieden:** `csrf_schutz` ist eine FastAPI-Abhängigkeit, die über
`APIRouter(dependencies=[Depends(csrf_schutz)])` an *jedem* Router der Oberfläche hängt.
Sie greift nur bei zustandsändernden Methoden und nur, wenn die Anfrage ein Sitzungscookie
trägt. Dazu kommt ein Wächtertest (`tests/test_csrf.py`), der jede zustandsändernde Route
aufzählt und rot wird, sobald eine ohne diesen Schutz dazukommt.

**Warum:** Die Auflage aus dem Abschlussreview war nicht „weniger Codewiederholung", sondern
„irgendwann vergisst man sie". Eine gemeinsame Funktion, die man je Route aufrufen muss,
löst das nicht — man vergisst dann den Aufruf. Erst die Kombination aus Router-Abhängigkeit
(neue Routen sind von sich aus geschützt) und Wächtertest (ein neuer Router ohne den Schutz
fällt auf) beseitigt das Vergessen als Fehlerquelle.

**Verworfen:**
- *Middleware für alle unsicheren Methoden.* Wirkt global, ist aber nicht mehr
  routenweise ausnehmbar, ohne Pfade als Zeichenketten zu vergleichen. Die REST-API
  braucht eine Ausnahme, und eine Pfadliste in einer Middleware verfällt still.
- *Prüfung je Route wie bisher, nur in eine Hilfsfunktion ausgelagert.* Genau das
  Vergessen, das die Auflage beseitigen wollte.

**Ausnahme, bewusst:** Die REST-API (`/api/…`) hängt nicht am CSRF-Schutz. Sie wertet
ausschließlich den `Authorization`-Header aus und niemals ein Cookie — ohne
Cookie-Authentifizierung gibt es keinen CSRF-Weg. Damit diese Ausnahme nicht still
ungültig wird, hält `test_api_nimmt_kein_sitzungscookie_an` genau das nach.

---

## 2026-08-29 — Zwei Wächtertests liefen ins Leere (Fund, keine Wahl)

Kein Ermessen, aber festhaltenswert, weil es die dritte Fehlerklasse derselben Art ist.

Seit FastAPI 0.141 legt `include_router()` keine flache Routenliste mehr an: In
`app.routes` steht ein `_IncludedRouter`, der den ursprünglichen Router unter
`original_router` trägt. Beide Wächter — `test_endpunktabdeckung.py` und der neue
`test_csrf.py` — filterten `app.routes` auf `APIRoute` und fanden dadurch nur noch
`/healthz`. Sie waren grün, weil sie nichts mehr prüften.

Behoben durch `tests/hilfen.alle_api_routen()`, das über `original_router` absteigt, und
durch eine Gegenprobe für beide Wächter (Schutz entfernen beziehungsweise ungetestete Route
ergänzen — beide werden rot).

Dabei fiel ein zweiter Mangel auf: Die Endpunktabdeckung wertete ihre Mitschrift **mitten
im Lauf** aus, nach Dateinamen sortiert vor `test_rauchtest.py`. Sie hätte alles danach
Aufgerufene als ungeprüft gemeldet. `pytest_collection_modifyitems` zieht sie jetzt ans
Ende des Laufs.

**Lehre, die zur bestehenden passt:** Ein Wächtertest braucht seine Gegenprobe nicht nur
beim Schreiben, sondern nach jedem Versionssprung der Bibliothek, deren Interna er abfragt.
