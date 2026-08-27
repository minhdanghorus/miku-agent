## Context

The cockpit renders one thing: the causal tree of a turn's trace records. `buildTree` places
records by `parent`, `renderTree` emits nested `<ul>`s, and both the live pane and the traces pane
use the same pair. That was the right first surface — it is the only view that can show five
fan-out branches landing under one step — but it answers only "where did this turn go?", never
"what is this agent?".

Two facts about the existing data shaped this design more than anything else.

**Every record already carries `ts`, and nothing reads it.** `tracing.py:150` stamps
`datetime.now(UTC).isoformat()` onto every event. `describe()` in `app.js` reads `kind`, `node`,
`tool`, `args`, `branch`, `angle`, `day`, `start_time`, `facts`, `iteration` — and not `ts`. The
information needed to see that a fan-out costs five seconds has been in the file since Phase 1.

**Every record already carries `node`, and it already matches the name the node is registered
under.** `turn.tracer.event("node", node="assemble", ...)` in `nodes.py:127` uses the same string
as `builder.add_node("assemble", ...)` in `build.py:38`. That correspondence was never designed —
it fell out of naming things consistently — but it means a diagram can be lit from records with no
new field, no reshaped event, and no change to the sink.

The constraint that ruled out the obvious approach is that LangGraph's graph and the diagram a
reader wants are not the same object. `recall_facts` (`nodes.py:124`) is exactly the "retrieve
context" step a reader expects to see, and it is a line of code inside `assemble`, not a node.
`load_persona` likewise. The whole fan-out is behind a tool because `build.py` says so in its own
docstring: *"delegation adds no node and no edge here"*. A diagram derived from
`compiled.get_graph()` would be truthful about the scheduler and silent about the architecture.

## Goals / Non-Goals

**Goals:**

- A reader who has never seen the code can look at the cockpit and say what the agent does, before
  running a turn.
- A reader watching a turn can see which parts of that shape it went through, and how long it
  spent getting there.
- The turn that is hardest to follow — a fan-out, eight requests, five seconds of silence — is the
  turn the diagram helps most with.
- The hand-authored parts cannot silently disagree with the code where a machine can tell.
- No backend change. No new endpoint, no new field, no new reach past `open_session`.

**Non-Goals:**

- Per-node duration. Deliberately not built (Decision 3).
- A time-scrubbed replay. The paint function is shaped so this is later a slider (Decision 7), but
  no slider is built and none is tested.
- Drawing the fan-out subgraph's internals (Decision 5).
- Deriving the diagram from the compiled graph (Decision 1).
- Any claim about how the page *looks*. See Risks.

## Decisions

### 1. The diagram is hand-authored, not derived from `compiled.get_graph()`

**Chosen:** a declarative topology written by hand in `app.js`, describing boxes, the steps inside
them, edges, and — for boxes that correspond to real graph nodes — which node each claims.

**Rejected: derive it from the compiled graph.** This was the first design, and it was wrong for a
reason worth recording, because it is the reason a reader will ask about first. `get_graph()` can
only report what the scheduler knows: three nodes and a cycle. It cannot report `recall_facts`,
because that is not a node. It cannot report the fan-out, because that is behind a tool. Deriving
would produce a diagram that is unfalsifiable and uninformative at the same time — every box
correct, and the two things a reader most wants to see absent.

A secondary problem killed it independently: the derived design needed a spike to establish
whether `get_graph()` exposes the `plan_angles -> select_best` conditional edge at all
(`fanout.py:471` registers both destinations, and the zero-branch budget-exhausted path takes the
second one). Choosing to hand-author removed the question entirely, and with it a blocking task.

**Rejected: Mermaid via `draw_mermaid()`.** LangGraph offers it, and it is dead on arrival here.
`test_the_page_loads_its_script_as_a_module_without_a_bundle` forbids any `http://` or `https://`
in the page, so no CDN. `test_the_repository_declares_no_javascript_toolchain` forbids
`node_modules`, so no local copy. Server-side PNG rendering needs a headless browser and network
access. Three independent blocks, each one an existing test.

