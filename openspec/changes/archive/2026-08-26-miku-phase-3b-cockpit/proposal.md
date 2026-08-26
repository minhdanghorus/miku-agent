## Why

Phase 1 built the CLI behind a deliberate constraint: a gateway moves text and nothing
else — no prompt assembly, no model calls, no tool execution, no memory reads —
*specifically* so that a second gateway would be cheap. That claim has never been cashed.
Phase 2 then built span/parent tracing, and a fan-out turn now emits ~13 causally linked
records with nothing that reads them back but a test.

3b pays both debts at once with the smallest surface that can: a local web cockpit that
watches a turn think. The UI is the visible half; the load-bearing half is the answer to
one question — **does a second gateway reach past `open_session` for anything?** If it
does, the seam is in the wrong place, and it is cheaper to learn that now than under
another phase of weight.

## What Changes

- **A second gateway, `miku/gateway/web.py`.** FastAPI + uvicorn, its own console script
  `miku-web`. No import edge from `cli.py`: the two gateways are peers and neither knows
  the other exists.
- **A turn is streamable over HTTP.** `POST /api/turn` returns `text/event-stream`, one
  connection per turn, fed by bridging the existing `on_event` callback into an
  `asyncio.Queue`. No new event vocabulary — the browser consumes the `{kind, ...}`
  records the sink already writes.
- **`tool_call` moves onto the tracer.** Today `session.py` synthesises that event and
  hands it to the listener directly, bypassing the sink. Two consequences, both wanted:
  the event gains `span`/`parent`/`turn_id` so a live UI can place it in the tree, and it
  starts passing through redaction. Tool arguments consequently begin appearing in trace
  files, which they do not today.
- **`miku/runtime/inspect.py`, read-only.** Where every cockpit tab gets its data —
  config, tools, memory, loop shape, traces. Read-only and environment-free by
  construction: it takes `Settings` and a store handle, never opens a session, never calls
  a model, never writes.
- **A static frontend under `miku/gateway/static/`.** Hand-written HTML/CSS/JS served by
  the same server. No npm, no bundler, no build step.
- **A `web` optional-dependency extra.** `fastapi` and `uvicorn` install via
  `uv sync --extra web`, so a CLI-only install stays free of a web stack and the
  optionality is stated in the manifest rather than assumed.

## Capabilities

### New Capabilities

- `web-gateway`: the local web server — its entry point, the streaming turn endpoint, the
  read-only endpoints backing the cockpit, static asset serving, and the same
  "moves data only" constraint the CLI is held to.
- `runtime-inspection`: the read-only view of a running system that any gateway may
  render — what is configured, which tools exist, what memory holds, what a past turn did.

### Modified Capabilities

- `deterministic-evals`: the suite gains a second and third skip axis — an optional
  dependency and a system tool, alongside the existing credential gate — plus rules for how
  web-gateway and concurrency cases must assert. Added during the archive assessment: the
  delta was written before those cases existed and did not describe them.
- `agent-tracing`: a tool call is now traced when it is *requested*, carrying its
  arguments, in addition to the existing event recording whether it *succeeded*. And the
  sink's guarantee is extended to the observation seam: everything a gateway is shown has
  already passed redaction, which is today asserted in a docstring and false in practice.

## Impact

**Code.** `miku/gateway/web.py` (new), `miku/gateway/static/` (new),
`miku/runtime/inspect.py` (new), `miku/graph/nodes.py` (emit the tool-intent event),
`miku/runtime/session.py` (delete the synthesised `on_event` call; keep collecting
`tool_calls` for `TurnResult`), `miku/ops/tracing.py` (the listener guarantee).
`miku/gateway/cli.py` is expected to need no change — its `print_tool_activity` already
keys on `kind == "tool_call"`, and that kind survives. Whether that expectation holds is
itself part of what this change measures.

**Cost.** One extra trace line per tool call: a plain scheduling turn moves ~5 -> ~6
records, a fan-out turn ~13 -> ~14. Trace files begin carrying user-supplied text (event
titles, fact contents) that they do not carry today; `.miku/` is gitignored and redaction
covers configured secrets only.

**Dependencies.** `fastapi` and `uvicorn`, under a new `web` extra. Nothing else —
`sse-starlette` is deliberately not taken; starlette's `StreamingResponse` is sufficient.
Evals for this capability skip when the extra is absent, the same mechanism the live cases
already use for missing credentials.

**Unprobed.** Concurrency. `Deps`/`TurnContext` split the session-lived from the
turn-lived correctly, and tracer and budget are cloned per turn — but one SQLite store
handle and one checkpointer handle are shared, and a single-turn-at-a-time CLI has never
touched that. Two browser tabs would. This is measured early in the change rather than
discovered late.

## Out of Scope

- **The conversation screen** — the ChatGPT-style sidebar and transcript view. Deferred to
  a later phase; `thread_id` is already the key it needs, so waiting costs nothing.
- **Authentication, multi-user, and remote exposure.** The server binds loopback and
  serves one local user. Phase 4 territory if ever.
- **Fact selection and retrieval** — Phase 2.5b, unchanged by this and unblocked by it.
- **Any change to how deterministic evaluators assert.** They continue to read tool calls
  and stored rows, never reply wording; the cockpit adds event-shape assertions, not
  prose-reading ones.
