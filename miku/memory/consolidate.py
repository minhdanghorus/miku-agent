"""The consolidation pass — the only thing in miku that resolves a stored fact.

`remember` writes. `recall` reads. This dozens-of-lines module is the third verb:
it tidies. Without it long-term memory only accumulates, and since the Phase 2
judge probe measured a single remembered habit flipping a scheduling choice 0 ->
1 on three runs of three, a fact left sitting beside its own correction does not
sit there harmlessly -- it competes.

Four properties are load-bearing:

  * **It is a plain async function, not a subgraph.** `read -> one call ->
    validate -> apply` is a straight line. `graph/fanout.py` earned a StateGraph
    because it has `Send` parallelism and a reducer; this has neither, and
    wrapping a straight line in a graph would be exactly the framework
    indirection this repo exists to avoid.
  * **The model proposes; this module disposes.** The model never touches the
    store and is never given a tool that could. Everything it suggests goes
    through `validate_plan` first, and validation is a pure function in
    `plan.py` with no credentials and no I/O.
  * **A dry run is the same code path.** `apply=False` reads, plans, and
    validates identically and then returns without writing. Two code paths would
    mean the preview could disagree with the real thing, and a preview that can
    lie is worse than no preview.
  * **It never runs inside a turn.** No tool exposes it, nothing triggers it on
    a threshold, and it carries its own request budget so a pass over all of
    memory can never spend what a conversation was allotted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage

from miku.memory.plan import (
    KIND_EXPIRE,
    KIND_MERGE,
    Dropped,
    Operation,
    Plan,
    validate_plan,
)
from miku.memory.store import (
    LiveFact,
    expire_fact,
    live_facts,
    merge_facts,
    open_store,
    supersede_fact,
)
from miku.ops.tracing import Tracer, new_turn_id
from miku.runtime.budget import Budget
from miku.runtime.config import Settings, load_settings
from miku.runtime.limits import model_semaphore
from miku.runtime.providers import ProviderError, get_provider, resolve_capabilities, resolve_model
from miku.tools.clock import Clock

# Not a new entry in ROLES. Consolidation is not latency-sensitive, so if `fast`
# ever stops being an alias for `main` on this provider, `main` is the one that
# should keep doing this work. `judge` was rejected for a different reason: it
# exists so evaluation is graded by a model other than the one being graded, and
# borrowing it for production work would make that claim ambiguous.
CONSOLIDATION_ROLE = "main"

INSTRUCTIONS = """\
You are tidying a list of remembered facts about one person. Propose only
resolutions you are confident about. Proposing nothing is a good answer when the
list is already clean.

Each fact is numbered and dated. Use the numbers, never the text, to refer to
them. Today is {today}.

Four kinds of resolution, and when each does NOT apply:

supersede - a later fact corrects an earlier one, so the earlier one stops being
  true. stale = [the outdated number], winner = the number that replaced it. The
  winner must be dated AFTER the stale one. Do NOT use this for two facts that
  can both be true at once: "meetings in the morning" and "deep work in the
  afternoon" do not conflict, they describe different parts of a day.

duplicate - two or more facts say the same thing in different words. stale = the
  restatements, winner = the one to keep. Do NOT use this when the facts differ
  in any detail that matters; if one is more specific, that is a merge.

merge - several facts are fragments of one preference and read better as one.
  stale = every fragment, fact = the single sentence they become, winner = null.
  Write the merged sentence so it loses nothing the fragments said. Do NOT use
  this to combine unrelated facts just because both are about scheduling.

expire - a fact was only ever true for a window, and the window has passed.
  stale = [the number], winner = null. Judge this from the fact's own wording and
  its date: "this week I am in Hanoi" written months ago has expired. Do NOT
  expire a standing preference, however old. "I prefer mornings" does not go
  stale by sitting there.

