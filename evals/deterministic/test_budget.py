"""The per-turn request budget.

Two limits coexist and must not be conflated: `max_iterations` bounds how deep a
turn goes, the budget bounds how many model requests it spends in total. These
tests pin both the counter's own arithmetic and the fact that a turn owns its
allowance alone.
"""

from __future__ import annotations

import asyncio

import pytest

from evals.helpers import StubModel, says, wants
from miku.graph.nodes import BUDGET_REPLY, CAP_REPLY
from miku.ops.traceview import read_records
from miku.runtime.budget import Budget
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.clock import Clock

FIXED_CLOCK = Clock.fixed("2026-08-25")


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


# --- the counter itself ----------------------------------------------------


def test_spending_within_the_limit_succeeds():
    budget = Budget(limit=3)
    assert budget.spend() is True
    assert budget.spent == 1
    assert budget.remaining() == 2


def test_a_refused_claim_charges_nothing():
    """A caller that is told no must not have been billed anyway."""
    budget = Budget(limit=2)
    assert budget.spend(2) is True
    assert budget.spend() is False
    assert budget.spent == 2


def test_a_claim_larger_than_the_remainder_is_refused_whole():
    budget = Budget(limit=5)
    budget.spend(3)
    assert budget.spend(3) is False
    assert budget.spent == 3
    assert budget.remaining() == 2


def test_exhaustion_is_reported_at_the_limit():
    budget = Budget(limit=1)
    assert not budget.exhausted
    budget.spend()
    assert budget.exhausted


def test_a_cloned_budget_starts_fresh_and_is_independent():
    template = Budget(limit=4)
    first = template.for_turn()
    first.spend(4)

    second = template.for_turn()
    assert second.spent == 0
    assert first.spent == 4
    assert template.spent == 0  # the template is never spent from


# --- the budget inside a turn ---------------------------------------------


async def test_a_normal_turn_spends_one_request_per_model_call(settings):
    model = StubModel(
        [
            wants("list_events", {"day": "2026-08-25"}),
            says("Nothing today."),
        ]
    )

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("what is on today?", thread_id="t1")

    assert result.requests == 2 == model.invocations


async def test_a_normal_turn_traces_no_budget_event(settings):
    model = StubModel([says("All good.")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("hello", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert not [record for record in records if record["kind"] == "budget"]


async def test_a_second_turn_starts_with_a_full_allowance(settings):
    """The budget belongs to the turn, not the session."""
    tight = load_settings(
        state_dir=settings.state_dir, user_id="tester", max_requests_per_turn=1
    )
    model = StubModel([says("one"), says("two")])

    async with open_session(tight, model=model, clock=FIXED_CLOCK) as session:
        first = await session.run_turn("first", thread_id="t1")
        second = await session.run_turn("second", thread_id="t1")

    assert first.requests == 1
    assert second.requests == 1
    assert BUDGET_REPLY not in (first.reply, second.reply)


async def test_concurrent_turns_do_not_share_an_allowance(settings):
    """Two turns at once in one session — what the CLI never does and the web
    gateway does constantly. Each must get its own full budget."""
    tight = load_settings(
        state_dir=settings.state_dir, user_id="tester", max_requests_per_turn=1
    )
    model = StubModel([says("ok")])

    async with open_session(tight, model=model, clock=FIXED_CLOCK) as session:
        first, second = await asyncio.gather(
            session.run_turn("one", thread_id="a"),
            session.run_turn("two", thread_id="b"),
        )

    assert first.requests == 1
    assert second.requests == 1
    assert BUDGET_REPLY not in first.reply
    assert BUDGET_REPLY not in second.reply


async def test_exhaustion_ends_the_turn_with_a_reply(settings):
    """A model that only ever asks for tools, given a budget smaller than the
    iteration cap: the budget must be what stops it."""
    tight = load_settings(
        state_dir=settings.state_dir,
        user_id="tester",
        max_iterations=8,
        max_requests_per_turn=2,
    )
    forever = StubModel([wants("list_events", {"day": "2026-08-25"})])

    async with open_session(tight, model=forever, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("loop forever please", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert result.reply == BUDGET_REPLY
    assert result.reply != CAP_REPLY  # the budget stopped it, not the cap
    assert forever.invocations == 2
    assert [record["kind"] for record in records].count("budget") == 1


async def test_no_model_request_is_recorded_after_exhaustion(settings):
    tight = load_settings(
        state_dir=settings.state_dir, user_id="tester", max_requests_per_turn=2
    )
    forever = StubModel([wants("list_events", {"day": "2026-08-25"})])

    async with open_session(tight, model=forever, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("go", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    kinds = [record["kind"] for record in records]
    assert "budget" in kinds
    after_the_stop = records[kinds.index("budget") + 1 :]
    assert not [r for r in after_the_stop if r["kind"] == "node" and r["node"] == "agent"]
    # And the stop is the last thing the turn did.
    assert records[-1]["kind"] == "budget"


async def test_the_iteration_cap_still_stops_a_turn_the_budget_would_allow(settings):
    """The two limits are independent. A generous budget must not disable the
    cap, and `iterations` must still live in graph state."""
    generous = load_settings(
        state_dir=settings.state_dir,
        user_id="tester",
        max_iterations=3,
        max_requests_per_turn=100,
    )
    forever = StubModel([wants("list_events", {"day": "2026-08-25"})])

    async with open_session(generous, model=forever, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("loop forever please", thread_id="t1")

    assert result.reply == CAP_REPLY
    assert result.iterations == 3
    assert result.requests == 3
