# Herkunft der mitgelieferten Dateien

Dieses Verzeichnis enthält fremden Code (lokale Änderungen sind unten ausgewiesen). Er wird lokal
ausgeliefert (über `StaticFiles` in `thermoctl/app.py`), nicht über ein CDN --
`thermoctl` soll auch ohne Internetzugang im Heimnetz benutzbar bleiben, und ein
CDN-Aufruf würde jedem Betreiber-Netzwerk gegenüber Dritten verraten, wann jemand
die Heizungssteuerung öffnet.

Die npm-Abhängigkeiten der neuen Svelte-Oberfläche (Quelle `frontend/`, Ausgabe
`static/app/`) stehen mit Version und Lizenz in `frontend/HERKUNFT.md`. Hier liegt
als Fremdcode nur noch Swagger UI (für `/docs`); Bootstrap, HTMX und Mermaid der früheren
Jinja/HTMX-Oberfläche sind mit deren Ablösung entfernt.

## Swagger UI 5.17.14

Quelle: https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/
Lizenz: Apache 2.0 (https://github.com/swagger-api/swagger-ui/blob/master/LICENSE)

| Datei | SHA-384 |
|---|---|
| `vendor/swagger-ui/swagger-ui.css` | `sha384-wxLW6kwyHktdDGr6Pv1zgm/VGJh99lfUbzSn6HNHBENZlCN7W602k9VkGdxuFvPn` |
| `vendor/swagger-ui/swagger-ui-bundle.js` | `sha384-wmyclcVGX/WhUkdkATwhaK1X1JtiNrr2EoYJ+diV3vj4v6OC5yCeSu+yW13SYJep` |

Für `/docs`. FastAPI liefert diese Oberfläche von sich aus mit, zieht die Dateien aber
aus einem CDN und das Symbol sogar von `fastapi.tiangolo.com` — deshalb ist die
mitgelieferte Fassung abgeschaltet (`docs_url=None` in `thermoctl/app.py`) und durch eine
eigene Route ersetzt, die auf diese Dateien zeigt.

Das Symbol `static/favicon.svg` ist **kein** Fremdcode, sondern selbst gezeichnet: ein
Thermometer. Es liegt deshalb neben diesem Verzeichnis und nicht darin. Sonst hätte die
Seite als einziges Element noch am Netz gehangen — und ohne eigenes Symbol fragte jeder
Browser bei jedem Seitenaufruf vergeblich nach `/favicon.ico`.

ReDoc (`/redoc`) ist ersatzlos abgeschaltet: dasselbe CDN-Problem, und eine zweite
Lesefassung derselben Beschreibung ist den Ballast nicht wert.

## Pflege

Beim Aktualisieren einer dieser Dateien: neue Version laden, Hash in dieser Tabelle
nachführen, Version im Commit nennen. Keine dieser Dateien wird von Ruff oder mypy
geprüft -- sie sind Fremdcode, kein Projektquelltext.

## Lokale Änderungen für die Auslieferung

Bei den folgenden Dateien wurde ausschließlich der `sourceMappingURL`-Kommentar
entfernt. Die referenzierten Maps werden nicht mitgeliefert; Entwicklerwerkzeuge
fordern damit keine fehlenden Dateien mehr an. Lizenzhinweise bleiben erhalten.
Die Tabellen oben dokumentieren die Originaldateien der genannten Quellen; die
folgenden SHA-384-Werte gelten für die tatsächlich ausgelieferten Fassungen.
Beim Aktualisieren diesen Schritt wiederholen und beide Hash-Tabellen nachführen.

| Geänderte Datei | SHA-384 der lokalen Fassung |
|---|---|
| `vendor/swagger-ui/swagger-ui.css` | `sha384-ZpZakqAmqVkjVQvPHC7JrCdmocLHj570Ke/GDWRvH4HOi8I1rx8hZa2jIN6MMeNN` |
