## Context

Phase 1 built a legible three-node loop: `assemble -> agent <-> tools -> END`. Seven capability
specs describe it, 76 tests hold it, and its stated design constraint is legibility — explicit
code over framework indirection.

Phase 2 adds the first non-linear control flow. The risk is not that fan-out is hard to write;
it is that fan-out is where an agent codebase usually stops being readable. Every decision below
is chosen to keep the main graph as it is and put the new complexity behind one seam.

Two things were measured live on 2026-08-25 rather than assumed, both recorded in
`openspec/explorations/2026-08-25-miku-agent-architecture.md`:

- With four tools bound (the three real ones plus a stubbed `propose_slots`), `gemma-4-31b-it`
  returned an **identical tool choice on all three runs of all ten prompts**. Tool selection here
  is stable, not a coin flip. On unambiguous prompts it was also correct every time.
- The one genuine misroute was fixed by one sentence added to a tool description
  ("Do NOT use this when the user already said when — use `create_event` for that"), not by any
  change to control flow.

## Goals / Non-Goals

**Goals:**

- Answer "find me a good time for X" by generating N candidate slots in parallel and selecting
  one, using LangGraph's `Send` and a reducer field — built by hand, because that is the point.
- Leave the Phase 1 graph, its router, and its three nodes untouched.
- Bound the cost of a fan-out turn with a single number that both the parent graph and the
  subgraph respect.
- Keep a fan-out turn readable back from the JSONL trace as a tree.
- Make the fan-out assertable by the stub model, with no credentials.

**Non-Goals:**

- Retrieval gate, consolidation, embeddings — Phase 2.5.
- Node cache — see the proposal; branch inputs are deliberately never identical, so there is
  nothing for it to hit.
- Token/cost accounting — GreenNode usage metadata is unverified.
- More than one fan-out tool. Two overlapping fan-out tools were not probed.

## Decisions

### 1. Delegation is a tool, not a router node

`propose_slots` is registered beside `create_event`, `list_events`, and `remember`. The model
decides to fan out by selecting it.

*Alternative rejected — a classifier node before `agent`:* it costs one extra LLM call on every
turn, including "hi", and it splits the decision away from the model that must honour it.
Choosing a tool **is already** the model's decision surface; a router reinvents it. The spike
measured that the model uses that surface correctly.

*Alternative rejected — a CLI flag or keyword:* fully deterministic, but it is not product
behaviour, and it makes the user do the routing.

### 2. The tool's body is a compiled subgraph, not `asyncio.gather`

`asyncio.gather` over N model calls plus a judge would produce the same answer in a third of the
code — and would teach nothing about `Send`, reducers, or map-reduce in LangGraph, which is the
stated reason this phase exists. The subgraph also inherits streaming and node-level
observability for free.

*Trade-off accepted:* more machinery than the result strictly needs. Named, not hidden.

### 3. The subgraph formats its own result; the tools node does not change

`make_tools_node` coerces every result with `str(output)`. A subgraph returns a state dict.

**Decision:** the subgraph's final node returns a formatted string, so the tool looks like every
other tool from the node's side.

*Alternative rejected — teaching `make_tools_node` to recognise subgraph-backed tools:* it puts
knowledge of two tool kinds into the one node whose whole value is that it fits on a screen and
does one thing.

### 4. Per-turn context reaches the tool through `RunnableConfig`

The subgraph needs the turn's tracer span and budget. `Deps` is built once per **session**; these
are per-**turn**.

**Decision, in two hops:**

1. Into the graph: per-turn context enters the run through LangGraph's `context` parameter and
   reaches nodes as `Runtime.context`. `StateGraph(TurnState, context_schema=TurnContext)`,
   `graph.astream(inputs, config=..., context=TurnContext(tracer=..., budget=...))`.
2. Into the tool: `make_tools_node` forwards that context to `tool.ainvoke(args, config)` in a
   `RunnableConfig`, because a LangChain tool has no `Runtime`. A tool that needs it declares a
   `config` parameter.

**Verified live, not assumed** (both checked against the installed packages, not the docs):
`langgraph.runtime.Runtime` exists, `StateGraph.__init__` takes `context_schema`, and
`CompiledStateGraph.astream` takes `context`. On the LangChain side, `StructuredTool._arun`
inspects the coroutine's signature and injects `RunnableConfig` when the coroutine declares it
(`_get_runnable_config_param`), and `langchain_core.tools.InjectedToolArg` exists for hiding an
argument from the model-facing schema. Both hops are framework features used as intended.

*Revised during implementation.* This decision originally used `config["configurable"]` for both
hops. `Runtime` is the seam LangGraph 1.0 added for exactly this purpose — per-run dependencies
that must not be serialised into a checkpoint — so using `configurable` for graph-level context
would have been carrying dependencies through the field the checkpointer keys on.

