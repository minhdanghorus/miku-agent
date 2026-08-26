"""Tools: storage round-trips, input validation, registry lookup. No model calls."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from evals.helpers import StubModel, says, wants
from miku.graph.nodes import TURN_CONTEXT_KEY
from miku.memory.store import open_store, recall_facts
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.calendar_store import Event, events_on, insert_event
from miku.tools.clock import Clock
from miku.tools.proposals import _validate_window
from miku.tools.registry import UnknownToolError, build_tools, lookup
from miku.tools.scheduling import build_scheduling_tools


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


def tool_named(tools, name):
    return lookup(tools, name)


async def test_event_round_trips(settings):
    await insert_event(settings.db_path, Event("Tennis with Raj", "2026-08-29", "08:00"))
    found = await events_on(settings.db_path, "2026-08-29")
    assert [(e.title, e.start_time) for e in found] == [("Tennis with Raj", "08:00")]


async def test_two_events_on_one_day_both_persist_in_time_order(settings):
    await insert_event(settings.db_path, Event("Dentist", "2026-09-01", "09:00"))
    await insert_event(settings.db_path, Event("Gym", "2026-09-01", "07:00"))

    found = await events_on(settings.db_path, "2026-09-01")
    assert [e.title for e in found] == ["Gym", "Dentist"]


async def test_listing_an_empty_day_returns_nothing(settings):
    assert await events_on(settings.db_path, "2026-12-25") == []


async def test_events_survive_reopening_the_database(settings):
    await insert_event(settings.db_path, Event("Standup", "2026-08-26", "09:30"))
    # events_on opens its own connection, so this is a genuine reopen.
    reopened = load_settings(state_dir=settings.state_dir)
    assert len(await events_on(reopened.db_path, "2026-08-26")) == 1


async def test_create_event_tool_persists_and_confirms(settings):
    create = tool_named(build_scheduling_tools(settings), "create_event")
    result = await create.ainvoke(
        {"title": "Tennis with Raj", "day": "2026-08-29", "start_time": "08:00"}
    )

    assert "Tennis with Raj" in result
    assert len(await events_on(settings.db_path, "2026-08-29")) == 1


async def test_list_events_tool_reports_an_empty_day_honestly(settings):
    list_events = tool_named(build_scheduling_tools(settings), "list_events")
    result = await list_events.ainvoke({"day": "2026-12-25"})
    assert "No events" in result


async def test_create_event_rejects_a_weekday_name(settings):
    """Relative dates must be resolved before the tool, not inside it."""
    create = tool_named(build_scheduling_tools(settings), "create_event")
    with pytest.raises(ValueError, match="absolute ISO date"):
        await create.ainvoke({"title": "Tennis", "day": "Saturday", "start_time": "08:00"})


async def test_create_event_rejects_a_malformed_time(settings):
    create = tool_named(build_scheduling_tools(settings), "create_event")
    with pytest.raises(ValueError, match="HH:MM"):
        await create.ainvoke({"title": "Tennis", "day": "2026-08-29", "start_time": "8am"})


async def test_remember_tool_writes_one_fact(settings):
    async with open_store(settings) as store:
        remember = tool_named(build_tools(settings, store), "remember")
        await remember.ainvoke({"fact": "Alex prefers morning meetings"})
        assert await recall_facts(store, settings) == ["Alex prefers morning meetings"]


async def test_registry_exposes_the_expected_tools(settings):
    async with open_store(settings) as store:
        names = {tool.name for tool in build_tools(settings, store)}
        assert names == {"create_event", "list_events", "remember"}


async def test_unknown_tool_name_raises_and_names_the_alternatives(settings):
    async with open_store(settings) as store:
        tools = build_tools(settings, store)
        with pytest.raises(UnknownToolError) as excinfo:
            lookup(tools, "delete_everything")

    message = str(excinfo.value)
    assert "delete_everything" in message
    assert "create_event" in message


def test_fixed_clock_is_stable():
    clock = Clock.fixed("2026-08-29")
    assert clock.today.isoformat() == "2026-08-29"
    assert "Saturday" in clock.describe()


def test_real_clock_reports_today():
    from datetime import date

    assert Clock.real().today == date.today()


# --- per-turn context across the tool boundary -----------------------------
# A tool is a LangChain runnable, not a graph node, so it has no Runtime. The
# turn's tracer span and budget reach it in the invocation config instead. These
# tests hold that seam: the tool sees the current turn, the model never does.


async def test_a_config_declaring_tool_receives_the_turns_context(settings):
    seen = {}

    async def probe(note: str, config: RunnableConfig) -> str:
        """Record what context the turn handed over.

        Args:
            note: anything.
        """
        turn = (config.get("configurable") or {}).get(TURN_CONTEXT_KEY)
        # Read the counter here, not after the turn: the budget is shared by
        # reference on purpose, so a later read sees the whole turn's spending.
        seen["spent_at_call_time"] = turn.budget.spent
        seen["turn_id"] = turn.tracer.turn_id
        seen["parent"] = turn.tracer.parent
        return f"noted {note}"

    probe_tool = StructuredTool.from_function(
        coroutine=probe, name="probe", description="Records the turn context it was given."
    )
    # Appended after the graph was built, which the stub model does not mind:
    # the tools node resolves names against deps.tools at call time.
    model = StubModel([wants("probe", {"note": "hi"}), says("done")])

    async with open_session(settings, model=model, clock=Clock.fixed("2026-08-25")) as session:
        session.deps.tools.append(probe_tool)
        result = await session.run_turn("probe please", thread_id="t1")

    assert seen["spent_at_call_time"] == 1  # only the agent call that asked for it
    assert seen["turn_id"] == result.turn_id
    assert seen["parent"]  # anchored under the tools node, not at the turn root


async def test_two_turns_in_one_session_receive_different_context(settings):
    seen = []

    async def probe(note: str, config: RunnableConfig) -> str:
        """Record the turn.

        Args:
            note: anything.
        """
        turn = (config.get("configurable") or {})[TURN_CONTEXT_KEY]
        seen.append((turn.tracer.turn_id, turn.budget.spent))
        return "ok"

    probe_tool = StructuredTool.from_function(
        coroutine=probe, name="probe", description="Records the turn context it was given."
    )
    model = StubModel(
        [
            wants("probe", {"note": "a"}),
            says("one"),
            wants("probe", {"note": "b"}),
            says("two"),
        ]
    )

    async with open_session(settings, model=model, clock=Clock.fixed("2026-08-25")) as session:
        session.deps.tools.append(probe_tool)
        first = await session.run_turn("first", thread_id="t1")
        second = await session.run_turn("second", thread_id="t1")

    assert len(seen) == 2
    assert [turn_id for turn_id, _ in seen] == [first.turn_id, second.turn_id]
    # A fresh allowance each turn: the second tool call saw one request spent,
    # not three. Object identity is not asserted -- CPython happily reuses the
    # address of the first turn's collected budget.
    assert [spent for _, spent in seen] == [1, 1]


async def test_the_turn_context_is_invisible_to_the_model(settings):
    """It travels in the config, so it must not appear in any bound schema."""
    async with open_store(settings) as store:
        for tool in build_tools(settings, store):
            assert TURN_CONTEXT_KEY not in tool.args
            assert "config" not in tool.args


async def test_the_existing_tools_need_no_config_parameter(settings):
    """Passing a config must not disturb a tool that never asked for one."""
    async with open_store(settings) as store:
        create = lookup(build_tools(settings, store), "create_event")
        out = await create.ainvoke(
            {"title": "Gym", "day": "2026-09-01", "start_time": "07:00"},
            {"configurable": {TURN_CONTEXT_KEY: "ignored"}},
        )
    assert "Gym" in out
    assert len(await events_on(settings.db_path, "2026-09-01")) == 1


# --- the proposal tool -----------------------------------------------------
# Its fan-out behaviour lives in test_fanout.py. What is asserted here is the
# boundary: what it refuses, what it must not do, and the fact that the model is
# told when to use a different tool.


def test_window_validation_rejects_a_weekday_name():
    with pytest.raises(ValueError, match="absolute ISO date"):
        _validate_window("Saturday", "2026-08-30")


def test_window_validation_rejects_a_malformed_end_date():
    with pytest.raises(ValueError, match="end_day must be"):
        _validate_window("2026-08-26", "next Friday")


def test_a_backwards_window_is_reported_not_reversed():
    """Reversing it silently might not be what the model meant."""
    with pytest.raises(ValueError, match="before start_day"):
        _validate_window("2026-08-30", "2026-08-26")


def test_an_absurdly_wide_window_is_refused():
    with pytest.raises(ValueError, match="too wide"):
        _validate_window("2026-01-01", "2026-12-31")


def test_a_single_day_window_is_valid():
    assert _validate_window("2026-08-26", "2026-08-26") == ("2026-08-26", "2026-08-26")


async def test_the_proposal_tool_is_registered_in_a_session(settings):
    model = StubModel([says("ok")])
    async with open_session(settings, model=model, clock=Clock.fixed("2026-08-25")) as session:
        names = sorted(tool.name for tool in session.deps.tools)
    assert names == ["create_event", "list_events", "propose_slots", "remember"]


async def test_the_two_scheduling_tools_state_their_boundary(settings):
    """The spike measured this sentence, not the control flow, as what fixes
    misrouting. If it is edited away, this fails."""
    model = StubModel([says("ok")])
    async with open_session(settings, model=model, clock=Clock.fixed("2026-08-25")) as session:
        by_name = {tool.name: tool.description for tool in session.deps.tools}

    assert "create_event" in by_name["propose_slots"]
    assert "NOT" in by_name["propose_slots"]
    assert "propose_slots" in by_name["create_event"]
    assert "NOT" in by_name["create_event"]


async def test_the_proposal_tool_refuses_to_run_outside_a_turn(settings):
    """Without a turn context there is no budget to spend and no trace to write
    to, so fanning out would be unaccounted work."""
    model = StubModel([says("ok")])
    async with open_session(settings, model=model, clock=Clock.fixed("2026-08-25")) as session:
        tool = lookup(session.deps.tools, "propose_slots")
        with pytest.raises(ValueError, match="turn context"):
            await tool.ainvoke(
                {"task": "review", "start_day": "2026-08-26", "end_day": "2026-08-30"}
            )
