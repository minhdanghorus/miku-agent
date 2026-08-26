"""Shared eval helpers: a stub model, and the credential check for live cases."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

from miku.runtime.providers import GREENNODE


def has_credentials() -> bool:
    """Whether live-provider cases can run at all."""
    return bool(os.environ.get(GREENNODE.key_env, "").strip())


SKIP_REASON = f"no live provider credentials ({GREENNODE.key_env} unset)"


@dataclass
class StubModel:
    """A scripted stand-in for a chat model.

    Drives the loop deterministically and for free: no key, no network, no
    dependence on whether a small model happens to pick the right tool today.
    Returns `replies` in order, repeating the last one forever — which is how the
    runaway-turn case reaches the iteration cap.
    """

    replies: list[AIMessage]
    calls: list[list] = field(default_factory=list)
    bound_tools: list = field(default_factory=list)

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages, **_kwargs):
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        scripted = self.replies[index]

        # A real provider returns a distinct message every call. Reusing one
        # object would give every reply the same id, and `add_messages` would
        # replace rather than append — quietly changing the loop's shape.
        fresh = scripted.model_copy(deep=True)
        fresh.id = f"stub-{len(self.calls)}"
        for position, call in enumerate(fresh.tool_calls or []):
            call["id"] = f"{call['id']}-{len(self.calls)}-{position}"
        return fresh

    @property
    def invocations(self) -> int:
        return len(self.calls)


def tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def wants(name: str, args: dict, call_id: str = "call-1") -> AIMessage:
    """An assistant message that asks for one tool."""
    return AIMessage(content="", tool_calls=[tool_call(name, args, call_id)])


def says(text: str) -> AIMessage:
    return AIMessage(content=text)


@dataclass
class PromptModel:
    """A stub that answers according to what it was asked, not to call order.

    Fan-out branches run concurrently, so a script indexed by call number would
    make every assertion depend on which branch happened to finish first. This
    one dispatches on the prompt text, which is stable no matter the ordering.
    """

    respond: Callable[[str], AIMessage]
    calls: list[str] = field(default_factory=list)
    bound_tools: list = field(default_factory=list)

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages, **_kwargs):
        text = "\n".join(str(getattr(message, "content", "")) for message in messages)
        self.calls.append(text)
        fresh = self.respond(text).model_copy(deep=True)
        # Same reason as StubModel: a shared id makes add_messages replace
        # instead of append, silently changing the graph's shape.
        fresh.id = f"prompt-stub-{len(self.calls)}"
        for position, call in enumerate(fresh.tool_calls or []):
            call["id"] = f"{call['id']}-{len(self.calls)}-{position}"
        return fresh

    @property
    def invocations(self) -> int:
        return len(self.calls)

    def prompts_containing(self, needle: str) -> list[str]:
        return [text for text in self.calls if needle in text]


def slot_line(day: str, start_time: str, why: str = "fits the angle") -> AIMessage:
    """A branch reply in the one-line shape the fan-out parser accepts."""
    return AIMessage(content=f"{day} | {start_time} | {why}")


@dataclass
class PlanModel:
    """A stand-in for the consolidation model: returns plans, not messages.

    The pass asks for structured output, so this answers `with_structured_output`
    by handing back itself and then returning a `Plan` object directly. That
    keeps the whole consolidation suite free of credentials while still driving
    the real validation, the real writes, and the real trace.

    Plans are returned in order, repeating the last forever — which is what makes
    the idempotence case work: the second run gets the same proposal and must
    still change nothing.
    """

    plans: list = field(default_factory=list)
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)
    schemas: list = field(default_factory=list)

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return self

    async def ainvoke(self, messages, **_kwargs):
        text = "\n".join(str(getattr(message, "content", "")) for message in messages)
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        if not self.plans:
            raise AssertionError("PlanModel was called with no scripted plans")
        index = min(len(self.calls) - 1, len(self.plans) - 1)
        return self.plans[index]

    @property
    def invocations(self) -> int:
        return len(self.calls)

    @property
    def last_prompt(self) -> str:
        return self.calls[-1] if self.calls else ""
