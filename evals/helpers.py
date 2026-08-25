"""Shared eval helpers: a stub model, and the credential check for live cases."""

from __future__ import annotations

import os
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
