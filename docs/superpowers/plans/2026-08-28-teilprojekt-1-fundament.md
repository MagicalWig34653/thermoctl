# Teilprojekt 1 — Fundament: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Fundament von `thermoctl` bauen — Datenmodell, Migrationen, Konfiguration ohne
Hardcoding, Benutzer mit feingranularen Rechten, Sitzungen und Tokens, Logging und Audit,
Container und CI. Keine Geräteanbindung, keine Regellogik.

**Architecture:** Ein FastAPI-Dienst, ein Container. Die Domänenlogik unter `thermoctl/domain/`
kennt keinen Adapter; HTMX-Views (`web/`) und REST (`api/`) sind dünne Schichten darüber.
Persistenz über SQLAlchemy 2.0 mit Alembic, lauffähig auf SQLite **und** MariaDB.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pydantic-settings, argon2-cffi,
Jinja2, HTMX, Bootstrap 5, pytest, Ruff, mypy, Docker, GitHub Actions.

**Grundlage:** [TP1-Spezifikation](../specs/2026-08-28-teilprojekt-1-fundament-design.md).
Bei Widerspruch gilt die Spezifikation; dieser Plan setzt sie nur um.

## Global Constraints

Diese Bedingungen gelten für **jede** Aufgabe, auch wenn sie dort nicht wiederholt werden.

- **Python 3.12.** Keine Abhängigkeit, die nur unter einer anderen Version läuft.
- **Zwei Datenbanken.** Jede Änderung muss unter SQLite *und* MariaDB laufen. Kein `ENUM`,
  kein `SET`, keine JSON-Spalte als Datenmodell, keine partiellen Indizes, keine
  datenbankspezifischen Funktionen.
- **`group` ist reserviert.** Die Gruppentabelle heißt `access_group`, die Zuordnung
  `user_access_group`.
- **Jede `String`-Spalte bekommt eine ausdrückliche Länge.** SQLite ignoriert sie, MariaDB nicht.
- **Alle Zeitstempel in UTC, zeitzonenlos** (`DateTime(timezone=False)`), erzeugt über
  `datetime.now(timezone.utc).replace(tzinfo=None)`. Einzige Ausnahme: `schedule_point`
  speichert lokale Zeit als `minute_of_day`.
- **Keine Secrets im Repo** — auch nicht als Vorgabewert, auch nicht in Beispielen, auch nicht
  in Logs. `.env.example` enthält Namen und Erläuterungen, niemals Werte.
- **Jede Schemaänderung ist eine Alembic-Migration.** Kein `create_all` außerhalb der Tests.
- **Jede Aufgabe endet mit grüner CI und einem Commit.** Commit-Nachrichten auf Deutsch,
  Präfix `feat:`, `test:`, `chore:`, `fix:`, `docs:`.
- **`docs/STATUS.md` wird im selben Commit nachgezogen**, sobald eine Aufgabe abgeschlossen ist.

## Verteilung auf Agents

Vorgabe aus CLAUDE.md: rund 60 % an Codex, Rest an Claude-Code-Agents auf Sonnet, Opus nur
nach ausdrücklicher Genehmigung. Ein Worktree je Aufgabe, eigener Branch, kreuzweises Review.

| Agent | Aufgaben | Anteil |
|---|---|---|
| Codex | 1, 2, 4, 5, 7, 8, 9, 10, 11, 16, 17, 20, 21, 22 | 14 von 22 ≈ 64 % |
| Claude (Sonnet) | 3, 6, 12, 13, 14, 15, 18, 19 | 8 von 22 |

Bei Claude bleiben Logging-Maskierung, Datenbankgrundlage, alles rund um Identität und Rechte
sowie Anmeldung und Einrichtungsassistent — die Teile, in denen ein Fehler still zu einem
Sicherheitsloch wird statt zu einem roten Test. Aufgaben 8 bis 11 sind untereinander
unabhängig und können parallel laufen, ebenso 16 und 17.

## Dateistruktur

| Datei | Verantwortung |
|---|---|
| `pyproject.toml` | Abhängigkeiten, Ruff-, mypy- und pytest-Konfiguration |
| `thermoctl/config.py` | Umgebungseinstellungen, `get_settings()` |
| `thermoctl/logging.py` | JSON-Formatter, Anfrage-ID, Maskierung |
| `thermoctl/app.py` | FastAPI-Instanz, Middleware, Router-Einbindung |
| `thermoctl/db/engine.py` | Engine und Session-Factory |
| `thermoctl/db/base.py` | `Base`, Namenskonvention, Zeitstempel-Mixin |
| `thermoctl/db/models/lookup.py` | Nachschlagetabellen |
| `thermoctl/db/models/zone.py` | Zone, Modi, Sollwerte |
| `thermoctl/db/models/schedule.py` | Schaltpunkte |
| `thermoctl/db/models/override.py` | Übersteuerungen |
| `thermoctl/db/models/device.py` | Geräte, Fähigkeiten, Zuordnungen |
| `thermoctl/db/models/identity.py` | Benutzer, Gruppen, Berechtigungen |
| `thermoctl/db/models/credential.py` | Sitzungen, Tokens, Setup-Token |
| `thermoctl/db/models/operations.py` | `setting`, `audit_event` |
| `thermoctl/domain/principal.py` | `Principal` — Benutzer oder Token samt Rechten |
| `thermoctl/domain/authz.py` | `require()`, `visible_zones()` |
| `thermoctl/domain/schedule.py` | geltender Schaltpunkt, Sollwertauflösung |
| `thermoctl/domain/zone_settings.py` | Zonenwert mit Rückfall auf globalen Standard |
| `thermoctl/auth/passwords.py` | Argon2id |
| `thermoctl/auth/secrets.py` | Erzeugung und Hashing von Sitzungs- und Token-Geheimnissen |
| `thermoctl/auth/sessions.py` | Sitzungen anlegen, prüfen, widerrufen |
| `thermoctl/auth/tokens.py` | API-Tokens ausstellen, prüfen, widerrufen |
| `thermoctl/auth/dependencies.py` | FastAPI-Abhängigkeiten, die einen `Principal` liefern |
| `thermoctl/auth/csrf.py` | CSRF-Token erzeugen und prüfen |
| `thermoctl/web/` | Jinja-Templates und HTMX-Views |
| `thermoctl/api/` | REST-Endpunkte |
| `thermoctl/audit.py` | `record()` — schreibt `audit_event` |
| `migrations/` | Alembic |
| `docker/Dockerfile` | mehrstufiger Build, Nicht-root |
| `.github/workflows/ci.yml` | Ruff, mypy, pytest gegen beide Datenbanken |
| `.github/workflows/docker.yml` | Image-Bau, GHCR |

---

### Task 1: Projektgerüst und Werkzeuge — *Codex*

**Files:**
- Create: `pyproject.toml`, `thermoctl/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `.env.example`

**Interfaces:**
- Consumes: nichts
- Produces: importierbares Paket `thermoctl` mit `__version__: str`; `pytest`, `ruff`, `mypy` laufen

- [ ] **Step 1: `pyproject.toml` anlegen**

```toml
[project]
name = "thermoctl"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14",
    "pydantic-settings>=2.6",
    "argon2-cffi>=23.1",
    "jinja2>=3.1",
    "python-multipart>=0.0.17",
    "pymysql>=1.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "httpx>=0.28", "ruff>=0.8", "mypy>=1.13"]

[project.scripts]
thermoctl = "thermoctl.cli:main"

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["thermoctl*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S"]
ignore = ["S101"]  # assert ist in Tests erwünscht

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S105", "S106"]  # Testpasswörter im Klartext sind hier in Ordnung

[tool.mypy]
python_version = "3.12"
strict = true
plugins = []

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Paket und Test anlegen**

`thermoctl/__init__.py`:

```python
__version__ = "0.1.0"
```

`tests/test_paket.py`:

```python
import thermoctl


def test_paket_hat_version() -> None:
    assert thermoctl.__version__
```

- [ ] **Step 3: `.env.example` anlegen — ohne jeden Wert**

```bash
# Pflichtangaben. Ohne sie startet der Dienst nicht.
# Verbindungszeichenfolge, z. B. sqlite:///./data/thermoctl.db
#                          oder mysql+pymysql://benutzer:passwort@host:3306/thermoctl
THERMOCTL_DATABASE_URL=
# Zufälliger Schlüssel, mindestens 32 Zeichen. Erzeugen: python -c "import secrets; print(secrets.token_urlsafe(48))"
THERMOCTL_SECRET_KEY=

# Optional, mit Vorgabewerten
THERMOCTL_BIND_HOST=0.0.0.0
THERMOCTL_BIND_PORT=8000
THERMOCTL_LOG_LEVEL=INFO
THERMOCTL_LOG_FORMAT=json
# Nur einschalten, wenn der Dienst hinter TLS läuft
THERMOCTL_SECURE_COOKIES=false
```

- [ ] **Step 4: Installieren und alle drei Werkzeuge laufen lassen**

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/mypy thermoctl && .venv/bin/pytest
```

Erwartet: Ruff ohne Befund, mypy ohne Fehler, ein bestandener Test.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml thermoctl tests .env.example
git commit -m "chore: Projektgeruest, Werkzeuge und Beispielumgebung"
```

---

### Task 2: Konfiguration aus der Umgebung — *Codex*

**Files:**
- Create: `thermoctl/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nichts
- Produces: `Settings` (pydantic-settings) mit den Feldern `database_url: str`,
  `secret_key: SecretStr`, `bind_host: str`, `bind_port: int`, `log_level: str`,
  `log_format: str`, `secure_cookies: bool`; `get_settings() -> Settings` (zwischengespeichert
  über `functools.lru_cache`); `Settings.sanitized_database_url() -> str`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from thermoctl.config import Settings


def test_pflichtfelder_fehlen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THERMOCTL_DATABASE_URL", raising=False)
    monkeypatch.delenv("THERMOCTL_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_zu_kurzer_schluessel_wird_abgewiesen() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite://", secret_key="zu-kurz")


def test_werte_aus_der_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite:///./x.db")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    s = Settings(_env_file=None)
    assert s.database_url == "sqlite:///./x.db"
    assert s.bind_port == 8000
    assert s.secure_cookies is False


def test_schluessel_erscheint_nicht_in_der_darstellung() -> None:
    s = Settings(_env_file=None, database_url="sqlite://", secret_key="b" * 32)
    assert "b" * 32 not in repr(s)
    assert "b" * 32 not in str(s.model_dump())


def test_datenbank_url_ohne_zugangsdaten() -> None:
    s = Settings(
        _env_file=None,
        database_url="mysql+pymysql://nutzer:geheim@host:3306/thermoctl",
        secret_key="c" * 32,
    )
    bereinigt = s.sanitized_database_url()
    assert "geheim" not in bereinigt
    assert "host:3306/thermoctl" in bereinigt
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_config.py -v`
Erwartet: FAIL, `ModuleNotFoundError: No module named 'thermoctl.config'`

- [ ] **Step 3: `thermoctl/config.py` schreiben**

```python
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Einstellungen aus Umgebung oder .env.

    Hier steht ausschliesslich, was Secret ist oder vor der Datenbank gebraucht wird.
    Alles Fachliche kommt aus der Tabelle `setting`.
    """

    model_config = SettingsConfigDict(
        env_prefix="THERMOCTL_", env_file=".env", extra="ignore"
    )

    database_url: str
    secret_key: SecretStr = Field(min_length=32)
    bind_host: str = "0.0.0.0"  # noqa: S104 — im Container beabsichtigt
    bind_port: int = 8000
    log_level: str = "INFO"
    log_format: str = "json"
    secure_cookies: bool = False

    def sanitized_database_url(self) -> str:
        """Die Verbindungszeichenfolge ohne Zugangsdaten — fuer Logausgaben."""
        return make_url(self.database_url).render_as_string(hide_password=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Es gibt bewusst **keinen Vorgabewert** für `database_url` und `secret_key`. Ein
Vorgabe-Secret wäre genau der Fallback-Wert, den Grundsatz 2 aus CLAUDE.md verbietet.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_config.py -v`
Erwartet: 5 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/config.py tests/test_config.py
git commit -m "feat: Konfiguration aus der Umgebung ohne Vorgabe-Secrets"
```

---

### Task 3: Strukturiertes Logging mit Maskierung — *Claude (Sonnet)*

Sicherheitsrelevant: ein durchgerutschtes Secret im Log verletzt Grundsatz 2.

**Files:**
- Create: `thermoctl/logging.py`, `tests/test_logging.py`

**Interfaces:**
- Consumes: `thermoctl.config.Settings`
- Produces: `configure_logging(settings: Settings) -> None`;
  `request_id_var: ContextVar[str | None]`; `JsonFormatter(logging.Formatter)`;
  `mask(value: object) -> object`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_logging.py`:

```python
import json
import logging

from thermoctl.logging import JsonFormatter, mask, request_id_var


def test_ausgabe_ist_gueltiges_json() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    daten = json.loads(JsonFormatter().format(satz))
    assert daten["message"] == "hallo"
    assert daten["level"] == "INFO"
    assert "timestamp" in daten


def test_anfrage_id_landet_im_datensatz() -> None:
    marke = request_id_var.set("abc123")
    try:
        satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
        daten = json.loads(JsonFormatter().format(satz))
        assert daten["request_id"] == "abc123"
    finally:
        request_id_var.reset(marke)


def test_zusatzfelder_werden_uebernommen() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "hallo", None, None)
    satz.zone = "Wohnzimmer"  # type: ignore[attr-defined]
    daten = json.loads(JsonFormatter().format(satz))
    assert daten["zone"] == "Wohnzimmer"


def test_maskierung_greift_bei_bekannten_schluesseln() -> None:
    roh = {"username": "lino", "password": "geheim", "token": "tctl_x_y"}
    assert mask(roh) == {"username": "lino", "password": "***", "token": "***"}


def test_maskierung_wirkt_verschachtelt_und_in_listen() -> None:
    roh = {"aussen": {"secret_key": "s"}, "liste": [{"cookie": "c"}]}
    ergebnis = mask(roh)
    assert ergebnis == {"aussen": {"secret_key": "***"}, "liste": [{"cookie": "***"}]}


def test_maskierung_ist_unabhaengig_von_gross_kleinschreibung() -> None:
    assert mask({"Authorization": "Bearer x"}) == {"Authorization": "***"}


def test_maskierte_felder_erscheinen_nicht_in_der_ausgabe() -> None:
    satz = logging.LogRecord("t", logging.INFO, "p", 1, "start", None, None)
    satz.config = {"secret_key": "streng-geheim"}  # type: ignore[attr-defined]
    text = JsonFormatter().format(satz)
    assert "streng-geheim" not in text
    assert "***" in text
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_logging.py -v`
Erwartet: FAIL, `ModuleNotFoundError: No module named 'thermoctl.logging'`

- [ ] **Step 3: `thermoctl/logging.py` schreiben**

```python
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from thermoctl.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SENSIBLE_SCHLUESSEL = frozenset(
    {
        "password",
        "passwort",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "token_hash",
        "api_token",
        "authorization",
        "cookie",
        "set-cookie",
        "session",
    }
)

_STANDARDFELDER = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def mask(value: object) -> object:
    """Ersetzt Werte unter bekannten Schluesseln durch '***'.

    Rekursiv ueber Abbildungen und Sequenzen. Der Vergleich ist unabhaengig von
    Gross- und Kleinschreibung, weil HTTP-Kopfzeilen beliebig geschrieben ankommen.
    """
    if isinstance(value, dict):
        return {
            k: "***" if str(k).lower() in SENSIBLE_SCHLUESSEL else mask(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        daten: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        anfrage_id = request_id_var.get()
        if anfrage_id is not None:
            daten["request_id"] = anfrage_id
        for schluessel, wert in record.__dict__.items():
            if schluessel not in _STANDARDFELDER and not schluessel.startswith("_"):
                daten[schluessel] = wert
        if record.exc_info:
            daten["exception"] = self.formatException(record.exc_info)
        return json.dumps(mask(daten), ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.addHandler(handler)
    wurzel.setLevel(settings.log_level.upper())
```