Facts:
{facts}
"""


@dataclass(frozen=True)
class Applied:
    """One operation that actually reached the store, resolved to keys."""

    operation: Operation
    stale_keys: list[str]
    winner_key: str | None = None
    # Set for a merge: the key of the newly written fact.
    written_key: str | None = None


@dataclass
class ConsolidationResult:
    """What one run did. The same shape whether or not anything was written."""

    run_id: str
    dry_run: bool
    live_before: int
    live_after: int
    # The numbered list the plan refers to, carried so a caller can render an
    # operation as the facts it touches rather than as bare indices. A dry-run
    # report that only printed numbers would be unreadable.
    facts: list[LiveFact] = field(default_factory=list)
    proposed: int = 0
    # Operations that passed validation: what a dry run would write, and what an
    # applying run tried to. `applied` is the subset that actually reached the
    # store, so the two differ exactly by `skipped`.
    applicable: list[Operation] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    # Operations that validated but whose facts had gone by the time we wrote:
    # a live session moved underneath the run. Ordinary, not an error.
    skipped: list[Operation] = field(default_factory=list)
    budget_exhausted: bool = False
    error: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def require_consolidation_model(settings: Settings):
    """Build the model for the pass, refusing loudly if it cannot do the job.

    Structured output is not optional here: the plan is nested lists of indices,
    and hand-parsing that from prose is where quiet bugs live. Whether a model
    can do it is already declared in the provider descriptor, so this reads the
    declaration rather than probing or guessing -- capability flags are declared,
    never inferred. Configuration errors are the one failure that fails loudly.
    """
    capabilities = resolve_capabilities(settings, CONSOLIDATION_ROLE)
    if capabilities.native_structured_output != "yes":
        model_id = resolve_model(settings, CONSOLIDATION_ROLE)
        raise ProviderError(
            f"Consolidation needs structured output, and {model_id!r} (role "
            f"{CONSOLIDATION_ROLE!r}) declares native_structured_output="
            f"{capabilities.native_structured_output!r}. Point MIKU_MODEL_MAIN at a "
            f"model that declares 'yes', or add the capability to the descriptor."
        )

    from miku.runtime.providers import chat_model

    return chat_model(settings, CONSOLIDATION_ROLE)


def render_facts(facts: list[LiveFact]) -> str:
    """The numbered list the model counts against. 1-based, oldest first."""
    return "\n".join(
        f"{index}. [{fact.created_at[:10]}] {fact.fact}" for index, fact in enumerate(facts, 1)
    )


def build_prompt(facts: list[LiveFact], today: str) -> str:
    return INSTRUCTIONS.format(today=today, facts=render_facts(facts))


async def _propose(model, settings: Settings, prompt: str) -> Plan:
    """One model call, returning a validated-by-pydantic plan."""
    planner = model.with_structured_output(Plan)
    async with model_semaphore(settings.max_concurrency):
        result = await planner.ainvoke([HumanMessage(content=prompt)])
    # A provider may hand back a dict rather than the model instance.
    return result if isinstance(result, Plan) else Plan.model_validate(result)


async def _apply_one(store, settings: Settings, operation: Operation, facts: list[LiveFact]):
    """Resolve indices to keys and write. Returns None if the facts had moved."""
    stale = [facts[index - 1] for index in operation.stale]
    winner = None if operation.winner is None else facts[operation.winner - 1]

    if operation.kind == KIND_MERGE:
        written = await merge_facts(store, settings, operation.fact, [f.key for f in stale])
        return Applied(
            operation=operation,
            stale_keys=[f.key for f in stale],
            written_key=written,
        )

    resolved: list[str] = []
    for fact in stale:
        if operation.kind == KIND_EXPIRE:
            ok = await expire_fact(store, settings, fact.key)
        else:
            ok = await supersede_fact(store, settings, fact.key, winner.key)  # type: ignore[union-attr]
        if ok:
            resolved.append(fact.key)

    if not resolved:
        return None
    return Applied(
        operation=operation,
        stale_keys=resolved,
        winner_key=None if winner is None else winner.key,
    )


async def consolidate(
    store,
    settings: Settings,
    model,
    *,
    clock: Clock,
    tracer: Tracer,
    apply: bool = False,
    budget: Budget | None = None,
) -> ConsolidationResult:
    """Run one pass. Writes only when `apply` is true.

    Never raises for anything the model does. A refused budget, a provider
    failure, or a plan full of nonsense all come back as a result the caller can
    print -- errors degrade, they do not crash.
    """
    run_id = tracer.turn_id
    facts = await live_facts(store, settings)
    result = ConsolidationResult(
        run_id=run_id,
        dry_run=not apply,
        live_before=len(facts),
        live_after=len(facts),
        facts=facts,
    )

    root = tracer.event("consolidate", node="read", facts=len(facts), dry_run=result.dry_run)
    if not facts:
        tracer.event("consolidate", node="done", parent=root, applied=0, reason="no facts")
        return result

    # One allowance per run, never a turn's. Injectable so the exhaustion path is
    # reachable in a test: a fresh budget with a floor of one request can never
    # refuse the single call this pass makes today, and an unasserted guard is
    # the one that stops working when chunking finally needs it.
    budget = budget or Budget(limit=settings.max_requests_per_consolidation)
    if not budget.spend():
        result.budget_exhausted = True
        tracer.event("budget", node="consolidate", parent=root, spent=budget.spent)
        return result

    try:
        plan = await _propose(model, settings, build_prompt(facts, clock.describe()))
    except Exception as error:  # noqa: BLE001 - a bad plan costs the run, not the process
        result.error = f"{type(error).__name__}: {error}"
        tracer.event("consolidate", node="plan", parent=root, ok=False, error=result.error)
        return result

    applicable, dropped = validate_plan(plan, facts)
    result.proposed = len(plan.operations)
    result.applicable = applicable
    result.dropped = dropped

    tracer.event(
        "consolidate",
        node="plan",
        parent=root,
        ok=True,
        proposed=result.proposed,
        applicable=len(applicable),
        dropped=len(dropped),
    )
    for drop in dropped:
        tracer.event(
            "consolidate",
            node="dropped",
            parent=root,
            kind_proposed=drop.operation.kind,
            stale=drop.operation.stale,
            winner=drop.operation.winner,
            reason=drop.reason,
        )

    if not apply:
        tracer.event(
            "consolidate", node="done", parent=root, applied=0, reason="dry run"
        )
        return result

    for operation in applicable:
        written = await _apply_one(store, settings, operation, facts)
        if written is None:
            result.skipped.append(operation)
            tracer.event(
                "consolidate",
                node="skipped",
                parent=root,
                kind_proposed=operation.kind,
                reason="facts moved since the read",
            )
            continue
        result.applied.append(written)
        tracer.event(
            "consolidate",
            node="applied",
            parent=root,
            kind_proposed=operation.kind,
            stale=len(written.stale_keys),
            wrote=bool(written.written_key),
        )

    result.live_after = len(await live_facts(store, settings))
    tracer.event(
        "consolidate",
        node="done",
        parent=root,
        applied=len(result.applied),
        live_before=result.live_before,
        live_after=result.live_after,
    )
    return result


@asynccontextmanager
async def open_consolidation(
    settings: Settings | None = None,
    model=None,
    clock: Clock | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> AsyncIterator[Callable[..., object]]:
    """The runtime entry point a gateway calls: opens the store, yields a runner.

    Deliberately narrower than `open_session` — no checkpointer, no graph, no
    tools. A consolidation run is not a conversation and needs none of them.
    """
    settings = settings or load_settings()
    settings.ensure_dirs()

    if model is None:
        model = require_consolidation_model(settings)

    provider = get_provider(settings)
    tracer = Tracer(
        traces_dir=settings.traces_dir,
        turn_id=new_turn_id(),
        secret_env_names=(provider.key_env,),
    )
    if on_event is not None:
        tracer.listener = lambda record: on_event(record["kind"], record)

    async with open_store(settings) as store:

        async def run(apply: bool = False) -> ConsolidationResult:
            return await consolidate(
                store,
                settings,
                model,
                clock=clock or Clock.real(),
                tracer=tracer,
                apply=apply,
            )

        yield run
