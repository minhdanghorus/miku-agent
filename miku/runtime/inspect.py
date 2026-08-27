"""Reading a running system back — the view every gateway renders.

A gateway moves data and nothing else. A cockpit still has to *show* something:
what is configured, which tools exist, what memory holds, what a turn did. That
reading lives here rather than in whichever gateway happens to want it, so the
constraint the CLI is held to survives contact with a second surface.

Two rules, or this becomes the drawer everything is dropped into:

  * **Read-only.** Nothing here writes to the store, *modifies* checkpointed
    state, invokes a model, or opens a session. Every function is safe to call
    from a request handler at any moment during a turn. Reading thread state is
    permitted and is what the conversation views do; the prohibition that
    matters is on writing it, and removal lives on `Session` for that reason.
  * **No environment.** `config.py` is where configuration is resolved, and it
    stays the only place. These functions receive `Settings`, a store handle and
    a checkpointer handle as arguments. A second module reading `os.environ` is
    how two answers to "what is configured?" start to diverge.

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

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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


# --- Conversations ----------------------------------------------------------

# How much of the opening message becomes the title. A sidebar entry, not a
# summary: long enough to recognise a conversation, short enough not to wrap.
TITLE_WIDTH = 60


@dataclass(frozen=True)
class ThreadView:
    """One held conversation, as a list shows it.

    `title` is derived at read time from the first thing the user said. No
    stored field backs it, which is what keeps `checkpointer.py`'s standing
    claim -- that a conversation list needs no new data model -- true.

    `message_count` is here because history is unbounded: `nodes.py` sends every
    stored message on every turn, with no trimming and no prompt caching, so the
    cost of a conversation grows with its length. This change does not fix that.
    It refuses to hide it.
    """

    thread_id: str
    title: str
    message_count: int
    updated_at: str


@dataclass(frozen=True)
class Exchange:
    """One line of a transcript.

    `role` is `user`, `assistant` or `tool` -- never a message class name. The
    persisted format belongs to LangGraph and changes on its schedule; a browser
    and a terminal that both read it would both have to move with it.
    """

    role: str
    text: str


def _text_of(message) -> str:
    """A message's content as a string, whatever shape it was stored in."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    # Multimodal content is a list of parts. Nothing in this project produces
    # one today; rendering it as its repr beats rendering nothing.
    return "" if content is None else str(content)


def _messages_of(checkpoint) -> list:
    return list((checkpoint or {}).get("channel_values", {}).get("messages", []) or [])


def _title_of(messages: list) -> str:
    """The first thing the user said, flattened and truncated.

    Not a model call. A variable-latency, variable-cost request attached to an
    interaction that did not ask for one is the exact thing consolidation is
    forbidden from doing, and a sidebar is a weaker justification than memory
    hygiene was.
    """
    for message in messages:
        if isinstance(message, HumanMessage):
            flat = " ".join(_text_of(message).split())
            if not flat:
                continue
            if len(flat) > TITLE_WIDTH:
                return flat[: TITLE_WIDTH - 3] + "..."
            return flat
    return ""


async def thread_list(checkpointer) -> list[ThreadView]:
    """Every held conversation, most recently active first.

    Grouped, not listed. `alist(None)` yields every *checkpoint*, not one row
    per thread -- measured at 272 tuples across 15 conversations, roughly 18 to
    1, so a naive listing would have shown one conversation fifty-six times.
    The newest checkpoint per thread is the one that describes it.

    The scan is recorded rather than indexed: 272 rows over a 2.5MB database is
    instant, and an index would be the first schema this project adds on top of
    the checkpointer's own. The ratio is in CLAUDE.md so the phase that has to
    care can tell whether the number moved.

    Ordering is computed here rather than inherited from the stream. `alist`
    happens to yield newest first today; relying on that would make this correct
    by coincidence.
    """
    latest: dict[str, tuple[str, list]] = {}

    async for tuple_ in checkpointer.alist(None):
        thread_id = tuple_.config.get("configurable", {}).get("thread_id")
        if not thread_id:
            continue
        stamp = str(tuple_.checkpoint.get("ts", ""))
        seen = latest.get(thread_id)
        if seen is None or stamp > seen[0]:
            latest[thread_id] = (stamp, _messages_of(tuple_.checkpoint))

    views = [
        ThreadView(
            thread_id=thread_id,
            title=_title_of(messages),
            message_count=len(messages),
            updated_at=stamp,
        )
        for thread_id, (stamp, messages) in latest.items()
    ]
    # Newest first, so the list answers "what was I doing" rather than "what
    # exists". Ties break on the identifier so two paintings of one database are
    # the same list rather than merely the same set.
    views.sort(key=lambda view: (view.updated_at, view.thread_id), reverse=True)
    return views


async def conversation_view(checkpointer, thread_id: str) -> list[Exchange]:
    """One conversation, as exchanges, in the order they were stored.

    The filtering is the interesting part, and it was measured rather than
    guessed. A turn that calls a tool stores this:

        HumanMessage  "Beside Naruto, I also like Detective Conan"
        AIMessage     content=''   tool_calls=2
        ToolMessage   "Remembered: Dang likes Detective Conan"
        ToolMessage   "Remembered: Dang likes Dragon Ball"
        AIMessage     "Got it. I've added those to your preferences."

    The empty `AIMessage` is a real stored message that says nothing -- it
    exists to carry the requested calls -- and rendering it produces a blank
    bubble corresponding to no moment the user experienced. It is dropped.

    The `ToolMessage`s go the other way, and that is the discovery. This
    project's tools return prose, not serialised data, so showing tool activity
    is a filtering decision and not a formatting one: the text below is the text
    the tool returned, unchanged. Without it a transcript would say Miku replied
    and not that she did anything.

    A conversation that does not exist reads as empty, like every other absence
    here.
    """
    saved = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return []

    exchanges: list[Exchange] = []
    for message in _messages_of(saved.checkpoint):
        text = _text_of(message)
        if isinstance(message, HumanMessage):
            exchanges.append(Exchange(role="user", text=text))
        elif isinstance(message, ToolMessage):
            exchanges.append(Exchange(role="tool", text=text))
        elif isinstance(message, AIMessage) and text:
            exchanges.append(Exchange(role="assistant", text=text))
    return exchanges


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
