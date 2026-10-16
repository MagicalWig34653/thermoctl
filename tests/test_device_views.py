from datetime import datetime, timedelta


def test_age_in_words_answers_the_question_of_freshness() -> None:
    """A raw timestamp with microseconds demands mental arithmetic.

    But for a heating control system the question is always: fresh or stale?
    That is why the overview shows the age, and the timestamp only in the tooltip.
    """
    from datetime import datetime

    from thermoctl.domain.time import age_in_words

    now = datetime(2026, 8, 29, 12, 0, 0)
    assert age_in_words(None) == "noch nie"
    assert age_in_words(now - timedelta(seconds=1), now) == "vor 1 Sekunde"
    assert age_in_words(now - timedelta(seconds=42), now) == "vor 42 Sekunden"
    assert age_in_words(now - timedelta(minutes=1), now) == "vor 1 Minute"
    assert age_in_words(now - timedelta(minutes=59), now) == "vor 59 Minuten"
    assert age_in_words(now - timedelta(hours=3), now) == "vor 3 Stunden"
    assert age_in_words(now - timedelta(days=1), now) == "vor 1 Tag"
    assert age_in_words(now - timedelta(days=9), now) == "vor 9 Tagen"
    # Without an explicit now-moment, the project's clock takes over.
    assert age_in_words(datetime(2000, 1, 1)).endswith("Tagen")


def test_age_in_words_gives_a_future_moment_its_own_wording() -> None:
    """A still-running override's end lies in the future, and 'vor -42 Minuten' or

    the older bug's 'gerade eben' both misreport that: the override has not just
    ended, it still has 42 minutes left. The counter-check that would fail without
    the fix: before it, every one of the asserts below returned 'gerade eben',
    regardless of how far in the future the moment actually was.
    """

    from thermoctl.domain.time import age_in_words

    now = datetime(2026, 8, 29, 12, 0, 0)
    assert age_in_words(now + timedelta(minutes=42), now) == "noch 42 Minuten"
    assert age_in_words(now + timedelta(seconds=30), now) == "noch 30 Sekunden"
    assert age_in_words(now + timedelta(hours=2), now) == "noch 2 Stunden"
    assert age_in_words(now + timedelta(days=1), now) == "noch 1 Tag"