Die Maskierung läuft im Formatter und damit an der letzten Stelle vor der Ausgabe. Ein
Aufrufer, der versehentlich ein Passwort mitgibt, kann sie so nicht umgehen.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_logging.py -v`
Erwartet: 6 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/logging.py tests/test_logging.py
git commit -m "feat: JSON-Logging mit Anfrage-ID und Maskierung sensibler Felder"
```

---

### Task 4: FastAPI-Rumpfdienst mit Anfrage-ID — *Codex*

**Files:**
- Create: `thermoctl/app.py`, `thermoctl/cli.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `get_settings()`, `configure_logging()`, `request_id_var`
- Produces: `create_app() -> FastAPI`; Endpunkt `GET /healthz` → `{"status": "ok", "version": str}`;
  Antwortkopf `X-Request-ID` bei jeder Antwort; `main() -> None` als Konsolenbefehl

- [ ] **Step 1: Die Tests schreiben**

`tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

from thermoctl.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", "a" * 32)
    from thermoctl.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def test_healthz_antwortet(client: TestClient) -> None:
    antwort = client.get("/healthz")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "ok"


def test_jede_antwort_traegt_eine_anfrage_id(client: TestClient) -> None:
    antwort = client.get("/healthz")
    assert antwort.headers["X-Request-ID"]


def test_mitgegebene_anfrage_id_wird_uebernommen(client: TestClient) -> None:
    antwort = client.get("/healthz", headers={"X-Request-ID": "vorgegeben"})
    assert antwort.headers["X-Request-ID"] == "vorgegeben"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_app.py -v`
Erwartet: FAIL, `ModuleNotFoundError: No module named 'thermoctl.app'`

- [ ] **Step 3: `thermoctl/app.py` schreiben**

```python
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

import thermoctl
from thermoctl.config import get_settings
from thermoctl.logging import configure_logging, request_id_var


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="thermoctl", version=thermoctl.__version__)

    @app.middleware("http")
    async def anfrage_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        kennung = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        marke = request_id_var.set(kennung)
        try:
            antwort = await call_next(request)
        finally:
            request_id_var.reset(marke)
        antwort.headers["X-Request-ID"] = kennung
        return antwort

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": thermoctl.__version__}

    return app
```

`thermoctl/cli.py`:

```python
import uvicorn

from thermoctl.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "thermoctl.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,
    )
```

`log_config=None` verhindert, dass uvicorn die eigene Logging-Einrichtung überschreibt und
die JSON-Ausgabe wieder verloren geht.

In `create_app()` werden nach `configure_logging` die wirksamen Einstellungen protokolliert —
die Datenbank-URL **ohne** Zugangsdaten:

```python
    log.info(
        "thermoctl startet",
        extra={
            "database": settings.sanitized_database_url(),
            "bind": f"{settings.bind_host}:{settings.bind_port}",
            "secure_cookies": settings.secure_cookies,
        },
    )
```

`sanitized_database_url()` aus Task 2 und die Maskierung aus Task 3 greifen hier
übereinander: Der Filter fängt ab, was die bereinigte Ausgabe übersehen hat.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_app.py -v`
Erwartet: 3 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/app.py thermoctl/cli.py tests/test_app.py
git commit -m "feat: FastAPI-Rumpfdienst mit Healthcheck und Anfrage-ID"
```

---

### Task 5: Container und CI — *Codex*

**Files:**
- Create: `docker/Dockerfile`, `docker/entrypoint.sh`, `.dockerignore`,
  `.github/workflows/ci.yml`, `.github/workflows/docker.yml`

**Interfaces:**
- Consumes: `thermoctl.cli:main`, `/healthz`
- Produces: Image mit Startbefehl `thermoctl`; zwei Workflows

- [ ] **Step 1: `docker/Dockerfile` schreiben**

```dockerfile
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml ./
COPY thermoctl ./thermoctl
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --system --create-home --uid 10001 thermoctl
COPY --from=build /install /usr/local
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data && chown thermoctl /data
WORKDIR /app
USER thermoctl
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["entrypoint.sh"]
```

`docker/entrypoint.sh`:

```bash
#!/bin/sh
set -e
alembic upgrade head
exec thermoctl
```

Die Migration läuft beim Start. Bei einer selbst gehosteten Anwendung ist eine vergessene
Migration sonst ein wiederkehrender Betriebsfehler.

`.dockerignore`:

```
.git
.venv
tests
docs
*.db
__pycache__
.pytest_cache
.ruff_cache
.mypy_cache
```

**Hinweis:** `migrations/` und `alembic.ini` entstehen erst in Task 6. Bis dahin schlägt der
Image-Bau fehl — deshalb legt dieser Schritt beide bereits als leeres Gerüst an
(`alembic.ini` mit `script_location = migrations`, `migrations/versions/.gitkeep`), damit
die CI von Beginn an grün laufen kann.

- [ ] **Step 2: `.github/workflows/ci.yml` schreiben**

```yaml
name: CI
on:
  push:
  pull_request:

jobs:
  pruefen:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        datenbank: [sqlite, mariadb]
    services:
      mariadb:
        image: mariadb:11
        env:
          MARIADB_ROOT_PASSWORD: pruefen
          MARIADB_DATABASE: thermoctl_test
        ports: ["3306:3306"]
        options: >-
          --health-cmd="healthcheck.sh --connect --innodb_initialized"
          --health-interval=5s --health-timeout=5s --health-retries=20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - name: Verbindungszeichenfolge setzen
        run: |
          if [ "${{ matrix.datenbank }}" = "sqlite" ]; then
            echo "THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db" >> "$GITHUB_ENV"
          else
            echo "THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test" >> "$GITHUB_ENV"
          fi
      - run: ruff check .
      - run: mypy thermoctl
      - run: pytest -v
```

Die MariaDB läuft in beiden Matrixzweigen mit; das kostet wenige Sekunden und erspart eine
zweite, fast identische Jobdefinition.

- [ ] **Step 3: `.github/workflows/docker.yml` schreiben**

```yaml
name: Docker
on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  bauen:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: marken
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=sha,prefix=sha-,enable=${{ github.ref == 'refs/heads/main' }}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
          flavor: latest=false
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile
          push: true
          tags: ${{ steps.marken.outputs.tags }}
          labels: ${{ steps.marken.outputs.labels }}
      - name: latest nur bei einem Tag setzen
        if: startsWith(github.ref, 'refs/tags/v')
        run: |
          docker buildx imagetools create \
            --tag ghcr.io/${{ github.repository }}:latest \
            ghcr.io/${{ github.repository }}:${GITHUB_REF_NAME#v}
```

`flavor: latest=false` schaltet die automatische `latest`-Marke ab; sie wird ausschließlich
im letzten Schritt gesetzt, und der läuft nur bei einem Git-Tag. Ein Push auf `main` erzeugt
nur `sha-<kurzer Commit>` — ein Testimage, das gezielt gezogen werden kann, aber niemanden
versehentlich erwischt.

- [ ] **Step 4: Image örtlich bauen und starten**

```bash
docker build -f docker/Dockerfile -t thermoctl:test .
docker run --rm -e THERMOCTL_DATABASE_URL=sqlite:////data/t.db \
  -e THERMOCTL_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))") \
  -p 8000:8000 thermoctl:test
curl -fsS localhost:8000/healthz
```

Erwartet: `{"status":"ok","version":"0.1.0"}`, und der Prozess läuft nicht als root
(`docker exec <id> id -u` ergibt `10001`).

- [ ] **Step 5: Commit und CI beobachten**

```bash
git add docker .dockerignore .github alembic.ini migrations
git commit -m "chore: Container und CI, latest-Image nur aus einem Tag"
git push -u origin main
gh run watch
```

Erwartet: beide Matrixzweige grün, `docker.yml` erzeugt eine `sha-`-Marke und **kein**
`latest`.

---

### Task 6: Datenbankgrundlage, Alembic, Testaufbau gegen beide Datenbanken — *Claude (Sonnet)*

Die kniffligste Aufgabe des Teilprojekts: Alles Folgende hängt daran, und die Unterschiede
zwischen SQLite und MariaDB müssen hier einmal richtig abgefangen werden.

**Files:**
- Create: `thermoctl/db/__init__.py`, `thermoctl/db/base.py`, `thermoctl/db/engine.py`,
  `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `tests/conftest.py`,
  `tests/test_migrations.py`
- Modify: `docker/entrypoint.sh` (bereits vorhanden)

**Interfaces:**
- Consumes: `get_settings()`
- Produces: `Base` (DeclarativeBase mit Namenskonvention); `TimestampMixin` mit
  `created_at`/`updated_at`; `utcnow() -> datetime`; `create_engine_from_settings(settings) -> Engine`;
  `session_factory(engine) -> sessionmaker[Session]`; pytest-Fixtures `engine`, `session`

- [ ] **Step 1: `thermoctl/db/base.py` schreiben**

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Ohne diese Konvention vergibt SQLAlchemy anonyme Constraint-Namen. Alembic kann sie
# dann unter SQLite nicht wieder aufloesen, weil dort jede Aenderung als Tabellenkopie
# laeuft (batch mode). Das faellt erst bei der zweiten Migration auf.
NAMENSKONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """UTC ohne Zonenangabe — MariaDB DATETIME traegt keine."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMENSKONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
```

- [ ] **Step 2: `thermoctl/db/engine.py` schreiben**

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from thermoctl.config import Settings


def create_engine_from_settings(settings: Settings) -> Engine:
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fremdschluessel_einschalten(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            # SQLite prueft Fremdschluessel sonst gar nicht. Ohne das laufen Tests
            # gruen, die unter MariaDB an einer Verletzung scheitern wuerden.
            zeiger = dbapi_connection.cursor()
            zeiger.execute("PRAGMA foreign_keys=ON")
            zeiger.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    sitzung = factory()
    try:
        yield sitzung
        sitzung.commit()
    except Exception:
        sitzung.rollback()
        raise
    finally:
        sitzung.close()
```

- [ ] **Step 3: Alembic einrichten**

```bash
.venv/bin/alembic init -t generic migrations
```

Dann `alembic.ini` auf `script_location = migrations` setzen und die Zeile
`sqlalchemy.url = ...` **entfernen** — die URL kommt aus der Umgebung, sie darf nicht in
einer eingecheckten Datei stehen.

`migrations/env.py` (die erzeugte Fassung ersetzen):

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from thermoctl.config import get_settings
from thermoctl.db.base import Base
from thermoctl.db import models  # noqa: F401 — laedt alle Modelle in die Metadaten

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    verbindungswerk = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with verbindungswerk.connect() as verbindung:
        context.configure(
            connection=verbindung,
            target_metadata=target_metadata,
            # Pflicht fuer SQLite: dort gibt es kein ALTER TABLE fuer Constraints,
            # Alembic baut die Tabelle stattdessen neu auf.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`thermoctl/db/models/__init__.py` legt zunächst nur an, was es gibt; jede spätere Aufgabe
ergänzt ihren Import hier.

- [ ] **Step 4: `tests/conftest.py` schreiben**

```python
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from thermoctl.config import Settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings, session_factory

TEST_DATABASE_URL = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "sqlite:///./test.db")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, secret_key="t" * 32
    )


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    werk = create_engine_from_settings(settings)
    Base.metadata.drop_all(werk)
    Base.metadata.create_all(werk)
    yield werk
    Base.metadata.drop_all(werk)
    werk.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Jeder Test laeuft in einer Transaktion, die anschliessend zurueckgerollt wird.

    Dadurch teilen sich alle Tests ein Schema, ohne einander zu beeinflussen — unter
    MariaDB waere ein Neuaufbau je Test sonst spuerbar langsam.
    """
    verbindung = engine.connect()
    transaktion = verbindung.begin()
    sitzung = session_factory(engine)(bind=verbindung)
    try:
        yield sitzung
    finally:
        sitzung.close()
        transaktion.rollback()
        verbindung.close()
```

- [ ] **Step 5: `tests/test_migrations.py` schreiben**

```python
import subprocess

import pytest
from sqlalchemy import Engine, inspect

from tests.conftest import TEST_DATABASE_URL


