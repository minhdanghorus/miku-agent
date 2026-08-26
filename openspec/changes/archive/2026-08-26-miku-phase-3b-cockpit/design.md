## Context

The terminal gateway is 214 lines and holds no agent logic. Everything a caller needs is
two symbols:

```
open_session(settings) -> Session
Session.run_turn(message, thread_id, on_event) -> TurnResult
```

`session.py:70-76` already names the eventual second consumer in a comment: *"a terminal
and a future browser UI can be fed from one stream"*. So the interesting work in 3b is not
inventing a transport — it is finding out whether that sentence is true, and paying for the
places where it turns out not to be.

Reading the current code before designing turned up three such places, all small, one of
which is a live defect rather than a gap.

**The event stream has two shapes, not one.** Records from `tracer.event()` carry
`{turn_id, span, parent, kind, node, ts, ...}`. The `tool_call` event does not: it is
synthesised at `session.py:104` from the graph's update stream and handed straight to the
listener. A terminal printing one line per event does not care. A cockpit assembling a
causal tree cannot place a node that has no parent.

**The listener's stated guarantee is false.** `tracing.py:79-82` says a listener *"sees
exactly what the file sees — never the raw payload — so a listener cannot become a second
way to leak a key."* The synthesised `tool_call` bypasses `_redact` entirely, so tool
arguments reach the listener raw. Under the CLI this is inert; 3b turns the listener into
something that pushes bytes over a socket, which is precisely the scenario the docstring
claims is impossible.

**The gateway constraint is stated more broadly than it is meant.** The `cli-gateway` spec
says a gateway *"reads no memory"*. A cockpit with a memory tab must read memory. Either
the constraint is wrong or the reading belongs somewhere that is not the gateway.

## Goals / Non-Goals

**Goals:**

- Watch a turn think in a browser, including a fan-out — five concurrent branches placed
  correctly in a tree, which is the first thing that reads Phase 2's tracing back.
- Cash the Phase 1 claim: measure how many times the web gateway must reach past
  `open_session`, and record the number whatever it is.
- Make the sink's redaction guarantee true for every consumer, not just the file.
- Keep the repo buildless. No npm inside a uv-managed Python project.

**Non-Goals:**

- The conversation screen (sidebar + transcript). Deferred; `thread_id` already covers it.
- Auth, multi-user, remote exposure. Loopback, one local user.
- Any change to how the agent reasons. No node, edge, prompt, or tool behaviour moves.
- A pretty UI. Legible beats polished; this is an instrument, not a product.

## Decisions

### 1. SSE over a POST fetch, with starlette's `StreamingResponse` and no SSE library

One connection per turn. The browser POSTs the message and reads `text/event-stream` off
the response body; each record arrives as `data: {json}` followed by a blank line.

*Alternative considered — WebSockets.* Rejected: the traffic is one-directional after the
request, and a WebSocket adds a connection lifecycle (reconnect, heartbeat, close codes)
that buys nothing here.

*Alternative considered — `sse-starlette`.* Rejected: it wraps about thirty lines of
formatting. The repo's constraint is legibility, and a dependency whose source is shorter
than the cost of explaining why it is there is a poor trade. `StreamingResponse` over an
async generator is the whole mechanism.

*Alternative considered — `GET /api/turn?message=...` with `EventSource`.* Genuinely
tempting, because `EventSource` handles reconnection for free. Rejected: it puts the user's
message in a URL, which lands in logs and browser history, and reconnect semantics are
wrong for a turn — a re-run is a second turn, not a resumption.

### 2. The `on_event` callback bridges into an `asyncio.Queue`

`run_turn` pushes synchronously; SSE pulls asynchronously. The adapter is a queue plus a
sentinel marking the end of the turn:

```
tracer.event() -> listener(record) -> queue.put_nowait(record)
                                             |
                      async for <- generator -+-> "data: {...}"
```

`put_nowait` on an unbounded queue never blocks and never awaits, so the listener stays a
plain sync callable and no graph node ever waits on a slow browser.

