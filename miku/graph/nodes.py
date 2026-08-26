"""The three nodes.

  assemble  — build the system prompt from persona + recalled facts + today
  agent     — one model call, with the tools bound
  tools     — run every requested call, turning failures into results

Each node takes state and returns only what it changes. The wiring between them
lives in build.py, so the control flow is readable in one place.

Two things reach a node from outside its state:

  * `Deps` — everything that lives as long as the session: the store, the tool
    list, the model, the clock.
  * `TurnContext` — everything that lives as long as *one turn*: its tracer and
    its request budget. These arrive as LangGraph's `Runtime.context` rather
    than in `Deps`, because a session serves many turns and will serve them
    concurrently. Sharing one budget across turns is a bug waiting for the web
    gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.memory.store import recall_facts
from miku.ops.tracing import Tracer
from miku.runtime.budget import Budget
from miku.runtime.config import Settings
from miku.runtime.limits import model_semaphore
from miku.tools.clock import Clock
from miku.tools.registry import UnknownToolError, lookup

SOUL_PATH = Path(__file__).resolve().parent.parent / "SOUL.md"

CAP_REPLY = (
    "I hit my step limit for this turn, so I stopped before finishing. "
    "Tell me what to focus on and I will pick it back up."
)

BUDGET_REPLY = (
    "I used up the request budget for this turn before finishing. "
    "Ask me again with a narrower question and I will get further."
)

# The key the per-turn context travels under when it crosses into a tool. A tool
# is a LangChain runnable, not a graph node, so it has no Runtime of its own.
TURN_CONTEXT_KEY = "miku_turn"


@dataclass
class Deps:
    """Everything the nodes need for the life of the session, passed in rather
    than reached for."""

    settings: Settings
    store: AsyncSqliteStore
    tools: list[BaseTool]
    model: object  # BaseChatModel; typed loosely so stubs work in evals
    tracer: Tracer
    clock: Clock
    # The other two roles the graph calls. Separate fields rather than a lookup
    # so a stub can be substituted for all three at once, and so the fan-out's
    # use of the cheap role is visible here rather than buried in a call site.
    fast_model: object | None = None
    select_model: object | None = None

    def __post_init__(self) -> None:
        # One injected model stands in for every role: that is what lets the
        # eval suite drive the whole graph, fan-out included, with one stub.
        self.fast_model = self.fast_model or self.model
        self.select_model = self.select_model or self.model


@dataclass
class TurnContext:
    """Everything scoped to one turn. Delivered as `Runtime.context`."""

    tracer: Tracer
    budget: Budget

    def child_context(self, parent: str, branch: int | None = None) -> TurnContext:
        """A view of this turn anchored under `parent`.

        The tracer is cloned so parentage is explicit; the budget is *not*, so
        that a delegated subgraph spends from the same allowance as the turn
        that called it.
        """
        return TurnContext(tracer=self.tracer.child(parent, branch=branch), budget=self.budget)


def load_persona() -> str:
    return SOUL_PATH.read_text(encoding="utf-8").strip()


def build_system_prompt(persona: str, facts: list[str], today: str) -> str:
    """Persona, then what we know, then what day it is.

    Today's date is stated because the tools refuse relative dates — the model
    has to do that resolution, and it cannot without knowing the date.
    """
    parts = [persona, f"Today is {today}."]
    if facts:
        remembered = "\n".join(f"- {fact}" for fact in facts)
        parts.append(f"What you remember about the user:\n{remembered}")
    parts.append(
        "When scheduling, always pass an absolute ISO date (YYYY-MM-DD) and a "
        "24-hour HH:MM time to the tools. Resolve weekday names and words like "
        "'tomorrow' yourself, against today's date."
    )
    return "\n\n".join(parts)


def make_assemble_node(deps: Deps):
    """Working memory: persona + recalled facts + today, into state."""

    async def assemble(state, runtime: Runtime[TurnContext]):
        turn = runtime.context
        facts = await recall_facts(deps.store, deps.settings)
        today = state.get("today") or deps.clock.describe()

        span = turn.tracer.event("node", node="assemble", facts=len(facts), today=today)

        return {
            "facts": facts,
            "today": today,
            "system": build_system_prompt(load_persona(), facts, today),
            # Reset per turn. State survives across turns on a thread, so
            # carrying the count forward would starve later turns of iterations.
            "iterations": 0,
            # The root of this turn's trace tree.
            "span": span,
        }

    return assemble


def make_agent_node(deps: Deps):
    """One model call, subject to two independent limits.

    The iteration cap bounds how many times this node runs in a turn. The
    request budget bounds how many model calls the whole turn makes, this node's
    and any delegated subgraph's together. Both end the turn with a reply rather
    than an exception.
    """
    bound = deps.model.bind_tools(deps.tools)

    async def agent(state, runtime: Runtime[TurnContext]):
        turn = runtime.context
        iterations = state.get("iterations", 0)
        parent = state.get("span")

        if iterations >= deps.settings.max_iterations:
            span = turn.tracer.event(
                "cap",
                node="agent",
                parent=parent,
                iterations=iterations,
                limit=deps.settings.max_iterations,
            )
            # No tool calls on this message, so the router sends the turn to END.
            return {
                "messages": [AIMessage(content=CAP_REPLY)],
                "iterations": iterations,
                "span": span,
            }

        if not turn.budget.spend():
            span = turn.tracer.event(
                "budget",
                node="agent",
                parent=parent,
                spent=turn.budget.spent,
                limit=turn.budget.limit,
            )
            return {
                "messages": [AIMessage(content=BUDGET_REPLY)],
                "iterations": iterations,
                "span": span,
            }

        messages = [SystemMessage(content=state["system"]), *state["messages"]]

        async with model_semaphore(deps.settings.max_concurrency):
            reply = await bound.ainvoke(messages)

        span = turn.tracer.event(
            "node",
            node="agent",
            parent=parent,
            iteration=iterations + 1,
            tool_calls=[call["name"] for call in getattr(reply, "tool_calls", [])],
        )
        return {"messages": [reply], "iterations": iterations + 1, "span": span}

    return agent


def make_tools_node(deps: Deps):
    """Run every requested call. A failure becomes a result, not an exception.

    Every tool is invoked the same way, whether it is a plain function or a
    compiled subgraph. The per-turn context rides along in the invocation config
    so a delegating tool can trace under this node and spend from this turn's
    budget; a tool that does not declare a `config` parameter never sees it.
    """

    async def tools(state, runtime: Runtime[TurnContext]):
        turn = runtime.context
        parent = state.get("span")
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", []) or [])

        # One event per node entry, emitted before the work so the tools and any
        # subgraph beneath them have a span to hang under. Every requested call
        # is executed -- failures become results -- so the count is not a
        # prediction.
        span = turn.tracer.event("node", node="tools", parent=parent, executed=len(calls))
        config = {"configurable": {TURN_CONTEXT_KEY: turn.child_context(span)}}

        results = []

        for call in calls:
            name = call["name"]
            try:
                tool = lookup(deps.tools, name)
                output = await tool.ainvoke(call["args"], config)
                content = str(output)
                ok = True
            except UnknownToolError as error:
                content = f"Error: {error}"
                ok = False
            except Exception as error:  # noqa: BLE001 - the model gets to see and recover
                content = f"Error running {name}: {error}"
                ok = False

            turn.tracer.tool_event(name, ok=ok, parent=span)
            # tool_call_id ties the result back to the call that asked for it.
            results.append(ToolMessage(content=content, name=name, tool_call_id=call["id"]))

        return {"messages": results, "span": span}

    return tools


def route_after_agent(state) -> str:
    """Tool calls mean another lap; anything else ends the turn."""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "__end__"
