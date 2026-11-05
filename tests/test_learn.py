"""Tests for the spaced-repetition store."""

from datetime import datetime, timedelta, timezone

import pytest

from local_mcp.lib import learn

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def learn_db(temp_dir, monkeypatch):
    # Env too: other tests purge local_mcp from sys.modules, so the tool may import a fresh copy
    monkeypatch.setenv("LEARN_DB", str(temp_dir / "learn.db"))
    monkeypatch.setattr(learn, "DB_PATH", temp_dir / "learn.db")


def log_one(event, front="食べる", now=T0, **fields):
    return learn.log("japanese", [{"front": front, "event": event, **fields}], now=now)[0]


@pytest.mark.parametrize("event, ease, interval, due", [
    ("used", 2.6, 1, T0 + timedelta(days=1)),
    ("asked", 2.35, 0, T0),
    ("misused", 2.3, 0, T0),
    ("seen", 2.5, 0, T0),
])
def test_first_event_schedules_new_item(event, ease, interval, due):
    item = log_one(event)
    assert (item["ease"], item["interval_days"], item["due"]) == (
        pytest.approx(ease), interval, due.isoformat(),
    )


@pytest.mark.parametrize("event", ["used", "seen"])
def test_events_before_due_leave_schedule_alone(event):
    first = log_one("used")
    later = log_one(event, now=T0 + timedelta(hours=2))
    assert (later["ease"], later["interval_days"], later["due"]) == (
        first["ease"], first["interval_days"], first["due"],
    )


def test_successful_reviews_grow_interval():
    log_one("used")
    second = log_one("used", now=T0 + timedelta(days=1))
    third = log_one("used", now=T0 + timedelta(days=7))
    assert (second["interval_days"], third["interval_days"]) == (6, pytest.approx(6 * 2.8))


def test_lapse_resets_progress_and_makes_item_due_now():
    log_one("used")
    log_one("used", now=T0 + timedelta(days=1))
    lapsed = log_one("misused", now=T0 + timedelta(days=2))
    assert (lapsed["interval_days"], lapsed["reps"], lapsed["lapses"], lapsed["due"]) == (
        0, 0, 1, (T0 + timedelta(days=2)).isoformat(),
    )


def test_ease_never_drops_below_floor():
    items = [log_one("misused") for _ in range(10)]
    assert items[-1]["ease"] == pytest.approx(1.3)


def test_log_creates_then_updates_fields():
    log_one("seen", reading="たべる", back="to eat")
    item = log_one("asked", note="confused with 飲む")
    assert (item["reading"], item["back"], item["note"]) == ("たべる", "to eat", "confused with 飲む")


def test_due_lists_hardest_first_and_skips_future_and_retired():
    log_one("used", front="easy")
    log_one("asked", front="hard")
    log_one("misused", front="harder")
    log_one("asked", front="retired")
    learn.edit("japanese", "retired", retired=True)
    fronts = [i["front"] for i in learn.due("japanese", now=T0 + timedelta(minutes=1))]
    assert fronts == ["harder", "hard"]


def test_decks_are_separate():
    log_one("asked")
    assert learn.due("physics", now=T0) == []
    assert learn.get("physics", "食べる") is None


def test_get_includes_event_history():
    log_one("seen")
    log_one("asked", now=T0 + timedelta(minutes=5))
    events = [e["event"] for e in learn.get("japanese", "食べる")["events"]]
    assert events == ["seen", "asked"]


def test_edit_unknown_item_raises():
    with pytest.raises(KeyError):
        learn.edit("japanese", "nope", back="x")


@pytest.mark.parametrize("gap, same_session", [
    (timedelta(minutes=29), True),
    (timedelta(minutes=31), False),
])
def test_sessions_split_on_idle_gap(gap, same_session):
    log_one("seen", front="a")
    log_one("seen", front="b", now=T0 + gap)
    fronts = [i["front"] for i in learn.recent("japanese")["items"]]
    assert fronts == (["a", "b"] if same_session else ["b"])


def test_recent_can_fetch_earlier_session():
    log_one("seen", front="a")
    log_one("seen", front="b", now=T0 + timedelta(hours=2))
    earlier = learn.recent("japanese", session=learn.recent("japanese")["session"] - 1)
    assert [i["front"] for i in earlier["items"]] == ["a"]


def test_invalid_event_rejected_before_writing():
    with pytest.raises(ValueError):
        learn.log("japanese", [
            {"front": "a", "event": "seen"},
            {"front": "b", "event": "guessed"},
        ], now=T0)
    assert learn.get("japanese", "a") is None


def test_learn_log_tool_returns_items():
    from local_mcp.tools.learn import learn_log
    result = learn_log.fn(deck="japanese", entries=[{"front": "食べる", "event": "asked"}])
    assert [i["front"] for i in result["items"]] == ["食べる"]
