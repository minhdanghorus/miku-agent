"""Reading a running system back — the view every gateway renders.

A gateway moves data and nothing else. A cockpit still has to *show* something:
what is configured, which tools exist, what memory holds, what a turn did. That
reading lives here rather than in whichever gateway happens to want it, so the
constraint the CLI is held to survives contact with a second surface.

Two rules, or this becomes the drawer everything is dropped into:

  * **Read-only.** Nothing here writes to the store, touches checkpointed state,
    invokes a model, or opens a session. Every function is safe to call from a
    request handler at any moment during a turn.
  * **No environment.** `config.py` is where configuration is resolved, and it
    stays the only place. These functions receive `Settings` and a store handle
    as arguments. A second module reading `os.environ` is how two answers to
    "what is configured?" start to diverge.

Absence is data, not an error. No facts stored, no trace file for a date, no
turn matching an id — each comes back empty. A caller should never have to catch
an exception to tell "nothing yet" from "broken".

Traces are not reimplemented: `ops/traceview.py` already reads a flat file back
as the tree its parent links describe, and says in its own docstring that this
is what it was for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from langchain_core.tools import BaseTool
from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.memory.store import LiveFact, live_facts
from miku.ops.traceview import TraceNode, build_tree, read_records
from miku.runtime.config import Settings
from miku.runtime.providers import ROLES, ProviderError, get_provider, resolve_model


@dataclass(frozen=True)
class RoleView:
    """One role and the model it currently resolves to.

    `error` carries the reason instead of raising, because a descriptor missing
    one role should not blank the whole configuration tab.
    """

    role: str
    model: str = ""
    override: str = ""
    error: str = ""


@dataclass(frozen=True)
class ConfigView:
    provider: str
    roles: list[RoleView]
    limits: dict[str, object]
    state_dir: str
    user_id: str


@dataclass(frozen=True)
class ToolView:
    """A tool as the model is offered it.

    The description is the model's whole basis for choosing, and tool boundaries
    in this repo live in that prose rather than in routing code -- so showing it
    verbatim is the point, not a detail.
    """

    name: str
    description: str


def config_view(settings: Settings) -> ConfigView:
    """What is configured right now, per role, with the limits that apply."""
    try:
        provider_name = get_provider(settings).name
    except ProviderError as error:
        provider_name = f"{settings.provider} (unregistered: {error})"

    roles = []
    for role in ROLES:
        override = settings.model_override(role)
        try:
            roles.append(
                RoleView(role=role, model=resolve_model(settings, role), override=override)
            )
        except ProviderError as error:
            roles.append(RoleView(role=role, override=override, error=str(error)))

    return ConfigView(
        provider=provider_name,
        roles=roles,
        limits={
            "max_iterations": settings.max_iterations,
            "fanout_branches": settings.fanout_branches,
            "max_requests_per_turn": settings.max_requests_per_turn,
            "max_requests_per_consolidation": settings.max_requests_per_consolidation,
            "request_timeout": settings.request_timeout,
            "max_retries": settings.max_retries,
            "max_concurrency": settings.max_concurrency,
        },
        state_dir=str(settings.state_dir),
        user_id=settings.user_id,
    )


def tools_view(tools: list[BaseTool]) -> list[ToolView]:
    """The registered tools, by name, with the description the model reads.

    Takes the session's own list rather than rebuilding one: the delegating
    tools are appended after `Deps` exists, so anything reconstructed here would
    be quietly missing them.
    """
    return [
        ToolView(name=tool.name, description=(tool.description or "").strip())
        for tool in sorted(tools, key=lambda tool: tool.name)
    ]


async def memory_view(
    store: AsyncSqliteStore, settings: Settings, limit: int | None = None
) -> list[LiveFact]:
    """Live facts for the active user, oldest first.

    Superseded rows are excluded by `live_facts` itself, which is why this is a
    call rather than a query: the definition of "live" belongs next to the
    tombstone fields, not in a second place that can drift from them.
    """
    if limit is None:
        return await live_facts(store, settings)
    return await live_facts(store, settings, limit=limit)


def trace_dates(settings: Settings) -> list[str]:
    """Every date that has a trace file, newest first."""
    traces = settings.traces_dir
    if not traces.is_dir():
        return []
    return sorted((path.stem for path in traces.glob("*.jsonl")), reverse=True)


def _trace_path(settings: Settings, day: str | None) -> Path:
    return settings.traces_dir / f"{day or date.today().isoformat()}.jsonl"


def turn_ids_on(settings: Settings, day: str | None = None) -> list[str]:
    """The turns recorded on a date, in the order they first appear."""
    seen: list[str] = []
    for record in read_records(_trace_path(settings, day)):
        turn_id = record.get("turn_id")
        if turn_id and turn_id not in seen:
            seen.append(turn_id)
    return seen


def turn_view(settings: Settings, turn_id: str, day: str | None = None) -> list[TraceNode]:
    """One recorded turn, reconstructed as the tree its parent links describe.

    A turn that was never recorded -- wrong id, wrong date, no file at all --
    comes back as an empty list. That is the same answer as a turn that wrote
    nothing, and deliberately so: a gateway rendering "no events" is correct in
    both cases.
    """
    return build_tree(read_records(_trace_path(settings, day), turn_id=turn_id))