**Rejected: hand-placed SVG coordinates.** Legible for exactly as long as nobody edits it. The
declarative topology renders through HTML and CSS instead — rows of boxes, connectors as borders
and pseudo-elements — so adding a step is adding a list entry rather than re-solving a layout by
hand.

**Rejected: a layout algorithm (BFS ranking with back-edge detection).** Considered seriously and
dropped. It is roughly eighty lines of graph-theoretic indirection to position four boxes, in a
repo whose stated constraint is legibility. The one structure it would have handled automatically —
the `tools -> agent` back edge — is a single CSS rule when written out.

### 2. Drift is guarded, not prevented

A hand-authored diagram is a second place the architecture is written down, and the honest
statement is that this change accepts that rather than solves it. What it does not accept is that
the second place goes unwatched.

**Chosen:** a test comparing the set of graph-node names the topology claims against the set the
builders register, in both directions, failing with the offending name.

**Rejected: no guard, on the grounds that three nodes are easy to remember.** This is the argument
that is true until the day it is not, and the repo has already paid for it once: the Phase 3b
delta specs were incomplete because the author was confident about what he had written. The guard
costs one test.

**Rejected: generating the diagram so drift is impossible.** That is Decision 1, and it is refused
there for a different reason.

The guard's boundary is worth stating precisely because it is exactly the residual risk: it covers
the part of the diagram that corresponds to something derivable, and the part of the diagram that
is *not* derivable — the steps drawn inside `assemble`, the tool names on the delegation edge — is
the part it cannot check. That is not an oversight. It is the same boundary as Decision 1, seen
from the other side: what cannot be derived cannot be checked against a derivation.

### 3. Start times only. No `ms` field, no duration

**Chosen:** render each record's offset from the turn's first record. Nothing else about time.

**Rejected: a `ms` field measured with `perf_counter()` at each node.** This was the recommended
option during exploration and the user overruled it, on the grounds that a human reading a column
of offsets subtracts them without help. The rejection is recorded because the argument *for* it
was real: this repo prefers a declared fact over an inferred one everywhere else, and a duration
is the kind of thing that later wants to be machine-readable — a slow-node report, a latency
regression case. If that day comes, the field is added then, by the phase that needs it, and this
paragraph says why it was not added now.

**Rejected: inferring duration from the gap to the previous record.** This is the version that
looks right and is quietly false. The events do not bracket work:

| node | emits its event | so the preceding gap is |
|---|---|---|
| `tools` (`nodes.py:223`) | **before** the work, so children have a parent | nothing |
| `assemble` (`nodes.py:127`) | after | the fact recall |
| `agent` (`nodes.py:192`) | after | the model call |
| `plan_angles` (`fanout.py:208`) | after | the calendar read |
| `generate` (`fanout.py:309`) | after | that branch's model call |
| `select_best` (`fanout.py:403`) | after | the selection call |

Five of six make the gap meaningful and one does not, and nothing in the record distinguishes
them. A UI that printed `tools: +0.00s` next to `plan_angles: +4.80s` would be telling the truth
by accident. Offsets from the turn's start are true for all six with no case analysis at all.

### 4. The label is neutral

`+2.3s`, not "started at". For five of the six nodes the timestamp is when the step *finished*.
The absolute clock time goes in a `title` attribute for anyone who wants it. This is a one-word
decision with a real consequence: it is the difference between a display that is correct for every
row and one that is correct for one row.

Negative offsets are clamped to zero rather than rendered. `datetime.now(UTC)` is not monotonic
and a clock step during a turn would otherwise produce `-0.4s`, which reads as a bug in the agent
rather than a bug in the clock.

### 5. The delegated subgraph is one dimmed box

**Chosen:** a single box attached to `tools`, labelled with the tool that reaches it, lit when any
record names `plan_angles`, `generate`, `select_best`, or `format`.

**Rejected: omit it, since the tree below already shows the branches.** This was the initial
scope, and the failure mode is specific rather than aesthetic. A fan-out turn costs eight requests
and several seconds. Without the box, the diagram lights `assemble`, `agent`, `tools` in the first
second and then does not change again for the rest of the turn, while everything interesting
happens below it. The diagram would go quiet exactly during the turn it exists to explain.

