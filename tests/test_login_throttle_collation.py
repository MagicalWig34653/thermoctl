"""Der Drosselungsschlüssel folgt der Kollation der Datenbank (Karte A3AEF34D).

Bis hierher war der Schlüssel der getippte Name, in Python gefaltet. Was die Kollation von
MariaDB (`utf8mb4_uca1400_ai_ci`) darüber hinaus gleichsetzt -- das Kombinationszeichen U+034F,
das Tatweel U+0640, `ø` = `o` und mehr --, bekam einen eigenen Zähler: ein Name mit einem solchen
eingestreuten Zeichen meldet mit dem richtigen Passwort an, wartete aber wie ein frischer Name.
Wer das wusste, riet für ein Konto mit beliebig vielen Schreibweisen ungebremst.

Das Orakel bleibt dabei zu: Der Schlüssel hängt nur vom getippten Namen und von der Kollation ab,
nie davon, ob es das Konto gibt.
"""

from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, make_url, select, text
from sqlalchemy.orm import Session

import thermoctl.web.shared.login as login
from thermoctl.db.models.identity import User
from thermoctl.web.shared.login import FEHLVERSUCHE

FALSCH = "falsch-aber-lang"


@pytest.fixture(autouse=True)
def _clean_counters() -> Any:
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()
    yield
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()


def _needs_mariadb(session: Session) -> None:
    if session.get_bind().dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite vergleicht Namen binär, "
            "dort gibt es keine zweite Schreibweise eines Kontos"
        )


def _post(client: Any, typed: str) -> None:
    client.cookies.clear()
    response = client.post("/ui-api/login", json={"username": typed, "password": FALSCH})
    assert response.status_code == 401


def test_spellings_only_the_collation_equates_share_one_counter(
    client: Any, session: Session, user: User
) -> None:
    """`li͏no` und `liـno` sind auf MariaDB das Konto `lino` -- und dessen Zähler."""
    _needs_mariadb(session)
    spellings = ("lino", "li͏no", "liـno", "LI͏NـO ")
    for typed in spellings:
        # Vorbedingung: Die Datenbank hält jede dieser Schreibweisen wirklich für das Konto.
        assert session.scalar(select(User.id).where(User.username == typed)) == user.id
    for typed in spellings:
        _post(client, typed)

    assert list(FEHLVERSUCHE.values()) == [4]


