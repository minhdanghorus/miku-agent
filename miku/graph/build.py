"""The loop, wired by hand.

    assemble ──▶ agent ──tool calls?──▶ tools
                   │                      │
                   │◀─────────────────────┘
                   ▼
                  END

Three nodes, one conditional edge, one cycle. `create_react_agent` would do this
in a line and hide exactly the part worth reading — so it is not used here.

This shape survived Phase 2 unchanged. Best-of-N fan-out lives in a subgraph
behind a tool (see graph/fanout.py), so delegation adds no node and no edge
here: the model decides to fan out by choosing a tool, which is a decision it
already makes. Per-turn context — the tracer and the request budget — arrives as
`Runtime.context` rather than in `Deps`, because a session outlives a turn.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from miku.graph.nodes import (
    Deps,
    TurnContext,
    make_agent_node,
    make_assemble_node,
    make_tools_node,
    route_after_agent,
)
from miku.graph.state import TurnState


def build_graph(deps: Deps, checkpointer=None):
    """Compile the turn graph for one session."""
    builder = StateGraph(TurnState, context_schema=TurnContext)

    builder.add_node("assemble", make_assemble_node(deps))
    builder.add_node("agent", make_agent_node(deps))
    builder.add_node("tools", make_tools_node(deps))

    builder.add_edge(START, "assemble")
    builder.add_edge("assemble", "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer, store=deps.store)
