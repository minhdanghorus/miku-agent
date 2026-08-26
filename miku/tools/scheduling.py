"""Scheduling — the flagship task's two tools.

Both take an ABSOLUTE date. Resolving "Saturday" to a date is the model's job,
and the model is told today's date by the context assembler (see graph/nodes.py
and tools/clock.py). Keeping relative-date handling out of here means the stored
row is unambiguous and an eval can assert on it directly.
"""

from __future__ import annotations

from datetime import date, datetime

from langchain_core.tools import BaseTool, StructuredTool

from miku.runtime.config import Settings
from miku.tools.calendar_store import Event, events_on, insert_event


def _validate(day: str, start_time: str) -> tuple[str, str]:
    """Normalise the two fields we refuse to guess at."""
    try:
        parsed_day = date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"day must be an absolute ISO date like 2026-08-29, got {day!r}") from None

    try:
        parsed_time = datetime.strptime(start_time.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError(
            f"start_time must be 24-hour HH:MM like 08:00, got {start_time!r}"
        ) from None

    return parsed_day.isoformat(), parsed_time.strftime("%H:%M")


def build_scheduling_tools(settings: Settings) -> list[BaseTool]:
    """The scheduling tools, bound to this session's database."""

    async def create_event(title: str, day: str, start_time: str) -> str:
        """Add an event to the calendar.

        Args:
            title: What the event is, e.g. "Tennis with Raj".
            day: Absolute ISO date, e.g. "2026-08-29". Never a weekday name.
            start_time: 24-hour HH:MM, e.g. "08:00".
        """
        iso_day, hhmm = _validate(day, start_time)
        event = Event(title=title.strip(), day=iso_day, start_time=hhmm)
        if not event.title:
            raise ValueError("title cannot be empty")
        await insert_event(settings.db_path, event)
        return f"Created: {event.describe()}"

    async def list_events(day: str) -> str:
        """List the events on one day, earliest first.

        Args:
            day: Absolute ISO date, e.g. "2026-08-29". Never a weekday name.
        """
        iso_day, _ = _validate(day, "00:00")
        found = await events_on(settings.db_path, iso_day)
        if not found:
            return f"No events on {iso_day}."
        lines = [f"{event.start_time} {event.title}" for event in found]
        return f"Events on {iso_day}:\n" + "\n".join(lines)

    return [
        StructuredTool.from_function(
            coroutine=create_event,
            name="create_event",
            description=(
                "Book one event on the calendar at a time the user has already "
                "chosen. Requires an absolute ISO date and a 24-hour HH:MM start "
                "time. Do NOT use this when the user has not said when they want "
                "it: use propose_slots to find a time first."
            ),
        ),
        StructuredTool.from_function(
            coroutine=list_events,
            name="list_events",
            description="List the calendar events on one absolute ISO date, earliest first.",
        ),
    ]
