"""Spaced-repetition store: SM-2-style scheduling driven by conversational events.

Events:
    seen    - came up, no reaction; recorded but never moves the schedule
    asked   - had to ask what it meant (lapse)
    used    - used it correctly (a successful review, only counted once due)
    misused - used it wrong (lapse)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

from typing_extensions import NotRequired, TypedDict

from local_mcp.settings import LEARN_DB_PATH

DB_PATH = LEARN_DB_PATH

Event = Literal["seen", "asked", "used", "misused"]
EVENTS = get_args(Event)
EDITABLE = {"reading", "back", "note", "retired"}

INITIAL_EASE = 2.5
MIN_EASE = 1.3
EASE_DELTA: dict[str, float] = {"used": 0.1, "asked": -0.15, "misused": -0.2}
SESSION_GAP = timedelta(minutes=30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    deck TEXT NOT NULL,
    front TEXT NOT NULL,
    reading TEXT,
    back TEXT,
    note TEXT,
    ease REAL NOT NULL,
    interval_days REAL NOT NULL,
    reps INTEGER NOT NULL,
    lapses INTEGER NOT NULL,
    due TEXT NOT NULL,
    retired INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    PRIMARY KEY (deck, front)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    deck TEXT NOT NULL,
    front TEXT NOT NULL,
    event TEXT NOT NULL,
    ts TEXT NOT NULL,
    session INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS events_item ON events (deck, front);
CREATE INDEX IF NOT EXISTS events_session ON events (deck, session);
"""


class Entry(TypedDict):
    front: str
    event: Event
    reading: NotRequired[str]
    back: NotRequired[str]
    note: NotRequired[str]


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Autocommit mode so write transactions can be BEGIN IMMEDIATE
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction():
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def to_dict(row: sqlite3.Row) -> dict:
    return {**dict(row), "retired": bool(row["retired"])}


def new_item(deck: str, front: str, now: datetime) -> dict:
    return {
        "deck": deck, "front": front, "reading": None, "back": None, "note": None,
        "ease": INITIAL_EASE, "interval_days": 0, "reps": 0, "lapses": 0,
        "due": stamp(now), "retired": False, "last_seen": None,
    }


def schedule(item: dict, event: str, now: datetime) -> dict:
    """Return the item's scheduling after `event` at `now`."""
    if event == "seen":
        return item
    if event in ("asked", "misused"):
        return {
            **item,
            "ease": max(MIN_EASE, item["ease"] + EASE_DELTA[event]),
            "interval_days": 0,
            "reps": 0,
            "lapses": item["lapses"] + 1,
            "due": stamp(now),
        }
    # Using a word repeatedly in one conversation shouldn't push it out by months
    if item["due"] > stamp(now):
        return item
    ease = item["ease"] + EASE_DELTA["used"]
    interval = {0: 1, 1: 6}.get(item["reps"], item["interval_days"] * ease)
    return {
        **item,
        "ease": ease,
        "interval_days": interval,
        "reps": item["reps"] + 1,
        "due": stamp(now + timedelta(days=interval)),
    }


def current_session(conn: sqlite3.Connection, now: datetime) -> int:
    last = conn.execute("SELECT session, ts FROM events ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        return 1
    if now - datetime.fromisoformat(last["ts"]) > SESSION_GAP:
        return last["session"] + 1
    return last["session"]


def fetch(conn: sqlite3.Connection, deck: str, front: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM items WHERE deck = ? AND front = ?", (deck, front)
    ).fetchone()
    return to_dict(row) if row else None


def save(conn: sqlite3.Connection, item: dict) -> None:
    cols = list(item)
    conn.execute(
        f"INSERT OR REPLACE INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [item[c] for c in cols],
    )


def log(deck: str, entries: list[Entry], now: datetime | None = None) -> list[dict]:
    """Record a batch of events, creating unknown items. Returns the updated items."""
    now = now or datetime.now(timezone.utc)
    bad = [e.get("event") for e in entries if e.get("event") not in EVENTS]
    if bad:
        raise ValueError(f"Unknown events {bad}; expected one of {EVENTS}")

    with write_transaction() as conn:
        session = current_session(conn, now)
        results = []
        for entry in entries:
            front = entry["front"]
            item = fetch(conn, deck, front) or new_item(deck, front, now)
            fields = {k: entry[k] for k in ("reading", "back", "note") if entry.get(k)}
            item = {**schedule(item, entry["event"], now), **fields, "last_seen": stamp(now)}
            save(conn, item)
            conn.execute(
                "INSERT INTO events (deck, front, event, ts, session) VALUES (?, ?, ?, ?, ?)",
                (deck, front, entry["event"], stamp(now), session),
            )
            results.append(item)
        return results


def due(deck: str, limit: int = 20, now: datetime | None = None) -> list[dict]:
    """Due, non-retired items, hardest first."""
    now = now or datetime.now(timezone.utc)
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM items WHERE deck = ? AND NOT retired AND due <= ?
               ORDER BY ease, due LIMIT ?""",
            (deck, stamp(now), limit),
        ).fetchall()
    return [to_dict(r) for r in rows]


def get(deck: str, front: str) -> dict | None:
    with connect() as conn:
        item = fetch(conn, deck, front)
        if not item:
            return None
        events = conn.execute(
            "SELECT event, ts, session FROM events WHERE deck = ? AND front = ? ORDER BY id",
            (deck, front),
        ).fetchall()
    return {**item, "events": [dict(e) for e in events]}


def edit(deck: str, front: str, **fields) -> dict:
    unknown = set(fields) - EDITABLE
    if unknown:
        raise ValueError(f"Cannot edit {sorted(unknown)}; editable: {sorted(EDITABLE)}")
    with write_transaction() as conn:
        item = fetch(conn, deck, front)
        if not item:
            raise KeyError(f"No item {front!r} in deck {deck!r}")
        item = {**item, **fields}
        save(conn, item)
    return item


def recent(deck: str, session: int | None = None) -> dict:
    """Items touched in a session (default: the deck's latest), in first-touched order."""
    with connect() as conn:
        if session is None:
            session = conn.execute(
                "SELECT MAX(session) FROM events WHERE deck = ?", (deck,)
            ).fetchone()[0]
        rows = conn.execute(
            """SELECT i.*, GROUP_CONCAT(e.event) AS session_events
               FROM events e JOIN items i USING (deck, front)
               WHERE e.deck = ? AND e.session = ?
               GROUP BY i.deck, i.front ORDER BY MIN(e.id)""",
            (deck, session),
        ).fetchall()
    items = [{**to_dict(r), "session_events": r["session_events"].split(",")} for r in rows]
    return {"session": session, "items": items}