**Span parentage is not context; it belongs in state.** A node's parent span changes from node to
node, so it cannot be a per-run constant. `TurnState` carries `span`: each node treats it as its
parent, emits its own event, and returns its own span. That makes "a linear turn reconstructs as
a chain" literally true, keeps the value a checkpoint-safe string, and gives the tools node a
concrete span to hand to a delegating tool.

*Alternative rejected — capturing budget/tracer in the tool closure at build time:* tools are
built per session, so every turn would share one budget. That is the concurrency bug below,
baked in.

*Alternative rejected — passing budget as a normal tool argument:* it makes the model declare its
own limit, and it adds a field to a schema the spike showed to be delicately balanced. The model
would omit it or pass a large number.

### 5. Budget is a mutable counter with the tracer's lifecycle

One `Budget` object per turn, shared by reference between parent graph and subgraph, exposing
`spend()` / `remaining()` / `exhausted`. It is created per turn the same way `Tracer` already is
(`for_turn(turn_id)`), and reaches the subgraph via decision 4.

This mirrors pydantic-ai's agent-delegation pattern, where the delegate receives the parent's
accumulator explicitly (`usage=ctx.usage`) and limits apply to the whole run rather than per
agent. Same shape, arrived at independently.

*Alternative rejected — a cap local to the subgraph:* local caps do not compose. `max_iterations
= 8` and a fan-out of `5+1` both report "within limits" while the turn spends 48 calls.

*Alternative rejected — a plain int in `Deps`:* each level gets a copy, which is the local-cap
failure wearing different clothes.

*Alternative rejected — budget in graph state, like `iterations`:* the subgraph runs inside a
tool and cannot see the parent's state. `iterations` stays in state precisely because only the
parent reads it.

### 6. The budget counts requests, one dimension only

`max_iterations` bounds depth; the missing bound is breadth x depth. A request count captures
that and is always countable.

*Alternative deferred — tokens or cost:* economically truer, but it requires GreenNode usage
metadata that has not been probed. A Phase 2 that depends on an unverified response field is a
Phase 2 that can fail for a reason unrelated to fan-out.

### 7. Diversity is structural: an angle per branch

`plan_angles` assigns each branch a distinct angle from a default list held in code (early
morning, after lunch, avoid busy days, adjacent to an existing event, later in the week). The
model may pass its own list as a tool argument; absent that, the default is used.

The default carries the weight on purpose: it is deterministic, so an evaluator can assert five
branches received five distinct angles with no model involved. A model-authored angle list is
the flexible case, not the baseline.

*Alternative rejected — temperature only:* five samples of one prompt are five similar answers.
This is the failure mode that makes best-of-N pointless. It was never even on the table here:
`chat_model` builds every model with `temperature=0`, so sampling diversity does not exist in
this codebase without a deliberate change.

*Alternative rejected — the supervisor inventing angles every time:* more "agentic", but it puts
the whole value of the feature into an unverifiable prompt and forces evaluators to assert on
prose — which the repo's rules forbid.

*Alternative deferred — one model per branch:* a real source of diversity, and the provider
adapter already supports it. Only three chat models exist, and one (`qwen`) is weaker.

### 8. Traces gain `span` and `parent`; the tree is reconstructed

Adding parentage rather than changing the format. Line order becomes arrival order and stops
mattering; `parent` carries causality.

*Alternative rejected — a file per branch:* loses global ordering, costs descriptors, and a turn
stops being readable with one `cat`.

*Alternative rejected — one nested JSON document per turn:* reads nicely, but the tree must be
held in memory until the turn ends, forfeiting append-only, which forfeits streaming, which
forfeits the Phase 3 dashboard — it consumes events one at a time. A crashed turn would leave
nothing on disk.

Note: `span`/`parent` is OpenTelemetry's data model in miniature. Deliberate — a later OTel
export becomes a field mapping instead of a redesign.

### 9. Branches run on `fast`, selection on `judge`

The seam is right and costs nothing to build. **Honest caveat:** in today's descriptor both
`main` and `fast` map to `gemma-4-31b-it`, so this changes no behaviour yet. It is a seam being
put in the correct place, not a measured saving.

## Risks / Trade-offs

**The `judge` role defaults to `openai/gpt-4o-mini`, which Phase 1 measured at 4/6 on
weekday-resolution cases** — it booked "Saturday" as a Thursday. Judging candidate slots with a
model that is bad at date arithmetic is a real hazard.
→ `generate` emits absolute ISO dates it has already resolved, and `select_best` chooses among
given candidates without computing dates. If selection quality still disappoints, the judge role
is one descriptor line away from `gemma`.

**`gemma` judging `gemma` is self-grading.** Same family, same blind spots.
→ Accepted for Phase 2: `select_best` ranks concrete alternatives against stated user facts, not
open-ended quality. Judge-model validation is a Phase 3 concern where `LLMJudge` lands.

