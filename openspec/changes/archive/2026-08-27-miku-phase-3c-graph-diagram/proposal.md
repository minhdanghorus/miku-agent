## Why

The cockpit shows what a turn *did* — a causal tree of trace records — and nothing about what
the agent *is*. A reader watching `node:agent iteration 1 / node:tools / node:agent iteration 2`
has to infer in their head that those are one node entered twice, that five `generate` lines ran
concurrently, and that `plan_angles` is not part of the main loop at all. The tree carries
parentage; it cannot carry shape.

It also shows no time. Every record already has a `ts` and the renderer has never read it, so a
turn that spends five seconds inside a fan-out looks exactly like one that spends fifty
milliseconds.

Two additions fix both, and neither needs a backend change.

## What Changes

**Time, rendered from data already present.**

- Each record in the live turn pane and the traces pane gains an elapsed offset, computed in the
  browser from the `ts` every record already carries. No new field, no change to `tracing.py`,
  no change to any node.
- The label is neutral — `+2.3s`, with the absolute clock available on hover — and never says
  "started". `tools` emits its event *before* its work so that children have a parent to hang
  under; `assemble`, `agent`, `plan_angles`, `generate` and `select_best` all emit *after*
  theirs. The timestamp is therefore an end time for five of six nodes, and a label claiming
  otherwise would be false on most lines.

**A static architecture diagram, above the live turn.**

- A hand-authored diagram of the agent, rendered above the live turn pane and painted again in
  the traces pane for a finished turn.
- It is deliberately **not** derived from `compiled.get_graph()`. What LangGraph knows is the
  execution schedule — three nodes and a cycle. What a reader needs is the architecture, and the
  two are not the same thing here by design: `recall_facts` and `load_persona` run *inside*
  `assemble`, and the entire fan-out lives behind a tool because `build.py` chose that
  ("delegation adds no node and no edge here"). There is no object to ask what happens inside a
  node, so this diagram cannot be derived — it has to be written.
- Drift is guarded instead of prevented: a test asserts the set of node names the diagram claims
  equals the set actually registered with `StateGraph` in `build.py` and `fanout.py`. Adding a
  node without drawing it fails the suite. The diagram may be hand-written; it may not be
  hand-written *and* unwatched.
- The fan-out subgraph appears as one dimmed box under `tools`, labelled `propose_slots
  (subgraph)`, lit when any record carries a `node` belonging to it. Its internals are not drawn.
  Without that box the diagram falls silent for the whole of the longest and most complex turn —
  which is precisely the turn the diagram exists for.
- Live highlighting joins on `record.node`, a key every record already carries and which already
  matches the name each node is registered under. No event shape changes.
- Painting is a pure function of `(topology, records)`, never an incremental accumulation. That
  is what lets one function serve the live pane, the traces pane, and — later, as nothing but a
  slider — a time-scrubbed replay.

Nothing is added to the HTTP surface: no new endpoint, no new read, no new reach past
`open_session`. The web gateway's count of two stays two.

## Capabilities

### New Capabilities

- `cockpit-diagram`: what the cockpit renders about the agent's shape and a turn's timing — the
  hand-authored architecture diagram, its drift guard, the join between records and boxes, the
  delegated-subgraph box, and the neutral elapsed-time label.

### Modified Capabilities

- `deterministic-evals`: a new class of assertion — that a hand-authored description of the
  system matches the system. The suite already asserts on tool calls, stored rows and trace
  structure; it has never asserted that a document agrees with the code it describes.
- `agent-tracing`: the timestamp requirement is clarified to state that timestamps within one
  turn are comparable and non-decreasing in causal order. Previously "a timestamp" was enough
  because nothing consumed it; an elapsed offset rendered per record does.

## Impact

**Changed**

- `miku/gateway/static/app.js` — the elapsed label, the diagram's topology description, `layout`,
  `paint`, and wiring into both panes.
- `miku/gateway/static/index.html` — a container above the live turn pane.
- `miku/gateway/static/style.css` — box, edge, lit and dim states.
- `evals/deterministic/test_cockpit.py` — cases for the elapsed computation, the paint join, and
  the drift guard.
- `openspec/specs/` — one new capability, two modified.
- `CLAUDE.md` — the architecture map, and three known limits.

**Not changed, deliberately**

- `miku/ops/tracing.py`, `miku/graph/nodes.py`, `miku/graph/fanout.py` — no `ms` field, no new
  event, no reshaped record.
- `miku/runtime/inspect.py` — no `graph_view()`.
- `miku/runtime/session.py` — no `graph` accessor.
- `miku/gateway/web.py` — no new endpoint.

**Out of scope, and which phase owns it**

- **Per-node duration.** Deliberately not built: start times are enough for a human to read the
  gaps, and a truthful duration would mean an `ms` field on every node event. If a later phase
  wants machine-readable timing — a slow-node report, a regression check on latency — that phase
  adds the field, and this change's decision record says why it did not.
- **Level-2 replay** (a slider that scrubs a finished turn through the diagram). The pure paint
  function is chosen so this is a UI addition rather than a rewrite, but no slider is built and
  none is tested. A later cockpit phase owns it.
- **Drawing the fan-out's internals.** One box now. If the subgraph grows past four nodes, or a
  second delegated subgraph appears, that is when it earns its own view.
- **Deriving the diagram from the compiled graph.** Rejected here with reasons; a later phase
  that makes retrieval or skills into real graph nodes may revisit it, because at that point the
  two diagrams start to converge.
- **A conversation screen.** Still Phase 3d or later, unchanged by this.
