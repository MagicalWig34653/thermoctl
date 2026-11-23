"""Die Anmeldedrosselung hält auch bei gleichzeitigen Versuchen (Codex-Befund 9).

Würde der Zähler gelesen, gewartet und erst nach der Prüfung zurückgeschrieben, sähen N
parallele Anfragen alle denselben Stand, warteten dieselbe Sekunde und prüften dann alle: die
Wartezeit bremste einen Versuch nach dem anderen, einen Schwall gleichzeitiger gar nicht. Hier
wird sie so geprüft, wie ein Angreifer sie benutzt: mit vielen Anfragen auf einmal.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

import thermoctl.web.shared.login as login
from thermoctl.db.models.identity import User
from thermoctl.web.shared.login import (
    FEHLVERSUCHE,
    MAX_PARALLEL_ATTEMPTS,
    TooManyAttempts,
    attempt_login,
    login_key,
    reset_failures,
    throttled_attempt,
)

FALSCH = "falsch-aber-lang"
RICHTIG = "passwort-lang-genug"


@pytest.fixture(autouse=True)
def _clean_counters() -> Any:
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()
    yield
    FEHLVERSUCHE.clear()
    login._IN_FLIGHT.clear()


class _Gate:
    """Hält jede Wartezeit an, bis der Test sie freigibt, und merkt sich ihre Länge."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.open = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        await self.open.wait()


async def _bounded(scenario: Any) -> Any:
    """Ein Test, der an der Sperre hängt, soll scheitern, nicht ewig laufen."""
    return await asyncio.wait_for(scenario, timeout=10)


def _fake_request() -> Any:
    return SimpleNamespace(headers={}, client=None)


def test_parallel_attempts_are_counted_before_they_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder gleichzeitige Versuch sieht den Zähler der vorigen: 1, 2, 4 -- nicht dreimal 1."""
    gate = _Gate()
    monkeypatch.setattr(login, "sleep", gate.sleep)

    async def scenario() -> None:
        async def attempt() -> None:
            async with throttled_attempt("k"):
                pass

        tasks = [asyncio.create_task(attempt()) for _ in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert gate.delays == [1, 2, 4]
        assert FEHLVERSUCHE["k"] == 3
        gate.open.set()
        await asyncio.gather(*tasks)

    asyncio.run(_bounded(scenario()))


def test_the_attempts_under_way_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate()
    monkeypatch.setattr(login, "sleep", gate.sleep)

    async def scenario() -> None:
        async def attempt(key: str = "k") -> None:
            async with throttled_attempt(key):
                pass

        tasks = [asyncio.create_task(attempt()) for _ in range(MAX_PARALLEL_ATTEMPTS)]
        await asyncio.sleep(0)
        assert login._IN_FLIGHT == {"k": MAX_PARALLEL_ATTEMPTS}
        counted = FEHLVERSUCHE["k"]
        with pytest.raises(TooManyAttempts):
            async with throttled_attempt("k"):
                raise AssertionError("must not get in")  # pragma: no cover
        # Refused before anything was counted or waited for.
        assert FEHLVERSUCHE["k"] == counted
        assert len(gate.delays) == MAX_PARALLEL_ATTEMPTS
        # Another key is not affected: it gets in (and waits like the others).
        other = asyncio.create_task(attempt("anderer"))
        await asyncio.sleep(0)
        assert login._IN_FLIGHT["anderer"] == 1
        gate.open.set()
        await asyncio.gather(*tasks, other)
        assert login._IN_FLIGHT == {}
        # Space again once they are done (the gate is open now).
        await attempt()

    asyncio.run(_bounded(scenario()))


def test_the_slot_is_given_back_after_an_error_and_after_a_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _Gate()
    monkeypatch.setattr(login, "sleep", gate.sleep)

    async def scenario() -> None:
        gate.open.set()
        with pytest.raises(RuntimeError):
            async with throttled_attempt("k"):
                raise RuntimeError("Prüfung ging schief")
        assert login._IN_FLIGHT == {}

        gate.open.clear()
        waiting = asyncio.create_task(throttled_attempt("k").__aenter__())
        await asyncio.sleep(0)
        assert login._IN_FLIGHT == {"k": 1}
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert login._IN_FLIGHT == {}

    asyncio.run(_bounded(scenario()))


def test_a_success_resets_the_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate()
    gate.open.set()
    monkeypatch.setattr(login, "sleep", gate.sleep)

    async def scenario() -> None:
        for _ in range(3):
            async with throttled_attempt("k"):
                pass
        assert FEHLVERSUCHE["k"] == 3
        async with throttled_attempt("k"):
            reset_failures("k")
        assert FEHLVERSUCHE["k"] == 0

    asyncio.run(_bounded(scenario()))
    assert gate.delays == [1, 2, 4, 5]


def test_a_flood_of_parallel_logins_gets_only_a_few_password_checks(
    session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 gleichzeitige Anmeldungen: höchstens `MAX_PARALLEL_ATTEMPTS` werden geprüft."""
    gate = _Gate()
    monkeypatch.setattr(login, "sleep", gate.sleep)
    checks: list[str] = []

    def count(password: str, hashed: str) -> bool:
        checks.append(password)
        return False

    monkeypatch.setattr(login, "verify_password", count)

    async def scenario() -> list[Any]:
        tasks = [
            asyncio.create_task(attempt_login(_fake_request(), session, "lino", f"{FALSCH}-{n}"))
            for n in range(20)
        ]
        await asyncio.sleep(0.05)
        gate.open.set()
        return list(await asyncio.gather(*tasks))

    outcomes = asyncio.run(_bounded(scenario()))

    assert outcomes == [None] * 20
    assert len(checks) == MAX_PARALLEL_ATTEMPTS
    assert FEHLVERSUCHE == {"name:lino": MAX_PARALLEL_ATTEMPTS}
    assert login._IN_FLIGHT == {}


