"""`propose_slots` — the tool that fans out.

This is where delegation is decided, and the decision is not made here: it is
made by the model choosing this tool over `create_event`. There is no router
node and no classifier. Choosing a tool is already the model's decision surface,
so a separate mechanism for "should I fan out" would be reinventing it.

A live spike (recorded in the exploration doc) measured that boundary holding:
with these four tools bound, the model made the same choice on all three runs of
all ten prompts, and the one genuine misroute was fixed by the sentence in this
tool's description saying when *not* to use it. That sentence is load-bearing.

Like the other scheduling tools, this one takes absolute dates. Resolving "this
week" into a date range is the model's job, and it is told today's date.
"""

from __future__ import annotations

from datetime import date

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool

from miku.graph.fanout import NO_CANDIDATES, build_fanout_graph
from miku.graph.nodes import TURN_CONTEXT_KEY, Deps
from miku.memory.store import recall_facts

DESCRIPTION = (
    "Find the best time for something when the user has NOT said when. Proposes "
    "several candidate slots in parallel, weighs them against the user's habits "
    "and existing calendar, and recommends one. Requires an absolute ISO start "
    "and end date for the window to search. Proposes only -- it never books. Do "
    "NOT use this when the user already said which day and time they want: use "
    "create_event for that."
)

MAX_WINDOW_DAYS = 60


def _validate_window(start_day: str, end_day: str) -> tuple[str, str]:
    """The two fields we refuse to guess at, checked the way scheduling.py does.

    A window that runs backwards is a model mistake worth reporting rather than
    quietly reversing -- the reversal might not be what was meant.
    """
    try:
        start = date.fromisoformat(start_day)
    except ValueError:
        raise ValueError(
            f"start_day must be an absolute ISO date like 2026-08-25, got {start_day!r}"
        ) from None
    try:
        end = date.fromisoformat(end_day)
    except ValueError:
        raise ValueError(
            f"end_day must be an absolute ISO date like 2026-08-31, got {end_day!r}"
        ) from None

    if end < start:
        raise ValueError(f"end_day {end_day!r} is before start_day {start_day!r}")
    span = (end - start).days + 1
    if span > MAX_WINDOW_DAYS:
        raise ValueError(
            f"window of {span} days is too wide to search; ask for {MAX_WINDOW_DAYS} or fewer"
        )
    return start.isoformat(), end.isoformat()


def build_proposal_tools(deps: Deps) -> list[BaseTool]:
    """The proposal tool, bound to this session's subgraph.

    The graph is compiled once per session rather than per call: it holds no
    per-turn state, which is exactly why the turn's tracer and budget arrive in
    the invocation config instead.
    """
    graph = build_fanout_graph(deps)

    async def propose_slots(
        task: str,
        start_day: str,
        end_day: str,
        config: RunnableConfig,
        angles: list[str] | None = None,
    ) -> str:
        """Propose candidate time slots for something and recommend one.

        Args:
            task: What needs scheduling, e.g. "1-hour design review".
            start_day: First day of the window to search, absolute ISO date.
            end_day: Last day of the window to search, absolute ISO date.
            angles: Optional. Different aspects to explore, one per candidate,
                e.g. ["mornings only", "after my standup"]. Leave this out to
                use the default set.
        """
        start, end = _validate_window(start_day, end_day)
        if not task.strip():
            raise ValueError("task cannot be empty")

        turn = (config.get("configurable") or {}).get(TURN_CONTEXT_KEY)
        if turn is None:
            # Only reachable if a caller invokes the tool outside a turn. Say so
            # rather than fanning out with no budget to spend and no trace.
            raise ValueError("propose_slots needs a turn context; call it from the graph")

        facts = await recall_facts(deps.store, deps.settings)
        result = await graph.ainvoke(
            {
                "task": task.strip(),
                "start_day": start,
                "end_day": end,
                "today": deps.clock.describe(),
                "facts": facts,
                "angles": list(angles) if angles else [],
                "span": turn.tracer.parent or "",
            },
            context=turn,
        )
        return result.get("answer") or NO_CANDIDATES

    return [
        StructuredTool.from_function(
            coroutine=propose_slots,
            name="propose_slots",
            description=DESCRIPTION,
        )
    ]
