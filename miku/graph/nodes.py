"""The three nodes.

  assemble  — build the system prompt from persona + recalled facts + today
  agent     — one model call, with the tools bound
  tools     — run every requested call, turning failures into results

Each node takes state and returns only what it changes. The wiring between them
lives in build.py, so the control flow is readable in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.memory.store import recall_facts
from miku.ops.tracing import Tracer
from miku.runtime.config import Settings
from miku.runtime.limits import model_semaphore
from miku.tools.clock import Clock
from miku.tools.registry import UnknownToolError, lookup

SOUL_PATH = Path(__file__).resolve().parent.parent / "SOUL.md"

CAP_REPLY = (
    "I hit my step limit for this turn, so I stopped before finishing. "
    "Tell me what to focus on and I will pick it back up."
)


def load_persona() -> str:
    return SOUL_PATH.read_text(encoding="utf-8").strip()


@dataclass
class Deps:
    """Everything the nodes need, passed in rather than reached for."""

    settings: Settings
    store: AsyncSqliteStore
    tools: list[BaseTool]
    model: object  # BaseChatModel; typed loosely so stubs work in evals
    tracer: Tracer
    clock: Clock


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

    async def assemble(state):
        facts = await recall_facts(deps.store, deps.settings)
        today = state.get("today") or deps.clock.describe()

        deps.tracer.event("node", node="assemble", facts=len(facts), today=today)

        return {
            "facts": facts,
            "today": today,
            "system": build_system_prompt(load_persona(), facts, today),
            # Reset per turn. State survives across turns on a thread, so
            # carrying the count forward would starve later turns of iterations.
            "iterations": 0,
        }

    return assemble


def make_agent_node(deps: Deps):
    """One model call. Also where the iteration cap stops a runaway turn."""
    bound = deps.model.bind_tools(deps.tools)

    async def agent(state):
        iterations = state.get("iterations", 0)

        if iterations >= deps.settings.max_iterations:
            deps.tracer.event(
                "cap", node="agent", iterations=iterations, limit=deps.settings.max_iterations
            )
            # No tool calls on this message, so the router sends the turn to END.
            return {"messages": [AIMessage(content=CAP_REPLY)], "iterations": iterations}

        messages = [SystemMessage(content=state["system"]), *state["messages"]]

        async with model_semaphore(deps.settings.max_concurrency):
            reply = await bound.ainvoke(messages)

        deps.tracer.event(
            "node",
            node="agent",
            iteration=iterations + 1,
            tool_calls=[call["name"] for call in getattr(reply, "tool_calls", [])],
        )
        return {"messages": [reply], "iterations": iterations + 1}

    return agent


def make_tools_node(deps: Deps):
    """Run every requested call. A failure becomes a result, not an exception."""

    async def tools(state):
        last = state["messages"][-1]
        results = []

        for call in getattr(last, "tool_calls", []):
            name = call["name"]
            try:
                tool = lookup(deps.tools, name)
                output = await tool.ainvoke(call["args"])
                content = str(output)
                ok = True
            except UnknownToolError as error:
                content = f"Error: {error}"
                ok = False
            except Exception as error:  # noqa: BLE001 - the model gets to see and recover
                content = f"Error running {name}: {error}"
                ok = False

            deps.tracer.tool_event(name, ok=ok)
            # tool_call_id ties the result back to the call that asked for it.
            results.append(ToolMessage(content=content, name=name, tool_call_id=call["id"]))

        deps.tracer.event("node", node="tools", executed=len(results))
        return {"messages": results}

    return tools


def route_after_agent(state) -> str:
    """Tool calls mean another lap; anything else ends the turn."""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "__end__"
