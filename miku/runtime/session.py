"""A session — everything wired together for the life of one process.

The CLI opens one of these. So does each eval case, which is why nothing here is
a module-level global: two sessions in one process must not share a database
handle, a tool list, or a tracer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from miku.graph.build import build_graph
from miku.graph.nodes import Deps
from miku.memory.checkpointer import open_checkpointer
from miku.memory.store import open_store
from miku.ops.tracing import Tracer, new_turn_id
from miku.runtime.config import Settings, load_settings
from miku.runtime.providers import get_provider
from miku.tools.clock import Clock
from miku.tools.registry import build_tools


@dataclass
class TurnResult:
    """What one turn produced. Tool calls are recorded so evals can assert on
    behaviour rather than on the wording of the reply."""

    reply: str
    tool_calls: list[dict]
    iterations: int
    turn_id: str

    def called(self, name: str) -> bool:
        return any(call["name"] == name for call in self.tool_calls)

    def args_for(self, name: str) -> dict:
        for call in self.tool_calls:
            if call["name"] == name:
                return call["args"]
        return {}


class Session:
    """Runs turns against one compiled graph."""

    def __init__(self, settings: Settings, graph, deps: Deps):
        self.settings = settings
        self.graph = graph
        self.deps = deps

    async def run_turn(
        self,
        message: str,
        thread_id: str,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> TurnResult:
        """One user message in, one reply out.

        `on_event` is how a gateway watches progress — it receives the same
        `{kind, ...}` shape the trace records, so a terminal and a future browser
        UI can be fed from the same stream.
        """
        turn_id = new_turn_id()
        self.deps.tracer.turn_id = turn_id
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"messages": [HumanMessage(content=message)], "turn_id": turn_id}

        tool_calls: list[dict] = []
        reply = ""
        iterations = 0

        async for update in self.graph.astream(inputs, config=config, stream_mode="updates"):
            for node, patch in update.items():
                if not isinstance(patch, dict):
                    continue
                iterations = patch.get("iterations", iterations)

                for produced in patch.get("messages", []):
                    for call in getattr(produced, "tool_calls", []) or []:
                        tool_calls.append(call)
                        if on_event:
                            on_event("tool_call", {"tool": call["name"], "args": call["args"]})
                    if isinstance(produced, AIMessage) and produced.content:
                        reply = str(produced.content)

                if on_event:
                    on_event("node", {"node": node})

        return TurnResult(
            reply=reply, tool_calls=tool_calls, iterations=iterations, turn_id=turn_id
        )


@asynccontextmanager
async def open_session(
    settings: Settings | None = None,
    model=None,
    clock: Clock | None = None,
) -> AsyncIterator[Session]:
    """Open every resource a session needs, and close them all on exit.

    `model` and `clock` are injectable: evals drive the loop with a stubbed model
    and a frozen date, using the same graph the CLI uses.
    """
    settings = settings or load_settings()
    settings.ensure_dirs()

    async with open_store(settings) as store, open_checkpointer(settings) as checkpointer:
        if model is None:
            from miku.runtime.providers import chat_model

            model = chat_model(settings, "main")

        provider = get_provider(settings)
        tracer = Tracer(
            traces_dir=settings.traces_dir,
            secret_env_names=(provider.key_env,),
        )

        deps = Deps(
            settings=settings,
            store=store,
            tools=build_tools(settings, store),
            model=model,
            tracer=tracer,
            clock=clock or Clock.real(),
        )
        yield Session(settings, build_graph(deps, checkpointer=checkpointer), deps)
