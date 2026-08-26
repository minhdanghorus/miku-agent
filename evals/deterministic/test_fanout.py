"""Best-of-N fan-out: shape, diversity, degradation. No credentials needed.

Every assertion here reads either a stored row or the trace tree. None reads the
wording of a recommendation, because a fan-out's value is in its structure -- how
many branches ran, what caused them, what each was told to look for -- and
structure is what a small model cannot phrase differently every run.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from evals.helpers import PromptModel, slot_line, wants
from miku.graph.fanout import ANGLES, NO_CANDIDATES, resolve_angles
from miku.ops.traceview import branches_under, build_tree, parents_of, read_records
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.calendar_store import Event, events_between, insert_event
from miku.tools.clock import Clock

FIXED_CLOCK = Clock.fixed("2026-08-25")  # a Tuesday
WINDOW = {"task": "1-hour design review", "start_day": "2026-08-26", "end_day": "2026-08-30"}

# Markers that identify which of the three prompt kinds the stub is answering.
BRANCH_MARKER = "You are proposing ONE candidate"
SELECT_MARKER = "Choose the single best time slot"


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


def responder(
    *,
    day_for=lambda index: f"2026-08-{26 + index:02d}",
    choose: str = "1",
    branch_reply=None,
):
    """A prompt-dispatching stub: ask for the tool, answer branches, then judge.

    Each branch gets a different day, keyed off which angle it was given, so a
    candidate can be traced back to the branch that produced it regardless of
    the order branches finish in.
    """
    angle_index = {angle.name: index for index, angle in enumerate(ANGLES)}

    def respond(prompt: str) -> AIMessage:
        if BRANCH_MARKER in prompt:
            for name, index in angle_index.items():
                if f"Your angle for this proposal: {name}" in prompt:
                    if branch_reply is not None:
                        return branch_reply(name, index)
                    return slot_line(day_for(index), f"{9 + index:02d}:00", f"best for {name}")
            # A caller-supplied angle, not one of the defaults.
            return slot_line("2026-08-27", "15:00", "supplied angle")
        if SELECT_MARKER in prompt:
            return AIMessage(content=choose)
        # Otherwise this is the agent node. Tool schemas are not part of message
        # content, so the tool result is what tells us the fan-out already ran.
        already_ran = "Recommended:" in prompt or NO_CANDIDATES in prompt
        if already_ran:
            return AIMessage(content="There you go.")
        return wants("propose_slots", dict(WINDOW))

    return respond


# --- angle resolution, in isolation ---------------------------------------


def test_the_default_angles_are_distinct():
    names = [angle.name for angle in ANGLES]
    assert len(set(names)) == len(names)


def test_resolution_clamps_to_the_limit():
    assert len(resolve_angles(None, 3)) == 3
    assert len(resolve_angles(None, 99)) == len(ANGLES)
    assert resolve_angles(None, 0) == []


def test_supplied_angles_replace_the_defaults():
    resolved = resolve_angles(["mornings only", "after standup"], 5)
    assert [angle.name for angle in resolved] == ["mornings only", "after standup"]


def test_duplicate_supplied_angles_collapse():
    """Two branches with the same angle cost two requests and buy one answer."""
    resolved = resolve_angles(["mornings", "Mornings", " mornings "], 5)
    assert len(resolved) == 1


# --- the shape of a fan-out ----------------------------------------------


async def test_a_fanout_runs_one_branch_per_configured_width(settings):
    tuned = load_settings(state_dir=settings.state_dir, user_id="tester", fanout_branches=3)
    model = PromptModel(respond=responder())

    async with open_session(tuned, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    generated = branches_under(records, "generate")
    assert len(generated) == 3
    assert sorted(record["branch"] for record in generated) == [0, 1, 2]


async def test_every_branch_hangs_under_one_parent(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    parents = parents_of(records, "generate")
    assert len(parents) == 1
    plan = [r for r in records if r["node"] == "plan_angles"]
    assert parents == {plan[0]["span"]}


async def test_each_branch_receives_a_distinct_angle(settings):
    """Diversity asserted structurally: five branches, five different angles,
    no reading of generated prose."""
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    angles = [record["angle"] for record in branches_under(records, "generate")]
    assert len(angles) == len(ANGLES)
    assert len(set(angles)) == len(angles)


async def test_the_whole_turn_reconstructs_as_one_tree(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    roots = build_tree(records)
    assert len(roots) == 1
    visited = [node.node for node in roots[0].walk()]
    for expected in ("assemble", "agent", "tools", "plan_angles", "generate", "select_best"):
        assert expected in visited


async def test_selection_runs_exactly_once_after_the_branches(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    selections = [r for r in records if r["node"] == "select_best"]
    assert len(selections) == 1
    assert selections[0]["candidates"] == len(ANGLES)
    assert selections[0]["judged"] is True

    # And it came after every branch: no generate event is written later.
    last_generate = max(i for i, r in enumerate(records) if r["node"] == "generate")
    assert records.index(selections[0]) > last_generate


async def test_the_judges_choice_is_what_gets_recommended(settings):
    model = PromptModel(respond=responder(choose="3"))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    selection = next(r for r in records if r["node"] == "select_best")
    assert selection["chosen"] == 3


async def test_an_out_of_range_choice_falls_back_to_the_earliest(settings):
    """A judge that answers badly should cost a worse slot, not the answer."""
    model = PromptModel(respond=responder(choose="the fourth one, obviously"))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert next(r for r in records if r["node"] == "select_best")["chosen"] == 0
    assert "Recommended:" in result.reply or result.tool_calls


# --- clamping -------------------------------------------------------------


async def test_asking_for_more_branches_than_angles_clamps_and_says_so(settings):
    tuned = load_settings(state_dir=settings.state_dir, user_id="tester", fanout_branches=9)
    model = PromptModel(respond=responder())

    async with open_session(tuned, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert len(branches_under(records, "generate")) == len(ANGLES)
    clamp = next(r for r in records if r["kind"] == "clamp")
    assert clamp["asked"] == 9
    assert clamp["using"] == len(ANGLES)
    assert clamp["reason"] == "angles"


async def test_a_tight_budget_clamps_the_width_and_says_so(settings):
    """One request for the agent, so three remain: two branches plus the judge."""
    tuned = load_settings(
        state_dir=settings.state_dir, user_id="tester", max_requests_per_turn=4
    )
    model = PromptModel(respond=responder())

    async with open_session(tuned, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert len(branches_under(records, "generate")) == 2
    clamp = next(r for r in records if r["kind"] == "clamp")
    assert clamp["reason"] == "budget"
    assert result.requests <= 4


async def test_the_defaults_are_used_when_the_model_supplies_none(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    used = next(r for r in records if r["node"] == "plan_angles")["angles"]
    assert used == [angle.name for angle in ANGLES]


async def test_angles_supplied_by_the_model_replace_the_defaults(settings):
    """The default list is the baseline; the model may override it per call."""
    supplied = ["mornings only", "after my standup"]

    def respond(prompt):
        if BRANCH_MARKER in prompt:
            return slot_line("2026-08-27", "09:00", "supplied angle")
        if SELECT_MARKER in prompt:
            return AIMessage(content="0")
        if "Recommended:" in prompt or NO_CANDIDATES in prompt:
            return AIMessage(content="Done.")
        return wants("propose_slots", {**WINDOW, "angles": supplied})

    model = PromptModel(respond=respond)

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("only mornings please", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    plan = next(r for r in records if r["node"] == "plan_angles")
    assert plan["angles"] == supplied
    assert len(branches_under(records, "generate")) == len(supplied)
    # And none of the defaults leaked into the branches.
    used = {r["angle"] for r in branches_under(records, "generate")}
    assert used == set(supplied)


# --- degradation ----------------------------------------------------------


async def test_one_candidate_is_selected_without_a_model_call(settings):
    tuned = load_settings(state_dir=settings.state_dir, user_id="tester", fanout_branches=1)
    model = PromptModel(respond=responder())

    async with open_session(tuned, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    selection = next(r for r in records if r["node"] == "select_best")
    assert selection["candidates"] == 1
    assert selection["judged"] is False
    assert not model.prompts_containing(SELECT_MARKER)


async def test_an_unparseable_branch_drops_out_and_the_rest_still_select(settings):
    def broken(name, index):
        if index == 0:
            return AIMessage(content="Sorry, I cannot help with that.")
        return slot_line(f"2026-08-{26 + index:02d}", "10:00", f"for {name}")

    model = PromptModel(respond=responder(branch_reply=broken))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    failed = [r for r in branches_under(records, "generate") if r.get("ok") is False]
    assert len(failed) == 1
    assert failed[0]["reason"] == "unparseable"
    assert next(r for r in records if r["node"] == "select_best")["candidates"] == len(ANGLES) - 1
    # The recommendation is in the tool result handed back to the agent, not in
    # the agent's own closing sentence.
    assert model.prompts_containing("Recommended:")
    assert result.called("propose_slots")


async def test_a_slot_outside_the_window_is_not_a_candidate(settings):
    """A branch that answers in shape but out of bounds is still a failure."""

    def out_of_window(name, index):
        return slot_line("2026-12-25", "10:00", "nowhere near the window")

    model = PromptModel(respond=responder(branch_reply=out_of_window))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")
        records = read_records(session.deps.tracer.path, result.turn_id)

    assert next(r for r in records if r["node"] == "select_best")["candidates"] == 0
    assert NO_CANDIDATES in result.reply or result.reply


async def test_no_candidate_at_all_still_produces_a_reply(settings):
    def nothing(name, index):
        return AIMessage(content="no idea")

    model = PromptModel(respond=responder(branch_reply=nothing))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")

    assert result.reply  # a reply, not an exception
    assert result.called("propose_slots")


# --- what a proposal must not do -----------------------------------------


async def test_proposing_books_nothing(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("when should I do the review?", thread_id="t1")

    stored = await events_between(settings.db_path, "2026-08-01", "2026-12-31")
    assert stored == []


async def test_existing_events_are_shown_to_every_branch(settings):
    """"Quietest day" is meaningless without the calendar, so the calendar has
    to reach the branches."""
    await insert_event(settings.db_path, Event("Standup", "2026-08-27", "09:30"))
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("when should I do the review?", thread_id="t1")

    branch_prompts = model.prompts_containing(BRANCH_MARKER)
    assert len(branch_prompts) == len(ANGLES)
    assert all("Standup" in prompt for prompt in branch_prompts)


async def test_events_outside_the_window_are_not_shown(settings):
    await insert_event(settings.db_path, Event("Holiday", "2026-12-25", "09:00"))
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("when should I do the review?", thread_id="t1")

    assert all(
        "Holiday" not in prompt for prompt in model.prompts_containing(BRANCH_MARKER)
    )


async def test_the_answer_names_the_alternatives_considered(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        result = await session.run_turn("when should I do the review?", thread_id="t1")

    # The tool result, not the reply: what the model was handed to work from.
    assert model.prompts_containing("Also considered")
    assert result.called("propose_slots")


# --- the reduced pile is not the ranked list ------------------------------
# Regression: select_best used to write its sorted order back to `candidates`,
# which has an operator.add reducer. The sorted copy was appended rather than
# replacing, so `chosen` indexed a list that no longer matched and the answer
# listed every candidate twice. A live run found it; these keep it found.


async def test_the_answer_lists_each_candidate_exactly_once(settings):
    model = PromptModel(respond=responder())

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("when should I do the review?", thread_id="t1")

    handed_back = model.prompts_containing("Also considered")[-1]
    offered = handed_back[handed_back.index("Also considered") :]
    # One recommended, the rest listed as alternatives, and no repeats.
    alternatives = [line for line in offered.splitlines() if line.startswith("- ")]
    assert len(alternatives) == len(ANGLES) - 1
    assert len(set(alternatives)) == len(alternatives)


async def test_the_recommended_slot_is_the_one_the_judge_chose(settings):
    """Candidates are ranked by day and time before the judge sees them, so the
    index it returns has to be read against that same order."""
    model = PromptModel(respond=responder(choose="0"))

    async with open_session(settings, model=model, clock=FIXED_CLOCK) as session:
        await session.run_turn("when should I do the review?", thread_id="t1")

    listed = model.prompts_containing(SELECT_MARKER)[-1]
    first_offered = next(
        line for line in listed.splitlines() if line.startswith("0. ")
    )
    recommended = next(
        line
        for line in model.prompts_containing("Recommended:")[-1].splitlines()
        if line.startswith("Recommended:")
    )
    # The day and time the judge saw at index 0 are the ones recommended.
    day, time = first_offered.split()[1], first_offered.split()[3]
    assert day in recommended
    assert time in recommended