**Rejected: draw the subgraph's four nodes.** Deferred rather than refused. Four more boxes, a
second layout context, and a fan-out indicator, in exchange for detail the tree beneath already
renders correctly and is already tested. When a second delegated subgraph exists, or this one
grows, that trade changes.

### 6. Records join to boxes on `node`, which already exists

**Chosen:** a box declares which node names it represents; a record marks it if its `node` matches.

**Rejected: add a `box` or `step` field to trace events.** It would mean editing every
`tracer.event` call site and widening the record shape that `traceview.py`, the evals, and an
archived spec all describe — to carry information that the existing `node` field already carries.
It would also mean the trace format changing shape for the benefit of one renderer, which is the
wrong direction of dependency.

A record naming an unknown node is ignored rather than treated as an error. A turn from an older
trace file, or from a future graph, must still paint what it can.

### 7. `paint(topology, records)` is pure, and never accumulates

**Chosen:** painting takes the whole record set and returns the whole painted state. The live pane
calls it again on each arriving record, passing everything seen so far.

**Rejected: incremental accumulation** — mark a box as each record arrives. Cheaper per event and
wrong for two reasons. First, it makes the live pane and the traces pane structurally different,
and `app.js` already says in its own header comment that a second renderer would be a second place
for the logic to be wrong. Second, it forecloses replay: an accumulating painter has no way to
express "the state at t", so a scrubber would require rewriting it. With a pure function, replay
is `paint(topology, records.filter(r => r.ts <= t))` and the only new code is the slider.

The cost is repainting a handful of boxes per event. The graph has four boxes.

### 8. A box shows a count, not a time

`agent` is entered three times in a three-lap turn. There is no single timestamp for it. The box
badge is therefore the number of records that marked it — `agent x3`, `generate x5` — and the
timing lives in the tree below, one row per record, where each row has exactly one timestamp and
the question does not arise.

### 9. The guard reads the topology by running it

**Chosen:** `app.js` exports `TOPOLOGY`; the guard test runs `node` to dump the node names it
claims, and compares against `build_graph(...).get_graph().nodes` and
`build_fanout_graph(...).get_graph().nodes` with `__start__` and `__end__` filtered out.

This keeps the test asserting on identifiers rather than on source text, which the repo's own rule
requires — a grep over `app.js` would be an assertion on prose, and the last two times a test in
this repo asserted on prose it broke on a copy edit.

**Rejected: put the topology in a JSON file both sides read.** It would make the guard run without
`node` installed, which is a genuine advantage. It was dropped because the page would then have to
fetch its own topology at load, adding a failure mode to the runtime in order to remove a skip
condition from a test — and the node-dependent skip axis already exists and is already accepted for
`buildTree`.

The consequence is stated as a limit rather than hidden: on a machine without `node`, the drift
guard skips, exactly as the existing frontend cases do.

**Note on the reach count.** The guard builds its own graph inside the test. The gateway does not
gain access to `session.graph`, so the web gateway's reaches past `open_session` stay at two
(`deps.tools`, `deps.store`) and the corresponding known limit is unchanged rather than worsened.
An earlier version of this design added a `Session.graph` accessor for a derived diagram; Decision
1 removed the need for it.

### 10. The spine is horizontal; anything that loops back hangs below it

**Chosen:** boxes run left to right -- start, assemble context, think, answer -- and a box that
loops back to another is drawn *inside that box's column*, below it. `tools` sits under `agent`;
`propose_slots` sits under `tools`, indented.

**Rejected: the vertical stack this shipped as first.** Every box and every connector on its own
row, nine rows for four boxes. It wasted the width the page has and spent the height it does not.

**Rejected: a single horizontal rail with everything inline** -- `assemble > agent > tools >
propose_slots > END`. Most compact, and it lies twice: `tools` never leads to `END`, and
`propose_slots` is not a fourth step in the pipeline, it is delegated from inside `tools`.

What the chosen layout buys beyond space is that the vertical axis now means something: distance
below the spine is depth of delegation. `tools` is one step off because the turn detours and
returns; `propose_slots` is two because it is a subgraph behind a tool. In the vertical version
that axis meant nothing -- boxes were below each other because that is where the next one went.