def _alembic(*argumente: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *argumente],
        env={"THERMOCTL_DATABASE_URL": TEST_DATABASE_URL, "THERMOCTL_SECRET_KEY": "t" * 32,
             "PATH": "/usr/bin:/bin:/usr/local/bin:.venv/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.migration
def test_migration_vorwaerts_und_rueckwaerts() -> None:
    hoch = _alembic("upgrade", "head")
    assert hoch.returncode == 0, hoch.stderr
    runter = _alembic("downgrade", "base")
    assert runter.returncode == 0, runter.stderr
    wieder_hoch = _alembic("upgrade", "head")
    assert wieder_hoch.returncode == 0, wieder_hoch.stderr


@pytest.mark.migration
def test_modelle_und_migrationen_stimmen_ueberein() -> None:
    """`alembic check` meldet, wenn ein Modell ohne Migration geaendert wurde."""
    _alembic("upgrade", "head")
    ergebnis = _alembic("check")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


def test_fremdschluessel_werden_unter_sqlite_geprueft(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("nur fuer SQLite sinnvoll")
    with engine.connect() as verbindung:
        assert verbindung.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
```

`pyproject.toml` um die Markierung ergänzen:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = ["migration: laeuft Alembic als Unterprozess"]
```

- [ ] **Step 6: Gegen beide Datenbanken laufen lassen**

```bash
THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db .venv/bin/pytest -v
docker run -d --name mariadb-test -e MARIADB_ROOT_PASSWORD=pruefen \
  -e MARIADB_DATABASE=thermoctl_test -p 3306:3306 mariadb:11
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test \
  .venv/bin/pytest -v
```

Erwartet: beide Läufe bestanden.

- [ ] **Step 7: Commit**

```bash
git add thermoctl/db alembic.ini migrations tests pyproject.toml
git commit -m "feat: Datenbankgrundlage, Alembic und Testaufbau gegen SQLite und MariaDB"
```

---

### Task 7: Nachschlagetabellen — *Codex*

Ersetzen die `ENUM`- und `SET`-Spalten des Altsystems (Fallstrick 3).

**Files:**
- Create: `thermoctl/db/models/lookup.py`, `migrations/versions/0001_nachschlagetabellen.py`,
  `tests/test_lookup.py`
- Modify: `thermoctl/db/models/__init__.py`

**Interfaces:**
- Consumes: `Base`
- Produces: Modelle `OperatingMode`, `Integration`, `DeviceCapability`, `DeviceRole`,
  `ActorSource`, `Permission` — jeweils mit `id: int`, `code: str`, `label`/`description: str`;
  `Permission` zusätzlich `is_zone_scoped: bool`. Konstanten `PERMISSIONS: list[tuple[str, str, bool]]`

- [ ] **Step 1: Den Test schreiben**

`tests/test_lookup.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode, Permission


def test_code_ist_eindeutig(session: Session) -> None:
    session.add(OperatingMode(code="auto", label="Automatik"))
    session.flush()
    session.add(OperatingMode(code="auto", label="Nochmal"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_berechtigung_kennt_ihren_geltungsbereich(session: Session) -> None:
    p = Permission(code="zone.read", description="Zonen sehen", is_zone_scoped=True)
    session.add(p)
    session.flush()
    assert p.is_zone_scoped is True
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_lookup.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/lookup.py` schreiben**

```python
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class _Nachschlage(Base):
    """Gemeinsame Form aller Nachschlagetabellen: eine Kennung, ein Code, ein Klartext."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)


class OperatingMode(_Nachschlage):
    """auto, manual, off. 'off' heisst Frostschutz, nicht stromlos."""

    __tablename__ = "operating_mode"


class Integration(_Nachschlage):
    """Wie ein Geraet erreicht wird: zigbee2mqtt, meross."""

    __tablename__ = "integration"


class DeviceCapability(_Nachschlage):
    """Was ein Geraet kann: temperature, switch, setpoint_display, contact, battery."""

    __tablename__ = "device_capability"


class DeviceRole(_Nachschlage):
    """Wozu ein Geraet in einer Zone dient: actuator, window_contact, controller."""

    __tablename__ = "device_role"


class ActorSource(_Nachschlage):
    """Ueber welchen Adapter etwas geschah: web, api, mcp, cli, system."""

    __tablename__ = "actor_source"


class Permission(Base):
    __tablename__ = "permission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    is_zone_scoped: Mapped[bool] = mapped_column(Boolean, nullable=False)


OPERATING_MODES = [("auto", "Automatik"), ("manual", "Manuell"), ("off", "Aus")]
INTEGRATIONS = [("zigbee2mqtt", "Zigbee2MQTT"), ("meross", "Meross")]
DEVICE_CAPABILITIES = [
    ("temperature", "Temperaturmessung"),
    ("switch", "Schaltausgang"),
    ("setpoint_display", "Sollwertanzeige"),
    ("contact", "Kontakt"),
    ("battery", "Batteriestand"),
]
DEVICE_ROLES = [
    ("actuator", "Aktor"),
    ("window_contact", "Fensterkontakt"),
    ("controller", "Bediengeraet"),
]
ACTOR_SOURCES = [
    ("web", "Weboberflaeche"),
    ("api", "REST-API"),
    ("mcp", "MCP"),
    ("cli", "Kommandozeile"),
    ("system", "System"),
]

# (code, beschreibung, zonenbezogen) — die Liste aus Abschnitt 2.6 der Spezifikation
PERMISSIONS: list[tuple[str, str, bool]] = [
    ("zone.read", "Zonen und ihren Zustand sehen", True),
    ("zone.manage", "Zonen anlegen, aendern, loeschen", True),
    ("setpoint.write", "Sollwerte je Modus aendern", True),
    ("schedule.manage", "Zeitplaene aendern", True),
    ("override.create", "Uebersteuern", True),
    ("override.cancel", "Fremde Uebersteuerung aufheben", True),
    ("device.read", "Geraete und Zuordnungen sehen", True),
    ("device.manage", "Geraete zuordnen, tauschen, entfernen", True),
    ("mode.manage", "Sollwert-Modi anlegen und aendern", False),
    ("setting.manage", "Globale Einstellungen aendern", False),
    ("user.manage", "Benutzer verwalten", False),
    ("group.manage", "Gruppen und Rechte verwalten", False),
    ("token.self", "Eigene Tokens ausstellen und widerrufen", False),
    ("token.manage", "Fremde Tokens verwalten", False),
    ("audit.read", "Audit-Protokoll einsehen", False),
]
```

- [ ] **Step 4: Migration erzeugen und die Werte einfüllen**

```bash
.venv/bin/alembic revision --autogenerate -m "Nachschlagetabellen"
```

In der erzeugten Datei nach `op.create_table(...)` ergänzen:

```python
from thermoctl.db.models.lookup import (
    ACTOR_SOURCES, DEVICE_CAPABILITIES, DEVICE_ROLES, INTEGRATIONS,
    OPERATING_MODES, PERMISSIONS,
)


def _fuellen(tabellenname: str, zeilen: list[tuple[str, str]]) -> None:
    op.bulk_insert(
        sa.table(
            tabellenname,
            sa.column("code", sa.String),
            sa.column("label", sa.String),
        ),
        [{"code": c, "label": b} for c, b in zeilen],
    )


# am Ende von upgrade():
_fuellen("operating_mode", OPERATING_MODES)
_fuellen("integration", INTEGRATIONS)
_fuellen("device_capability", DEVICE_CAPABILITIES)
_fuellen("device_role", DEVICE_ROLES)
_fuellen("actor_source", ACTOR_SOURCES)
op.bulk_insert(
    sa.table(
        "permission",
        sa.column("code", sa.String),
        sa.column("description", sa.String),
        sa.column("is_zone_scoped", sa.Boolean),
    ),
    [{"code": c, "description": d, "is_zone_scoped": z} for c, d, z in PERMISSIONS],
)
```

Berechtigungen gehören zum Code und nicht zu den Nutzdaten — deshalb kommen sie aus einer
Migration und nicht aus dem Einrichtungsassistenten.

- [ ] **Step 5: Tests und Migration prüfen**

Run: `.venv/bin/pytest tests/test_lookup.py tests/test_migrations.py -v`
Erwartet: alle bestanden, auch `alembic check`.

- [ ] **Step 6: Commit**

```bash
git add thermoctl/db/models migrations/versions tests/test_lookup.py
git commit -m "feat: Nachschlagetabellen statt ENUM und SET"
```

---

### Task 8: Zonen, Sollwert-Modi und Sollwerte — *Codex*

Ersetzt `rooms` und `thermostate` (Fallstricke 6 und 8). Läuft parallel zu 9, 10, 11.

**Files:**
- Create: `thermoctl/db/models/zone.py`, `migrations/versions/0002_zonen.py`, `tests/test_zone.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `OperatingMode`
- Produces: `Zone` mit `id`, `name`, `display_name`, `operating_mode_id`,
  `temperature_source_device_id` (erst in Task 11 als Fremdschlüssel verknüpft), `sort_order`
  und den sechs nullbaren Regelparametern; `SetpointMode` mit `id`, `code`, `name`,
  `sort_order`, `is_builtin`; `ZoneSetpoint` mit zusammengesetztem Schlüssel
  `(zone_id, setpoint_mode_id)` und `temperature_c: Decimal`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_zone.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint


def _betriebsart(session: Session) -> OperatingMode:
    art = session.query(OperatingMode).filter_by(code="auto").one_or_none()
    if art is None:
        art = OperatingMode(code="auto", label="Automatik")
        session.add(art)
        session.flush()
    return art


def test_regelparameter_sind_standardmaessig_leer(session: Session) -> None:
    zone = Zone(name="wohnzimmer", display_name="Wohnzimmer",
                operating_mode_id=_betriebsart(session).id)
    session.add(zone)
    session.flush()
    assert zone.hysteresis_k is None
    assert zone.min_on_seconds is None
    assert zone.sensor_timeout_seconds is None


def test_zonenname_ist_eindeutig(session: Session) -> None:
    art = _betriebsart(session).id
    session.add(Zone(name="bad", display_name="Bad", operating_mode_id=art))
    session.flush()
    session.add(Zone(name="bad", display_name="Bad oben", operating_mode_id=art))
    with pytest.raises(IntegrityError):
        session.flush()


def test_ein_sollwert_je_zone_und_modus(session: Session) -> None:
    zone = Zone(name="kueche", display_name="Kueche",
                operating_mode_id=_betriebsart(session).id)
    modus = SetpointMode(code="tag", name="Tag")
    session.add_all([zone, modus])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("21.0")))
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("22.0")))
    with pytest.raises(IntegrityError):
        session.flush()


def test_nachkommastelle_bleibt_erhalten(session: Session) -> None:
    zone = Zone(name="flur", display_name="Flur",
                operating_mode_id=_betriebsart(session).id)
    modus = SetpointMode(code="nacht", name="Nacht")
    session.add_all([zone, modus])
    session.flush()
    session.add(ZoneSetpoint(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("18.5")))
    session.commit()
    geladen = session.query(ZoneSetpoint).filter_by(zone_id=zone.id).one()
    assert geladen.temperature_c == Decimal("18.5")
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_zone.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/zone.py` schreiben**

```python
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, TimestampMixin


class SetpointMode(Base):
    """Frei anlegbarer Sollwert-Modus: Tag, Nacht, Frostschutz, Urlaub, …

    Welcher Modus der Frostschutz ist, steht ausschliesslich in
    `setting.frost_protection_mode_id` und nicht zusaetzlich hier — zwei Quellen fuer
    dieselbe Aussage geraten auseinander.
    """

    __tablename__ = "setpoint_mode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Zone(TimestampMixin, Base):
    """Ersetzt `rooms` und `thermostate` gemeinsam.

    Die sechs Regelparameter sind nullbar: leer heisst 'globaler Standard aus `setting`'.
    So steht jeder Wert genau einmal irgendwo.
    """

    __tablename__ = "zone"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    operating_mode_id: Mapped[int] = mapped_column(
        ForeignKey("operating_mode.id"), nullable=False
    )
    temperature_source_device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    hysteresis_k: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    min_on_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_off_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sensor_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_offset_k: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    window_resume_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ZoneSetpoint(Base):
    __tablename__ = "zone_setpoint"

    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), primary_key=True
    )
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), primary_key=True
    )
    temperature_c: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
```

`temperature_source_device_id` bleibt vorerst ein einfacher Integer; Task 11 macht daraus
einen Fremdschlüssel, sobald `device` existiert. Ein Fremdschlüssel auf eine noch fehlende
Tabelle würde die Migration hier scheitern lassen.

- [ ] **Step 4: Migration erzeugen und beide Datenbanken prüfen**

```bash
.venv/bin/alembic revision --autogenerate -m "Zonen, Sollwert-Modi und Sollwerte"
THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db .venv/bin/pytest tests/test_zone.py -v
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test \
  .venv/bin/pytest tests/test_zone.py -v
```

Erwartet: 4 Tests bestanden, unter beiden Datenbanken.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/db/models/zone.py migrations/versions tests/test_zone.py
git commit -m "feat: Zone als eine Entitaet mit Regelparametern je Zone"
```

---

### Task 9: Schaltpunkte — *Codex*

Ersetzt den JSON-Blob `temperatureTargetNightHours` (Fallstrick 1).

**Files:**
- Create: `thermoctl/db/models/schedule.py`, `migrations/versions/0003_schaltpunkte.py`,
  `tests/test_schedule_model.py`

**Interfaces:**
- Consumes: `Zone`, `SetpointMode`
- Produces: `SchedulePoint` mit `id`, `zone_id`, `weekday: int` (1–7), `minute_of_day: int`
  (0–1439), `setpoint_mode_id`; eindeutig über `(zone_id, weekday, minute_of_day)`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_schedule_model.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.schedule import SchedulePoint
from tests.hilfen import zone_anlegen, modus_anlegen


def test_zwei_punkte_zur_selben_zeit_sind_ausgeschlossen(session: Session) -> None:
    zone = zone_anlegen(session, "wohnzimmer")
    modus = modus_anlegen(session, "tag")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=modus.id))
    session.flush()
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                              setpoint_mode_id=modus.id))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("wochentag", [0, 8, -1])
def test_wochentag_ausserhalb_1_bis_7_wird_abgewiesen(session: Session, wochentag: int) -> None:
    zone = zone_anlegen(session, f"z{wochentag}")
    modus = modus_anlegen(session, f"m{wochentag}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=wochentag, minute_of_day=0,
                              setpoint_mode_id=modus.id))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("minute", [-1, 1440, 5000])
def test_minute_ausserhalb_des_tages_wird_abgewiesen(session: Session, minute: int) -> None:
    zone = zone_anlegen(session, f"zm{minute}")
    modus = modus_anlegen(session, f"mm{minute}")
    session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=minute,
                              setpoint_mode_id=modus.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dieselbe_zeit_in_zwei_zonen_ist_erlaubt(session: Session) -> None:
    modus = modus_anlegen(session, "tag2")
    for name in ("bad", "kueche"):
        zone = zone_anlegen(session, name)
        session.add(SchedulePoint(zone_id=zone.id, weekday=1, minute_of_day=360,
                                  setpoint_mode_id=modus.id))
    session.flush()
```

`tests/hilfen.py` — von hier an von mehreren Testdateien genutzt:

```python
from sqlalchemy.orm import Session

from thermoctl.db.models.lookup import OperatingMode
from thermoctl.db.models.zone import SetpointMode, Zone


def betriebsart(session: Session, code: str = "auto") -> OperatingMode:
    art = session.query(OperatingMode).filter_by(code=code).one_or_none()
    if art is None:
        art = OperatingMode(code=code, label=code)
        session.add(art)
        session.flush()
    return art


def zone_anlegen(session: Session, name: str) -> Zone:
    zone = Zone(name=name, display_name=name.capitalize(),
                operating_mode_id=betriebsart(session).id)
    session.add(zone)
    session.flush()
    return zone


def modus_anlegen(session: Session, code: str, name: str | None = None) -> SetpointMode:
    modus = SetpointMode(code=code, name=name or code.capitalize())
    session.add(modus)
    session.flush()
    return modus
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_schedule_model.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/schedule.py` schreiben**

```python
from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class SchedulePoint(Base):
    """Ein Schaltpunkt gilt bis zum naechsten — wie bei klassischen Heizungsreglern.

    Daraus folgt, dass es weder Luecken noch Ueberlappungen geben kann. `minute_of_day`
    ist ein Integer und kein TIME, weil Integer ueber SQLite und MariaDB identisch
    vergleicht und sortiert. Die Zeit ist lokale Zeit (`setting.timezone`), damit sich
    die Nachtabsenkung bei der Zeitumstellung nicht verschiebt.
    """

    __tablename__ = "schedule_point"
    __table_args__ = (
        UniqueConstraint("zone_id", "weekday", "minute_of_day", name="zeitpunkt_je_zone"),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="wochentag_1_bis_7"),
        CheckConstraint("minute_of_day BETWEEN 0 AND 1439", name="minute_im_tag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = Montag … 7 = Sonntag
    minute_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    setpoint_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
```

- [ ] **Step 4: Migration erzeugen, Tests gegen beide Datenbanken**

```bash
.venv/bin/alembic revision --autogenerate -m "Schaltpunkte"
.venv/bin/pytest tests/test_schedule_model.py tests/test_migrations.py -v
```

Erwartet: alle bestanden. Prüfen, dass die `CheckConstraint`s auch unter MariaDB greifen —
MariaDB setzt sie seit 10.2 durch, ältere Versionen ignorierten sie stillschweigend.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/db/models/schedule.py migrations/versions tests/
git commit -m "feat: Zeitplaene als Schaltpunkte statt JSON-Blob"
```

---

### Task 10: Übersteuerungen — *Codex*

**Files:**
- Create: `thermoctl/db/models/override.py`, `migrations/versions/0004_uebersteuerungen.py`,
  `tests/test_override_model.py`

**Interfaces:**
- Consumes: `Zone`, `SetpointMode`, `ActorSource`
- Produces: `ZoneOverride` mit `id`, `zone_id`, `setpoint_mode_id | None`,
  `temperature_c | None`, `starts_at`, `ends_at | None`, `cancelled_at | None`,
  `created_at`, `created_by_user_id | None`, `created_by_token_id | None`, `source_id`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_override_model.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.override import ZoneOverride
from tests.hilfen import modus_anlegen, quelle, zone_anlegen


def test_entweder_modus_oder_temperatur_aber_nicht_beides(session: Session) -> None:
    zone = zone_anlegen(session, "z1")
    modus = modus_anlegen(session, "tag")
    session.add(ZoneOverride(zone_id=zone.id, setpoint_mode_id=modus.id,
                             temperature_c=Decimal("22.0"), starts_at=utcnow(),
                             source_id=quelle(session, "web").id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_weder_modus_noch_temperatur_wird_abgewiesen(session: Session) -> None:
    zone = zone_anlegen(session, "z2")
    session.add(ZoneOverride(zone_id=zone.id, starts_at=utcnow(),
                             source_id=quelle(session, "web").id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dauerhafte_uebersteuerung_hat_kein_ende(session: Session) -> None:
    zone = zone_anlegen(session, "z3")
    ueber = ZoneOverride(zone_id=zone.id, temperature_c=Decimal("23.0"),
                         starts_at=utcnow(), ends_at=None,
                         source_id=quelle(session, "web").id)
    session.add(ueber)
    session.flush()
    assert ueber.ends_at is None
    assert ueber.cancelled_at is None


def test_uebersteuerung_bleibt_als_historie_erhalten(session: Session) -> None:
    """Aufheben loescht nicht, es setzt cancelled_at."""
    zone = zone_anlegen(session, "z4")
    ueber = ZoneOverride(zone_id=zone.id, temperature_c=Decimal("19.0"),
                         starts_at=utcnow(), source_id=quelle(session, "web").id)
    session.add(ueber)
    session.flush()
    ueber.cancelled_at = utcnow()
    session.flush()
    assert session.query(ZoneOverride).filter_by(zone_id=zone.id).count() == 1
```

In `tests/hilfen.py` ergänzen:

```python
from thermoctl.db.models.lookup import ActorSource


def quelle(session: Session, code: str = "web") -> ActorSource:
    q = session.query(ActorSource).filter_by(code=code).one_or_none()
    if q is None:
        q = ActorSource(code=code, label=code)
        session.add(q)
        session.flush()
    return q
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_override_model.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/override.py` schreiben**

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class ZoneOverride(Base):
    """Eine Uebersteuerung des Zeitplans.

    Drei Enden: bis zum naechsten Schaltpunkt, fuer eine Dauer, oder dauerhaft. In den
    ersten beiden Faellen wird `ends_at` beim Anlegen konkret ausgerechnet, nicht als
    Regel abgelegt — so steht in der Datenbank immer, wann Schluss ist, und eine spaetere
    Zeitplanaenderung verschiebt eine laufende Uebersteuerung nicht rueckwirkend.

    Zeilen werden nie geloescht; sie sind die Historie.
    """

    __tablename__ = "zone_override"
    __table_args__ = (
        CheckConstraint(
            "(setpoint_mode_id IS NULL) <> (temperature_c IS NULL)",
            name="entweder_modus_oder_temperatur",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False, index=True
    )
    setpoint_mode_id: Mapped[int | None] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=True
    )
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_token_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
```

`created_by_user_id` und `created_by_token_id` bleiben vorerst einfache Integer; Task 13
macht Fremdschlüssel daraus, sobald `user` und `api_token` existieren.

- [ ] **Step 4: Migration erzeugen, Tests gegen beide Datenbanken**

```bash
.venv/bin/alembic revision --autogenerate -m "Uebersteuerungen"
.venv/bin/pytest tests/test_override_model.py -v
```

Erwartet: 4 bestanden. Unter SQLite prüfen, dass der `CheckConstraint` greift — ohne das in
Task 6 gesetzte `PRAGMA foreign_keys=ON` und mit `render_as_batch` laufen solche Prüfungen
sonst still ins Leere.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/db/models/override.py migrations/versions tests/
git commit -m "feat: Uebersteuerungen mit Historie und ausgerechnetem Ende"
```

---

### Task 11: Geräte, Fähigkeiten und Zuordnungen — *Codex*

Ersetzt `valveIdRadiatorList` (Fallstrick 5) und den Thermostat-`type` (Fallstrick 8).

**Files:**
- Create: `thermoctl/db/models/device.py`, `migrations/versions/0005_geraete.py`,
  `tests/test_device_model.py`

**Interfaces:**
- Consumes: `Integration`, `DeviceCapability`, `DeviceRole`, `Zone`
- Produces: `Device` mit `id`, `integration_id`, `external_id`, `display_name`, `model`,
  `is_enabled`, `first_seen_at`, `last_seen_at`; `DeviceCapabilityLink`; `ZoneDevice` mit
  `id`, `zone_id`, `device_id`, `device_role_id`, `sort_order`. Ergänzt außerdem den
  Fremdschlüssel `zone.temperature_source_device_id → device.id`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_device_model.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.device import Device, ZoneDevice
from tests.hilfen import anbindung, geraet_anlegen, rolle, zone_anlegen


def test_adresse_ist_je_anbindung_eindeutig(session: Session) -> None:
    z2m = anbindung(session, "zigbee2mqtt")
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="A"))
    session.flush()
    session.add(Device(integration_id=z2m.id, external_id="sensor_wz", display_name="B"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_dieselbe_adresse_in_zwei_anbindungen_ist_erlaubt(session: Session) -> None:
    for code in ("zigbee2mqtt", "meross"):
        session.add(Device(integration_id=anbindung(session, code).id,
                           external_id="schalter", display_name=code))
    session.flush()


def test_zone_hat_beliebig_viele_aktoren(session: Session) -> None:
    zone = zone_anlegen(session, "wohnzimmer")
    aktor = rolle(session, "actuator")
    for name in ("aktor_1", "aktor_2"):
        session.add(ZoneDevice(zone_id=zone.id, device_id=geraet_anlegen(session, name).id,
                               device_role_id=aktor.id))
    session.flush()
    assert session.query(ZoneDevice).filter_by(zone_id=zone.id).count() == 2


def test_ein_geraet_kann_zwei_rollen_haben(session: Session) -> None:
    """Ein Aqara W100 misst und bedient zugleich."""
    zone = zone_anlegen(session, "bad")
    w100 = geraet_anlegen(session, "w100_bad")
    for code in ("controller", "actuator"):
        session.add(ZoneDevice(zone_id=zone.id, device_id=w100.id,
                               device_role_id=rolle(session, code).id))
    session.flush()
    assert session.query(ZoneDevice).filter_by(device_id=w100.id).count() == 2


def test_dieselbe_rolle_zweimal_am_selben_geraet_ist_ausgeschlossen(session: Session) -> None:
    zone = zone_anlegen(session, "kueche")
    geraet = geraet_anlegen(session, "aktor_kueche")
    aktor = rolle(session, "actuator")
    session.add(ZoneDevice(zone_id=zone.id, device_id=geraet.id, device_role_id=aktor.id))
    session.flush()
    session.add(ZoneDevice(zone_id=zone.id, device_id=geraet.id, device_role_id=aktor.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_zone_hat_genau_eine_messquelle(session: Session) -> None:
    """Die Kardinalitaet steckt in der Spalte, nicht in einer Anwendungsregel."""
    zone = zone_anlegen(session, "flur")
    zone.temperature_source_device_id = geraet_anlegen(session, "sensor_flur").id
    session.flush()
    assert zone.temperature_source_device_id is not None


def test_geraetetausch_laesst_die_zone_unberuehrt(session: Session) -> None:
    zone = zone_anlegen(session, "buero")
    alt = geraet_anlegen(session, "aktor_alt")
    neu = geraet_anlegen(session, "aktor_neu")
    zuordnung = ZoneDevice(zone_id=zone.id, device_id=alt.id,
                           device_role_id=rolle(session, "actuator").id)
    session.add(zuordnung)
    session.flush()
    zuordnung.device_id = neu.id
    session.flush()
    assert zone.display_name == "Buero"
```

In `tests/hilfen.py` ergänzen:

```python
from thermoctl.db.models.device import Device
from thermoctl.db.models.lookup import DeviceRole, Integration


def anbindung(session: Session, code: str = "zigbee2mqtt") -> Integration:
    a = session.query(Integration).filter_by(code=code).one_or_none()
    if a is None:
        a = Integration(code=code, label=code)
        session.add(a)
        session.flush()
    return a


def rolle(session: Session, code: str) -> DeviceRole:
    r = session.query(DeviceRole).filter_by(code=code).one_or_none()
    if r is None:
        r = DeviceRole(code=code, label=code)
        session.add(r)
        session.flush()
    return r


def geraet_anlegen(session: Session, external_id: str) -> Device:
    g = Device(integration_id=anbindung(session).id, external_id=external_id,
               display_name=external_id)
    session.add(g)
    session.flush()
    return g
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_device_model.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/device.py` schreiben**

```python
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base


class Device(Base):
    """Ein Geraet, wie es ueber seine Anbindung erreichbar ist.

    Getrennt von der Rolle, die es in einer Zone spielt: derselbe Schaltaktor kann ueber
    Zigbee2MQTT oder Meross haengen, ohne dass die Zone davon etwas merkt.
    """

    __tablename__ = "device"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_id", name="adresse_je_anbindung"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integration.id"), nullable=False)
    # 191 Zeichen: unter utf8mb4 die Grenze indizierbarer Schluessellaenge in MariaDB
    external_id: Mapped[str] = mapped_column(String(191), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeviceCapabilityLink(Base):
    __tablename__ = "device_capability_link"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), primary_key=True
    )
    capability_id: Mapped[int] = mapped_column(
        ForeignKey("device_capability.id"), primary_key=True
    )


class ZoneDevice(Base):
    __tablename__ = "zone_device"
    __table_args__ = (
        UniqueConstraint("zone_id", "device_id", "device_role_id", name="rolle_je_zuordnung"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("device.id"), nullable=False)
    device_role_id: Mapped[int] = mapped_column(ForeignKey("device_role.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```

In `thermoctl/db/models/zone.py` die Spalte zum Fremdschlüssel machen:

```python
    temperature_source_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 4: Migration erzeugen**

```bash
.venv/bin/alembic revision --autogenerate -m "Geraete, Faehigkeiten und Zuordnungen"
```

Prüfen, dass die erzeugte Migration den Fremdschlüssel auf `zone` in einem
`with op.batch_alter_table("zone") as batch:`-Block hinzufügt — ohne diesen Block scheitert
sie unter SQLite, weil es dort kein `ALTER TABLE ... ADD CONSTRAINT` gibt.

- [ ] **Step 5: Tests gegen beide Datenbanken**

```bash
THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db .venv/bin/pytest tests/ -v
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test \
  .venv/bin/pytest tests/ -v
```

Erwartet: alles bestanden, `alembic check` ohne Befund.

- [ ] **Step 6: Commit**

```bash
git add thermoctl/db/models migrations/versions tests/
git commit -m "feat: Geraete generisch ueber Anbindung und Rolle statt fester Typen"
```

---

### Task 12: Benutzer, Gruppen und Rechtezuordnung — *Claude (Sonnet)*

**Files:**
- Create: `thermoctl/db/models/identity.py`, `migrations/versions/0006_identitaet.py`,
  `tests/test_identity_model.py`

**Interfaces:**
- Consumes: `Permission`, `Zone`
- Produces: `User` (`id`, `username`, `display_name`, `password_hash`, `is_active`,
  `created_at`, `last_login_at`); `AccessGroup` (`id`, `name`, `description`, `is_builtin`);
  `UserAccessGroup` (`user_id`, `access_group_id`); `GroupPermission` (`id`,
  `access_group_id`, `permission_id`, `zone_id | None`)

- [ ] **Step 1: Die Tests schreiben**

`tests/test_identity_model.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.models.identity import AccessGroup, GroupPermission, User, UserAccessGroup
from tests.hilfen import berechtigung, zone_anlegen


def test_benutzername_ist_eindeutig(session: Session) -> None:
    session.add(User(username="lino", display_name="Lino", password_hash="x"))
    session.flush()
    session.add(User(username="lino", display_name="Zweiter", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_benutzer_kann_in_mehreren_gruppen_sein(session: Session) -> None:
    nutzer = User(username="a", display_name="A", password_hash="x")
    session.add(nutzer)
    for name in ("Verwaltung", "Bedienung"):
        gruppe = AccessGroup(name=name)
        session.add(gruppe)
        session.flush()
        session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppe.id))
    session.flush()
    assert session.query(UserAccessGroup).filter_by(user_id=nutzer.id).count() == 2


def test_recht_anlagenweit_und_zonenbezogen_nebeneinander(session: Session) -> None:
    """NULL in zone_id heisst anlagenweit; beides darf nebeneinander stehen."""
    gruppe = AccessGroup(name="Gemischt")
    session.add(gruppe)
    session.flush()
    lesen = berechtigung(session, "zone.read", zonenbezogen=True)
    zone = zone_anlegen(session, "bad")
    session.add(GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id,
                                zone_id=None))
    session.add(GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id,
                                zone_id=zone.id))
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=gruppe.id).count() == 2


def test_dieselbe_zuordnung_zweimal_ist_ausgeschlossen(session: Session) -> None:
    gruppe = AccessGroup(name="Doppelt")
    session.add(gruppe)
    session.flush()
    lesen = berechtigung(session, "zone.read", zonenbezogen=True)
    zone = zone_anlegen(session, "kueche")
    for _ in range(2):
        session.add(GroupPermission(access_group_id=gruppe.id, permission_id=lesen.id,
                                    zone_id=zone.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_gruppe_wird_mit_ihren_rechten_geloescht(session: Session) -> None:
    gruppe = AccessGroup(name="Weg")
    session.add(gruppe)
    session.flush()
    session.add(GroupPermission(access_group_id=gruppe.id,
                                permission_id=berechtigung(session, "audit.read").id))
    session.flush()
    session.delete(gruppe)
    session.flush()
    assert session.query(GroupPermission).filter_by(access_group_id=gruppe.id).count() == 0
```

In `tests/hilfen.py` ergänzen:

```python
from thermoctl.db.models.lookup import Permission


def berechtigung(session: Session, code: str, zonenbezogen: bool = False) -> Permission:
    p = session.query(Permission).filter_by(code=code).one_or_none()
    if p is None:
        p = Permission(code=code, description=code, is_zone_scoped=zonenbezogen)
        session.add(p)
        session.flush()
    return p
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_identity_model.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/identity.py` schreiben**

```python
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AccessGroup(Base):
    """Heisst nicht `group` — das ist in SQLite wie MariaDB ein reserviertes Wort."""

    __tablename__ = "access_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserAccessGroup(Base):
    __tablename__ = "user_access_group"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    access_group_id: Mapped[int] = mapped_column(
        ForeignKey("access_group.id", ondelete="CASCADE"), primary_key=True
    )


class GroupPermission(Base):
    """`zone_id = NULL` heisst anlagenweit.

    Fuer nicht zonenbezogene Berechtigungen muss `zone_id` leer sein; das prueft die
    Domaenenlogik anhand von `Permission.is_zone_scoped`, weil eine Datenbankbedingung
    ueber zwei Tabellen hinweg nicht portabel formulierbar ist.
    """

    __tablename__ = "group_permission"
    __table_args__ = (
        UniqueConstraint(
            "access_group_id", "permission_id", "zone_id", name="recht_je_gruppe_und_zone"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_group_id: Mapped[int] = mapped_column(
        ForeignKey("access_group.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(ForeignKey("permission.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True
    )
```

**Achtung MariaDB:** Ein `UNIQUE` über eine nullbare Spalte behandelt `NULL` in beiden
Systemen als „immer verschieden". Zwei anlagenweite Zuordnungen desselben Rechts sind
dadurch technisch möglich; die Domänenlogik in Task 15 wertet Rechte als Menge aus, sodass
eine Dopplung folgenlos bleibt.

- [ ] **Step 4: Migration erzeugen, Tests gegen beide Datenbanken**

```bash
.venv/bin/alembic revision --autogenerate -m "Benutzer, Gruppen und Rechtezuordnung"
.venv/bin/pytest tests/test_identity_model.py -v
```

Erwartet: 5 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/db/models/identity.py migrations/versions tests/
git commit -m "feat: Benutzer, Gruppen und zonenbezogene Rechtezuordnung"
```

---

### Task 13: Sitzungen, Tokens, Einstellungen, Audit — *Claude (Sonnet)*

**Files:**
- Create: `thermoctl/db/models/credential.py`, `thermoctl/db/models/operations.py`,
  `migrations/versions/0007_zugaenge_und_betrieb.py`, `tests/test_credential_model.py`

**Interfaces:**
- Consumes: `User`, `Permission`, `Zone`, `SetpointMode`, `ActorSource`
- Produces: `Session_` (Tabelle `session`), `ApiToken`, `ApiTokenPermission`, `SetupToken`,
  `Setting`, `AuditEvent`. Ergänzt die Fremdschlüssel in `zone_override` auf `user` und
  `api_token`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_credential_model.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission, SetupToken
from thermoctl.db.models.operations import AuditEvent, Setting
from tests.hilfen import benutzer_anlegen, berechtigung, modus_anlegen, quelle


def test_token_hash_ist_eindeutig(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "a")
    session.add(ApiToken(user_id=nutzer.id, name="HA", prefix="ab12", token_hash="h1"))
    session.flush()
    session.add(ApiToken(user_id=nutzer.id, name="Zweit", prefix="cd34", token_hash="h1"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_token_traegt_eigenen_rechteumfang(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "b")
    token = ApiToken(user_id=nutzer.id, name="HA", prefix="ef56", token_hash="h2")
    session.add(token)
    session.flush()
    session.add(ApiTokenPermission(api_token_id=token.id,
                                   permission_id=berechtigung(session, "zone.read", True).id))
    session.flush()
    assert session.query(ApiTokenPermission).filter_by(api_token_id=token.id).count() == 1


def test_token_wird_mit_seinem_besitzer_geloescht(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "c")
    session.add(ApiToken(user_id=nutzer.id, name="X", prefix="gh78", token_hash="h3"))
    session.flush()
    session.delete(nutzer)
    session.flush()
    assert session.query(ApiToken).filter_by(user_id=nutzer.id).count() == 0


def test_es_gibt_genau_eine_einstellungszeile(session: Session) -> None:
    modus = modus_anlegen(session, "frostschutz", "Frostschutz")
    session.add(Setting(id=1, timezone="Europe/Berlin", frost_protection_mode_id=modus.id))
    session.flush()
    session.add(Setting(id=2, timezone="Europe/Berlin", frost_protection_mode_id=modus.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_audit_haelt_urheber_und_quelle_fest(session: Session) -> None:
    nutzer = benutzer_anlegen(session, "d")
    session.add(AuditEvent(occurred_at=utcnow(), source_id=quelle(session, "web").id,
                           actor_user_id=nutzer.id, action="login",
                           object_type="user", object_id=str(nutzer.id),
                           summary="Anmeldung erfolgreich"))
    session.flush()
    eintrag = session.query(AuditEvent).one()
    assert eintrag.actor_user_id == nutzer.id
    assert eintrag.actor_token_id is None


def test_setup_token_wird_nur_einmal_verbraucht(session: Session) -> None:
    marke = SetupToken(token_hash="s1")
    session.add(marke)
    session.flush()
    assert marke.consumed_at is None
    marke.consumed_at = utcnow()
    session.flush()
    assert marke.consumed_at is not None
```

In `tests/hilfen.py` ergänzen:

```python
from thermoctl.db.models.identity import User


def benutzer_anlegen(session: Session, name: str) -> User:
    nutzer = User(username=name, display_name=name.upper(), password_hash="platzhalter")
    session.add(nutzer)
    session.flush()
    return nutzer
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_credential_model.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/db/models/credential.py` schreiben**

```python
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Session_(Base):
    """Sitzung der Weboberflaeche. Gespeichert wird nur der SHA-256 des Cookie-Geheimnisses."""

    __tablename__ = "session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6


class ApiToken(Base):
    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiTokenPermission(Base):
    """Der eigene, engere Rechteumfang eines Tokens."""

    __tablename__ = "api_token_permission"
    __table_args__ = (
        UniqueConstraint("api_token_id", "permission_id", "zone_id", name="recht_je_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_token_id: Mapped[int] = mapped_column(
        ForeignKey("api_token.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(ForeignKey("permission.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True
    )


class SetupToken(Base):
    """Einmal-Token fuer den Einrichtungsassistenten."""

    __tablename__ = "setup_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

`thermoctl/db/models/operations.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class Setting(Base):
    """Genau eine Zeile mit typisierten Spalten — ersetzt die EAV-Tabelle `heizung_conf`.

    Eine neue Einstellung ist eine Alembic-Migration statt eines Strings, der erst zur
    Laufzeit als Fehler auffaellt.
    """

    __tablename__ = "setting"
    __table_args__ = (CheckConstraint("id = 1", name="genau_eine_zeile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin", nullable=False)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_hysteresis_k: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.30"), nullable=False
    )
    default_min_on_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_min_off_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    default_sensor_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=1800, nullable=False
    )
    default_window_resume_delay_seconds: Mapped[int] = mapped_column(
        Integer, default=120, nullable=False
    )
    frost_protection_mode_id: Mapped[int] = mapped_column(
        ForeignKey("setpoint_mode.id"), nullable=False
    )
    session_lifetime_seconds: Mapped[int] = mapped_column(
        Integer, default=1209600, nullable=False  # 14 Tage
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AuditEvent(Base):
    """Was Wochen spaeter noch beantwortbar sein soll.

    Wird in derselben Transaktion geschrieben wie die Aenderung, damit kein Eintrag zu
    einer Aenderung existiert, die nicht stattfand.
    """

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("actor_source.id"), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    actor_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
```

In `thermoctl/db/models/override.py` die beiden Urheberspalten zu Fremdschlüsseln machen:

```python
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_by_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
```

`ON DELETE SET NULL` und nicht `CASCADE`: Ein gelöschter Benutzer darf die Historie seiner
Übersteuerungen nicht mitnehmen.

- [ ] **Step 4: Migration erzeugen, beide Datenbanken prüfen**

```bash
.venv/bin/alembic revision --autogenerate -m "Zugaenge, Einstellungen und Audit"
THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db .venv/bin/pytest tests/ -v
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test \
  .venv/bin/pytest tests/ -v
```

Erwartet: alles bestanden. In der Migration prüfen, dass die Fremdschlüssel auf
`zone_override` in einem `batch_alter_table`-Block stehen.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/db/models migrations/versions tests/
git commit -m "feat: Sitzungen, Tokens, typisierte Einstellungen und Audit-Protokoll"
```

---

### Task 14: Passwörter und Geheimnisse — *Claude (Sonnet)*

**Files:**
- Create: `thermoctl/auth/__init__.py`, `thermoctl/auth/passwords.py`,
  `thermoctl/auth/secrets.py`, `tests/test_auth_primitives.py`

**Interfaces:**
- Consumes: nichts
- Produces: `hash_password(klartext: str) -> str`;
  `verify_password(klartext: str, hash_wert: str) -> bool`;
  `PasswordTooShort(ValueError)`; `MIN_PASSWORT_LAENGE = 12`;
  `neues_geheimnis() -> str`; `hash_geheimnis(geheimnis: str) -> str`;
  `neues_token() -> tuple[str, str, str]` — liefert `(klartext, prefix, hash)`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_auth_primitives.py`:

```python
import pytest

from thermoctl.auth.passwords import (
    MIN_PASSWORT_LAENGE, PasswordTooShort, hash_password, verify_password,
)
from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis, neues_token


def test_hash_ist_kein_klartext() -> None:
    wert = hash_password("ein-sehr-gutes-passwort")
    assert "ein-sehr-gutes-passwort" not in wert
    assert wert.startswith("$argon2id$")


def test_gleiches_passwort_ergibt_verschiedene_hashes() -> None:
    """Argon2 salzt selbst; zwei gleiche Passwoerter duerfen nicht gleich aussehen."""
    assert hash_password("passwort-genug-lang") != hash_password("passwort-genug-lang")


def test_pruefung_erkennt_richtig_und_falsch() -> None:
    wert = hash_password("passwort-genug-lang")
    assert verify_password("passwort-genug-lang", wert) is True
    assert verify_password("etwas-anderes-langes", wert) is False


def test_zu_kurzes_passwort_wird_abgewiesen() -> None:
    with pytest.raises(PasswordTooShort):
        hash_password("a" * (MIN_PASSWORT_LAENGE - 1))


def test_pruefung_gegen_unsinnigen_hash_wirft_nicht() -> None:
    assert verify_password("egal-welches-passwort", "kein-gueltiger-hash") is False


def test_geheimnis_ist_lang_genug_und_jedes_mal_neu() -> None:
    werte = {neues_geheimnis() for _ in range(100)}
    assert len(werte) == 100
    assert all(len(w) >= 43 for w in werte)  # 256 Bit base64url


def test_geheimnis_hash_ist_stabil_und_sechzig_vier_zeichen() -> None:
    g = neues_geheimnis()
    assert hash_geheimnis(g) == hash_geheimnis(g)
    assert len(hash_geheimnis(g)) == 64


def test_token_hat_erwartete_form() -> None:
    klartext, prefix, hash_wert = neues_token()
    assert klartext.startswith("tctl_")
    assert klartext.split("_")[1] == prefix
    assert hash_wert == hash_geheimnis(klartext.split("_", 2)[2])
    assert len(prefix) == 8
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_auth_primitives.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Die beiden Module schreiben**

`thermoctl/auth/passwords.py`:

```python
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_PASSWORT_LAENGE = 12

_hasher = PasswordHasher()


class PasswordTooShort(ValueError):
    pass


def hash_password(klartext: str) -> str:
    if len(klartext) < MIN_PASSWORT_LAENGE:
        raise PasswordTooShort(
            f"Das Passwort muss mindestens {MIN_PASSWORT_LAENGE} Zeichen haben."
        )
    return _hasher.hash(klartext)


def verify_password(klartext: str, hash_wert: str) -> bool:
    """Prueft ein Passwort. Gibt False zurueck statt zu werfen — auch bei kaputtem Hash."""
    try:
        return _hasher.verify(hash_wert, klartext)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
```

Die Argon2-Parameter bleiben bei der Bibliotheksvorgabe und stehen im Hash selbst. Eine
spätere Verschärfung braucht deshalb keine Schemaänderung.

`thermoctl/auth/secrets.py`:

```python
import hashlib
import secrets

TOKEN_PRAEFIX = "tctl"
PREFIX_LAENGE = 8


def neues_geheimnis() -> str:
    """256 Bit Zufall, base64url-kodiert."""
    return secrets.token_urlsafe(32)


def hash_geheimnis(geheimnis: str) -> str:
    """SHA-256 statt Argon2id.

    Bei 256 Bit Zufall traegt ein langsamer Hash nichts bei, muss aber bei jeder
    API-Anfrage berechnet werden. Fuer Passwoerter gilt das Gegenteil — siehe passwords.py.
    """
    return hashlib.sha256(geheimnis.encode("utf-8")).hexdigest()


def neues_token() -> tuple[str, str, str]:
    """Liefert (klartext, prefix, hash). Der Klartext erscheint genau einmal."""
    prefix = secrets.token_hex(PREFIX_LAENGE // 2)
    geheimnis = neues_geheimnis()
    return f"{TOKEN_PRAEFIX}_{prefix}_{geheimnis}", prefix, hash_geheimnis(geheimnis)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_auth_primitives.py -v`
Erwartet: 8 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/auth tests/test_auth_primitives.py
git commit -m "feat: Argon2id fuer Passwoerter, SHA-256 fuer Zufallsgeheimnisse"
```

---

### Task 15: Rechteprüfung in der Domänenlogik — *Claude (Sonnet)*

**Die sicherheitskritischste Aufgabe des Teilprojekts.** Ein Fehler hier wird nicht zu einem
roten Test, sondern zu still zu viel ausgelieferten Daten. Wird zusätzlich in der
Hauptsession gegengelesen.

**Files:**
- Create: `thermoctl/domain/__init__.py`, `thermoctl/domain/principal.py`,
  `thermoctl/domain/authz.py`, `tests/test_authz.py`

**Interfaces:**
- Consumes: `User`, `AccessGroup`, `GroupPermission`, `ApiToken`, `ApiTokenPermission`,
  `Permission`, `Zone`
- Produces:
  - `Principal` (Dataclass): `user_id: int`, `token_id: int | None`,
    `grants: frozenset[tuple[str, int | None]]`
  - `principal_fuer_benutzer(session, user) -> Principal`
  - `principal_fuer_token(session, token) -> Principal`
  - `hat_recht(principal, code: str, zone_id: int | None = None) -> bool`
  - `require(principal, code: str, zone_id: int | None = None) -> None`, wirft `Forbidden`
  - `visible_zones(session, principal, code: str) -> list[Zone]`
  - `Forbidden(Exception)`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_authz.py`:

```python
import pytest
from sqlalchemy.orm import Session

from thermoctl.domain.authz import (
    Forbidden, hat_recht, principal_fuer_benutzer, principal_fuer_token, require,
    visible_zones,
)
from tests.hilfen import (
    benutzer_mit_rechten, token_mit_rechten, zone_anlegen,
)


def test_anlagenweites_recht_gilt_fuer_jede_zone(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "a", [("zone.read", None)])
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "zone.read", bad.id) is True
    assert hat_recht(p, "zone.read", kueche.id) is True


def test_zonenbezogenes_recht_gilt_nur_dort(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "b", [("zone.read", bad.id)])
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "zone.read", bad.id) is True
    assert hat_recht(p, "zone.read", kueche.id) is False


def test_zonenbezogenes_recht_gilt_nicht_anlagenweit(session: Session) -> None:
    """Wer nur das Bad darf, darf nicht 'ueberall'."""
    bad = zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "c", [("zone.read", bad.id)])
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "zone.read", None) is False


def test_visible_zones_liefert_nur_erlaubte(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "d", [("zone.read", bad.id)])
    sichtbar = visible_zones(session, principal_fuer_benutzer(session, nutzer), "zone.read")
    assert [z.name for z in sichtbar] == ["bad"]


def test_visible_zones_liefert_bei_anlagenweitem_recht_alle(session: Session) -> None:
    zone_anlegen(session, "bad")
    zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "e", [("zone.read", None)])
    sichtbar = visible_zones(session, principal_fuer_benutzer(session, nutzer), "zone.read")
    assert {z.name for z in sichtbar} == {"bad", "kueche"}


def test_visible_zones_ist_leer_ohne_recht(session: Session) -> None:
    zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "f", [])
    assert visible_zones(session, principal_fuer_benutzer(session, nutzer), "zone.read") == []


def test_require_wirft_ohne_recht(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "g", [])
    p = principal_fuer_benutzer(session, nutzer)
    with pytest.raises(Forbidden):
        require(p, "zone.read", bad.id)


def test_rechte_mehrerer_gruppen_werden_vereinigt(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(
        session, "h", [("zone.read", bad.id)], zweite_gruppe=[("zone.read", kueche.id)]
    )
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "zone.read", bad.id) is True
    assert hat_recht(p, "zone.read", kueche.id) is True


def test_token_darf_nicht_mehr_als_sein_besitzer(session: Session) -> None:
    """Verliert der Besitzer ein Recht, verliert es das Token bei der Pruefung ebenfalls."""
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "i", [("zone.read", bad.id)])
    token = token_mit_rechten(session, nutzer, [("zone.read", bad.id),
                                                ("zone.read", kueche.id)])
    p = principal_fuer_token(session, token)
    assert hat_recht(p, "zone.read", bad.id) is True
    assert hat_recht(p, "zone.read", kueche.id) is False


def test_token_kann_weniger_als_sein_besitzer(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "j", [("zone.read", None),
                                                 ("zone.manage", None)])
    token = token_mit_rechten(session, nutzer, [("zone.read", None)])
    p = principal_fuer_token(session, token)
    assert hat_recht(p, "zone.read", bad.id) is True
    assert hat_recht(p, "zone.manage", bad.id) is False


def test_inaktiver_benutzer_hat_keine_rechte(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "k", [("zone.read", None)])
    nutzer.is_active = False
    session.flush()
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "zone.read", bad.id) is False


def test_widerrufenes_token_hat_keine_rechte(session: Session) -> None:
    from thermoctl.db.base import utcnow

    bad = zone_anlegen(session, "bad")
    nutzer = benutzer_mit_rechten(session, "l", [("zone.read", None)])
    token = token_mit_rechten(session, nutzer, [("zone.read", None)])
    token.revoked_at = utcnow()
    session.flush()
    assert hat_recht(principal_fuer_token(session, token), "zone.read", bad.id) is False
```

Die Hilfen `benutzer_mit_rechten` und `token_mit_rechten` in `tests/hilfen.py` ergänzen: sie
legen Benutzer beziehungsweise Token an, hängen eine Gruppe daran und tragen die übergebenen
`(code, zone_id)`-Paare als `GroupPermission` beziehungsweise `ApiTokenPermission` ein.

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_authz.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/domain/principal.py` und `authz.py` schreiben**

```python
# thermoctl/domain/principal.py
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Wer handelt — Benutzer oder Token — samt seinem effektiven Rechteumfang.

    Die Adapter (HTMX, REST, spaeter MCP) bekommen nur diesen Typ zu sehen und muessen
    nicht wissen, womit sie es zu tun haben.

    `grants` enthaelt Paare (berechtigungs_code, zone_id). `zone_id = None` heisst
    anlagenweit.
    """

    user_id: int
    token_id: int | None
    grants: frozenset[tuple[str, int | None]]
```

```python
# thermoctl/domain/authz.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import (
    GroupPermission, User, UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.zone import Zone
from thermoctl.domain.principal import Principal


class Forbidden(Exception):
    """Die Handlung ist diesem Principal nicht erlaubt."""


def _benutzerrechte(session: Session, user: User) -> frozenset[tuple[str, int | None]]:
    if not user.is_active:
        return frozenset()
    zeilen = session.execute(
        select(Permission.code, GroupPermission.zone_id)
        .join(GroupPermission, GroupPermission.permission_id == Permission.id)
        .join(
            UserAccessGroup,
            UserAccessGroup.access_group_id == GroupPermission.access_group_id,
        )
        .where(UserAccessGroup.user_id == user.id)
    ).all()
    return frozenset((code, zone_id) for code, zone_id in zeilen)


def principal_fuer_benutzer(session: Session, user: User) -> Principal:
    return Principal(user_id=user.id, token_id=None, grants=_benutzerrechte(session, user))


def principal_fuer_token(session: Session, token: ApiToken) -> Principal:
    """Der Umfang eines Tokens ist stets die Schnittmenge mit den Rechten des Besitzers.

    Zur Laufzeit, nicht nur beim Ausstellen: verliert der Besitzer spaeter ein Recht,
    verliert das Token es ebenfalls.
    """
    jetzt = utcnow()
    if token.revoked_at is not None or (
        token.expires_at is not None and token.expires_at <= jetzt
    ):
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())

    besitzer = session.get(User, token.user_id)
    if besitzer is None:
        return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset())
    vom_besitzer = _benutzerrechte(session, besitzer)

    zeilen = session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .where(ApiTokenPermission.api_token_id == token.id)
    ).all()
    vom_token = frozenset((code, zone_id) for code, zone_id in zeilen)

    wirksam = {
        (code, zone_id)
        for code, zone_id in vom_token
        if (code, zone_id) in vom_besitzer or (code, None) in vom_besitzer
    }
    return Principal(user_id=token.user_id, token_id=token.id, grants=frozenset(wirksam))


def hat_recht(principal: Principal, code: str, zone_id: int | None = None) -> bool:
    """Ein anlagenweites Recht deckt jede Zone ab, ein zonenbezogenes nur die eigene.

    Umgekehrt gilt das ausdruecklich nicht: wer nur das Bad darf, darf nicht 'ueberall'.
    """
    if (code, None) in principal.grants:
        return True
    if zone_id is None:
        return False
    return (code, zone_id) in principal.grants


def require(principal: Principal, code: str, zone_id: int | None = None) -> None:
    if not hat_recht(principal, code, zone_id):
        raise Forbidden(f"Recht {code} fehlt" + (f" fuer Zone {zone_id}" if zone_id else ""))


def visible_zones(session: Session, principal: Principal, code: str) -> list[Zone]:
    """Die Zonen, auf die ein Principal mit diesem Recht sehen darf.

    Jede Liste und jede API-Antwort geht hier durch. Das ist die Stelle, an der
    zonenbezogene Rechte still lecken, wenn man sie irgendwo vergisst — deshalb liegt
    sie in der Domaenenlogik und nicht in den Adaptern.
    """
    if (code, None) in principal.grants:
        return list(session.scalars(select(Zone).order_by(Zone.sort_order, Zone.name)))
    erlaubt = {
        zone_id for vergebener_code, zone_id in principal.grants
        if vergebener_code == code and zone_id is not None
    }
    if not erlaubt:
        return []
    return list(
        session.scalars(
            select(Zone).where(Zone.id.in_(erlaubt)).order_by(Zone.sort_order, Zone.name)
        )
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_authz.py -v`
Erwartet: 12 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/domain tests/test_authz.py tests/hilfen.py
git commit -m "feat: Rechtepruefung mit Zonenbezug in der Domaenenlogik"
```

---

### Task 16: Sollwertauflösung aus dem Zeitplan — *Codex*

Reine Berechnung ohne Datenbankschreibzugriff. Läuft parallel zu 17.

**Files:**
- Create: `thermoctl/domain/schedule.py`, `tests/test_domain_schedule.py`

**Interfaces:**
- Consumes: `SchedulePoint`, `Zone`, `Setting`, `ZoneOverride`, `ZoneSetpoint`
- Produces:
  - `geltender_punkt(punkte: list[SchedulePoint], zeitpunkt: datetime) -> SchedulePoint | None`
  - `naechster_punkt(punkte: list[SchedulePoint], zeitpunkt: datetime) -> datetime | None`
  - `aufgeloester_sollwert(session, zone, jetzt_utc: datetime) -> Sollwert`
  - `Sollwert` (Dataclass): `temperature_c: Decimal`, `grund: str`, `modus_code: str | None`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_domain_schedule.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from thermoctl.domain.schedule import (
    aufgeloester_sollwert, geltender_punkt, naechster_punkt,
)
from tests.hilfen import punkt, zone_mit_zeitplan


def test_punkt_gilt_bis_zum_naechsten() -> None:
    punkte = [punkt(1, 360, "tag"), punkt(1, 1380, "nacht")]  # Mo 06:00 und 23:00
    montag_zehn_uhr = datetime(2026, 8, 31, 10, 0)
    assert geltender_punkt(punkte, montag_zehn_uhr).minute_of_day == 360


def test_vor_dem_ersten_punkt_gilt_der_letzte_der_woche() -> None:
    """Der Sonntagabend-Punkt wirkt bis Montagfrueh — die Woche ist ein Ring."""
    punkte = [punkt(1, 360, "tag"), punkt(7, 1320, "nacht")]  # Mo 06:00, So 22:00
    montag_drei_uhr = datetime(2026, 8, 31, 3, 0)
    gilt = geltender_punkt(punkte, montag_drei_uhr)
    assert gilt.weekday == 7 and gilt.minute_of_day == 1320


def test_ohne_punkte_gibt_es_keinen_geltenden() -> None:
    assert geltender_punkt([], datetime(2026, 8, 31, 10, 0)) is None


def test_punkt_genau_zur_schaltminute_gilt_bereits() -> None:
    punkte = [punkt(1, 360, "tag")]
    assert geltender_punkt(punkte, datetime(2026, 8, 31, 6, 0)) is not None


def test_naechster_punkt_liegt_in_der_zukunft() -> None:
    punkte = [punkt(1, 360, "tag"), punkt(1, 1380, "nacht")]
    naechster = naechster_punkt(punkte, datetime(2026, 8, 31, 10, 0))
    assert naechster == datetime(2026, 8, 31, 23, 0)


def test_ohne_zeitplan_gilt_der_frostschutz(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "leer", punkte=[], frostschutz=Decimal("16.0"))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("16.0")
    assert "Frostschutz" in ergebnis.grund


def test_betriebsart_aus_ergibt_frostschutz(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "aus", punkte=[(1, 360, "tag", Decimal("21.0"))],
                             betriebsart="off", frostschutz=Decimal("16.0"))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("16.0")


def test_uebersteuerung_schlaegt_den_zeitplan(session: Session) -> None:
    zone = zone_mit_zeitplan(session, "ueber", punkte=[(1, 360, "tag", Decimal("21.0"))],
                             uebersteuerung=(Decimal("23.5"), None))
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("23.5")
    assert "Uebersteuerung" in ergebnis.grund


def test_abgelaufene_uebersteuerung_wirkt_nicht_mehr(session: Session) -> None:
    zone = zone_mit_zeitplan(
        session, "abgelaufen", punkte=[(1, 360, "tag", Decimal("21.0"))],
        uebersteuerung=(Decimal("23.5"), datetime(2026, 8, 31, 9, 0)),
    )
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert ergebnis.temperature_c == Decimal("21.0")


def test_grund_benennt_die_entscheidung(session: Session) -> None:
    """Grundsatz 5: nachvollziehbar, warum dieser Sollwert gilt."""
    zone = zone_mit_zeitplan(session, "grund", punkte=[(1, 360, "tag", Decimal("21.0"))])
    ergebnis = aufgeloester_sollwert(session, zone, datetime(2026, 8, 31, 10, 0))
    assert "Tag" in ergebnis.grund and "06:00" in ergebnis.grund
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_domain_schedule.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/domain/schedule.py` schreiben**

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.models.operations import Setting
from thermoctl.db.models.override import ZoneOverride
from thermoctl.db.models.schedule import SchedulePoint
from thermoctl.db.models.zone import SetpointMode, Zone, ZoneSetpoint

MINUTEN_JE_WOCHE = 7 * 24 * 60


@dataclass(frozen=True)
class Sollwert:
    """Das Ergebnis samt Begruendung — Grundsatz 5 aus CLAUDE.md."""

    temperature_c: Decimal
    grund: str
    modus_code: str | None


def _wochenminute(zeitpunkt: datetime) -> int:
    return (zeitpunkt.isoweekday() - 1) * 24 * 60 + zeitpunkt.hour * 60 + zeitpunkt.minute


def _punktminute(punkt: SchedulePoint) -> int:
    return (punkt.weekday - 1) * 24 * 60 + punkt.minute_of_day


def geltender_punkt(
    punkte: list[SchedulePoint], zeitpunkt: datetime
) -> SchedulePoint | None:
    """Der letzte Punkt vor oder genau auf dem Zeitpunkt.

    Die Woche ist ein Ring: liegt kein Punkt davor, gilt der letzte der Woche. Deshalb
    kann es weder Luecken noch Ueberlappungen geben, solange ueberhaupt ein Punkt da ist.
    """
    if not punkte:
        return None
    jetzt = _wochenminute(zeitpunkt)
    davor = [p for p in punkte if _punktminute(p) <= jetzt]
    return max(davor or punkte, key=_punktminute)


def naechster_punkt(punkte: list[SchedulePoint], zeitpunkt: datetime) -> datetime | None:
    """Wann der naechste Schaltpunkt faellt — Grundlage fuer 'bis zur naechsten Schaltung'."""
    if not punkte:
        return None
    jetzt = _wochenminute(zeitpunkt)
    kandidaten = sorted(_punktminute(p) for p in punkte)
    spaeter = [m for m in kandidaten if m > jetzt]
    ziel = spaeter[0] if spaeter else kandidaten[0] + MINUTEN_JE_WOCHE
    wochenanfang = (zeitpunkt - timedelta(days=zeitpunkt.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return wochenanfang + timedelta(minutes=ziel)


def _temperatur_fuer_modus(session: Session, zone: Zone, modus_id: int) -> Decimal | None:
    return session.scalar(
        select(ZoneSetpoint.temperature_c).where(
            ZoneSetpoint.zone_id == zone.id, ZoneSetpoint.setpoint_mode_id == modus_id
        )
    )


def aufgeloester_sollwert(session: Session, zone: Zone, jetzt_utc: datetime) -> Sollwert:
    """Welcher Sollwert gerade gilt, und warum.

    Rangfolge: Betriebsart 'off' schlaegt alles, dann eine laufende Uebersteuerung,
    dann der Zeitplan, zuletzt der Frostschutz.
    """
    einstellungen = session.get(Setting, 1)
    assert einstellungen is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    frost_id = einstellungen.frost_protection_mode_id
    frost_temp = _temperatur_fuer_modus(session, zone, frost_id) or Decimal("16.0")
    frost_code = session.scalar(select(SetpointMode.code).where(SetpointMode.id == frost_id))

    if zone.operating_mode.code == "off":
        return Sollwert(frost_temp, "Betriebsart Aus — Frostschutz", frost_code)

    laufend = session.scalars(
        select(ZoneOverride)
        .where(
            ZoneOverride.zone_id == zone.id,
            ZoneOverride.cancelled_at.is_(None),
            ZoneOverride.starts_at <= jetzt_utc,
        )
        .order_by(ZoneOverride.created_at.desc())
    ).first()
    if laufend is not None and (laufend.ends_at is None or laufend.ends_at > jetzt_utc):
        if laufend.temperature_c is not None:
            return Sollwert(laufend.temperature_c, "Uebersteuerung (feste Temperatur)", None)
        temp = _temperatur_fuer_modus(session, zone, laufend.setpoint_mode_id or 0)
        code = session.scalar(
            select(SetpointMode.code).where(SetpointMode.id == laufend.setpoint_mode_id)
        )
        if temp is not None:
            return Sollwert(temp, f"Uebersteuerung auf Modus {code}", code)

    # Zeitplaene stehen in lokaler Zeit, damit sich die Nachtabsenkung bei der
    # Zeitumstellung nicht verschiebt.
    lokal = jetzt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo(einstellungen.timezone)
    )
    punkte = list(
        session.scalars(select(SchedulePoint).where(SchedulePoint.zone_id == zone.id))
    )
    gilt = geltender_punkt(punkte, lokal.replace(tzinfo=None))
    if gilt is not None:
        temp = _temperatur_fuer_modus(session, zone, gilt.setpoint_mode_id)
        modus = session.get(SetpointMode, gilt.setpoint_mode_id)
        if temp is not None and modus is not None:
            uhrzeit = f"{gilt.minute_of_day // 60:02d}:{gilt.minute_of_day % 60:02d}"
            return Sollwert(temp, f"Zeitplan: Modus {modus.name} ab {uhrzeit}", modus.code)

    return Sollwert(frost_temp, "Kein Zeitplan hinterlegt — Frostschutz", frost_code)
```

Dafür braucht `Zone` eine Beziehung auf die Betriebsart. In `thermoctl/db/models/zone.py`
ergänzen:

```python
    operating_mode: Mapped["OperatingMode"] = relationship(lazy="joined")
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_domain_schedule.py -v`
Erwartet: 10 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/domain/schedule.py thermoctl/db/models/zone.py tests/
git commit -m "feat: Sollwertaufloesung aus Zeitplan, Uebersteuerung und Betriebsart"
```

---

### Task 17: Zonenwerte mit Rückfall auf den globalen Standard — *Codex*

**Files:**
- Create: `thermoctl/domain/zone_settings.py`, `tests/test_zone_settings.py`

**Interfaces:**
- Consumes: `Zone`, `Setting`
- Produces: `Regelparameter` (Dataclass) mit `hysteresis_k: Decimal`, `min_on_seconds: int`,
  `min_off_seconds: int`, `sensor_timeout_seconds: int`, `temperature_offset_k: Decimal`,
  `window_resume_delay_seconds: int`; `regelparameter(session, zone) -> Regelparameter`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_zone_settings.py`:

```python
from decimal import Decimal

from sqlalchemy.orm import Session

from thermoctl.domain.zone_settings import regelparameter
from tests.hilfen import einstellungen_anlegen, zone_anlegen


def test_leere_zonenwerte_fallen_auf_den_standard(session: Session) -> None:
    einstellungen_anlegen(session, hysterese=Decimal("0.30"), min_ein=300)
    zone = zone_anlegen(session, "bad")
    werte = regelparameter(session, zone)
    assert werte.hysteresis_k == Decimal("0.30")
    assert werte.min_on_seconds == 300


def test_gesetzter_zonenwert_hat_vorrang(session: Session) -> None:
    einstellungen_anlegen(session, hysterese=Decimal("0.30"))
    zone = zone_anlegen(session, "kueche")
    zone.hysteresis_k = Decimal("0.80")
    session.flush()
    assert regelparameter(session, zone).hysteresis_k == Decimal("0.80")


def test_null_ist_ein_gueltiger_zonenwert(session: Session) -> None:
    """0 darf nicht als 'nicht gesetzt' missverstanden werden."""
    einstellungen_anlegen(session, min_ein=300)
    zone = zone_anlegen(session, "flur")
    zone.min_on_seconds = 0
    session.flush()
    assert regelparameter(session, zone).min_on_seconds == 0


def test_standardaenderung_wirkt_auf_nicht_ueberschriebene_zonen(session: Session) -> None:
    e = einstellungen_anlegen(session, hysterese=Decimal("0.30"))
    zone = zone_anlegen(session, "buero")
    e.default_hysteresis_k = Decimal("0.50")
    session.flush()
    assert regelparameter(session, zone).hysteresis_k == Decimal("0.50")
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_zone_settings.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/domain/zone_settings.py` schreiben**

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from sqlalchemy.orm import Session

from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import Zone

T = TypeVar("T")


@dataclass(frozen=True)
class Regelparameter:
    hysteresis_k: Decimal
    min_on_seconds: int
    min_off_seconds: int
    sensor_timeout_seconds: int
    temperature_offset_k: Decimal
    window_resume_delay_seconds: int


def _oder_standard(zonenwert: T | None, standard: T) -> T:
    """Nur None gilt als 'nicht gesetzt' — 0 und 0.0 sind gueltige Zonenwerte."""
    return standard if zonenwert is None else zonenwert


def regelparameter(session: Session, zone: Zone) -> Regelparameter:
    """Die wirksamen Regelparameter einer Zone.

    Leere Zonenfelder heissen 'globaler Standard'. So steht jeder Wert genau einmal
    irgendwo, und eine Aenderung des Standards wirkt auf alle Zonen, die ihn nicht
    ausdruecklich ueberschrieben haben.
    """
    e = session.get(Setting, 1)
    assert e is not None, "setting-Zeile fehlt — Einrichtung unvollstaendig"
    return Regelparameter(
        hysteresis_k=_oder_standard(zone.hysteresis_k, e.default_hysteresis_k),
        min_on_seconds=_oder_standard(zone.min_on_seconds, e.default_min_on_seconds),
        min_off_seconds=_oder_standard(zone.min_off_seconds, e.default_min_off_seconds),
        sensor_timeout_seconds=_oder_standard(
            zone.sensor_timeout_seconds, e.default_sensor_timeout_seconds
        ),
        temperature_offset_k=_oder_standard(zone.temperature_offset_k, Decimal("0.00")),
        window_resume_delay_seconds=_oder_standard(
            zone.window_resume_delay_seconds, e.default_window_resume_delay_seconds
        ),
    )
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_zone_settings.py -v`
Erwartet: 4 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/domain/zone_settings.py tests/test_zone_settings.py
git commit -m "feat: Regelparameter je Zone mit Rueckfall auf den globalen Standard"
```

---

### Task 18: Sitzungen, Anmeldung und CSRF-Schutz — *Claude (Sonnet)*

**Files:**
- Create: `thermoctl/auth/sessions.py`, `thermoctl/auth/csrf.py`,
  `thermoctl/auth/dependencies.py`, `thermoctl/audit.py`,
  `thermoctl/web/__init__.py`, `thermoctl/web/templates/basis.html`,
  `thermoctl/web/templates/anmeldung.html`, `thermoctl/web/auth_views.py`,
  `tests/test_login.py`
- Modify: `thermoctl/app.py` (Router einbinden)

**Interfaces:**
- Consumes: `verify_password`, `neues_geheimnis`, `hash_geheimnis`, `Principal`, `Session_`
- Produces:
  - `sitzung_anlegen(session, user, lebensdauer_s, user_agent, ip) -> tuple[Session_, str]`
  - `sitzung_aufloesen(session, cookie_wert) -> Session_ | None`
  - `sitzung_widerrufen(session, sitzung) -> None`
  - `csrf_token(sitzung_geheimnis: str, secret_key: str) -> str`; `csrf_pruefen(...) -> bool`
  - `aktueller_principal` als FastAPI-Abhängigkeit
  - `audit.record(session, *, source, action, object_type, object_id, summary, user=None, token=None, detail=None) -> None`
  - Routen `GET /login`, `POST /login`, `POST /logout`
- Konstanten: Cookie-Name `thermoctl_session`, CSRF-Header `X-CSRF-Token`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_login.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.models.credential import Session_
from thermoctl.db.models.operations import AuditEvent


def test_anmeldung_mit_richtigem_passwort(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert "thermoctl_session" in antwort.cookies


def test_anmeldung_mit_falschem_passwort_scheitert(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert antwort.status_code == 401
    assert "thermoctl_session" not in antwort.cookies


def test_fehlermeldung_verraet_nicht_ob_der_benutzer_existiert(client: TestClient, benutzer) -> None:
    a = client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    b = client.post("/login", data={"username": "gibtsnicht", "password": "falsch-aber-lang"})
    assert a.status_code == b.status_code == 401
    assert a.text == b.text


def test_cookie_ist_httponly_und_samesite(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    kopf = antwort.headers["set-cookie"].lower()
    assert "httponly" in kopf
    assert "samesite=lax" in kopf


def test_cookie_enthaelt_nicht_den_gespeicherten_hash(client: TestClient, benutzer,
                                                      session: Session) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"},
                          follow_redirects=False)
    gespeichert = session.query(Session_).one().token_hash
    assert gespeichert not in antwort.headers["set-cookie"]


def test_inaktiver_benutzer_kommt_nicht_hinein(client: TestClient, benutzer,
                                               session: Session) -> None:
    benutzer.is_active = False
    session.flush()
    assert client.post("/login",
                       data={"username": "lino", "password": "passwort-lang-genug"}).status_code == 401


def test_abmelden_widerruft_die_sitzung(client: TestClient, benutzer, session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    client.post("/logout")
    assert session.query(Session_).one().revoked_at is not None


def test_aenderung_ohne_csrf_token_wird_abgewiesen(client: TestClient, benutzer) -> None:
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    antwort = client.post("/logout", headers={"X-CSRF-Token": "falsch"})
    assert antwort.status_code == 403


def test_anmeldung_und_fehlversuch_landen_im_audit(client: TestClient, benutzer,
                                                   session: Session) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    aktionen = [e.action for e in session.query(AuditEvent).all()]
    assert "login_failed" in aktionen
    assert "login" in aktionen


def test_passwort_erscheint_in_keiner_antwort(client: TestClient, benutzer) -> None:
    antwort = client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    assert "passwort-lang-genug" not in antwort.text
```

Die Fixtures `client` und `benutzer` in `tests/conftest.py` ergänzen: `client` baut die App
mit der Testdatenbank, `benutzer` legt `lino` mit gehashtem Passwort und der Gruppe
*Verwaltung* an.

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_login.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Die Module schreiben**

`thermoctl/auth/sessions.py`:

```python
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import Session_
from thermoctl.db.models.identity import User

COOKIE_NAME = "thermoctl_session"


def sitzung_anlegen(
    session: Session, user: User, lebensdauer_s: int,
    user_agent: str | None = None, ip: str | None = None,
) -> tuple[Session_, str]:
    """Legt eine Sitzung an und liefert sie samt Klartext-Geheimnis fuer das Cookie.

    Gespeichert wird nur der Hash — wer die Datenbank liest, kann sich damit nicht anmelden.
    """
    geheimnis = neues_geheimnis()
    eintrag = Session_(
        user_id=user.id,
        token_hash=hash_geheimnis(geheimnis),
        expires_at=utcnow() + timedelta(seconds=lebensdauer_s),
        user_agent=user_agent,
        ip_address=ip,
    )
    session.add(eintrag)
    session.flush()
    return eintrag, geheimnis


def sitzung_aufloesen(session: Session, cookie_wert: str) -> Session_ | None:
    eintrag = session.scalar(
        select(Session_).where(Session_.token_hash == hash_geheimnis(cookie_wert))
    )
    if eintrag is None or eintrag.revoked_at is not None or eintrag.expires_at <= utcnow():
        return None
    eintrag.last_seen_at = utcnow()
    return eintrag


def sitzung_widerrufen(session: Session, sitzung: Session_) -> None:
    sitzung.revoked_at = utcnow()
```

`thermoctl/auth/csrf.py`:

```python
import hashlib
import hmac

CSRF_HEADER = "X-CSRF-Token"


def csrf_token(sitzung_geheimnis: str, secret_key: str) -> str:
    """An die Sitzung gebunden: ein Token aus einer fremden Sitzung passt nicht."""
    return hmac.new(
        secret_key.encode(), sitzung_geheimnis.encode(), hashlib.sha256
    ).hexdigest()


def csrf_pruefen(uebermittelt: str | None, sitzung_geheimnis: str, secret_key: str) -> bool:
    if not uebermittelt:
        return False
    return hmac.compare_digest(uebermittelt, csrf_token(sitzung_geheimnis, secret_key))
```

`hmac.compare_digest` statt `==`, damit die Laufzeit des Vergleichs nichts über den
erwarteten Wert verrät.

`thermoctl/audit.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.db.base import utcnow
from thermoctl.db.models.lookup import ActorSource
from thermoctl.db.models.operations import AuditEvent


def record(
    session: Session, *, source: str, action: str, object_type: str,
    summary: str, object_id: str | None = None, user_id: int | None = None,
    token_id: int | None = None, detail: str | None = None,
) -> None:
    """Schreibt einen Audit-Eintrag in dieselbe Transaktion wie die Aenderung.

    Damit gibt es keinen Eintrag zu einer Aenderung, die zurueckgerollt wurde — und keine
    Aenderung ohne Eintrag.
    """
    quelle_id = session.scalar(select(ActorSource.id).where(ActorSource.code == source))
    session.add(
        AuditEvent(
            occurred_at=utcnow(), source_id=quelle_id, action=action,
            object_type=object_type, object_id=object_id, summary=summary,
            actor_user_id=user_id, actor_token_id=token_id, detail=detail,
        )
    )
```

Für `thermoctl/web/auth_views.py` gilt: `POST /login` prüft Benutzer und Passwort, schreibt
bei Erfolg `login` und bei Misserfolg `login_failed` ins Audit, setzt das Cookie mit
`httponly=True`, `samesite="lax"` und `secure=settings.secure_cookies`, und antwortet mit
`303` auf `/`. Bei Misserfolg **immer dieselbe** Antwort mit Status 401, unabhängig davon,
ob der Benutzername existiert — sonst wird die Anmeldemaske zur Benutzerliste. Vor der
Antwort steht in beiden Fällen dieselbe kleine Verzögerung.

Fehlversuche werden je Benutzername gezählt und die Antwort zunehmend verzögert (etwa
`min(2 ** fehlversuche, 5)` Sekunden, zurückgesetzt bei erfolgreicher Anmeldung). Die Zählung
läuft im Speicher des Prozesses, nicht in der Datenbank — sie soll Rateversuche bremsen, nicht
überdauern. Eine **Kontosperre gibt es ausdrücklich nicht**: in einem Einhaushalt-System wäre
sie vor allem eine bequeme Möglichkeit, sich selbst auszusperren. Zwei zusätzliche Tests:

```python
def test_fehlversuche_werden_zunehmend_verzoegert(client, benutzer, monkeypatch) -> None:
    verzoegerungen: list[float] = []
    monkeypatch.setattr("thermoctl.web.auth_views.schlafen", verzoegerungen.append)
    for _ in range(3):
        client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    assert verzoegerungen == sorted(verzoegerungen)
    assert verzoegerungen[-1] > verzoegerungen[0]


def test_erfolgreiche_anmeldung_setzt_den_zaehler_zurueck(client, benutzer) -> None:
    client.post("/login", data={"username": "lino", "password": "falsch-aber-lang"})
    client.post("/login", data={"username": "lino", "password": "passwort-lang-genug"})
    from thermoctl.web.auth_views import FEHLVERSUCHE

    assert FEHLVERSUCHE.get("lino", 0) == 0
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_login.py -v`
Erwartet: 12 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/auth thermoctl/audit.py thermoctl/web tests/
git commit -m "feat: Anmeldung mit Sitzungscookie, CSRF-Schutz und Audit"
```

---

### Task 19: Einrichtungsassistent — *Claude (Sonnet)*

**Files:**
- Create: `thermoctl/web/setup_views.py`, `thermoctl/web/templates/einrichtung.html`,
  `thermoctl/setup.py`, `tests/test_setup.py`
- Modify: `thermoctl/app.py` (Setup-Token beim Start erzeugen)

**Interfaces:**
- Consumes: `hash_password`, `neues_geheimnis`, `hash_geheimnis`, alle Identitätsmodelle
- Produces:
  - `einrichtung_noetig(session) -> bool` — wahr, solange kein Benutzer existiert
  - `setup_token_erzeugen(session) -> str` — legt den Hash ab, liefert den Klartext
  - `setup_token_pruefen(session, klartext) -> bool`
  - `einrichtung_durchfuehren(session, *, username, display_name, passwort, zeitzone, token) -> User`
  - `BEISPIELGRUPPEN: dict[str, list[str]]`
  - Routen `GET /setup`, `POST /setup`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_setup.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from thermoctl.db.models.identity import AccessGroup, User
from thermoctl.db.models.operations import Setting
from thermoctl.setup import einrichtung_noetig, setup_token_erzeugen


def test_ohne_benutzer_ist_einrichtung_noetig(session: Session) -> None:
    assert einrichtung_noetig(session) is True


def test_mit_benutzer_ist_sie_nicht_mehr_noetig(session: Session, benutzer) -> None:
    assert einrichtung_noetig(session) is False


def test_setup_ohne_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": ""})
    assert antwort.status_code == 403
    assert session.query(User).count() == 0


def test_setup_mit_falschem_token_wird_abgewiesen(client: TestClient, session: Session) -> None:
    setup_token_erzeugen(session)
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin",
                                          "setup_token": "erraten"})
    assert antwort.status_code == 403
    assert session.query(User).count() == 0


def test_setup_legt_verwalter_gruppen_und_einstellungen_an(client: TestClient,
                                                           session: Session) -> None:
    marke = setup_token_erzeugen(session)
    antwort = client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                          "password": "passwort-lang-genug",
                                          "timezone": "Europe/Berlin", "setup_token": marke},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert session.query(User).count() == 1
    assert {g.name for g in session.query(AccessGroup)} == {
        "Verwaltung", "Bedienung", "Nur lesen", "Integration"
    }
    assert session.get(Setting, 1) is not None


def test_setup_token_ist_nur_einmal_verwendbar(client: TestClient, session: Session) -> None:
    marke = setup_token_erzeugen(session)
    daten = {"username": "a", "display_name": "A", "password": "passwort-lang-genug",
             "timezone": "Europe/Berlin", "setup_token": marke}
    client.post("/setup", data=daten)
    zweite = client.post("/setup", data={**daten, "username": "b"})
    assert zweite.status_code in (403, 404)
    assert session.query(User).count() == 1


def test_setup_ist_nach_abschluss_geschlossen(client: TestClient, session: Session,
                                              benutzer) -> None:
    assert client.get("/setup").status_code == 404


def test_erster_benutzer_ist_verwalter(client: TestClient, session: Session) -> None:
    marke = setup_token_erzeugen(session)
    client.post("/setup", data={"username": "lino", "display_name": "Lino",
                                "password": "passwort-lang-genug",
                                "timezone": "Europe/Berlin", "setup_token": marke})
    from thermoctl.domain.authz import hat_recht, principal_fuer_benutzer

    nutzer = session.query(User).one()
    p = principal_fuer_benutzer(session, nutzer)
    assert hat_recht(p, "user.manage") is True
    assert hat_recht(p, "setting.manage") is True


def test_setup_token_erscheint_nicht_im_klartext_in_der_datenbank(session: Session) -> None:
    from thermoctl.db.models.credential import SetupToken

    marke = setup_token_erzeugen(session)
    assert session.query(SetupToken).one().token_hash != marke
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_setup.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/setup.py` schreiben**

```python
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.passwords import hash_password
from thermoctl.auth.secrets import hash_geheimnis, neues_geheimnis
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import SetupToken
from thermoctl.db.models.identity import (
    AccessGroup, GroupPermission, User, UserAccessGroup,
)
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.operations import Setting
from thermoctl.db.models.zone import SetpointMode

log = logging.getLogger(__name__)

# Beispiele, nach der Einrichtung frei aenderbar. Leere Liste heisst 'alle Rechte'.
BEISPIELGRUPPEN: dict[str, list[str]] = {
    "Verwaltung": [],
    "Bedienung": ["zone.read", "setpoint.write", "override.create", "override.cancel",
                  "token.self"],
    "Nur lesen": ["zone.read", "device.read"],
    "Integration": ["zone.read"],
}

EINGEBAUTE_MODI = [("tag", "Tag", 0), ("nacht", "Nacht", 1), ("frostschutz", "Frostschutz", 2)]


def einrichtung_noetig(session: Session) -> bool:
    return session.scalar(select(User.id).limit(1)) is None


def setup_token_erzeugen(session: Session) -> str:
    """Erzeugt ein Einmal-Token, legt seinen Hash ab und gibt den Klartext zurueck.

    Der Aufrufer schreibt ihn ins Log. Ohne diesen Schutz gewinnt im unguenstigen Fall
    der Erste im Netz, der die Einrichtungsseite findet.
    """
    klartext = neues_geheimnis()
    session.add(SetupToken(token_hash=hash_geheimnis(klartext)))
    session.flush()
    return klartext


def setup_token_pruefen(session: Session, klartext: str) -> bool:
    marke = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_geheimnis(klartext),
            SetupToken.consumed_at.is_(None),
        )
    )
    return marke is not None


def einrichtung_durchfuehren(
    session: Session, *, username: str, display_name: str, passwort: str,
    zeitzone: str, token: str,
) -> User:
    """Legt den ersten Verwalter, die Beispielgruppen und die Einstellungszeile an."""
    if not einrichtung_noetig(session):
        raise PermissionError("Die Einrichtung ist bereits abgeschlossen.")
    marke = session.scalar(
        select(SetupToken).where(
            SetupToken.token_hash == hash_geheimnis(token),
            SetupToken.consumed_at.is_(None),
        )
    )
    if marke is None:
        raise PermissionError("Ungueltiges oder verbrauchtes Einrichtungs-Token.")

    for code, name, reihenfolge in EINGEBAUTE_MODI:
        if session.scalar(select(SetpointMode).where(SetpointMode.code == code)) is None:
            session.add(
                SetpointMode(code=code, name=name, sort_order=reihenfolge, is_builtin=True)
            )
    session.flush()

    alle = {p.code: p for p in session.scalars(select(Permission))}
    gruppen: dict[str, AccessGroup] = {}
    for name, codes in BEISPIELGRUPPEN.items():
        gruppe = AccessGroup(name=name, is_builtin=True)
        session.add(gruppe)
        session.flush()
        gruppen[name] = gruppe
        for code in codes or alle:
            session.add(
                GroupPermission(access_group_id=gruppe.id, permission_id=alle[code].id,
                                zone_id=None)
            )

    nutzer = User(username=username, display_name=display_name,
                  password_hash=hash_password(passwort))
    session.add(nutzer)
    session.flush()
    session.add(
        UserAccessGroup(user_id=nutzer.id, access_group_id=gruppen["Verwaltung"].id)
    )

    frost = session.scalar(select(SetpointMode).where(SetpointMode.code == "frostschutz"))
    session.add(Setting(id=1, timezone=zeitzone, frost_protection_mode_id=frost.id))

    marke.consumed_at = utcnow()
    session.flush()
    log.info("Einrichtung abgeschlossen", extra={"username": username})
    return nutzer
```

`GET /setup` und `POST /setup` antworten mit `404`, sobald `einrichtung_noetig()` falsch
ist — dauerhaft geschlossen, nicht nur ausgeblendet. In `create_app()` wird beim Start
geprüft: existiert kein Benutzer und kein unverbrauchtes Setup-Token, wird eines erzeugt und
**einmalig** ins Log geschrieben. Das ist die einzige Stelle im ganzen Projekt, an der ein
Geheimnis absichtlich im Log erscheint; sie gehört als Ausnahme in `logging.py` vermerkt.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_setup.py -v`
Erwartet: 9 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/setup.py thermoctl/web tests/test_setup.py
git commit -m "feat: Einrichtungsassistent mit Einmal-Token und Beispielgruppen"
```

---

### Task 20: Verwaltung von Benutzern, Gruppen und Tokens — *Codex*

**Files:**
- Create: `thermoctl/web/admin_views.py`, `thermoctl/web/templates/benutzer.html`,
  `thermoctl/web/templates/gruppen.html`, `thermoctl/web/templates/tokens.html`,
  `thermoctl/auth/tokens.py`, `tests/test_admin_views.py`

**Interfaces:**
- Consumes: `require`, `Principal`, `neues_token`, `audit.record`
- Produces:
  - `token_ausstellen(session, besitzer, name, rechte, gueltig_bis) -> tuple[ApiToken, str]`
    — wirft `Forbidden`, wenn `rechte` keine Teilmenge der Rechte des Besitzers sind
  - `token_widerrufen(session, token) -> None`
  - Routen `/benutzer`, `/gruppen`, `/tokens` mit HTMX-Teilansichten

- [ ] **Step 1: Die Tests schreiben**

`tests/test_admin_views.py` — die wesentlichen Fälle:

```python
import pytest
from sqlalchemy.orm import Session

from thermoctl.auth.tokens import token_ausstellen
from thermoctl.domain.authz import Forbidden
from tests.hilfen import benutzer_mit_rechten, zone_anlegen


def test_token_klartext_erscheint_genau_einmal(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "a", [("zone.read", None), ("token.self", None)])
    token, klartext = token_ausstellen(session, nutzer, "HA", [("zone.read", None)], None)
    assert klartext.startswith("tctl_")
    assert klartext not in (token.token_hash, token.prefix)


def test_token_mit_mehr_rechten_als_der_besitzer_wird_abgewiesen(session: Session) -> None:
    nutzer = benutzer_mit_rechten(session, "b", [("zone.read", None), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Zuviel", [("zone.manage", None)], None)


def test_token_mit_fremder_zone_wird_abgewiesen(session: Session) -> None:
    bad = zone_anlegen(session, "bad")
    kueche = zone_anlegen(session, "kueche")
    nutzer = benutzer_mit_rechten(session, "c", [("zone.read", bad.id), ("token.self", None)])
    with pytest.raises(Forbidden):
        token_ausstellen(session, nutzer, "Fremd", [("zone.read", kueche.id)], None)


def test_benutzerliste_braucht_user_manage(client_als) -> None:
    ohne = client_als([("zone.read", None)])
    assert ohne.get("/benutzer").status_code == 403
    mit = client_als([("user.manage", None)])
    assert mit.get("/benutzer").status_code == 200


def test_passwort_hash_erscheint_in_keiner_ansicht(client_als) -> None:
    antwort = client_als([("user.manage", None)]).get("/benutzer")
    assert "$argon2id$" not in antwort.text
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_admin_views.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: `thermoctl/auth/tokens.py` schreiben**

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_geheimnis, neues_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.domain.authz import Forbidden, hat_recht, principal_fuer_benutzer


def token_ausstellen(
    session: Session, besitzer: User, name: str,
    rechte: list[tuple[str, int | None]], gueltig_bis: datetime | None,
) -> tuple[ApiToken, str]:
    """Stellt ein Token aus. Der Klartext erscheint genau einmal — hier.

    Der Umfang muss eine Teilmenge der Rechte des Besitzers sein. Geprueft wird das
    zusaetzlich bei jeder Anfrage (siehe principal_fuer_token); hier faellt der Fehler
    frueh und mit einer verstaendlichen Meldung auf.
    """
    p = principal_fuer_benutzer(session, besitzer)
    for code, zone_id in rechte:
        if not hat_recht(p, code, zone_id):
            raise Forbidden(
                f"{besitzer.username} kann kein Token mit {code} ausstellen — "
                "das Recht fehlt ihm selbst."
            )

    klartext, prefix, hash_wert = neues_token()
    token = ApiToken(user_id=besitzer.id, name=name, prefix=prefix,
                     token_hash=hash_wert, expires_at=gueltig_bis)
    session.add(token)
    session.flush()

    alle = {code: pid for code, pid in session.execute(
        select(Permission.code, Permission.id)
    ).all()}
    for code, zone_id in rechte:
        session.add(ApiTokenPermission(api_token_id=token.id, permission_id=alle[code],
                                       zone_id=zone_id))
    session.flush()
    return token, klartext


def token_aufloesen(session: Session, klartext: str) -> ApiToken | None:
    teile = klartext.split("_", 2)
    if len(teile) != 3 or teile[0] != "tctl":
        return None
    token = session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_geheimnis(teile[2]))
    )
    if token is None or token.revoked_at is not None:
        return None
    if token.expires_at is not None and token.expires_at <= utcnow():
        return None
    token.last_used_at = utcnow()
    return token


def token_widerrufen(session: Session, token: ApiToken) -> None:
    token.revoked_at = utcnow()
```

Die Views prüfen jeweils zu Beginn `require(principal, "user.manage")`,
`require(principal, "group.manage")` beziehungsweise `require(principal, "token.self")` und
liefern bei `Forbidden` den Status 403. Ausgegeben werden **nie** `password_hash` oder
`token_hash`.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_admin_views.py -v`
Erwartet: 5 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/web thermoctl/auth/tokens.py tests/test_admin_views.py
git commit -m "feat: Verwaltung von Benutzern, Gruppen und Tokens"
```

---

### Task 21: REST-Adapter — *Codex*

**Files:**
- Create: `thermoctl/api/__init__.py`, `thermoctl/api/routes.py`,
  `thermoctl/api/schemas.py`, `tests/test_api.py`
- Modify: `thermoctl/app.py`

**Interfaces:**
- Consumes: `token_aufloesen`, `principal_fuer_token`, `require`, `visible_zones`
- Produces: `GET /api/v1/zones`, `GET /api/v1/zones/{id}`, `GET /api/v1/me`,
  `POST /api/v1/zones/{id}/override`, `DELETE /api/v1/zones/{id}/override`.
  Authentifizierung über `Authorization: Bearer tctl_…`

- [ ] **Step 1: Die Tests schreiben**

`tests/test_api.py`:

```python
def test_ohne_token_kein_zugriff(client) -> None:
    assert client.get("/api/v1/zones").status_code == 401


def test_ungueltiges_token_wird_abgewiesen(client) -> None:
    antwort = client.get("/api/v1/zones", headers={"Authorization": "Bearer tctl_x_y"})
    assert antwort.status_code == 401


def test_token_sieht_nur_erlaubte_zonen(client, token_fuer) -> None:
    """visible_zones muss auch hier wirken — sonst leckt die API, was die UI verbirgt."""
    kopf = token_fuer([("zone.read", "bad")])
    namen = [z["name"] for z in client.get("/api/v1/zones", headers=kopf).json()]
    assert namen == ["bad"]


def test_zugriff_auf_fremde_zone_ergibt_404(client, token_fuer) -> None:
    """404 und nicht 403: ein 403 verraet, dass die Zone existiert."""
    kopf = token_fuer([("zone.read", "bad")])
    assert client.get("/api/v1/zones/2", headers=kopf).status_code == 404


def test_uebersteuern_ohne_recht_wird_abgewiesen(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 403


def test_uebersteuern_mit_recht_legt_eintrag_an(client, token_fuer, session) -> None:
    from thermoctl.db.models.override import ZoneOverride

    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201
    eintrag = session.query(ZoneOverride).one()
    assert eintrag.ends_at is not None  # Dauer wird beim Anlegen ausgerechnet
    assert eintrag.created_by_token_id is not None


def test_api_braucht_kein_csrf_token(client, token_fuer) -> None:
    """Token-Anfragen schicken kein Cookie und sind damit nicht CSRF-gefaehrdet."""
    kopf = token_fuer([("zone.read", "bad"), ("override.create", "bad")])
    antwort = client.post("/api/v1/zones/1/override", headers=kopf,
                          json={"temperature_c": "22.0", "dauer_minuten": 30})
    assert antwort.status_code == 201


def test_token_hash_erscheint_in_keiner_antwort(client, token_fuer) -> None:
    kopf = token_fuer([("zone.read", "bad"), ("token.self", None)])
    assert "token_hash" not in client.get("/api/v1/me", headers=kopf).text
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `.venv/bin/pytest tests/test_api.py -v`
Erwartet: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Den Adapter schreiben**

Der Adapter bleibt dünn: er löst das Token auf, baut daraus einen `Principal`, ruft
`visible_zones` beziehungsweise `require` und übersetzt `Forbidden` in 403. **Keine Regel
wird hier zum zweiten Mal implementiert** (Grundsatz 6). Für die Übersteuerung ruft er
dieselbe Domänenfunktion, die auch die HTMX-Ansicht benutzt, und rechnet `dauer_minuten` bei
Bedarf über `naechster_punkt` in ein konkretes `ends_at` um.

Ein Zugriff auf eine nicht sichtbare Zone antwortet mit **404, nicht 403** — sonst verrät
die Antwort, dass es die Zone gibt.

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/pytest tests/test_api.py -v`
Erwartet: 8 bestanden.

- [ ] **Step 5: Commit**

```bash
git add thermoctl/api tests/test_api.py
git commit -m "feat: REST-Adapter ueber derselben Domaenenlogik"
```

---

### Task 22: Architekturtest und Abschluss — *Codex*

**Files:**
- Create: `tests/test_architektur.py`
- Modify: `docs/STATUS.md`, `README.md`

**Interfaces:**
- Consumes: alles Bisherige
- Produces: ein Test, der die Abhängigkeitsrichtung festhält

- [ ] **Step 1: Den Test schreiben**

`tests/test_architektur.py`:

```python
import ast
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent / "thermoctl"
VERBOTEN_FUER_DOMAIN = ("thermoctl.web", "thermoctl.api", "fastapi")


def _importe(datei: Path) -> set[str]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen.add(knoten.module)
    return namen


def test_domaene_kennt_keinen_adapter() -> None:
    """Eine Regel wird einmal implementiert (Grundsatz 6).

    Sobald die Domaene einen Adapter importiert, weicht diese Trennung schleichend auf —
    deshalb steht sie hier als Test und nicht nur als Absicht in der Spezifikation.
    """
    verstoesse = [
        f"{datei.relative_to(WURZEL)} importiert {name}"
        for datei in (WURZEL / "domain").rglob("*.py")
        for name in _importe(datei)
        if name.startswith(VERBOTEN_FUER_DOMAIN)
    ]
    assert not verstoesse, "\n".join(verstoesse)


def test_kein_modell_nutzt_verbotene_spaltentypen() -> None:
    """Kein ENUM, kein SET, keine JSON-Spalte — SQLite kann sie nicht."""
    verstoesse = [
        f"{datei.relative_to(WURZEL)}: {wort}"
        for datei in (WURZEL / "db" / "models").rglob("*.py")
        for wort in ("Enum(", "JSON(", "SET(")
        if wort in datei.read_text(encoding="utf-8")
    ]
    assert not verstoesse, "\n".join(verstoesse)
```

- [ ] **Step 2: Test laufen lassen**

Run: `.venv/bin/pytest tests/test_architektur.py -v`
Erwartet: 2 bestanden. Schlägt einer fehl, ist das ein echter Befund und kein Testproblem.

- [ ] **Step 3: Gesamtlauf gegen beide Datenbanken**

```bash
THERMOCTL_TEST_DATABASE_URL=sqlite:///./test.db .venv/bin/pytest -v
THERMOCTL_TEST_DATABASE_URL=mysql+pymysql://root:pruefen@127.0.0.1:3306/thermoctl_test \
  .venv/bin/pytest -v
.venv/bin/ruff check . && .venv/bin/mypy thermoctl
```

Erwartet: alles grün unter beiden Datenbanken.

- [ ] **Step 4: `README.md` und `docs/STATUS.md` schreiben**

`README.md` beschreibt: was thermoctl ist, wie man es mit Docker startet, welche
Umgebungsvariablen nötig sind, und dass das Einrichtungs-Token beim ersten Start im Log
steht. **Keine Beispielwerte für Secrets.**

`docs/STATUS.md` auf „Teilprojekt 1 abgeschlossen, Teilprojekt 2 als Nächstes" setzen.

- [ ] **Step 5: Commit und Freigabe**

```bash
git add tests/test_architektur.py README.md docs/STATUS.md
git commit -m "test: Architekturgrenzen festhalten, Teilprojekt 1 abgeschlossen"
git tag v0.1.0 && git push origin main --tags
```

Der Tag erzeugt das erste `latest`-Image. Vorher prüfen, dass `gh run watch` für
`docker.yml` grün ist.

---

## Nach Abschluss

Teilprojekt 2 (Geräte-Anbindung im Schattenbetrieb) bekommt einen eigenen Zyklus aus
Brainstorming, Spezifikation und Plan. Offen bleibt, was in Abschnitt 10 der Spezifikation
steht: Datenübernahme aus dem Altschema, Umgang mit den alten MQTT-Topics, und dass
`vm130-nginx` bis zum abgeschlossenen Cutover unangetastet die Rückfallebene bleibt.
