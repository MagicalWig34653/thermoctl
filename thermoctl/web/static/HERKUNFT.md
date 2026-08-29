# Herkunft der mitgelieferten Dateien

Dieses Verzeichnis enthaelt fremden, unveraendert uebernommenen Code. Er wird lokal
ausgeliefert (ueber `StaticFiles` in `thermoctl/app.py`), nicht ueber ein CDN --
`thermoctl` soll auch ohne Internetzugang im Heimnetz benutzbar bleiben, und ein
CDN-Aufruf wuerde jedem Betreiber-Netzwerk gegenueber Dritten verraten, wann jemand
die Heizungssteuerung oeffnet.

## Bootstrap 5.3.3

Quelle: https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/
Lizenz: MIT (https://github.com/twbs/bootstrap/blob/main/LICENSE)

| Datei | SHA-384 |
|---|---|
| `vendor/bootstrap/bootstrap.min.css` | `sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH` |
| `vendor/bootstrap/bootstrap.bundle.min.js` | `sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz` |

Das Bundle-JS enthaelt Popper.js, wird fuer Dropdowns/Toasts/Tooltips mitgeliefert,
auch wenn die aktuellen Templates es noch nicht ausnutzen.

## HTMX 2.0.4

Quelle: https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js
Lizenz: BSD 2-Clause (https://github.com/bigskysoftware/htmx/blob/master/LICENSE)

| Datei | SHA-384 |
|---|---|
| `vendor/htmx/htmx.min.js` | `sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+` |

## Swagger UI 5.17.14

Quelle: https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/
Lizenz: Apache 2.0 (https://github.com/swagger-api/swagger-ui/blob/master/LICENSE)

| Datei | SHA-384 |
|---|---|
| `vendor/swagger-ui/swagger-ui.css` | `sha384-wxLW6kwyHktdDGr6Pv1zgm/VGJh99lfUbzSn6HNHBENZlCN7W602k9VkGdxuFvPn` |
| `vendor/swagger-ui/swagger-ui-bundle.js` | `sha384-wmyclcVGX/WhUkdkATwhaK1X1JtiNrr2EoYJ+diV3vj4v6OC5yCeSu+yW13SYJep` |

Fuer `/docs`. FastAPI liefert diese Oberflaeche von sich aus mit, zieht die Dateien aber
aus einem CDN und das Symbol sogar von `fastapi.tiangolo.com` — deshalb ist die
mitgelieferte Fassung abgeschaltet (`docs_url=None` in `thermoctl/app.py`) und durch eine
eigene Route ersetzt, die auf diese Dateien zeigt.

Das Symbol `static/favicon.svg` ist **kein** Fremdcode, sondern selbst gezeichnet: ein
Thermometer. Es liegt deshalb neben diesem Verzeichnis und nicht darin. Sonst haette die
Seite als einziges Element noch am Netz gehangen — und ohne eigenes Symbol fragte jeder
Browser bei jedem Seitenaufruf vergeblich nach `/favicon.ico`.

ReDoc (`/redoc`) ist ersatzlos abgeschaltet: dasselbe CDN-Problem, und eine zweite
Lesefassung derselben Beschreibung ist den Ballast nicht wert.

## Pflege

Beim Aktualisieren einer dieser Dateien: neue Version laden, Hash in dieser Tabelle
nachfuehren, Version im Commit nennen. Keine dieser Dateien wird von Ruff oder mypy
geprueft -- sie sind fremder, unveraenderter Code, kein Projektquelltext.
