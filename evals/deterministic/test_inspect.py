"""The read-only introspection surface: what it reports, and what it must not do."""

from __future__ import annotations

import inspect as stdlib_inspect

import pytest

from evals.helpers import StubModel, says, wants
from miku.memory.store import open_store, remember_fact, supersede_fact
from miku.runtime import inspect as runtime_inspect
from miku.runtime.config import load_settings
from miku.runtime.providers import GREENNODE, ROLES
from miku.runtime.session import open_session
from miku.tools.clock import Clock

FIXED_CLOCK = Clock.fixed("2026-08-25")  # a Tuesday


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester", max_iterations=3)


# --- Configuration ----------------------------------------------------------


def test_every_role_is_reported_with_the_model_it_resolves_to(settings):
    view = runtime_inspect.config_view(settings)

    assert view.provider == GREENNODE.name
    assert [role.role for role in view.roles] == list(ROLES)
    assert all(role.model for role in view.roles)
    assert not any(role.error for role in view.roles)


def test_an_override_is_reported_alongside_the_resolved_model():
    """Otherwise a reader cannot tell a default from a deliberate choice."""
    tuned = load_settings(model_judge="openai/gpt-4o-mini")
    judge = next(role for role in runtime_inspect.config_view(tuned).roles if role.role == "judge")

    assert judge.override == "openai/gpt-4o-mini"
    assert judge.model == "openai/gpt-4o-mini"


def test_an_unresolvable_role_does_not_blank_the_others(settings, monkeypatch):
    """One broken role is a line in the view, not a dead tab."""
    monkeypatch.delitem(GREENNODE.models, "embed")
    view = runtime_inspect.config_view(settings)

    embed = next(role for role in view.roles if role.role == "embed")
    main = next(role for role in view.roles if role.role == "main")

    assert embed.error and not embed.model
    assert main.model and not main.error


def test_an_unregistered_provider_is_reported_rather_than_raised():
    view = runtime_inspect.config_view(load_settings(provider="nope"))
    assert "nope" in view.provider


def test_the_configured_limits_are_reported(settings):
    limits = runtime_inspect.config_view(settings).limits

    assert limits["max_iterations"] == 3
    assert limits["max_requests_per_turn"] == settings.max_requests_per_turn


# --- Tools ------------------------------------------------------------------


async def test_tools_are_reported_with_the_description_the_model_reads(settings):
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        view = runtime_inspect.tools_view(session.deps.tools)

    by_name = {tool.name: tool.description for tool in view}
    assert {"create_event", "list_events", "remember", "propose_slots"} <= set(by_name)
    assert all(description for description in by_name.values())


async def test_the_delegating_tool_is_not_missing_from_the_view(settings):
    """`propose_slots` is appended after `Deps` exists.

    A view that rebuilt the registry itself would silently omit it, and the tab
    would be wrong in exactly the way nobody checks.
    """
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        names = [tool.name for tool in runtime_inspect.tools_view(session.deps.tools)]

    assert "propose_slots" in names


# --- Memory -----------------------------------------------------------------


async def test_memory_reports_live_facts_and_excludes_superseded_ones(settings):
    async with open_store(settings) as store:
        stale = await remember_fact(store, settings, "I train on Tuesdays")
        fresh = await remember_fact(store, settings, "I train on Thursdays")
        await supersede_fact(store, settings, stale, fresh)

        view = await runtime_inspect.memory_view(store, settings)

    assert [fact.fact for fact in view] == ["I train on Thursdays"]


async def test_an_empty_store_reports_an_empty_list(settings):
    async with open_store(settings) as store:
        assert await runtime_inspect.memory_view(store, settings) == []


async def test_inspecting_memory_writes_nothing(settings):
    """Read-only, checked rather than asserted in a docstring."""
    async with open_store(settings) as store:
        await remember_fact(store, settings, "I train on Thursdays")
        before = await runtime_inspect.memory_view(store, settings)

        for _ in range(3):
            await runtime_inspect.memory_view(store, settings)

        after = await runtime_inspect.memory_view(store, settings)

    assert [(fact.key, fact.fact, fact.created_at) for fact in before] == [
        (fact.key, fact.fact, fact.created_at) for fact in after
    ]


# --- Traces -----------------------------------------------------------------


async def test_a_recorded_turn_is_reported_as_a_tree(settings):
    model = StubModel(
        [
            wants("create_event", {"title": "Tennis", "day": "2026-08-29", "start_time": "08:00"}),
            says("Booked."),
        ]
    )
    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("book tennis", thread_id="t1")

    roots = runtime_inspect.turn_view(settings, result.turn_id)

    assert len(roots) == 1
    walked = list(roots[0].walk())
    assert len(walked) > 1
    assert any(node.kind == "tool_call" for node in walked)


