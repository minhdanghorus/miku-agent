"""Event storage — a plain SQLite table in the same state database.

Deliberately queryable by hand. Evals assert on rows here rather than on reply
prose, because prose varies with the model and rows do not.

No .ics export in this phase: that is a convenience feature, not architecture.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    day        TEXT NOT NULL,   -- ISO date, always absolute
    start_time TEXT NOT NULL,   -- HH:MM, 24-hour
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_day ON events (day, start_time);
"""


@dataclass(frozen=True)
class Event:
    title: str
    day: str
    start_time: str

    def describe(self) -> str:
        return f"{self.title} on {self.day} at {self.start_time}"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_event_sync(db_path: Path, event: Event) -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO events (title, day, start_time, created_at) VALUES (?, ?, ?, ?)",
            (
                event.title,
                event.day,
                event.start_time,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def events_on_sync(db_path: Path, day: str) -> list[Event]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT title, day, start_time FROM events WHERE day = ? ORDER BY start_time, id",
            (day,),
        ).fetchall()
        return [Event(title=r[0], day=r[1], start_time=r[2]) for r in rows]
    finally:
        conn.close()


def events_between_sync(db_path: Path, start_day: str, end_day: str) -> list[Event]:
    """Every event in an inclusive ISO date range, earliest first.

    ISO dates sort lexicographically, which is why a BETWEEN on text works here
    and why the day column is stored absolute rather than as a weekday name.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT title, day, start_time FROM events "
            "WHERE day BETWEEN ? AND ? ORDER BY day, start_time, id",
            (start_day, end_day),
        ).fetchall()
        return [Event(title=r[0], day=r[1], start_time=r[2]) for r in rows]
    finally:
        conn.close()


async def insert_event(db_path: Path, event: Event) -> int:
    """Async wrapper — sqlite3 is blocking, and the graph is async."""
    return await asyncio.to_thread(insert_event_sync, db_path, event)


async def events_on(db_path: Path, day: str) -> list[Event]:
    return await asyncio.to_thread(events_on_sync, db_path, day)


async def events_between(db_path: Path, start_day: str, end_day: str) -> list[Event]:
    return await asyncio.to_thread(events_between_sync, db_path, start_day, end_day)