*Alternative considered — changing `on_event` to accept an async callable.* Rejected: it
would touch every emit site in `tracing.py`, and it exports one gateway's transport problem
into the tracing layer. The queue keeps the mismatch where it belongs.

*Trade-off accepted:* unbounded means a client that disappears mid-turn leaves records
accumulating until the turn ends. A turn is bounded by `MIKU_MAX_REQUESTS_PER_TURN`, so the
ceiling is tens of records. Not worth a bound.

### 3. `tool_call` is emitted by the tools node, through the tracer

Not by `session.py`. The tools node at `nodes.py:223` already holds the span that should
parent the event, and emitting there makes the gateway path *shorter*:

```
session.py:   - on_event("tool_call", {...})      deleted
              + tool_calls.append(call)           kept (TurnResult, evals)
nodes.py:     + tracer.event("tool_call", parent=span, tool=..., args=...)
```

This is also the decision that closes the redaction hole described in Context, since
everything routed through `tracer.event` passes `_redact` before either the file or the
listener sees it.

*Alternative considered — leave the event where it is and have the web gateway patch a
parent onto it.* Rejected twice over: it leaves the redaction hole open, and it puts
tree-assembly knowledge inside a gateway, which is the exact thing this phase exists to
test the absence of.

*Alternative considered — render it in the UI as a flat, unparented line.* Rejected for the
same redaction reason, and because a fan-out is where parentage matters most.

*Costs, measured 2026-08-26 after the move.* One extra record per tool call, and nothing
else moves:

| turn shape | before | after |
|---|---|---|
| plain, no tool | 2 | 2 |
| one tool call | 5 | 6 |
| fan-out, five branches | 13 | 14 |

A turn that calls no tool is untouched, which the estimate in the proposal did not
distinguish. And trace files begin carrying tool arguments —
event titles, fact text — which they do not today, because `tool_event` at `nodes.py:242`
records only the tool name and whether it succeeded. Redaction masks configured secrets,
not user content. `.miku/` is gitignored. This is a real change in what the file contains,
accepted deliberately rather than overlooked.

*Timing nuance.* Today the event fires when the model *emits* the call; afterwards it fires
when the tools node is *about to run* it. Microseconds apart, and the CLI's
`print_tool_activity` keys on `kind` and on payload fields that survive, so it is expected
to need no change. Expected, not verified — a test pins it.

### 4. Read-only introspection lives in `miku/runtime/inspect.py`

Every cockpit tab is a read. Rather than widen "a gateway reads no memory" into something
untrue, the reading moves behind a runtime module that both gateways may call, and the
gateway goes back to moving data.

Two rules, or it becomes a junk drawer:

- **Read-only by construction.** No writes, no model calls, no session opening.
- **Environment-free.** `config.py` is the only module that reads the environment (plus
  `providers.py` for the key). `inspect.py` receives `Settings` and a store handle as
  arguments.

Traces need no new code: `ops/traceview.py` is already dependency-free and read-only, and
its module docstring says *"a Phase 3 dashboard wants exactly this"*. `inspect.py` calls it
rather than reimplementing it.

*Alternative considered — the gateway reads the store directly, and the `cli-gateway` spec
is amended to say "no memory reads on the turn path".* Rejected, but it was close. That
amendment is arguably the more honest reading of what the constraint always meant. It lost
because the introspection has two plausible consumers already — a `miku inspect` subcommand
is an obvious future — and a shared module is where that belongs regardless of how the
sentence is worded.

### 5. A separate module and a second console script, not a subcommand

```
[project.scripts]
miku     = "miku.__main__:main"
miku-web = "miku.gateway.web:run"
```

```
gateway/cli.py --+
                 +--> runtime/session.py::open_session
gateway/web.py --+
```

No import edge between the gateways. That absence is not tidiness — it is the measurement.
If `web.py` runs without importing anything from `cli.py`, the Phase 1 claim held; if it
needs something, that something is misplaced and the change records where.