**The cycle is two arrows, not one.** `agent -> tools` is conditional: `route_after_agent` decides
it. `tools -> agent` is unconditional -- `build.py:45` adds a plain edge, so every tools run
returns. A single double-headed glyph would render a decision and a certainty as the same thing.
Two connectors with their own labels cost nothing and carry the distinction. Asserted by a case
counting them, so collapsing them back into one fails.

### 11. The fan-out loses its box and keeps its node names

**Chosen:** `propose_slots` is no longer drawn. Its four node names move onto `tools` as
`deferred`, so the guard still covers them and the records still count.

**Rejected: delete the names along with the box.** It would have shrunk `nodesClaimed` from seven
to three while the builders still register seven, so the guard would have failed — correctly.
Making the guard ignore the fan-out graph to get past that would have paid for a UI simplification
with a test, and a fan-out node added later would go unnoticed forever.

Decision 5 argued for the box on the grounds that without it the diagram falls silent through the
longest turn. That argument does not survive the names moving to `tools`: a fan-out now drives that
box's count from `x1` to `x8`, which is the same signal with one less box. The box was right when
the alternative was silence and wrong once the alternative was a number moving.

### 12. Edges carry no words

**Chosen:** no text on any connector. Dashed means the router decides, solid means the edge has no
condition, and the condition itself moves into a `title`.

**Rejected: keep the labels.** `no tool calls -> END` read as though some other destination were on
offer, when the exit is the only place a turn can go — it stated what the reader already knew and
charged pixels for it.

**Rejected: drop the labels and the distinction with them.** `agent -> tools` is decided by
`route_after_agent`; `tools -> agent` is `add_edge`, which has no condition to decide. Rendering
both as plain arrows would have made a decision and a certainty look identical, which is the
mistake Decision 10 avoided by using two connectors in the first place.

Dashed-means-conditional is a convention a reader half-knows rather than one this page teaches, and
that is the risk taken. The `title` is the escape hatch, and restoring the labels is a one-line
revert if it does not read.

### 13. The moving mark is a frontier, not a running node

**Chosen:** while records are arriving, the box owning the most recent placeable record is marked,
with a pulse and no label. Cleared when the reply lands. The traces pane passes `null`.

**Rejected: call it the running node.** It is not, and the reason is Decision 3 in a new costume.
`tools` records its event before its work, so when that record arrives tools really is running.
`assemble`, `agent`, `plan_angles`, `generate` and `select_best` record after finishing, so when
those arrive the node has stopped. "Running" would be true one time in six. "How far the turn has
got" is true every time, and needs no word on screen — which is fortunate, because the accurate
word is clumsy.

**Rejected: fold it into `paint`.** `paint` is required to be independent of arrival order and has
a case asserting it. A frontier is *defined* by arrival order. One function cannot be both, so
there are two, composed at the call site — and a case asserts the pair diverges in exactly that
way, which is what stops someone quietly merging them later.

### 14. Every box declares what its number counts

Found by the user reading a real trace, not by a test: a turn that called `remember` once showed
`tools x3`. The tools node emits three records for one call -- its entry (`nodes.py:223`), the
request (`nodes.py:236`) and the result (`nodes.py:251`) -- and the badge was counting records.
`agent x2` was right at the same moment, because the agent node emits nothing but `node` events. So
the same badge meant laps on one box and trace lines on another.

Every case passed, and would have kept passing. They all asserted that `paint` counts records,
which it did. The implementation was tested against itself.

**Chosen:** each box declares the kind of record that means one occurrence of what it counts, with
the unit:

| box | counts | reads |
|---|---|---|
| `assemble` | nothing | *(no badge -- it runs once a turn)* |
| `agent` | `kind: node` | `2 laps` |
| `tools` | `kind: tool_call` | `1 call` |

**Rejected: one uniform rule -- count node entries everywhere.** Consistent, and it makes `tools`
read `1` on almost every turn, which is the least interesting number available about a step whose
whole job is running calls. A uniform abstraction was what produced the bug; a second one is not
the fix.

**Rejected: bare numbers.** Two boxes counting different things, both rendering `x3`, is the same
trap one layer along -- a number that looks comparable and is not. The unit is rendered with it.

