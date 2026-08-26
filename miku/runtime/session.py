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
from miku.graph.nodes import Deps, TurnContext
from miku.memory.checkpointer import open_checkpointer
from miku.memory.store import open_store
from miku.ops.tracing import Tracer, new_turn_id
from miku.runtime.budget import Budget
from miku.runtime.config import Settings, load_settings
from miku.runtime.providers import get_provider
from miku.tools.clock import Clock
from miku.tools.proposals import build_proposal_tools
from miku.tools.registry import build_tools


@dataclass
class TurnResult:
    """What one turn produced. Tool calls are recorded so evals can assert on
    behaviour rather than on the wording of the reply."""

    reply: str
    tool_calls: list[dict]
    iterations: int
    turn_id: str
    # Model requests this turn actually spent, the fan-out's included. Reported
    # so an eval can assert cost without reading the trace back.
    requests: int = 0

    def called(self, name: str) -> bool:
        return any(call["name"] == name for call in self.tool_calls)

    def args_for(self, name: str) -> dict:
        for call in self.tool_calls:
            if call["name"] == name:
                return call["args"]
        return {}


class Session:
    """Runs turns against one compiled graph."""

    def __init__(self, settings: Settings, graph, deps: Deps, budget: Budget):
        self.settings = settings
        self.graph = graph
        self.deps = deps
        # A template, not a counter: every turn clones its own allowance off it.
        self.budget = budget

    async def run_turn(
        self,
        message: str,
        thread_id: str,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> TurnResult:
        """One user message in, one reply out.

        `on_event` is how a gateway watches progress. It is called with the
        `{kind, ...}` trace records themselves and with nothing else, so a
        terminal and a browser are fed from one stream and every record a
        watcher sees has passed the sink. Nodes inside a delegated subgraph
        reach it only this way.
        """
        turn_id = new_turn_id()
        # One tracer and one budget per turn, cloned rather than reset in place.
        # Resetting shared objects is how two concurrent turns come to share a
        # counter, which the web gateway would expose immediately.
        tracer = self.deps.tracer.for_turn(turn_id)
        if on_event is not None:
            # A delegated subgraph runs inside a tool, so its nodes never appear
            # in this graph's update stream. The trace is the only place they
            # surface, which makes it the right feed for a watching gateway.
            tracer.listener = lambda record: on_event(record["kind"], record)
        turn = TurnContext(tracer=tracer, budget=self.budget.for_turn())
        config = {"configurable": {"thread_id": thread_id}}
        inputs = {"messages": [HumanMessage(content=message)], "turn_id": turn_id}

        tool_calls: list[dict] = []
        reply = ""
        iterations = 0

        async for update in self.graph.astream(
            inputs, config=config, context=turn, stream_mode="updates"
        ):
            for patch in update.values():
                if not isinstance(patch, dict):
                    continue
                iterations = patch.get("iterations", iterations)

                for produced in patch.get("messages", []):
                    # Collected for TurnResult, which is what evals assert on.
                    # Nothing is announced from here: the tools node traces each
                    # call as it is requested, so a watcher already sees it --
                    # with a parent, and redacted. Announcing it a second time
                    # from outside the sink is how the raw arguments used to
                    # escape.
                    tool_calls.extend(getattr(produced, "tool_calls", []) or [])
                    if isinstance(produced, AIMessage) and produced.content:
                        reply = str(produced.content)

        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            iterations=iterations,
            turn_id=turn_id,
            requests=turn.budget.spent,
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
        fast_model = select_model = model
        if model is None:
            from miku.runtime.providers import chat_model

            model = chat_model(settings, "main")
            # The fan-out's branches run on the cheap role and its selection on
            # `select`. Today's descriptor points main, fast and select at one
            # model, so this buys nothing yet -- it puts the seams where they go.
            #
            # `select` and not `judge`, though both name gemma right now: `judge`
            # is chosen for grading evals and will move to whatever grades best,
            # while this picks a slot a real person will be offered. They were
            # the same role until remapping the judge silently moved scheduling
            # behaviour, which is the argument for keeping them apart.
            fast_model = chat_model(settings, "fast")
            select_model = chat_model(settings, "select")

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
            fast_model=fast_model,
            select_model=select_model,
        )
        # A delegating tool needs the session it runs inside -- its subgraph
        # calls the same models and reads the same store -- so it cannot be
        # built before Deps exists. Added here, before build_graph binds the
        # list to the model.
        deps.tools.extend(build_proposal_tools(deps))

        yield Session(
            settings,
            build_graph(deps, checkpointer=checkpointer),
            deps,
            Budget(limit=settings.max_requests_per_turn),
        )