def test_a_refused_login_looks_like_any_failed_one(
    client: Any, session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch die richtige Anmeldung wird über der Grenze abgewiesen, mit derselben Antwort."""
    checks: list[str] = []
    monkeypatch.setattr(
        login, "verify_password", lambda password, hashed: checks.append(password) or True
    )
    first = client.post("/ui-api/login", json={"username": "lino", "password": RICHTIG})
    assert first.status_code == 200  # Vergleichslauf: ohne Schwall kommt sie durch
    client.cookies.clear()  # sonst greift der CSRF-Schutz für die angemeldete Sitzung
    login._IN_FLIGHT["name:lino"] = MAX_PARALLEL_ATTEMPTS
    checks.clear()

    flooded = client.post("/ui-api/login", json={"username": "lino", "password": RICHTIG})
    wrong = client.post("/ui-api/login", json={"username": "unbekannt", "password": RICHTIG})

    assert flooded.status_code == wrong.status_code == 401
    assert flooded.json() == wrong.json()
    assert "set-cookie" not in flooded.headers
    assert len(checks) == 1  # nur der unbekannte Name wurde (gegen den Vergleichshash) geprüft


def test_the_wait_does_not_hold_a_database_transaction(
    session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Transaktion des Nachschlags endet vor der Wartezeit.

    Sonst hielte jede Anmeldung -- auch mit erfundenen Namen -- eine Verbindung aus dem Pool
    für die Dauer der Wartezeit, und ein Schwall legte den ganzen Dienst lahm.
    """
    events: list[str] = []

    async def sleep(seconds: float) -> None:
        events.append("wait")

    original_commit = session.commit

    def commit() -> None:
        events.append("commit")
        original_commit()

    monkeypatch.setattr(login, "sleep", sleep)
    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(login, "verify_password", lambda password, hashed: False)

    asyncio.run(attempt_login(_fake_request(), session, "lino", FALSCH))

    assert events[:2] == ["commit", "wait"]


def test_the_throttle_is_keyed_by_the_typed_name_whether_or_not_it_exists(
    client: Any, user: User
) -> None:
    client.post("/ui-api/login", json={"username": "lino", "password": FALSCH})
    client.post("/ui-api/login", json={"username": "unbekannt", "password": FALSCH})

    assert FEHLVERSUCHE == {"name:lino": 1, "name:unbekannt": 1}


@pytest.mark.parametrize(
    ("typed", "key"),
    [
        ("lino", "name:lino"),
        ("LINO", "name:lino"),
        ("Lino ", "name:lino"),
        ("  lInO\t", "name:lino"),
        ("L\u00edno", "name:lino"),  # Akzent entfällt
        ("l\u0131no", "name:l\u0131no"),  # punktloses i: casefold ändert es nicht
        ("\uff2c\uff29\uff2e\uff2f", "name:lino"),  # vollbreite Buchstaben (NFKD)
        ("benutzer:1", "name:benutzer:1"),
        # Was die Kollation der Datenbank gleichsetzt, darf keinen frischen Zähler ergeben:
        ("li\u200bno", "name:lino"),  # Zeichen der Breite null
        ("li\u00adno", "name:lino"),  # weicher Trennstrich
        ("\ufefflino", "name:lino"),  # Byte-Order-Mark
        ("li\u200c\u200d\u2060no", "name:lino"),
        ("li\x00no", "name:lino"),  # Steuerzeichen
        ("stra\u00dfe", "name:strasse"),  # casefold, nicht lower
        ("\u03c2", "name:\u03c3"),  # Schluss-Sigma
        ("\ufb01x", "name:fix"),  # Ligatur
        ("nutzer\u0663", "name:nutzer3"),  # arabisch-indische Ziffer
        ("nutzer\uff13", "name:nutzer3"),
    ],
)
def test_login_key_folds_the_spellings_of_one_name(typed: str, key: str) -> None:
    assert login_key(typed) == key


def test_login_key_keeps_real_differences_apart() -> None:
    assert login_key("lino") != login_key("lina")
    assert login_key("nutzer1") != login_key("nutzer2")
    assert login_key("lino") != login_key("lino x")  # Leerzeichen innen zählen


def test_existing_and_invented_names_take_the_same_delays_in_every_spelling(
    client: Any, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Orakel: Wie lange gewartet wird, hängt nur vom getippten Namen ab -- nicht
    davon, ob es das Konto gibt (auf MariaDB sind `lino` und `LINO` dasselbe Konto, auf
    SQLite nicht; die Wartezeiten dürfen es nicht verraten)."""
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(login, "sleep", record)
    existing = ("lino", "LINO", "Lino ", "li\u200bno", "lino")
    invented = ("erfunden", "ERFUNDEN", "Erfunden ", "erfun\u200bden", "erfunden")

    def series(typed_names: tuple[str, ...]) -> list[float]:
        waits.clear()
        client.cookies.clear()
        for typed in typed_names:
            client.post("/ui-api/login", json={"username": typed, "password": FALSCH})
        return list(waits)

    assert series(existing) == [1, 2, 4, 5, 5]
    assert series(invented) == [1, 2, 4, 5, 5]


def test_other_spellings_of_the_name_share_the_counter(
    client: Any, session: Session, user: User
) -> None:
    """MariaDB vergleicht Benutzernamen ohne Rücksicht auf Groß-/Kleinschreibung: `LINO`
    ist dasselbe Konto wie `lino` -- und darf keinen frischen Zähler bekommen."""
    if session.get_bind().dialect.name not in ("mysql", "mariadb"):
        pytest.skip(
            "braucht MariaDB (THERMOCTL_TEST_DATABASE_URL): SQLite vergleicht Namen "
            "unter Beachtung der Schreibweise, dort gibt es keine zwei Schreibweisen"
        )
    for typed in ("lino", "LINO", "Lino ", "lInO"):
        client.post("/ui-api/login", json={"username": typed, "password": FALSCH})

    assert FEHLVERSUCHE == {"name:lino": 4}
