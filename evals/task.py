"""The one task function every eval case drives.

`pydantic-evals` takes any async callable, so cases exercise the real compiled
graph rather than a reimplementation of it. Each case gets a fresh state
directory, which is what keeps cases order-independent.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.calendar_store import events_on_sync
from miku.tools.clock import Clock


class TurnInputs(BaseModel):
    """What a case sends in."""

    message: str
    # Frozen so that "book it for Saturday" asserts the same date forever.
    today: str = "2026-08-25"  # a Tuesday
    thread_id: str = "eval"
    # An optional first message, run before the one under test — used to set up
    # memory or history without a second task function.
    setup_message: str | None = None
    setup_thread_id: str | None = None


@dataclass
class TurnOutput:
    """What a case asserts on: behaviour and stored state, never prose."""

    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    iterations: int = 0

    def called(self, name: str) -> bool:
        return any(call["name"] == name for call in self.tool_calls)

    def args_for(self, name: str) -> dict:
        for call in self.tool_calls:
            if call["name"] == name:
                return call["args"]
        return {}


async def run_turn(inputs: TurnInputs) -> TurnOutput:
    """Run one turn of the real graph in an isolated state directory."""
    with tempfile.TemporaryDirectory(prefix="miku-eval-") as tmp:
        settings = load_settings(state_dir=Path(tmp) / "state", user_id="eval")
        clock = Clock.fixed(inputs.today)

        async with open_session(settings, clock=clock) as session:
            if inputs.setup_message:
                await session.run_turn(
                    inputs.setup_message,
                    thread_id=inputs.setup_thread_id or inputs.thread_id,
                )

            result = await session.run_turn(inputs.message, thread_id=inputs.thread_id)

        # Read the stored rows before the temporary directory goes away.
        days = {
            call["args"].get("day")
            for call in result.tool_calls
            if call["name"] in {"create_event", "list_events"}
        }
        days.add(inputs.today)

        events = []
        for day in sorted(d for d in days if d):
            for event in events_on_sync(settings.db_path, str(day)):
                events.append(
                    {"title": event.title, "day": event.day, "start_time": event.start_time}
                )

        return TurnOutput(
            reply=result.reply,
            tool_calls=result.tool_calls,
            events=events,
            iterations=result.iterations,
        )