async def test_a_recorded_turn_is_listed_among_the_days_turns(settings):
    async with open_session(settings, model=StubModel([says("hi")]), clock=FIXED_CLOCK) as session:
        result = await session.run_turn("hello", thread_id="t1")

    assert result.turn_id in runtime_inspect.turn_ids_on(settings)
    assert runtime_inspect.trace_dates(settings)


def test_a_turn_that_was_never_recorded_reports_empty(settings):
    settings.ensure_dirs()
    assert runtime_inspect.turn_view(settings, "no-such-turn") == []


def test_a_date_with_no_trace_file_reports_empty(settings):
    settings.ensure_dirs()
    assert runtime_inspect.turn_ids_on(settings, day="1999-01-01") == []
    assert runtime_inspect.turn_view(settings, "any", day="1999-01-01") == []


def test_no_traces_directory_at_all_reports_empty(settings):
    """Before anything has ever run. Absence is data, not an exception."""
    assert runtime_inspect.trace_dates(settings) == []
    assert runtime_inspect.turn_ids_on(settings) == []


def test_a_partially_written_trace_file_still_reports_its_records(settings):
    settings.ensure_dirs()
    path = settings.traces_dir / "2026-08-25.jsonl"
    path.write_text(
        '{"turn_id": "t", "span": "a", "parent": null, "kind": "node", "node": "assemble"}\n'
        "{not json at all\n"
        '{"turn_id": "t", "span": "b", "parent": "a", "kind": "node", "node": "agent"}\n',
        encoding="utf-8",
    )

    roots = runtime_inspect.turn_view(settings, "t", day="2026-08-25")

    assert len(roots) == 1
    assert len(list(roots[0].walk())) == 2


# --- Conversations ----------------------------------------------------------


def remembering_model() -> StubModel:
    """A turn that calls a tool before replying.

    The shape below is not invented. It is what `.miku/state.db` actually holds
    for a turn like this, read back before any of this was designed: a human
    message, an `AIMessage` with empty content carrying the call, the
    `ToolMessage`, then the real reply. Every filtering case here is driven by
    that shape rather than by a fixture bent to suit the filter.
    """
    return StubModel(
        [
            wants("remember", {"fact": "Dang likes Detective Conan"}),
            says("Got it. I've added that to your preferences."),
        ]
    )


async def test_conversations_are_listed_one_entry_each_not_one_per_checkpoint(settings):
    """`alist(None)` yields every checkpoint, not one row per thread.

    Measured on the real database at 272 tuples across 15 conversations -- so a
    listing that forwarded the stream would have shown one conversation
    fifty-six times. This is the case that would have caught it.
    """
    async with open_session(settings, model=remembering_model(), clock=FIXED_CLOCK) as session:
        await session.run_turn("I also like Detective Conan", thread_id="alpha")
        views = await runtime_inspect.thread_list(session.checkpointer)

    assert [view.thread_id for view in views] == ["alpha"]
    # Several checkpoints were written for that one turn.
    assert views[0].message_count > 1


async def test_the_most_recently_active_conversation_is_listed_first(settings):
    async with open_session(settings, model=StubModel([says("one"), says("two")])) as session:
        await session.run_turn("first", thread_id="older")
        await session.run_turn("second", thread_id="newer")
        views = await runtime_inspect.thread_list(session.checkpointer)

    assert [view.thread_id for view in views] == ["newer", "older"]


async def test_a_conversation_is_titled_from_its_opening_message(settings):
    """Derived at read time, and it does not move when the conversation does.

    No stored field backs it, which is what keeps `checkpointer.py`'s standing
    claim -- that a conversation list needs no new data model -- true.
    """
    async with open_session(settings, model=StubModel([says("a"), says("b")])) as session:
        await session.run_turn("Beside Naruto, I also like Detective Conan", thread_id="anime")
        first = await runtime_inspect.thread_list(session.checkpointer)
        await session.run_turn("and Dragon Ball", thread_id="anime")
        second = await runtime_inspect.thread_list(session.checkpointer)

    assert first[0].title == "Beside Naruto, I also like Detective Conan"
    assert second[0].title == first[0].title
    assert second[0].message_count > first[0].message_count


async def test_a_long_opening_message_is_truncated_rather_than_dropped(settings):
    async with open_session(settings, model=StubModel([says("ok")])) as session:
        await session.run_turn("word " * 40, thread_id="wordy")
        views = await runtime_inspect.thread_list(session.checkpointer)

    assert len(views[0].title) == runtime_inspect.TITLE_WIDTH
    assert views[0].title.endswith("...")


async def test_no_conversations_reads_as_empty(settings):
    async with open_session(settings, model=StubModel([says("hi")])) as session:
        assert await runtime_inspect.thread_list(session.checkpointer) == []


