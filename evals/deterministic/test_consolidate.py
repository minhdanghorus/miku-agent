"""The consolidation pass, end to end, against a stub model.

Every assertion lands on a stored row, an applied operation, or a trace record.
None compares a merged fact to an expected string: a small model phrases a merge
differently every run, but the rows it produced do not.
"""

from __future__ import annotations

import pytest

from evals.helpers import PlanModel
from miku.memory.consolidate import (
    CONSOLIDATION_ROLE,
    consolidate,
    render_facts,
    require_consolidation_model,
)
from miku.memory.plan import SUPERSEDE_BACKWARDS, Operation, Plan
from miku.memory.store import (
    facts_namespace,
    live_facts,
    open_store,
    recall_facts,
    supersede_fact,
)
from miku.ops.traceview import read_records
from miku.ops.tracing import NullTracer, Tracer, new_turn_id
from miku.runtime.budget import Budget
from miku.runtime.config import load_settings
from miku.runtime.providers import ProviderError
from miku.tools.clock import Clock

CLOCK = Clock.fixed("2026-08-26")


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


async def write(store, settings, text: str, day: str) -> str:
    """A fact with an explicit date, so direction is never a race on the clock."""
    key = f"k-{day}-{abs(hash(text)) % 10_000}"
    await store.aput(
        facts_namespace(settings),
        key,
        {"fact": text, "created_at": f"{day}T09:00:00+00:00"},
    )
    return key


def plan(*operations: Operation) -> PlanModel:
    return PlanModel(plans=[Plan(operations=list(operations))])


async def run(store, settings, model, *, apply=True, budget=None, tracer=None):
    return await consolidate(
        store,
        settings,
        model,
        clock=CLOCK,
        tracer=tracer or NullTracer(),
        apply=apply,
        budget=budget,
    )


async def row(store, settings, key) -> dict:
    return (await store.aget(facts_namespace(settings), key)).value


# --- the four operations ---------------------------------------------------


async def test_supersede_retires_the_older_fact(settings):
    async with open_store(settings) as store:
        old = await write(store, settings, "I prefer meetings in the morning", "2026-06-01")
        new = await write(store, settings, "I prefer meetings in the afternoon", "2026-08-01")

        result = await run(
            store, settings, plan(Operation(kind="supersede", stale=[1], winner=2))
        )

        assert len(result.applied) == 1
        assert result.applied[0].stale_keys == [old]
        assert result.applied[0].winner_key == new
        assert (await row(store, settings, old))["superseded_by"] == new
        assert "superseded_at" not in await row(store, settings, new)
        assert await recall_facts(store, settings) == ["I prefer meetings in the afternoon"]


async def test_duplicate_keeps_the_nominated_winner(settings):
    async with open_store(settings) as store:
        keep = await write(store, settings, "no meetings before 9am", "2026-05-01")
        await write(store, settings, "please, nothing before 9 in the morning", "2026-06-01")
        await write(store, settings, "I never take a 9am", "2026-07-01")

        result = await run(
            store, settings, plan(Operation(kind="duplicate", stale=[2, 3], winner=1))
        )

        assert len(result.applied[0].stale_keys) == 2
        assert await recall_facts(store, settings) == ["no meetings before 9am"]
        assert (await row(store, settings, keep)).get("superseded_at") is None


async def test_merge_writes_a_new_fact_and_links_its_sources(settings):
    async with open_store(settings) as store:
        a = await write(store, settings, "I like mornings", "2026-05-01")
        b = await write(store, settings, "nothing before 9am", "2026-06-01")
        c = await write(store, settings, "standup is at 9:30", "2026-07-01")

        result = await run(
            store,
            settings,
            plan(
                Operation(
                    kind="merge", stale=[1, 2, 3], fact="Mornings from 9:30, never before 9"
                )
            ),
        )

        written = result.applied[0].written_key
        assert written is not None
        assert (await row(store, settings, written))["derived_from"] == [a, b, c]
        for source in (a, b, c):
            assert (await row(store, settings, source))["superseded_by"] == written
        assert len(await recall_facts(store, settings)) == 1


async def test_expire_retires_a_time_bound_fact_with_no_successor(settings):
    async with open_store(settings) as store:
        key = await write(store, settings, "this week I am in Hanoi", "2026-03-01")
        await write(store, settings, "I prefer mornings", "2026-03-02")

        result = await run(store, settings, plan(Operation(kind="expire", stale=[1])))

        assert result.applied[0].winner_key is None
        stored = await row(store, settings, key)
        assert stored["superseded_at"]
        assert "superseded_by" not in stored
        assert await recall_facts(store, settings) == ["I prefer mornings"]


async def test_nothing_is_ever_deleted(settings):
    async with open_store(settings) as store:
        await write(store, settings, "one", "2026-01-01")
        await write(store, settings, "two", "2026-02-01")
        before = len(await store.asearch(facts_namespace(settings), limit=100))

        await run(store, settings, plan(Operation(kind="merge", stale=[1, 2], fact="both")))

        assert len(await store.asearch(facts_namespace(settings), limit=100)) >= before


# --- dry run ---------------------------------------------------------------


