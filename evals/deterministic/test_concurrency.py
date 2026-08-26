"""Two turns at once on one session.

The CLI runs one turn at a time and always has, so nothing until now has touched
the layer underneath: `Deps` is session-lived and holds one SQLite store handle
and one checkpointer handle, shared by every turn the session serves. A web
gateway serves turns concurrently from a single session by design, and two
browser tabs are enough to reach this.

`run_turn` clones a tracer and a budget per turn rather than resetting shared
ones, which is what these cases check actually holds end to end. They run before
any endpoint exists on purpose: if the shared handles cannot take it, the fix is
a lock around store access, not a session per request, and that is much cheaper
to learn now than after a frontend is sitting on top of it.

Overlap is forced rather than hoped for -- see `RendezvousModel`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from evals.helpers import PromptModel, says, wants
from miku.ops.traceview import read_records
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.clock import Clock

FIXED_CLOCK = Clock.fixed("2026-08-25")  # a Tuesday

# A barrier both turns have reached is released in microseconds, so this is six
# orders of magnitude of headroom and never fires on a healthy run. It is kept
# short because one case deliberately does wait it out.
RENDEZVOUS_TIMEOUT = 1.0


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester", max_iterations=3)


@dataclass
class RendezvousModel(PromptModel):
    """A stub that will not answer either turn until both have arrived.

    This is the control, and it is load-bearing. Isolation between two turns
    that never actually overlapped is not evidence of anything -- and the first
    version of this fixture did not overlap. `PromptModel` returns without ever
    awaiting, so a turn only yields at a real await, and measured on that
    fixture the first turn ran to completion before the second began: seven
    trace records, one switch between them. The cases below would have passed
    while testing nothing.

    Sleeping in the model was tried next and was worse than it looked: it made
    the overlap likely rather than certain, and counting context switches to
    prove it failed one run in eight. A flaky control is not a control.

    So the two turns rendezvous. Neither first model call returns until both
    have been made, which means both turns are provably in flight at once,
    holding the same store and checkpointer handles. If they somehow cannot
    overlap, the barrier times out and the case fails loudly instead of passing
    quietly.

    Only the first call of each turn waits. The turn that runs a tool comes back
    for a second call after its partner has already finished, and a barrier it
    joined would never be released.
    """

    parties: int = 2
    arrived: int = 0
    barrier: asyncio.Barrier = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.barrier = asyncio.Barrier(self.parties)

    async def ainvoke(self, messages, **kwargs):
        if self.arrived < self.parties:
            self.arrived += 1
            await asyncio.wait_for(self.barrier.wait(), timeout=RENDEZVOUS_TIMEOUT)
        return await super().ainvoke(messages, **kwargs)


def two_turn_model() -> RendezvousModel:
    """Answers by what it was asked, never by call order.

    Two turns interleave, so a script indexed by call number would make every
    assertion depend on which turn happened to reach the model first.

    The two turns are deliberately different lengths: one is answered outright,
    the other calls a tool and comes back. That asymmetry is what makes the
    budget assertion meaningful -- a pooled counter would show both turns
    spending the total.
    """

    def respond(text: str):
        if "Created:" in text:
            return says("Booked Tennis for Saturday at 08:00.")
        if "tennis" in text.lower():
            return wants(
                "create_event",
                {"title": "Tennis", "day": "2026-08-29", "start_time": "08:00"},
            )
        return says("Hei, nothing to book.")

    return RendezvousModel(respond=respond)


async def both_turns(session):
    """One chatty turn and one booking turn, overlapping by construction."""
    return await asyncio.gather(
        session.run_turn("just saying hello", thread_id="tab-a"),
        session.run_turn("book tennis saturday 8am", thread_id="tab-b"),
    )


async def test_two_turns_on_one_session_both_reply(settings):
    async with open_session(settings, model=two_turn_model(), clock=FIXED_CLOCK) as session:
        chat, booking = await both_turns(session)

    assert chat.reply == "Hei, nothing to book."
    assert booking.reply.startswith("Booked Tennis")
    assert booking.called("create_event")
    assert not chat.tool_calls


async def test_concurrent_turns_do_not_pool_their_budgets(settings):
    """Each turn spends its own allowance.

    One model call for the turn answered outright, two for the turn that ran a
    tool and looped back. A budget in `Deps` instead of `TurnContext` would
    report three for both.
    """
    async with open_session(settings, model=two_turn_model(), clock=FIXED_CLOCK) as session:
        chat, booking = await both_turns(session)

    assert chat.requests == 1
    assert booking.requests == 2


async def test_concurrent_turns_do_not_cross_attribute_their_events(settings):
    """No event of one turn is recorded against the other.

    Both turns append to the same day's file, interleaved. Isolation is a
    property of the records, not of the file: every record carries its own turn
    id, and every parent link stays inside the turn that wrote it.
    """
    async with open_session(settings, model=two_turn_model(), clock=FIXED_CLOCK) as session:
        chat, booking = await both_turns(session)
        trace_path = session.deps.tracer.path

    assert chat.turn_id != booking.turn_id
    assert read_records(trace_path), "the concurrent turns wrote no trace at all"

    for turn_id in (chat.turn_id, booking.turn_id):
        records = read_records(trace_path, turn_id=turn_id)
        assert records, f"turn {turn_id} wrote no records"

        # A parent link that reached outside its own turn would mean one turn's
        # tracer had been handed another turn's span -- the shape a shared,
        # reset-in-place tracer produces.
        spans = {record["span"] for record in records}
        for record in records:
            assert record["turn_id"] == turn_id
            assert record.get("parent") in spans or record.get("parent") is None


async def test_the_rendezvous_actually_forces_an_overlap(settings):
    """The control, checked rather than trusted.

    If the barrier were satisfied by one turn alone -- a miscounted `parties`, a
    stub that answers before waiting -- every case above would go quiet. A
    single turn against a two-party rendezvous must time out.
    """
    model = two_turn_model()

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        with pytest.raises(TimeoutError):
            await session.run_turn("just saying hello", thread_id="alone")