async def test_a_tool_calling_turn_reads_back_without_an_empty_assistant_line(settings):
    """The filtering, against the stored shape that motivated it.

    The empty `AIMessage` is a real stored message that says nothing -- it
    carries the requested call -- and rendering it produces a blank bubble
    corresponding to no moment the user experienced.
    """
    async with open_session(settings, model=remembering_model(), clock=FIXED_CLOCK) as session:
        await session.run_turn("I also like Detective Conan", thread_id="anime")
        exchanges = await runtime_inspect.conversation_view(session.checkpointer, "anime")

    assert [entry.role for entry in exchanges] == ["user", "tool", "assistant"]
    assert all(entry.text for entry in exchanges)
    # The tool's own sentence, unmodified. These tools return prose, which is
    # why showing tool activity is a filtering decision and not a formatting one.
    assert exchanges[1].text.startswith("Remembered:")


async def test_exchanges_are_reported_in_the_order_they_were_stored(settings):
    async with open_session(settings, model=StubModel([says("one"), says("two")])) as session:
        await session.run_turn("first", thread_id="ordered")
        await session.run_turn("second", thread_id="ordered")
        exchanges = await runtime_inspect.conversation_view(session.checkpointer, "ordered")

    assert [entry.text for entry in exchanges] == ["first", "one", "second", "two"]


async def test_a_conversation_view_exposes_no_persistence_structure(settings):
    """A transcript reports what was said, not how LangGraph stored it.

    The persisted format belongs to a library and moves on its schedule. A
    surface that leaked it would bind the browser, the terminal and every case
    here to that schedule.
    """
    async with open_session(settings, model=StubModel([says("hi")])) as session:
        await session.run_turn("hello", thread_id="plain")
        exchanges = await runtime_inspect.conversation_view(session.checkpointer, "plain")

    assert {entry.role for entry in exchanges} <= {"user", "assistant", "tool"}
    for entry in exchanges:
        fields = set(vars(entry))
        assert fields == {"role", "text"}, fields


async def test_an_unknown_conversation_reads_as_empty_and_creates_nothing(settings):
    async with open_session(settings, model=StubModel([says("hi")])) as session:
        assert await runtime_inspect.conversation_view(session.checkpointer, "no-such") == []
        assert await runtime_inspect.thread_list(session.checkpointer) == []


async def test_reading_conversations_writes_no_checkpoint(settings):
    """Read-only, checked rather than asserted in a docstring.

    Reading thread state is permitted here; writing it is not, which is why
    removal lives on `Session` and not in this module.
    """
    async with open_session(settings, model=StubModel([says("hi")])) as session:
        await session.run_turn("hello", thread_id="quiet")

        before = [
            saved.config["configurable"]["checkpoint_id"]
            async for saved in session.checkpointer.alist(None)
        ]
        for _ in range(3):
            await runtime_inspect.thread_list(session.checkpointer)
            await runtime_inspect.conversation_view(session.checkpointer, "quiet")
            await runtime_inspect.conversation_view(session.checkpointer, "no-such")
        after = [
            saved.config["configurable"]["checkpoint_id"]
            async for saved in session.checkpointer.alist(None)
        ]

    assert before == after


# --- The two rules ----------------------------------------------------------


def code_only(module) -> str:
    """A module's source with comments and string literals removed.

    Both rules below are about what the code *does*, and the first version of
    them was not: it failed on the module docstring explaining the rule. A
    grep over raw source cannot tell a call from a sentence about a call, and a
    test that trips over its own subject's prose is a test that will be deleted
    the next time someone edits a comment.
    """
    import io
    import tokenize

    source = stdlib_inspect.getsource(module)
    kept = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in (tokenize.COMMENT, tokenize.STRING)
    ]
    return " ".join(kept)


def test_the_helper_that_strips_prose_keeps_the_code():
    """The control. A stripper that returned nothing would pass both rules."""
    stripped = code_only(runtime_inspect)

    assert "def config_view" in stripped
    assert "resolve_model" in stripped
    assert "Absence is data" not in stripped


def test_the_module_reads_no_environment_variable():
    """`config.py` resolves configuration, and stays the only place that does.

    A second reader of the environment is how two answers to "what is
    configured?" begin to diverge.
    """
    stripped = code_only(runtime_inspect)

    for forbidden in ("environ", "getenv", "load_dotenv"):
        assert forbidden not in stripped, f"inspect.py must not read {forbidden}"


def test_the_module_neither_writes_nor_calls_a_model():
    """Pinning the rule that keeps this from becoming a junk drawer."""
    stripped = code_only(runtime_inspect)

    for forbidden in ("aput", "adelete", "ainvoke", "chat_model", "open_session"):
        assert forbidden not in stripped, f"inspect.py must not reference {forbidden}"
