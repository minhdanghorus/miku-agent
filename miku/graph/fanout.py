"""Best-of-N, as a subgraph.

    plan_angles ──Send x N──▶ generate ──▶ select_best ──▶ format ──▶ END
                                (concurrent)

This is the map-reduce shape: `plan_angles` maps the work across N branches with
`Send`, each `generate` proposes one candidate, the `candidates` field reduces
them with `operator.add`, and `select_best` picks one.

Three things are worth reading closely.

**Diversity is structural.** Five calls to one prompt do not give five different
answers, and `chat_model` builds every model at `temperature=0`, so sampling
diversity does not exist here at all. Each branch is instead given a different
*angle* — a different way to be right. Because the angles are a list in code,
an evaluator can assert five branches got five distinct angles without a model
being involved.

**Nothing outside knows this is a graph.** The last node renders text, so from
the tools node's side this is a tool like any other. That is what keeps the main
loop at three nodes.

**It spends the caller's budget, not its own.** A local cap here would compose
badly with the loop's iteration cap: both would report themselves satisfied
while a turn spent forty-eight requests.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from miku.graph.nodes import Deps, TurnContext
from miku.runtime.limits import model_semaphore
from miku.tools.calendar_store import Event, events_between


@dataclass(frozen=True)
class Angle:
    """One way to be right about when to schedule something."""

    name: str
    guidance: str


# The default angles. Order is stable so a clamped fan-out is reproducible.
# Adding an angle widens what a fan-out can actually explore; raising
# MIKU_FANOUT_BRANCHES alone does not, which is why the branch count clamps to
# the number of angles rather than repeating one.
ANGLES: tuple[Angle, ...] = (
    Angle("early morning", "the earliest slot in the window that still respects known habits"),
    Angle("after lunch", "early afternoon, once the day is already underway"),
    Angle("quietest day", "the day in the window with the fewest existing events"),
    Angle("beside existing work", "adjacent to something already booked, to batch the day"),
    Angle("late in the window", "toward the end of the window, leaving room to prepare"),
)

# What a branch must reply with. A strict single line, parsed strictly: anything
# else is a failed branch rather than a guess. Structured output is deliberately
# not used -- one of the three chat models cannot do it natively, and the stub
# model that makes this whole subgraph assertable without credentials would have
# to grow a second protocol.
CANDIDATE_LINE = re.compile(
    r"(?P<day>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<time>\d{1,2}:\d{2})\s*\|\s*(?P<why>.+)"
)

BRANCH_PROMPT = """You are proposing ONE candidate time slot, and only one.

Today is {today}.
The user wants to schedule: {task}
Search only within {start_day} to {end_day} inclusive.

Your angle for this proposal: {angle_name} -- {angle_guidance}.
Propose the slot that best fits THIS angle. Another proposer covers the others.

{facts_block}{events_block}
Reply with exactly one line and nothing else, in this format:
YYYY-MM-DD | HH:MM | one short sentence on why this slot fits the angle
"""

SELECT_PROMPT = """Choose the single best time slot for: {task}

Today is {today}.
{facts_block}
Candidates:
{candidate_block}