*Alternative considered — `miku serve` as a subcommand, matching `miku consolidate`.*
Rejected: `consolidate` is a runtime operation the terminal gateway triggers, so it belongs
there. A web server is a *peer gateway*, and hanging it off the terminal gateway would make
the terminal import the very thing whose independence is under test.

### 6. `fastapi` and `uvicorn` under a `web` extra

```
uv sync --extra dev --extra web
```

*Alternative considered — core dependencies.* Rejected: a CLI-only install would drag in a
web stack, and the manifest would state that the web gateway is essential when it is
optional. The cost is one more flag in every documented `uv sync`, and 3b's evals skipping
when the extra is absent — the same skip mechanism the live cases already use for missing
credentials, so nothing new is built for it.

### 7. One `Session` for the app's lifetime, opened in the FastAPI lifespan

Matching the CLI, which opens one per process. `Deps` is session-lived and `TurnContext` is
turn-lived precisely so this works: `run_turn` clones a tracer and a budget per turn rather
than resetting shared ones, and `session.py:79-81` says so in as many words.

*What is not yet known* is whether the layer below survives it: one SQLite store handle and
one checkpointer handle, shared across concurrent turns. The CLI has one turn in flight at a
time and has never touched this. Two browser tabs would.

**This is probed first, before any endpoint is written** — two `run_turn` calls on distinct
`thread_id`s awaited concurrently against one session, asserting that both replies arrive,
that budgets do not pool, and that neither turn's trace records land under the other's turn
id. If it fails, the fix is a lock around store access rather than a session per request,
and it is better to know that on day one than after a frontend exists.

*Alternative considered — one session per request.* Rejected pre-emptively: it re-opens
SQLite handles per turn and discards the checkpointer's warm state, and it would hide a
concurrency defect rather than answer the question.

**Measured 2026-08-26, before any endpoint was written — the assumption held.** Two turns
overlapping on one session both reply, spend separate allowances (1 and 2 requests, not 3
and 3), and write no event under the other's turn id. No lock is needed and none was
added.

Getting a *trustworthy* result took three attempts, and the first two are the reason this
is recorded rather than just ticked off:

1. The turns did not overlap at all. `PromptModel` returns without ever awaiting, so a
   turn only yields at a real database await — and the first turn ran to completion
   before the second began. Seven trace records, one switch between them. Three green
   assertions, nothing tested.
2. Making the stub sleep produced overlap that was *likely* rather than certain, and
   counting context switches to prove it failed one run in eight. A flaky control is not
   a control.
3. The turns now rendezvous: neither first model call returns until both have been made,
   so both are provably in flight while holding the same handles. Failure to overlap is a
   timeout, not a coin flip. A fourth case checks the barrier itself, since a rendezvous
   satisfiable by one turn would silently un-test the other three.

**What this does not cover.** Two turns, one process, stubbed models. Not many turns, not
real provider latency, and not write contention — the booking turn writes a calendar row
while the other only reads. Under `sqlite-vec` and 2.5b's index the write path gets
heavier, and this result should not be quoted as covering that.

### 8. Tested through `httpx.ASGITransport`, with an injectable session

No live server, no port, no credentials — the stub model and frozen clock the deterministic
suite already uses, driven through the real ASGI app. This requires the app factory to
accept a `Session` rather than always opening its own in the lifespan.

*Alternative considered — spawn uvicorn on a port in a fixture.* Rejected: it adds startup
timing, port collisions, and flakiness to a suite whose whole premise is determinism.

Assertions follow the existing evaluator rule — **event shape, never rendered wording.** A
cockpit test asserts that a fan-out turn's stream carries five `generate` records sharing
one parent; it does not assert what any of them says, or how the HTML looks.

### 9. A hand-written static frontend

`StaticFiles` from `miku/gateway/static/`. Plain HTML, one stylesheet, ES modules, no
framework and no build step. The event-application logic ports from waku essentially
unchanged, because it only ever cared about the `{kind, ...}` shape.

