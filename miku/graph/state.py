"""The state that flows through the graph.

Small on purpose. If a node needs something, it should be visible here rather
than reachable through a closure or a global.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class TurnState(TypedDict, total=False):
    """One turn's working state.

    `messages` is the thread's history and is reduced by `add_messages`, so each
    node returns only what it adds. Everything else is plain replacement.
    """

    # The conversation. Persisted per thread by the checkpointer.
    messages: Annotated[list[AnyMessage], add_messages]

    # How many times the agent node has run this turn. The cap reads this.
    iterations: int

    # Identifies this turn in the trace.
    turn_id: str

    # Facts read out of long-term memory at the start of the turn.
    facts: list[str]

    # The assembled system prompt: persona + facts + today. Built by the
    # assemble node and read by the agent node, so the agent node never touches
    # SOUL.md or the store itself.
    system: str

    # What "today" is for this turn — injectable so evals stay stable.
    today: str

    # The span of the most recent trace event, which is what the next node hangs
    # under. Parentage lives here rather than in the per-turn context because it
    # changes from node to node; a per-run constant could not express a chain.
    # It stays a plain string so the checkpointer can persist it.
    span: str