What makes this version checkable rather than merely more accurate is that both rules land on
quantities the session computes with no involvement from the diagram: `count(agent, node)` is
`TurnResult.iterations`, because the agent node emits one `node` record per lap in the same return
that increments the counter, and `count(tools, tool_call)` is `len(TurnResult.tool_calls)`. A case
runs a real turn and asserts the badges against those two numbers. That is the assertion the old
count could not fail.

Three consequences worth stating:

- **`lit` had to come off the count.** A turn that hits the iteration cap emits `cap` and no `node`
  record, so agent would report zero laps and go dark -- despite being what ended the turn. Lit is
  now "any record placed here", which is a different question and deserved a different answer.
- **The entry kinds are an allowlist** (`node`, `cap`, `budget`). A kind nobody has classified must
  fail to count rather than silently inflate a number, which is the direction the original bug ran.
- **The rule can rot.** Rename a kind and a badge quietly reads zero, with the drift guard none the
  wiser -- it compares node names, not kinds. A case runs a real turn and asserts every declared
  kind actually occurs.

## Risks / Trade-offs

**The diagram's appearance is unverifiable by machine, and this change makes that surface the
largest in the repo.** → `layout`, `paint`, the offset computation and the drift guard are all
callable functions with cases, so *correctness* is covered. Whether the picture is legible, whether
the lit state is visible against the dim one, whether the back edge reads as a loop — none of that
can be asserted. The mitigation is that a person opens it, and this is now the second phase in a
row where that has to be said out loud.

**The un-derivable half of the diagram is also the unguarded half.** → The steps drawn inside
`assemble` can go stale if `recall_facts` is moved or a third step is added there, and no test will
notice. Bounded by being small and by living next to a guarded inventory; not eliminated. Recorded
as a known limit in its own words rather than folded into a general statement about hand-authored
docs.

**The drift guard skips where `node` is absent.** → Same axis as the existing frontend cases, and
it runs in the environment the author develops in. A guard that runs on the author's machine and
skips on a bare CI box is worth strictly more than no guard.

**A clock step during a turn produces a negative offset.** → Clamped to zero. The alternative,
switching the trace to a monotonic clock, would change a field an archived spec describes for a
failure that has never been observed.

**A per-box counting rule is more surface than a uniform one.** → Three rules in one place, each
tied to a number the session computes and each asserted against it. The uniform rule it replaced
was smaller and wrong.

**Dashed-versus-solid is an untaught convention.** → A reader who does not notice it loses the
decision/certainty distinction and loses nothing else; the labels it replaced were noise on two of
three edges. Hovering restores the full statement. If the browser check says it does not read, the
loop labels come back.

**The frontier pulse is the only animation on the page, and animation reads as decoration.** →
It is the one thing on screen that changes for a reason a static render cannot express, and it is
suppressed under `prefers-reduced-motion`. If it distracts more than it informs, the pulse becomes
a static ring — the class is already applied, only the keyframes go.

**Offsets make the trace pane wider, and the terminal is not the only consumer.** → This is a
browser-only change. The CLI's own output is untouched, so the ASCII rule is not in play.

**A turn whose first record is missing has no origin to measure from.** → The first record present
becomes the origin. A truncated file already renders as roots in `buildTree`; this follows the same
principle that absence is data.

## Migration Plan

None required. The change is frontend, tests, and documentation:

- No schema, no stored data, no trace format change. Existing `.miku/traces/*.jsonl` files render
  in the new view without conversion, because every field the view reads was already being written.
- No new dependency, no new extra, no change to `pyproject.toml`.
- Rollback is reverting the commit. A cockpit without a diagram is the cockpit that shipped in
  Phase 3b.

## Open Questions

None blocking. Two deferred, both recorded above rather than left implicit:

- Whether the fan-out subgraph eventually earns its own expanded view (Decision 5) — revisit when a
  second delegated subgraph exists.
- Whether a duration field is added (Decision 3) — revisit when something other than a human needs
  to read the timing.

One question this change deliberately answers in the negative, because it will be asked again:
*should the diagram become derivable by making retrieval and skills into real graph nodes?* That is
a question about the graph, not about the cockpit, and making a node exist so a picture can be
generated is the tail wagging the dog. If Phase 2.5b makes retrieval a node because retrieval
should be a node, the diagram follows for free.