*Alternative considered — a frontend toolchain (a bundler plus a framework).* Rejected in
the exploration doc already and reaffirmed here: legibility is the constraint this repo
optimises for, and a build step inside a uv-managed Python repo is the fastest way to lose
it. The cockpit is a handful of panels; it does not need a virtual DOM.

## Risks / Trade-offs

- **Shared SQLite handles fail under concurrent turns** -> Decision 7 probes it before any
  endpoint is written. Fallback is a lock around store access, not a session per request.
- **Trace files start carrying user content** -> Accepted and documented. `.miku/` is
  gitignored; redaction covers configured secrets only. Recorded as a known limit rather
  than mitigated, because masking user text would make the trace useless for the thing it
  exists for.
- **`inspect.py` becomes a junk drawer** -> Two rules stated in Decision 4, pinned by a test
  that it neither writes nor reads the environment.
- **The cockpit quietly becomes a second place agent logic lives** -> The whole point is the
  opposite, so it is asserted rather than assumed: a test that the web gateway assembles no
  prompts, calls no models, and executes no tools.
- **`cli.py` needs changes after all** -> Not a risk to mitigate; it is the measurement. If
  it does, the change records what and why, and the Phase 1 claim is downgraded honestly.
- **Trace files grow by roughly a fifth per turn** -> Accepted. Nothing rotates them today
  either; that remains an unbuilt concern at this scale.
- **An unbounded queue backs up behind a dead client** -> Bounded in practice by the turn's
  own request allowance. Tens of records, not thousands.

## Migration Plan

Nothing to migrate. No stored data changes shape, no configuration key is removed, and the
CLI path is unchanged except that one event now arrives from a different emitter under the
same `kind`. `uv sync --extra web` is additive; without it, `miku` works exactly as before
and `miku-web` reports the missing extra as a sentence rather than an `ImportError`
traceback.

Rollback is deleting two new modules and reverting one event's emitter.

## Open Questions

All three were settled during implementation. Kept here with their answers rather than
deleted, because the reasoning is the useful part.

- ~~**Which tabs ship in this change.**~~ **Five: live turn, traces, config, tools,
  memory.** Once `inspect.py` existed each remaining tab was a `fetch` and a table, so the
  line between "justifies the phase" and "cheap" turned out not to be worth drawing. The
  sixth pillar the exploration doc lists — *loop* — did not ship: its content would be the
  graph's shape, which is static, already legible in `build.py`, and not something a
  cockpit tells you anything new about.
- ~~**Whether the live-turn panel and the trace tab share a renderer.**~~ **They share
  one.** The two differ only in how records arrive — one at a time off an open stream, or
  all at once from a finished turn — and not at all in what a record means. The server
  hands a finished turn back already nested, so `flatten` turns it into the flat sequence
  the live panel produces naturally, and one `renderTree` serves both. A second renderer
  would have been a second place for the parent-link logic to be wrong.
- ~~**How the cockpit shows a turn that errors mid-stream.**~~ **As a
  `{"kind": "error", ...}` event, the last one in the stream.** SSE has no status code once
  the headers are sent, so this is the only place left to report it. The response is still
  200, which is initially uncomfortable and correct: the *stream* succeeded, and what
  failed is inside it.

## Verification note

`buildTree` — the port of waku's event application — is exercised directly under node,
against branches delivered out of order and against a record whose parent has not arrived
yet. Node is a system tool, never a project dependency: the cases skip when it is absent,
the same way live cases skip without credentials, and task 6.8 asserts no package manifest,
lockfile, or bundler config exists.

This replaces "verify by eye" in task 6.4, and is stronger than it: five interleaved
branches landing under the wrong parent is not something looking at a page reliably
catches. What remains genuinely unverified is *visual* — nobody has loaded the page in a
browser. Layout, contrast, and whether the thing is pleasant to watch are unmeasured.