Pick the one that best fits the user's stated habits and the shape of their
week. Do not invent a new slot and do not recompute any date -- choose from the
list. Reply with the number of your choice and nothing else.
"""

JUDGE_ROLE = "You choose between options. Answer with one number and nothing else."

NO_CANDIDATES = (
    "I could not work out any candidate slots for that. Narrow the window or "
    "name a day and I will try again."
)


class FanoutState(TypedDict, total=False):
    """The subgraph's own state. Separate from the turn's, deliberately: nothing
    in here needs to survive the tool call that started it."""

    # The request, resolved to absolute dates before it ever gets here.
    task: str
    start_day: str
    end_day: str
    today: str
    facts: list[str]
    events: list[str]

    # Set by plan_angles: the angles actually used, after clamping.
    angles: list[str]

    # One branch's assignment, delivered by Send. Each branch sees only its own.
    angle_name: str
    angle_guidance: str
    branch: int

    # The map-reduce seam. Every branch appends; nothing replaces.
    candidates: Annotated[list[dict], operator.add]

    # The candidates in the order they were judged in. A separate field on
    # purpose: writing back to `candidates` would go through the reducer above
    # and *append* a second copy, leaving `chosen` indexing a list that no
    # longer matches. Measured, not theorised -- that was the first bug a live
    # run found.
    ranked: list[dict]

    chosen: int
    answer: str

    # The trace span this subgraph hangs its own events under, handed in by the
    # tool that started it.
    span: str


def _facts_block(facts: list[str]) -> str:
    if not facts:
        return ""
    listed = "\n".join(f"- {fact}" for fact in facts)
    return f"What you know about the user:\n{listed}\n\n"


def _events_block(events: list[str]) -> str:
    if not events:
        return "Their calendar is empty in that window.\n\n"
    listed = "\n".join(f"- {event}" for event in events)
    return f"Already on their calendar in that window:\n{listed}\n\n"


def _describe(candidate: dict) -> str:
    return f"{candidate['day']} at {candidate['start_time']} ({candidate['angle']})"


def resolve_angles(requested: list[str] | None, limit: int) -> list[Angle]:
    """The angles a fan-out will actually use.

    A caller-supplied list wins over the defaults, which is what lets the model
    adapt ("I can only do afternoons") without the baseline depending on it.
    Duplicates are dropped: two branches with the same angle is one branch's
    worth of diversity at two branches' cost.
    """
    if requested:
        seen: dict[str, Angle] = {}
        for name in requested:
            text = str(name).strip()
            if text and text.lower() not in seen:
                seen[text.lower()] = Angle(text, f"focus on {text}")
        pool = list(seen.values())
    else:
        pool = list(ANGLES)
    return pool[: max(0, limit)]


def make_plan_angles_node(deps: Deps):
    """Decide how wide to go, and on what.

    Three things clamp the width: the configured branch count, how many distinct
    angles exist, and how much budget is left after reserving one request for
    the selection step. Whichever is smallest wins, and a clamp is traced --
    silently doing less work than asked reads as success when it is not.
    """

    async def plan_angles(state: FanoutState, runtime: Runtime[TurnContext]):
        turn = runtime.context
        requested = state.get("angles") or None

        configured = deps.settings.fanout_branches
        # Reserve one request for select_best, so a fan-out cannot spend the
        # whole budget generating candidates nobody gets to choose between.
        affordable = max(0, turn.budget.remaining() - 1)
        angles = resolve_angles(requested, min(configured, affordable))

        events = await events_between(
            deps.settings.db_path, state["start_day"], state["end_day"]
        )

        span = turn.tracer.event(
            "node",
            node="plan_angles",
            branches=len(angles),
            configured=configured,
            affordable=affordable,
            angles=[angle.name for angle in angles],
        )
        if len(angles) < configured:
            turn.tracer.event(
                "clamp",
                node="plan_angles",
                parent=span,
                asked=configured,
                using=len(angles),
                reason="budget" if affordable < configured else "angles",
            )

        return {
            "angles": [angle.name for angle in angles],
            "events": [event.describe() for event in events],
            "span": span,
        }

    return plan_angles


def fan_out(state: FanoutState):
    """The map step: one `Send` per angle, each branch seeing only its own.

    Routing to `select_best` with no branches is what makes an exhausted budget
    a degraded answer instead of an exception.
    """
    angles = state.get("angles") or []
    if not angles:
        return "select_best"

    pool = {angle.name: angle for angle in ANGLES}
    sends = []
    for index, name in enumerate(angles):
        angle = pool.get(name, Angle(name, f"focus on {name}"))
        sends.append(
            Send(
                "generate",
                {
                    "task": state["task"],
                    "start_day": state["start_day"],
                    "end_day": state["end_day"],
                    "today": state["today"],
                    "facts": state.get("facts", []),
                    "events": state.get("events", []),
                    "angle_name": angle.name,
                    "angle_guidance": angle.guidance,
                    "branch": index,
                    "span": state.get("span"),
                },
            )
        )
    return sends


def make_generate_node(deps: Deps):
    """One branch: one model call, one candidate.

    Runs on the `fast` role -- the cheap seam for work that is repeated N times.
    A branch that fails, or answers in the wrong shape, contributes nothing and
    does not take the turn down with it.
    """

    async def generate(state: FanoutState, runtime: Runtime[TurnContext]):
        turn = runtime.context
        branch = state.get("branch", 0)
        tracer = turn.tracer.child(state.get("span") or "", branch=branch)

        if not turn.budget.spend():
            tracer.event("budget", node="generate", spent=turn.budget.spent)
            return {"candidates": []}

        prompt = BRANCH_PROMPT.format(
            today=state["today"],
            task=state["task"],
            start_day=state["start_day"],
            end_day=state["end_day"],
            angle_name=state["angle_name"],
            angle_guidance=state["angle_guidance"],
            facts_block=_facts_block(state.get("facts", [])),
            events_block=_events_block(state.get("events", [])),
        )

        try:
            async with model_semaphore(deps.settings.max_concurrency):
                reply = await deps.fast_model.ainvoke([HumanMessage(content=prompt)])
            candidate = _parse_candidate(str(reply.content), state, branch)
        except Exception as error:  # noqa: BLE001 - a lost branch is not a lost turn
            tracer.event("node", node="generate", ok=False, error=str(error)[:200])
            return {"candidates": []}

        if candidate is None:
            tracer.event("node", node="generate", ok=False, reason="unparseable")
            return {"candidates": []}

        tracer.event(
            "node",
            node="generate",
            ok=True,
            angle=candidate["angle"],
            day=candidate["day"],
            start_time=candidate["start_time"],
        )
        return {"candidates": [candidate]}

    return generate


def _parse_candidate(text: str, state: FanoutState, branch: int) -> dict | None:
    """One line, strictly. Anything else is a failed branch, not a guess."""
    match = CANDIDATE_LINE.search(text)
    if match is None:
        return None

    day, time, why = match.group("day"), match.group("time"), match.group("why")
    try:
        day = date.fromisoformat(day).isoformat()
        hour, minute = (int(part) for part in time.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
    except ValueError:
        return None

    # A slot outside the window it was asked for is not a usable answer.
    if not state["start_day"] <= day <= state["end_day"]:
        return None

    return {
        "branch": branch,
        "angle": state.get("angle_name", ""),
        "day": day,
        "start_time": f"{hour:02d}:{minute:02d}",
        "why": why.strip()[:200],
    }


def make_select_best_node(deps: Deps):
    """The reduce step: LLM-as-judge, inside the product loop.

    The same idea the eval pillar uses, one layer down. It chooses among the
    candidates and never computes a date of its own -- the default judge model
    was measured in Phase 1 getting weekday arithmetic wrong, so the arithmetic
    stays where it was already correct.
    """

    async def select_best(state: FanoutState, runtime: Runtime[TurnContext]):
        turn = runtime.context
        candidates = state.get("candidates", [])
        parent = state.get("span")

        if not candidates:
            turn.tracer.event("node", node="select_best", parent=parent, candidates=0)
            return {"ranked": [], "chosen": -1}

        if len(candidates) == 1:
            # Nothing to judge. Spending a request to confirm the only option
            # would be a request spent on arithmetic we already have.
            turn.tracer.event(
                "node", node="select_best", parent=parent, candidates=1, chosen=0, judged=False
            )
            return {"ranked": list(candidates), "chosen": 0}

        ordered = sorted(candidates, key=lambda c: (c["day"], c["start_time"]))
        listed = "\n".join(
            f"{index}. {_describe(candidate)} -- {candidate['why']}"
            for index, candidate in enumerate(ordered)
        )
        prompt = SELECT_PROMPT.format(
            task=state["task"],
            today=state["today"],
            facts_block=_facts_block(state.get("facts", [])),
            candidate_block=listed,
        )

        chosen = 0
        judged = False
        if turn.budget.spend():
            try:
                async with model_semaphore(deps.settings.max_concurrency):
                    reply = await deps.select_model.ainvoke(
                        [SystemMessage(content=JUDGE_ROLE), HumanMessage(content=prompt)]
                    )
                chosen = _parse_choice(str(reply.content), len(ordered))
                judged = True
            except Exception as error:  # noqa: BLE001 - fall back to the earliest slot
                turn.tracer.event(
                    "node", node="select_best", parent=parent, ok=False, error=str(error)[:200]
                )

        turn.tracer.event(
            "node",
            node="select_best",
            parent=parent,
            candidates=len(ordered),
            chosen=chosen,
            judged=judged,
        )
        return {"ranked": ordered, "chosen": chosen}

    return select_best


def _parse_choice(text: str, count: int) -> int:
    """The first in-range integer in the reply, or the earliest slot.

    Falling back rather than failing: a judge that answers badly should cost the
    user a slightly worse slot, not their answer.
    """
    for token in re.findall(r"\d+", text):
        value = int(token)
        if 0 <= value < count:
            return value
    return 0


def make_format_node(deps: Deps):
    """Render the result as text, so the tools node never learns what ran."""

    async def format_answer(state: FanoutState, runtime: Runtime[TurnContext]):
        # `ranked`, never `candidates`: the judge chose an index into the ranked
        # order, and `candidates` is the unordered pile the branches appended to.
        candidates = state.get("ranked", [])
        chosen = state.get("chosen", -1)

        if not candidates or not 0 <= chosen < len(candidates):
            runtime.context.tracer.event(
                "node", node="format", parent=state.get("span"), answer="none"
            )
            return {"answer": NO_CANDIDATES}

        pick = candidates[chosen]
        lines = [f"Recommended: {pick['day']} at {pick['start_time']} -- {pick['why']}"]
        others = [c for index, c in enumerate(candidates) if index != chosen]
        if others:
            lines.append("Also considered:")
            lines.extend(f"- {_describe(candidate)}" for candidate in others)
        lines.append("Nothing has been booked yet. Say the word and I will add it.")

        runtime.context.tracer.event(
            "node", node="format", parent=state.get("span"), considered=len(candidates)
        )
        return {"answer": "\n".join(lines)}

    return format_answer


def build_fanout_graph(deps: Deps):
    """Compile the best-of-N subgraph. No checkpointer: it lives and dies inside
    one tool call, so there is no thread for it to resume on."""
    builder = StateGraph(FanoutState, context_schema=TurnContext)

    builder.add_node("plan_angles", make_plan_angles_node(deps))
    builder.add_node("generate", make_generate_node(deps))
    builder.add_node("select_best", make_select_best_node(deps))
    builder.add_node("format", make_format_node(deps))

    builder.add_edge(START, "plan_angles")
    builder.add_conditional_edges("plan_angles", fan_out, ["generate", "select_best"])
    builder.add_edge("generate", "select_best")
    builder.add_edge("select_best", "format")
    builder.add_edge("format", END)

    return builder.compile()


__all__ = [
    "ANGLES",
    "NO_CANDIDATES",
    "Angle",
    "Event",
    "FanoutState",
    "build_fanout_graph",
    "resolve_angles",
]
