"""Der Drosselungsschlüssel folgt der Kollation der Datenbank (Karte A3AEF34D).

Bis hierher war der Schlüssel der getippte Name, in Python gefaltet. Was die Kollation von
MariaDB (`utf8mb4_uca1400_ai_ci`) darüber hinaus gleichsetzt -- das Kombinationszeichen U+034F,
das Tatweel U+0640, `ø` = `o` und mehr --, bekam einen eigenen Zähler: ein Name mit einem solchen
eingestreuten Zeichen meldet mit dem richtigen Passwort an, wartete aber wie ein frischer Name.
Wer das wusste, riet für ein Konto mit beliebig vielen Schreibweisen ungebremst.

Das Orakel bleibt dabei zu: Der Schlüssel hängt nur vom getippten Namen und von der Kollation ab,
nie davon, ob es das Konto gibt.
"""

from typing import Any

import pytest
from sqlalchemy import func, select
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
    client.post("/ui-api/login", json={"username": typed, "password": FALSCH})


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