**Argument quality for `propose_slots` is unmeasured.** The spike's tool was a stub and never
executed, so whether the model passes a sensible `task` and `window` is unknown.
→ The first task after the tool exists is a live probe of its arguments; validate and reject
unusable input the way `scheduling.py` already rejects non-ISO dates.

**Budget exhaustion mid-fan-out.** Some branches finish, some do not.
→ Errors degrade, they do not crash: `select_best` judges the candidates that arrived, the trace
records a budget event, and the reply says the search was cut short. Zero candidates yields a
tool result the model can see and respond to, not an exception.

**Trace volume rises ~6x on a fan-out turn**, and `event()` does a synchronous open/write/close
inside the event loop — invisible when the loop is sequential, a stall exactly when branches are
meant to overlap.
→ Measure during implementation and record the number. Do not pre-optimise; do not discover it
by wondering why fan-out feels slow.

**Adding a fifth tool may blur boundaries the spike measured with four.**
→ The boundary lives in tool descriptions (decision 1). Add live routing cases to the eval suite
so a description edit that breaks routing fails a test.

## Measured during implementation

Run live against GreenNode with `gemma-4-31b-it` as `main`, three seeded calendar
events, and one remembered habit ("I dislike meetings before 9am").

**Argument quality (the risk the spike could not test).** The model resolved every window
correctly, which was the open question since the spike's tool was a stub:

| Prompt | `start_day` / `end_day` |
|---|---|
| "...this week" (a Tuesday) | 2026-08-25 -> 2026-08-30 |
| "...next week?" | 2026-08-31 -> 2026-09-05 |
| "...before Friday" | 2026-08-25 -> 2026-08-28 |

`task` came through as a clean noun phrase each time ("1-hour design review", "dentist
appointment", "30 min vendor call"). Worth noting against the Phase 1 measurement that
`gpt-4o-mini` mishandles weekday arithmetic: `gemma`, which is what `main` actually is, did
not.

**Cost.** A fan-out turn: **8 requests** (1 agent + 5 branches + 1 judge + 1 agent),
**13 trace lines**, 2.0-9.4s wall. Against the Phase 1 baseline of 2 requests and 5 lines,
that is ~4x the requests and ~2.6x the lines. Not measured as a problem, and the synchronous
trace write was not visibly a bottleneck at this width -- recorded so a later regression has a
number to be compared against.

**The judge discriminates, and memory is what drives it.** Given a rigged candidate list where
the otherwise-obvious pick sits before 9am:

| Judge model | With the habit | Without it |
|---|---|---|
| `openai/gpt-4o-mini` (the default) | 1, 1, 1 | 0, 0, 0 |
| `google/gemma-4-31b-it` | 1, 1, 1 | 0, 0, 0 |

The remembered fact flips the choice, three runs out of three, on both models. So selection is
not defaulting, and the judge-model choice does not matter here -- which also means the
self-grading risk noted above cost nothing in practice. Both answered with a bare digit, so the
lenient parse never had to fall back.

**The live suite.** 8 cases, 18 assertions, all passing, including the two new routing cases.

### Two findings worth carrying forward

**Distinct angles can still converge on one slot.** "early morning" and "quietest day" returned
the identical slot in two of three runs. Not a bug -- arguably a slot two independent angles
both like is a *better* recommendation, and the trace shows which angles agreed -- but it means
five branches do not guarantee five options. Whether `select_best` should collapse duplicates,
and whether convergence should be surfaced as agreement rather than hidden as repetition, is a
tuning question this phase deliberately leaves open.

**An angle can be inapplicable to a window.** "beside existing work" produced an unparseable
reply when asked about a week with nothing booked -- there was nothing to sit beside. Degradation
handled it correctly (four candidates, selection ran, answer returned), but an angle that cannot
apply spends a request to say so. Angles conditional on the calendar would fix it.

## Open Questions

**Answered.**

- *How many branches by default?* Five, matching the five angles, since the width clamps to the
  number of distinct angles anyway. At that width a turn costs 8 requests, which is why the
  budget defaults to 24 -- three fan-outs deep.
- *Does the subgraph need its own iteration guard?* No. It has no cycle, and the request budget
  already bounds it. Revisit only if a branch is ever allowed to call tools.
- *Does the judge need to be a different model from the agent?* Measured as not mattering here;
  both candidates chose identically. The seam stays because the roles already existed.

**Still open.**

- Should `plan_angles` consult recalled facts when choosing *which* angles to use, rather than
  only `select_best` weighing them? Measurement shows facts already decide the outcome at
  selection, so the value would be in not spending a request on an angle a habit rules out.
- Should duplicate candidates be collapsed, or surfaced as two angles agreeing? See the findings
  above.
- The synchronous trace write inside the event loop is still synchronous. It cost nothing
  measurable at width five; it is unmeasured at width twenty.
