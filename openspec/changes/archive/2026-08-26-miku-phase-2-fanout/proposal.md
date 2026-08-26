# Phase 2 — best-of-N fan-out

## Why

Phase 1 can only do what the user already decided. "Book tennis Saturday 8am" works; "find me a
good time for the review this week" has no path through the loop, because answering it means
weighing several candidate times against each other rather than executing one instruction.

That gap is also the reason this repo exists. Fan-out — generate N candidates in parallel, judge
them, return one — is the pattern behind LangGraph's `Send` API, and building it by hand is the
Phase 2 learning goal. A live spike (2026-08-25, recorded in the exploration doc) confirmed the
model can decide on its own when to fan out, which removes the last blocker: no routing
machinery has to be invented for it.

## What Changes

- **A new scheduling tool, `propose_slots`**, for requests that name a task but not a time. The
  model reaches it by ordinary tool selection — there is no router node, no classifier, no user
  flag. Measured: on unambiguous prompts the model chose correctly 3/3 runs on every case.
- **The tool's body is a compiled subgraph** that fans out with `Send`: `plan_angles` emits N
  branches, each `generate` proposes a slot from a different angle, a reducer field
  (`Annotated[list, operator.add]`) gathers them, and `select_best` judges. The main graph
  (`assemble -> agent <-> tools -> END`) is **unchanged**.
- **Branch diversity is structural, not thermal.** Five calls to one prompt at high temperature
  produce five similar answers, which defeats best-of-N. Each branch receives a distinct angle
  from a default list held in code; the model may override the list through a tool argument.
- **A per-turn request budget** shared by the parent graph and the subgraph, so that breadth
  (N branches) multiplied by depth (`max_iterations` laps) is bounded by one number. It follows
  `Tracer`'s lifecycle exactly — per-turn, reachable through `Deps`, cloned per turn — because
  `Deps` is session-scoped and a session will serve concurrent turns from Phase 3 onward.
- **Traces gain `span`, `parent`, and `branch`.** A flat log carries causality in its line order;
  five concurrent branches destroy that. The tree is reconstructed from parent links, so
  out-of-order arrival is harmless and the format stays append-only and streamable.
- **Branches may run on a cheaper role.** `generate` on `fast`, `select_best` on `judge`. This is
  the first real use of the four-role system built in Phase 1 and never exercised.
- **Evals assert the shape of the fan-out**, not the wording of its answer: exactly N `generate`
  spans sharing one parent, each with a distinct `branch`, exactly one `select_best` after them,
  and no spans past a budget stop. All of it drivable by the stub model, with no credentials.

### Explicitly out of scope

| Deferred | Owner | Why |
|---|---|---|
| Retrieval gate, fact consolidation, embeddings | **Phase 2.5** | A separate axis: it touches `memory/`, needs `bge-m3`, and amends `agent-memory`. Nothing in fan-out depends on it. |
| LangGraph node cache | **later** | Assumed in earlier planning to earn its place *because of* fan-out. On inspection it does not: branches are deliberately given different angles, so no two branch inputs are ever identical and there is nothing to hit. Revisit when a repeated-input path actually exists. |
| Token- and cost-based budget | **later** | The truer economic dimension, but it requires reading GreenNode's usage metadata, which is unverified. Phase 2 counts requests, which is always countable. |
| OTel spans | **later** | `span`/`parent` is deliberately OTel's model in miniature, so this becomes a field mapping rather than a redesign. |
| Web UI, judge evals | **Phase 3** | Unchanged from the original roadmap. |
| A second fan-out tool | **later** | One is enough to learn the pattern, and the spike did not test two overlapping fan-out tools competing for selection. |

## Capabilities

### New Capabilities
- `fanout-selection`: the best-of-N subgraph — angle planning, `Send` fan-out, candidate
  reduction, and LLM-as-judge selection, including how a tool-backed subgraph returns its result.
- `turn-budget`: a per-turn request budget shared across the parent graph and any delegated
  subgraph, its lifecycle, and what happens when it is exhausted.

### Modified Capabilities
- `scheduling-tools`: adds `propose_slots`, and the requirement that a tool overlapping another
  tool's scope must state in its description when *not* to use it — the spike showed that
  sentence is what fixes misrouting.
- `agent-tracing`: trace records gain `span` and `parent` (and `branch` on fan-out nodes); a turn
  is read back as a tree built from parent links rather than from line order.
- `agent-loop`: adds — a registered tool may be backed by a subgraph, and the tools node treats
  it identically to a plain function; and per-turn context (trace parentage, budget) reaches
  tools through the invocation configuration. The existing three-node requirement is unchanged,
  which is the point.
- `deterministic-evals`: adds — evaluators assert fan-out shape from the trace (branch count,
  distinct angles, one selection, budget stop) under the stub model, plus live cases pinning
  which tool the model selects, since tool boundaries live in descriptions rather than in code.

## Impact

**New code**: `miku/graph/fanout.py` (the subgraph and its nodes), `miku/runtime/budget.py`,
`miku/tools/proposals.py`.

**Modified**: `miku/ops/tracing.py` (span/parent fields, a child-tracer clone),
`miku/graph/nodes.py` and `miku/runtime/session.py` (wiring budget and span parentage into
`Deps`), `miku/tools/registry.py` (registering the new tool), `miku/runtime/limits.py`
(currently unused — this is what it was written for), `miku/runtime/config.py` (fan-out width and
request-budget knobs, `MIKU_`-prefixed like every other).

**Evals**: new tree-shape evaluators in `evals/evaluators.py`; `evals/task.py` grows to expose
trace records; a new `evals/deterministic/test_fanout.py`.

**Dependencies**: none added.

**Open wiring question** carried into design: the subgraph lives inside a tool, so it must
receive its tracer span and budget either through the tool closure at build time or from
`make_tools_node` passing its own span down. The latter is cleaner but touches the tool calling
convention.