async def test_a_dry_run_reports_without_writing(settings):
    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")

        result = await run(
            store,
            settings,
            plan(Operation(kind="supersede", stale=[1], winner=2)),
            apply=False,
        )

        assert result.dry_run is True
        assert result.proposed == 1
        assert result.applied == []
        assert await recall_facts(store, settings) == ["old", "new"]


async def test_a_dry_run_and_a_real_run_agree(settings):
    """One code path, so the preview cannot disagree with the thing it previews."""
    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        proposal = Operation(kind="supersede", stale=[1], winner=2)

        preview = await run(store, settings, plan(proposal), apply=False)
        applied = await run(store, settings, plan(proposal), apply=True)

        assert preview.proposed == applied.proposed
        assert [drop.reason for drop in preview.dropped] == [
            drop.reason for drop in applied.dropped
        ]
        assert [operation.kind for operation in [a.operation for a in applied.applied]] == [
            "supersede"
        ]


# --- degrading rather than crashing ----------------------------------------


async def test_an_empty_plan_writes_nothing(settings):
    async with open_store(settings) as store:
        await write(store, settings, "clean", "2026-01-01")

        result = await run(store, settings, PlanModel(plans=[Plan()]))

        assert result.changed is False
        assert await recall_facts(store, settings) == ["clean"]


async def test_no_facts_means_no_model_call(settings):
    async with open_store(settings) as store:
        model = PlanModel(plans=[Plan()])
        result = await run(store, settings, model)

        assert model.invocations == 0
        assert result.live_before == 0


async def test_a_failing_provider_costs_the_run_not_the_process(settings):
    async with open_store(settings) as store:
        await write(store, settings, "untouched", "2026-01-01")

        result = await run(store, settings, PlanModel(error=RuntimeError("502 upstream")))

        assert "502 upstream" in result.error
        assert result.applied == []
        assert await recall_facts(store, settings) == ["untouched"]


async def test_an_exhausted_budget_reports_instead_of_raising(settings):
    async with open_store(settings) as store:
        await write(store, settings, "untouched", "2026-01-01")
        model = PlanModel(plans=[Plan()])

        result = await run(store, settings, model, budget=Budget(limit=1, spent=1))

        assert result.budget_exhausted is True
        assert model.invocations == 0
        assert await recall_facts(store, settings) == ["untouched"]


async def test_a_backwards_supersession_is_dropped_before_it_reaches_the_store(settings):
    """The direction guard, asserted where it matters: on the rows."""
    async with open_store(settings) as store:
        old = await write(store, settings, "I prefer mornings", "2026-01-01")
        new = await write(store, settings, "I prefer afternoons", "2026-08-01")

        result = await run(
            store, settings, plan(Operation(kind="supersede", stale=[2], winner=1))
        )

        assert [drop.reason for drop in result.dropped] == [SUPERSEDE_BACKWARDS]
        assert result.applied == []
        for key in (old, new):
            assert (await row(store, settings, key)).get("superseded_at") is None


async def test_a_partly_invalid_plan_still_applies_the_valid_half(settings):
    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")

        result = await run(
            store,
            settings,
            plan(
                Operation(kind="expire", stale=[99]),
                Operation(kind="supersede", stale=[1], winner=2),
            ),
        )

        assert len(result.applied) == 1
        assert len(result.dropped) == 1
        assert await recall_facts(store, settings) == ["new"]


# --- the race with a live session ------------------------------------------


class MovesTheStore:
    """A model that resolves a fact as a side effect of being asked.

    Stands in for a live session writing between the pass's read and its write.
    The operation stays valid but its target is gone, which must be skipped
    rather than raised.
    """

    def __init__(self, store, settings, key, proposal):
        self.store = store
        self.settings = settings
        self.key = key
        self.proposal = proposal
        self.schemas: list = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return self

    async def ainvoke(self, _messages, **_kwargs):
        await supersede_fact(self.store, self.settings, self.key, "someone-else")
        return self.proposal


async def test_an_operation_whose_fact_moved_is_skipped(settings):
    async with open_store(settings) as store:
        vanishing = await write(store, settings, "about to be resolved", "2026-01-01")
        await write(store, settings, "the newer one", "2026-02-01")

        model = MovesTheStore(
            store,
            settings,
            vanishing,
            Plan(operations=[Operation(kind="supersede", stale=[1], winner=2)]),
        )
        result = await run(store, settings, model)

        assert result.applied == []
        assert len(result.skipped) == 1


# --- idempotence -----------------------------------------------------------


async def test_a_second_run_changes_nothing(settings):
    """The stub repeats its last plan forever, so the second run is offered the
    same resolution and must still write nothing."""
    async with open_store(settings) as store:
        old = await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        model = plan(Operation(kind="supersede", stale=[1], winner=2))

        first = await run(store, settings, model)
        stamp = (await row(store, settings, old))["superseded_at"]
        second = await run(store, settings, model)

        assert len(first.applied) == 1
        assert second.applied == []
        assert (await row(store, settings, old))["superseded_at"] == stamp


