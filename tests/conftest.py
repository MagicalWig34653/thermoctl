import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.orm import Session

from tests.hilfen import benutzer_mit_rechten, quelle
from thermoctl.app import create_app
from thermoctl.auth.dependencies import get_session
from thermoctl.auth.passwords import hash_password
from thermoctl.auth.sessions import COOKIE_NAME, sitzung_anlegen
from thermoctl.config import Settings, get_settings
from thermoctl.db.base import Base
from thermoctl.db.engine import create_engine_from_settings
from thermoctl.db.models.identity import AccessGroup, User, UserAccessGroup
from thermoctl.db.models.lookup import ACTOR_SOURCES, PERMISSIONS, ActorSource, Permission

TEST_DATABASE_URL = os.environ.get("THERMOCTL_TEST_DATABASE_URL", "sqlite:///./test.db")


def _migrationsdatenbank_url(basis_url: str) -> str:
    """Leitet die Datenbank fuer die Migrationstests von ``TEST_DATABASE_URL`` ab.

    Die Migrationstests fuehren ``alembic upgrade``/``downgrade`` gegen eine **eigene**
    Datenbank aus, getrennt von der Fixture ``engine``: Sonst legt ``Base.metadata.create_all()``
    dieselben Tabellen an, die Alembic ebenfalls anlegen will, und die Migration scheitert an
    einer bereits vorhandenen Tabelle. Die Ableitung aus ``TEST_DATABASE_URL`` statt einer
    zweiten Konfiguration stellt sicher, dass die Migrationstests niemals unbemerkt gegen eine
    andere Datenbank laufen als der Rest der Suite.
    """
    url = make_url(basis_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            # Eine In-Memory-Datenbank gehoert ohnehin genau einem Prozess. Die
            # Migrationstests laufen als eigener Unterprozess und bekommen deshalb
            # eine eigene, leere Datenbank — eine abgeleitete URL waere hier
            # gegenstandslos.
            return basis_url
        pfad = Path(url.database)
        neuer_pfad = pfad.with_name(f"{pfad.stem}-migrations{pfad.suffix}")
        return url.set(database=str(neuer_pfad)).render_as_string(hide_password=False)
    return url.set(database=f"{url.database}_migrations").render_as_string(hide_password=False)


MIGRATIONS_DATABASE_URL = _migrationsdatenbank_url(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, secret_key="t" * 32
    )


@pytest.fixture(scope="session", autouse=True)
def _umgebung_fuer_die_ganze_sitzung(settings: Settings) -> Iterator[None]:
    """Setzt die Pflichtvariablen fuer den ganzen Lauf und schneidet die `.env` ab.

    Jeder Test, der `create_app()` aufruft, braucht sie — `Settings` verlangt
    `database_url` und `secret_key`. Die Fixture ``client`` setzt sie bisher selbst,
    aber die Waechter in `test_endpunktabdeckung.py` und `test_csrf.py` zaehlen Routen
    auf, ohne einen Client zu bauen.

    Bis hierher ging das gut, weil `get_settings` zwischenspeichert und zufaellig noch
    ein gueltiger Eintrag vom vorherigen Test im Cache lag. Als der Waechter ans Ende
    des Laufs sortiert wurde, lag dort keiner mehr — und oertlich fiel es trotzdem nicht
    auf, weil im Projektverzeichnis eine `.env` liegt, die pydantic von sich aus liest.
    In der CI gibt es keine. Ein Test darf nicht davon abhaengen, wer vor ihm lief und
    welche Dateien zufaellig herumliegen.

    Der zweite Anlauf auf denselben Fehler: Damals wurden die beiden Pflichtvariablen
    gesetzt, die `.env` aber weiter gelesen. Wer dort spaeter etwas eintrug -- eine
    Passkey-Kennung etwa -- sah Tests rot werden, die mit seiner Aenderung nichts zu tun
    hatten. Umgekehrt ist der gefaehrlichere Fall: eine Einstellung, die oertlich gesetzt
    ist und in der CI fehlt, laesst Tests gruen aussehen, die dort scheitern werden.
    `THERMOCTL_ENV_FILE=""` schneidet die Datei ab; die Suite sieht nur noch, was sie
    selbst setzt.
    """
    marke = pytest.MonkeyPatch()
    marke.setenv("THERMOCTL_ENV_FILE", "")
    marke.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    marke.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    yield
    marke.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrations_database_url() -> Iterator[str]:
    """Stellt sicher, dass die Migrationsdatenbank existiert, und liefert ihre URL.

    Unter MariaDB existiert das Schema fuer die Migrationstests vor dem ersten Lauf
    noch nicht — es wird hier per ``CREATE DATABASE IF NOT EXISTS`` selbst angelegt.
    Unter SQLite legt die Datei-URL die Datenbank beim ersten Verbindungsaufbau
    automatisch an, hier ist nichts vorzubereiten.
    """
    ziel_url = make_url(MIGRATIONS_DATABASE_URL)
    if ziel_url.get_backend_name() != "sqlite":
        server_url = URL.create(
            ziel_url.drivername,
            username=ziel_url.username,
            password=ziel_url.password,
            host=ziel_url.host,
            port=ziel_url.port,
        )
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as verbindung:
                verbindung.execute(text(f"CREATE DATABASE IF NOT EXISTS `{ziel_url.database}`"))
                verbindung.commit()
        finally:
            server_werk.dispose()

    yield MIGRATIONS_DATABASE_URL

    # Symmetrisch zur Fixture `engine`, die ihre Tabellen wieder entfernt: Bleibt die
    # Migrationsdatenbank liegen, laeuft der naechste Durchlauf gegen einen alten
    # Schemastand und scheitert an etwas, das mit dem Code nichts zu tun hat.
    if ziel_url.get_backend_name() == "sqlite":
        if ziel_url.database and ziel_url.database != ":memory:":
            Path(ziel_url.database).unlink(missing_ok=True)
    else:
        server_werk = create_engine(server_url, pool_pre_ping=True, future=True)
        try:
            with server_werk.connect() as verbindung:
                verbindung.execute(text(f"DROP DATABASE IF EXISTS `{ziel_url.database}`"))
                verbindung.commit()
        finally:
            server_werk.dispose()


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    werk = create_engine_from_settings(settings)
    Base.metadata.drop_all(werk)
    Base.metadata.create_all(werk)
    # Die Audit-Quellen gehoeren zum Schema wie die Rechte: Die Migration legt sie in
    # jeder echten Datenbank an. Vorher legte jeder Test die eine an, die er zufaellig
    # brauchte, und wer eine vergass, scheiterte an einem IntegrityError ueber
    # `audit_event.source_id` -- einer Meldung, die die Ursache nirgends nennt. Seit die
    # Quelle vom Adapter durchgereicht wird (web, api, mcp), braucht fast jeder
    # schreibende Test mehr als eine.
    #
    # Anders als die Rechte darf das hier stehen: Kein Test legt eine ActorSource von
    # Hand an, es gibt also keine UNIQUE-Kollision wie bei `Permission`.
    with Session(werk) as sitzung:
        vorhandene = {q.code for q in sitzung.query(ActorSource)}
        for code, bezeichnung in ACTOR_SOURCES:
            if code not in vorhandene:
                sitzung.add(ActorSource(code=code, label=bezeichnung))
        sitzung.commit()
    yield werk
    Base.metadata.drop_all(werk)
    werk.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Jeder Test laeuft in einer Transaktion, die anschliessend zurueckgerollt wird.

    Die Sitzung tritt der aeusseren Transaktion ueber ein Savepoint bei
    (``join_transaction_mode="create_savepoint"``). Loest ein Test absichtlich einen
    Fehler aus (z. B. einen ``IntegrityError`` bei einer Constraint-Verletzung) und die
    Sitzung rollt deshalb zurueck, betrifft das nur das Savepoint — die aeussere
    Transaktion bleibt bestehen und laesst sich im Teardown noch zurueckrollen.

    Grenze dieser Isolation: Zurueckgerollt werden Datenaenderungen, nicht der Zaehler
    fuer Auto-Increment-Schluessel — und unter MariaDB fuehrt DDL zu einem impliziten
    Commit. Tests duerfen sich deshalb nicht auf bestimmte Kennungswerte verlassen und
    keine Schemaaenderungen vornehmen. Der Preis dieser Loesung ist bewusst gewaehlt:
    ein Schemaaufbau je Test waere unter MariaDB unertraeglich langsam.

    Dadurch teilen sich alle Tests ein Schema, ohne einander zu beeinflussen — unter
    MariaDB waere ein Neuaufbau je Test sonst spuerbar langsam.
    """
    verbindung = engine.connect()
    transaktion = verbindung.begin()
    sitzung = Session(
        bind=verbindung, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield sitzung
    finally:
        sitzung.close()
        transaktion.rollback()
        verbindung.close()


@pytest.fixture(autouse=True)
def _berechtigungen_fuer_einrichtungsassistenten(
    request: pytest.FixtureRequest, session: Session
) -> None:
    """Seedet die Berechtigungstabelle innerhalb der Testtransaktion von ``tests/test_setup.py``.

    In Produktion sind alle Codes aus `Permission` bereits durch die Migration
    `3685e30419a4_nachschlagetabellen` vorhanden, bevor der Einrichtungsassistent je
    laeuft. `Base.metadata.create_all()` in der Fixture ``engine`` legt dagegen nur das
    Schema an, keine Referenzdaten — ohne diese Zeilen schluege
    `einrichtung_durchfuehren()` mit einem `KeyError` fehl, weil sie den Beispielgruppen
    vorhandene Berechtigungen zuordnet, statt sie selbst anzulegen.

    Bewusst nicht in der session-weiten Fixture ``engine`` seedebar: dort waeren die
    Zeilen fuer die gesamte Testsitzung sichtbar und wuerden `test_lookup.py`s
    `test_berechtigung_kennt_ihren_geltungsbereich` an der UNIQUE-Bedingung auf `code`
    scheitern lassen, die dort bewusst ein frisches `Permission("zone.read")` anlegt.
    Deshalb hier je Test, beschraenkt auf `test_setup.py`, und ueber dieselbe Sitzung
    wie der Test selbst — die Zeilen verschwinden mit deren Rollback wieder.
    """
    if request.node.fspath.basename != "test_setup.py":
        return
    vorhandene = {p.code for p in session.query(Permission)}
    for code, beschreibung, zonenbezogen in PERMISSIONS:
        if code not in vorhandene:
            session.add(Permission(code=code, description=beschreibung,
                                   is_zone_scoped=zonenbezogen))
    session.flush()


@pytest.fixture
def client(
    settings: Settings, session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Baut die App gegen die Testdatenbank und laesst Anfragen in derselben,
    per Test zurueckgerollten Transaktion laufen wie die Fixture ``session`` —
    sonst saehe ein Test nicht, was ein per HTTP ausgeloester Vorgang geschrieben hat.
    """
    monkeypatch.setenv("THERMOCTL_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("THERMOCTL_SECRET_KEY", settings.secret_key.get_secret_value())
    get_settings.cache_clear()
    app = create_app()

    def _session_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def client_als(
    client: TestClient, session: Session
) -> Callable[[list[tuple[str, int | None]]], TestClient]:
    zaehler = 0

    def _client_als(rechte: list[tuple[str, int | None]]) -> TestClient:
        nonlocal zaehler
        zaehler += 1
        nutzer = benutzer_mit_rechten(session, f"web-{zaehler}", rechte)
        _sitzung, geheimnis = sitzung_anlegen(session, nutzer, 3600)
        client.cookies.set(COOKIE_NAME, geheimnis)
        return client

    return _client_als


@pytest.fixture
def benutzer(session: Session) -> User:
    """Legt den Benutzer ``lino`` mit gehashtem Passwort und der Gruppe *Verwaltung* an."""
    quelle(session, "web")
    nutzer = User(
        username="lino",
        display_name="Lino",
        password_hash=hash_password("passwort-lang-genug"),
    )
    session.add(nutzer)
    session.flush()
    gruppe = AccessGroup(name="Verwaltung", is_builtin=True)
    session.add(gruppe)
    session.flush()
    session.add(UserAccessGroup(user_id=nutzer.id, access_group_id=gruppe.id))
    session.flush()
    return nutzer

@pytest.fixture(autouse=True)
def _ohne_echte_wartezeit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Anmeldedrosselung schlaeft nicht wirklich, waehrend Tests laufen.

    Die Drosselung selbst bleibt aktiv und wird von
    `test_fehlversuche_werden_zunehmend_verzoegert` geprueft — jener Test ersetzt
    `schlafen` selbst und sieht diese Fixture dadurch gar nicht. Ohne sie kostet
    jede Anmeldung in jedem Test echte Sekunden: die Suite lief dadurch von zwei
    auf dreiunddreissig Sekunden hoch, und eine langsame Suite wird seltener
    ausgefuehrt.
    """
    monkeypatch.setattr("thermoctl.web.auth_views.schlafen", lambda sekunden: None)

@pytest.fixture
def angemeldeter_client(
    client_als: Callable[[list[tuple[str, int | None]]], TestClient],
) -> TestClient:
    """Ein Client mit allen Rechten, fuer den Rauchtest ueber alle Seiten.

    Bewusst mit vollem Rechteumfang: Der Rauchtest fragt, ob eine Seite ueberhaupt
    existiert und ohne Fehler antwortet — ob sie die Rechte richtig prueft, gehoert in
    die Tests der jeweiligen Ansicht.
    """
    return client_als(
        [
            ("zone.read", None),
            ("zone.manage", None),
            ("device.read", None),
            ("device.manage", None),
            ("user.manage", None),
            ("group.manage", None),
            ("token.self", None),
            ("token.manage", None),
            ("audit.read", None),
            ("setting.manage", None),
            ("mode.manage", None),
            ("setpoint.write", None),
            ("schedule.manage", None),
            ("control.arm", None),
        ]
    )

def pytest_configure(config: pytest.Config) -> None:
    """Sammelstelle fuer die Endpunkte, die waehrend des Laufs tatsaechlich
    aufgerufen wurden — ausgewertet von tests/test_endpunktabdeckung.py."""
    config._aufgerufene_endpunkte = set()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _endpunkte_mitschreiben(request: pytest.FixtureRequest) -> Iterator[None]:
    """Zeichnet jeden HTTP-Aufruf auf, den ein Test ueber den TestClient macht."""
    from starlette.testclient import TestClient as _TestClient

    original = _TestClient.request
    gesammelt = request.config._aufgerufene_endpunkte  # type: ignore[attr-defined]

    def aufzeichnend(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        pfad = str(url).split("?")[0]
        for praefix in ("http://testserver", "https://testserver"):
            if pfad.startswith(praefix):
                pfad = pfad[len(praefix) :]
        gesammelt.add((str(method).upper(), pfad or "/"))
        return original(self, method, url, *args, **kwargs)

    _TestClient.request = aufzeichnend  # type: ignore[method-assign]
    try:
        yield
    finally:
        _TestClient.request = original  # type: ignore[method-assign]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Zieht die Endpunktabdeckung ans Ende des Laufs.

    Sie wertet die Mitschrift aller HTTP-Aufrufe aus, die waehrend des Laufs entsteht.
    Nach Dateinamen sortiert liefe sie mitten im Lauf — sie saehe dann nur, was bis
    dahin aufgerufen wurde, und meldete alles Spaetere als ungeprueft. Solange der
    Waechter durch die verschachtelten Router von FastAPI ohnehin ins Leere lief, fiel
    das nicht auf.
    """
    items.sort(key=lambda item: item.fspath.basename == "test_endpunktabdeckung.py")
