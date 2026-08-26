## 1. Probe concurrency before building on it

Design decision 7 rests on an untested assumption: one SQLite store handle and one
checkpointer handle shared across concurrent turns. Nothing downstream is worth writing
until this is known.

- [x] 1.1 Add an eval that runs two turns concurrently on one session with distinct thread ids, asserting both replies arrive
- [x] 1.2 Extend it to assert each turn's request count reflects only its own model requests
- [x] 1.3 Extend it to assert no event produced by one turn carries the other turn's identifier
- [x] 1.4 Record the outcome. If it fails, add a lock around store access and say so in design.md; do not switch to a session per request
- [x] 1.5 Prove the two turns actually overlap, rather than passing because they ran one after the other

## 2. Move `tool_call` onto the tracer

- [x] 2.1 Emit the tool-call-requested event from the tools node in `miku/graph/nodes.py`, parented to the tools span, carrying tool name and arguments
- [x] 2.2 Delete the synthesised `on_event("tool_call", ...)` in `miku/runtime/session.py`, keeping the `tool_calls` collection that `TurnResult` and the evals depend on
- [x] 2.3 Update `miku/ops/tracing.py`'s listener docstring — the claim it makes becomes true here rather than aspirational
- [x] 2.4 Test: a turn that calls a tool writes a requested event recording the arguments, parented within the same turn
- [x] 2.5 Test: an observer attached during a turn whose payload contains the provider key receives the redaction marker, never the key
- [x] 2.6 Test: the CLI's `print_tool_activity` still prints tool activity unchanged — this pins the claim that `cli.py` needs no edit
- [x] 2.7 Re-measure trace volume for a plain turn and a fan-out turn; record both numbers

## 3. Read-only inspection surface

- [x] 3.1 Create `miku/runtime/inspect.py` taking `Settings` and a store handle as arguments — no environment reads, no session, no model
- [x] 3.2 Report the active configuration: resolved provider, the model per role, the configured limits
- [x] 3.3 Report the registered tools by name with the descriptions the model is given
- [x] 3.4 Report live facts for the active user, excluding superseded rows
- [x] 3.5 Report a recorded turn as a tree by delegating to `ops/traceview.py` rather than reimplementing it
- [x] 3.6 Make every function return empty data for absent state — no facts, no trace file, no matching turn — rather than raising
- [x] 3.7 Test: every inspection function leaves stored facts identical and records no model request
- [x] 3.8 Test: the module's source reads no environment variable
- [x] 3.9 Test: a trace file containing one unparseable line still reports its remaining records

## 4. Package the web extra

- [x] 4.1 Add `[project.optional-dependencies] web = ["fastapi", "uvicorn"]` to `pyproject.toml`
- [x] 4.2 Add the `miku-web` console script pointing at `miku.gateway.web:run`
- [x] 4.3 Make a launch without the extra print one sentence naming what to install and exit non-zero, with no traceback
- [x] 4.4 Make the web evals skip when the extra is absent, using the mechanism the live cases already use for missing credentials

## 5. The server

- [x] 5.1 Create `miku/gateway/web.py` with an app factory that accepts an injected `Session`, and a lifespan that opens one when none is given
- [x] 5.2 Add `run()` binding loopback by default
- [x] 5.3 Implement `POST /api/turn`: bridge `on_event` into an `asyncio.Queue` and stream `{kind, ...}` records as server-sent events over one connection
- [x] 5.4 End every stream with a terminating event carrying reply, turn id, and request count
- [x] 5.5 Deliver a mid-turn failure as an event and close the connection, since SSE has no status code after the headers
- [x] 5.6 Add the read endpoints backing the tabs, each calling `inspect.py` and never the store
- [x] 5.7 Test through `httpx.ASGITransport` with a stubbed model and frozen clock: no port bound, no credentials
- [x] 5.8 Test: a plain turn streams at least one progress event before its terminating event, which carries reply, turn id, and request count
- [x] 5.9 Test: a fan-out turn streams five branch records sharing one parent, each with a distinct branch id — assert shape, never wording
- [x] 5.10 Test: a client disconnecting mid-turn leaves stored state and trace records identical to an uninterrupted turn
- [x] 5.11 Test: two concurrent posts on distinct threads both reply, with separate request counts and no cross-attributed events
- [x] 5.12 Test: importing the web gateway imports no module from the terminal gateway
- [x] 5.13 Test: the web gateway's source assembles no prompt, constructs no model client, and invokes no model

## 6. The cockpit frontend

- [x] 6.1 Add `miku/gateway/static/` served by `StaticFiles`, confirming hatch packages it into the wheel
- [x] 6.2 Write the page shell and tab bar — plain HTML, one stylesheet, ES modules, no framework
- [x] 6.3 Port `applyEvent()`: place each record under its `parent`, treating a record whose parent is unknown as a root rather than dropping it
- [x] 6.4 Build the live-turn panel and verify by eye that a real fan-out renders as five interleaved branches under one step
- [x] 6.5 Build the traces tab reading a past turn back
- [x] 6.6 Decide whether the live panel and the traces tab share one renderer, and record the answer in design.md's open questions
- [x] 6.7 Add whichever remaining tabs the read endpoints make cheap — config, tools, memory — and record which shipped
- [x] 6.8 Confirm the repository still declares no JavaScript package manifest, lockfile, or bundler configuration

## 7. Close the phase

- [x] 7.1 Count how many times `web.py` had to reach past `open_session`, and record the number in the exploration doc whatever it is — this is what 3b was for
- [x] 7.2 Record whether `cli.py` needed changes. If it did, downgrade the Phase 1 gateway claim explicitly rather than quietly
- [x] 7.3 Update `CLAUDE.md`: the architecture map, the `web` extra and its `uv sync` line, and the new commands
- [x] 7.4 Add to Known limits: trace files now carry user content; the conversation screen is deferred; concurrency is measured only at two turns
- [x] 7.5 Run `uv run ruff check .` clean and the full suite green, both with and without the `web` extra installed