async def test_only_live_facts_reach_the_model(settings):
    async with open_store(settings) as store:
        old = await write(store, settings, "SUPERSEDED-TEXT", "2026-01-01")
        new = await write(store, settings, "LIVE-TEXT", "2026-02-01")
        await supersede_fact(store, settings, old, new)

        model = PlanModel(plans=[Plan()])
        await run(store, settings, model)

        assert "LIVE-TEXT" in model.last_prompt
        assert "SUPERSEDED-TEXT" not in model.last_prompt


async def test_the_second_run_sees_a_shorter_list(settings):
    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        model = plan(Operation(kind="supersede", stale=[1], winner=2))

        first = await run(store, settings, model)
        second = await run(store, settings, model)

        assert first.live_before == 2
        assert first.live_after == 1
        assert second.live_before == 1


# --- the prompt and the capability gate ------------------------------------


async def test_facts_are_numbered_from_one_with_their_dates(settings):
    async with open_store(settings) as store:
        await write(store, settings, "first", "2026-01-01")
        await write(store, settings, "second", "2026-02-01")

        rendered = render_facts(await live_facts(store, settings))

        assert rendered.splitlines() == [
            "1. [2026-01-01] first",
            "2. [2026-02-01] second",
        ]


async def test_the_prompt_says_when_each_operation_does_not_apply(settings):
    """Tool and operation boundaries live in prose. Measured, not stylistic."""
    async with open_store(settings) as store:
        await write(store, settings, "anything", "2026-01-01")
        model = PlanModel(plans=[Plan()])
        await run(store, settings, model)

        # One "Do NOT" per operation. Counted on the short form because the
        # prompt is wrapped, and "Do NOT use this" can straddle a line break.
        assert model.last_prompt.count("Do NOT") == 4
        assert "does not go" in model.last_prompt  # a standing preference never expires


async def test_the_pass_asks_for_the_plan_schema(settings):
    async with open_store(settings) as store:
        await write(store, settings, "anything", "2026-01-01")
        model = PlanModel(plans=[Plan()])
        await run(store, settings, model)

        assert model.schemas == [Plan]


def test_a_model_without_structured_output_is_refused_loudly():
    """Declared, never inferred - and a configuration error fails at startup."""
    settings = load_settings(model_main="qwen/qwen3-5-27b")
    with pytest.raises(ProviderError) as error:
        require_consolidation_model(settings)
    assert "qwen/qwen3-5-27b" in str(error.value)
    assert CONSOLIDATION_ROLE in str(error.value)


def test_an_undeclared_model_counts_as_unsupported():
    """An unprobed capability is 'unknown', and unknown is not 'yes'."""
    settings = load_settings(model_main="some/unprobed-model")
    with pytest.raises(ProviderError):
        require_consolidation_model(settings)


# --- tracing ---------------------------------------------------------------


async def test_a_run_reads_back_under_one_run_id(settings):
    settings.ensure_dirs()
    run_id = new_turn_id()
    tracer = Tracer(traces_dir=settings.traces_dir, turn_id=run_id)

    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        await run(
            store,
            settings,
            plan(Operation(kind="supersede", stale=[1], winner=2)),
            tracer=tracer,
        )

    records = read_records(tracer.path, run_id)
    assert records, "the run wrote no trace"
    assert {record["kind"] for record in records} == {"consolidate"}
    nodes = [record["node"] for record in records]
    assert nodes[0] == "read"
    assert "applied" in nodes
    assert nodes[-1] == "done"


async def test_a_dropped_operation_records_its_reason(settings):
    settings.ensure_dirs()
    run_id = new_turn_id()
    tracer = Tracer(traces_dir=settings.traces_dir, turn_id=run_id)

    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        await run(
            store,
            settings,
            plan(Operation(kind="supersede", stale=[2], winner=1)),
            tracer=tracer,
        )

    dropped = [
        record for record in read_records(tracer.path, run_id) if record["node"] == "dropped"
    ]
    assert [record["reason"] for record in dropped] == [SUPERSEDE_BACKWARDS]


async def test_every_event_hangs_under_the_run_root(settings):
    settings.ensure_dirs()
    run_id = new_turn_id()
    tracer = Tracer(traces_dir=settings.traces_dir, turn_id=run_id)

    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")
        await run(
            store,
            settings,
            plan(Operation(kind="supersede", stale=[1], winner=2)),
            tracer=tracer,
        )

    records = read_records(tracer.path, run_id)
    root = records[0]
    assert root["parent"] is None
    assert all(record["parent"] == root["span"] for record in records[1:])


async def test_a_broken_trace_sink_does_not_stop_the_pass(settings, tmp_path):
    """Observability is not a correctness dependency."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("this is a file", encoding="utf-8")
    tracer = Tracer(traces_dir=blocked, turn_id=new_turn_id())

    async with open_store(settings) as store:
        await write(store, settings, "old", "2026-01-01")
        await write(store, settings, "new", "2026-02-01")

        result = await run(
            store,
            settings,
            plan(Operation(kind="supersede", stale=[1], winner=2)),
            tracer=tracer,
        )

        assert len(result.applied) == 1
        assert await recall_facts(store, settings) == ["new"]
