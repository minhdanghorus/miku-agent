"""The loop's contract, driven by a stubbed model. No credentials, no network."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from evals.helpers import StubModel, says, tool_call, wants
from miku.graph.nodes import CAP_REPLY, build_system_prompt, load_persona
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.calendar_store import events_on
from miku.tools.clock import Clock


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester", max_iterations=3)


FIXED_CLOCK = Clock.fixed("2026-08-25")  # a Tuesday


async def test_a_turn_needing_no_tools_ends_after_one_model_call(settings):
    model = StubModel([says("Hei, nothing to book.")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("just saying hello", thread_id="t1")

    assert result.reply == "Hei, nothing to book."
    assert result.tool_calls == []
    assert model.invocations == 1


async def test_a_tool_call_loops_back_through_the_agent(settings):
    model = StubModel(
        [
            wants("create_event", {"title": "Tennis", "day": "2026-08-29", "start_time": "08:00"}),
            says("Booked Tennis for Saturday at 08:00."),
        ]
    )

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("book tennis saturday 8am", thread_id="t1")

    assert result.called("create_event")
    assert model.invocations == 2  # the agent ran again after the tool
    assert len(await events_on(settings.db_path, "2026-08-29")) == 1
    assert result.reply.startswith("Booked Tennis")


async def test_multiple_tool_calls_in_one_response_all_execute(settings):
    both = AIMessage(
        content="",
        tool_calls=[
            tool_call(
                "create_event", {"title": "Gym", "day": "2026-08-31", "start_time": "07:00"}, "a"
            ),
            tool_call(
                "create_event",
                {"title": "Dentist", "day": "2026-08-31", "start_time": "09:00"},
                "b",
            ),
        ],
    )
    model = StubModel([both, says("Both booked.")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("book gym and dentist", thread_id="t1")

    stored = await events_on(settings.db_path, "2026-08-31")
    assert [event.title for event in stored] == ["Gym", "Dentist"]


async def test_a_raising_tool_still_yields_a_reply(settings):
    """A weekday name is rejected by the tool; the model must see the error."""
    model = StubModel(
        [
            wants("create_event", {"title": "Tennis", "day": "Saturday", "start_time": "08:00"}),
            says("That date was not absolute — which Saturday did you mean?"),
        ]
    )

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("book tennis saturday", thread_id="t1")

    assert "Saturday" in result.reply
    # The error came back as a tool result, and nothing was stored.
    tool_results = [
        message
        for message in model.calls[-1]
        if getattr(message, "type", "") == "tool"
    ]
    assert any("Error" in str(message.content) for message in tool_results)


async def test_an_unknown_tool_yields_an_error_without_running_a_substitute(settings):
    model = StubModel([wants("delete_everything", {}), says("I cannot do that.")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("wipe my calendar", thread_id="t1")

    tool_results = [m for m in model.calls[-1] if getattr(m, "type", "") == "tool"]
    assert any("Unknown tool" in str(m.content) for m in tool_results)
    assert result.reply == "I cannot do that."
    # No registered tool ran in its place.
    assert await events_on(settings.db_path, "2026-08-29") == []


async def test_the_cap_terminates_a_runaway_turn(settings):
    """A model that only ever asks for tools must still end the turn."""
    forever = StubModel([wants("list_events", {"day": "2026-08-25"})])

    async with open_session(settings, model=forever, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("loop forever please", thread_id="t1")

    assert result.reply == CAP_REPLY
    assert result.iterations == settings.max_iterations
    # Bounded work, not a hang: one model call per allowed iteration.
    assert forever.invocations == settings.max_iterations


async def test_the_cap_event_is_traced(settings):
    forever = StubModel([wants("list_events", {"day": "2026-08-25"})])

    async with open_session(settings, model=forever, clock=FIXED_CLOCK) as session:
        await session.run_turn("loop forever please", thread_id="t1")
        trace = session.deps.tracer.path

    kinds = [json.loads(line)["kind"] for line in trace.read_text(encoding="utf-8").splitlines()]
    assert "cap" in kinds


async def test_a_normal_turn_is_unaffected_by_the_cap(settings):
    model = StubModel([says("All good.")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("hello", thread_id="t1")

    assert CAP_REPLY not in result.reply
    assert result.iterations == 1


async def test_the_iteration_count_resets_between_turns(settings):
    """State survives on a thread; the budget must not carry over with it."""
    model = StubModel([says("one"), says("two")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("first", thread_id="t1")
        second = await session.run_turn("second", thread_id="t1")

    assert second.iterations == 1


async def test_a_thread_resumes_after_reopening_the_database(settings):
    model = StubModel([says("noted")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("my cat is called Mochi", thread_id="keep")

    # A second process would open its own session over the same state.db.
    model_two = StubModel([says("Mochi")])
    async with open_session(settings, model=model_two, clock=FIXED_CLOCK) as session:
        await session.run_turn("what is my cat called?", thread_id="keep")

    history = [str(getattr(m, "content", "")) for m in model_two.calls[-1]]
    assert any("Mochi" in text for text in history)


async def test_two_threads_do_not_see_each_other_history(settings):
    model = StubModel([says("ok")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("thread one secret: pineapple", thread_id="one")
        await session.run_turn("anything?", thread_id="two")

    second_turn_messages = [str(getattr(m, "content", "")) for m in model.calls[-1]]
    assert not any("pineapple" in text for text in second_turn_messages)


async def test_the_agent_never_reads_persona_or_memory_itself(settings):
    """The system prompt reaches the model through state, from assemble."""
    model = StubModel([says("ok")])

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("hi", thread_id="t1")

    system = model.calls[0][0]
    assert system.type == "system"
    assert "Miku" in system.content
    assert "2026-08-25" in system.content


async def test_recalled_facts_reach_the_system_prompt(settings):
    model = StubModel(
        [wants("remember", {"fact": "I dislike meetings before 9am"}), says("Remembered.")]
    )

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("remember I dislike meetings before 9am", thread_id="t1")

    # A later turn — the fact should now be part of working memory.
    later = StubModel([says("ok")])
    async with open_session(settings, model=later, clock=FIXED_CLOCK) as session:
        await session.run_turn("book something", thread_id="t2")

    assert "meetings before 9am" in later.calls[0][0].content


def test_an_empty_memory_produces_a_valid_prompt():
    prompt = build_system_prompt(load_persona(), [], "2026-08-25 (Tuesday)")
    assert "What you remember" not in prompt
    assert "2026-08-25" in prompt


async def test_every_node_transition_is_traced(settings):
    model = StubModel(
        [
            wants("list_events", {"day": "2026-08-25"}),
            says("Nothing today."),
        ]
    )

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("what is on today?", thread_id="t1")
        lines = session.deps.tracer.path.read_text(encoding="utf-8").splitlines()

    records = [json.loads(line) for line in lines]
    nodes = [r["node"] for r in records if r["kind"] == "node"]
    assert nodes == ["assemble", "agent", "tools", "agent"]
    assert all(r["turn_id"] == result.turn_id for r in records)