def test_collation_spellings_wait_the_same_for_existing_and_invented_names(
    client: Any, session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Orakel: Ein erfundener Name mit denselben Einstreuungen wartet genauso."""
    _needs_mariadb(session)
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(login, "sleep", record)

    def series(names: tuple[str, ...]) -> list[float]:
        waits.clear()
        for typed in names:
            _post(client, typed)
        return list(waits)

    existing = ("lino", "li͏no", "liـno", "lـino", "lino")
    invented = ("erfunden", "erf͏unden", "erfـunden", "eـrfunden", "erfunden")
    assert series(existing) == [1, 2, 4, 5, 5]
    assert series(invented) == [1, 2, 4, 5, 5]


def test_every_spelling_the_database_matches_has_the_accounts_key(
    session: Session, user: User
) -> None:
    """Gemessen statt aufgezählt: Jedes eingestreute Zeichen aus U+0020 bis U+2FFF, mit dem die
    Datenbank den Namen noch für `lino` hält, ergibt dessen Schlüssel. Läuft auf beiden
    Datenbanken; unter SQLite (binär) trifft nur `lino` selbst."""
    account_key = login.throttle_key(session, "lino")
    matched = 0
    for code in range(0x20, 0x3000):
        typed = f"li{chr(code)}no"
        same_account = session.scalar(
            select(func.count()).select_from(User).where(User.username == typed)
        )
        if same_account:
            matched += 1
            assert login.throttle_key(session, typed) == account_key, f"U+{code:04X}"
    if session.get_bind().dialect.name in ("mysql", "mariadb"):
        assert matched > 100  # die Probe misst wirklich etwas
    assert login.throttle_key(session, "lina") != account_key
    assert login.throttle_key(session, "lino x") != account_key


def test_trailing_blanks_do_not_split_the_counter(session: Session, user: User) -> None:
    """MariaDB füllt beim Vergleich mit Leerzeichen auf (PAD SPACE): `lino ` ist `lino`."""
    assert login.throttle_key(session, "lino  ") == login.throttle_key(session, "lino")
    assert login.throttle_key(session, "lino ") == login.throttle_key(session, "lino")


@pytest.mark.parametrize(
    "collation",
    [
        "utf8mb4_uca1400_ai_ci",
        "utf8mb4_uca1400_as_ci",
        "utf8mb4_uca1400_as_cs",
        "utf8mb4_uca1400_ai_cs",
        "utf8mb4_uca1400_swedish_as_ci",
        "utf8mb4_general_ci",
        "utf8mb4_bin",
    ],
)
def test_the_key_holds_for_other_collations_of_the_column(
    client: Any, session: Session, user: User, monkeypatch: pytest.MonkeyPatch, collation: str
) -> None:
    """Auch wenn die Spalte anders vergleicht als der Standard: Was diese Kollation für `lino`
    hält, hat dessen Schlüssel -- über die ganze Anmeldung eines Versuchs gerechnet."""
    _needs_mariadb(session)
    monkeypatch.setattr(
        login, "_username_collation", lambda current: ("utf8mb4", collation)
    )
    candidates = (
        "lino", "lino ", "lino\u00a0", "lino\u00a0\u00a0", "lino\u00a0 ", "lino \u00a0",
        "lino\u034f", "lino\u00a0\u034f", "li\u034fno", "LINO\u00a0", "li\u0640no\u00a0 ",
    )
    equal = text(
        f"SELECT CONVERT(:typed USING utf8mb4) COLLATE {collation} = "
        f"CONVERT('lino' USING utf8mb4) COLLATE {collation}"
    )
    same_account = [t for t in candidates if session.execute(equal, {"typed": t}).scalar()]
    assert "lino " in same_account or "nopad" in collation  # die Probe misst etwas
    expected = login.throttle_key(session, "lino")
    for typed in same_account:
        assert login.throttle_key(session, typed) == expected, repr(typed)
    assert login.throttle_key(session, "lina") != expected


def test_the_key_can_be_computed_under_every_collation_of_the_server(
    session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jede utf8mb4-Kollation, die der Server kennt: Der Schlüssel lässt sich bilden, und was
    die Kollation für `lino` hält, hat dessen Schlüssel -- auch mit angehängten Leerzeichen und
    Auffüll-Zeichen, die in mehrstufigen Gewichten (`_as_ci`, `thai_520_w2`) mitten im Gewicht
    stehen."""
    _needs_mariadb(session)
    collations = session.execute(
        text(
            "SELECT full_collation_name FROM information_schema.collation_character_set_"
            "applicability WHERE character_set_name = 'utf8mb4'"
        )
    ).scalars().all()
    assert len(collations) > 50
    tails = (
        "", " ", "  ", "\u00a0", "\u00a0\u00a0\u00a0\u00a0", "\u00a0 ", " \u00a0",
        "\u034f", "\u00a0\u034f", "\u2000", "\u3000", "\u200b",
    )
    candidates = [base + tail for base in ("lino", "li\u034fno", "LINO") for tail in tails]
    split: dict[str, list[str]] = {}
    for collation in collations:
        monkeypatch.setattr(
            login, "_username_collation", lambda current, name=collation: ("utf8mb4", name)
        )
        equal = ", ".join(
            f"CONVERT(:n{number} USING utf8mb4) COLLATE {collation} = "
            f"CONVERT('lino' USING utf8mb4) COLLATE {collation}"
            for number in range(len(candidates))
        )
        matches = session.execute(
            text(f"SELECT {equal}"), {f"n{n}": typed for n, typed in enumerate(candidates)}
        ).one()
        expected = login.throttle_key(session, "lino")
        wrong = [
            ascii(typed)
            for typed, same in zip(candidates, matches, strict=True)
            if same and login.throttle_key(session, typed) != expected
        ]
        if wrong:
            split[collation] = wrong
        assert login.throttle_key(session, "lina") != expected, collation
    assert split == {}


def test_long_names_with_expanding_characters_still_share_the_counter(
    session: Session, user: User
) -> None:
    """U+FDFA wiegt wie viele Zeichen: Auch ein langer Name daraus hat mit und ohne Anhang, den
    die Datenbank übergeht, denselben Schlüssel."""
    long_name = "\ufdfa" * 60
    assert login.throttle_key(session, long_name) == login.throttle_key(session, long_name + " ")
    assert login.throttle_key(session, long_name) != login.throttle_key(session, "lino")


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self.row

    def scalar_one(self) -> Any:
        assert self.row is not None
        return self.row[0]


class _FakeSession:
    """Steht für eine Datenbank, die in diesem Lauf nicht da ist -- damit beide CI-Läufe (SQLite
    und MariaDB) jeden Zweig von `throttle_key` sehen, nicht nur den ihrer eigenen Datenbank."""

    def __init__(self, dialect: str, *rows: tuple[Any, ...] | None) -> None:
        self.rows = list(rows)
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []
        self.bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect),
            engine=SimpleNamespace(url=make_url(f"mysql+pymysql://probe@localhost/{uuid4().hex}")),
        )

    def get_bind(self) -> Any:
        return self.bind

    def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return _Result(self.rows.pop(0))


def _key(fake: _FakeSession, typed: str) -> str:
    return login.throttle_key(fake, typed)  # type: ignore[arg-type]


def test_a_byte_comparing_database_gets_the_python_fold() -> None:
    fake = _FakeSession("sqlite")
    assert _key(fake, "LINO ") == login.login_key("LINO ")
    assert fake.statements == []  # keine Abfrage nötig


def test_the_database_weighs_and_pads_the_typed_name() -> None:
    fake = _FakeSession("mariadb", ("utf8mb4", "utf8mb4_uca1400_as_ci"), ("21B0",))
    assert _key(fake, "li ") == f"kollation:{sha256(b'21B0').hexdigest()}"
    assert "COLLATE utf8mb4_uca1400_as_ci AS CHAR(512)" in fake.statements[1]
    assert fake.parameters[1] == {"name": "li "}  # ungekürzt: das Auffüllen macht die Datenbank


def test_the_collation_is_read_once_per_database() -> None:
    fake = _FakeSession(
        "mariadb", ("utf8mb4", "utf8mb4_uca1400_ai_ci"), ("21B0",), ("21B1",)
    )
    assert _key(fake, "a") != _key(fake, "b")
    assert len(fake.statements) == 3


@pytest.mark.parametrize(
    "column",
    [
        None,  # Spalte nicht gefunden
        ("utf8mb4", None),
        ("utf8mb4", "utf8mb4_bin; DROP TABLE user"),  # nie in den SQL-Text
    ],
)
def test_an_unreadable_collation_falls_back_to_the_python_fold(
    column: tuple[Any, ...] | None,
) -> None:
    fake = _FakeSession("mariadb", column)
    assert _key(fake, "Lino") == login.login_key("Lino")
    assert len(fake.statements) == 1
