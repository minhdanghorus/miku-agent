## Why

The cockpit watches one turn and forgets it. `app.js` holds `thread_id` in a page variable, wipes
the reply box on every send, and never reads a conversation back — so a refresh starts a new
thread, a second tab disagrees with the first, and nothing in the browser can resume `work`.

The history already exists. `.miku/state.db` holds 272 checkpoints across 15 threads right now,
and `checkpointer.py` has said since Phase 1 that `thread_id` is "exactly the key a
conversation-list UI needs later, so the sidebar in a future phase needs no new data model". That
turned out to be true: this change adds no table, no column, and no field to any event.

What is missing is a *read path*. Nothing in the repo reads the checkpointer. The CLI resumes only
because LangGraph loads state on its own, not because any code asks it what a thread contains.

## What Changes

- **A new read surface for conversations.** `runtime/inspect.py` gains the ability to list threads
  and to report one thread's messages. It stays read-only and environment-free, as its two pinned
  tests require.
- **`Session` grows accessors.** `Session.checkpointer`, `Session.tools`, and `Session.store`.
  The web gateway reaches past `open_session` twice today (`deps.tools`, `deps.store`) — a count
  Phase 3b recorded deliberately as a measurement. This change spends that measurement rather
  than letting a conversation screen quietly make it three.
- **Two read endpoints.** `GET /api/threads` and `GET /api/threads/{thread_id}`. `POST /api/turn`
  is untouched: it already accepts `thread_id`, which is what a correctly placed seam looks like.
- **A conversation screen.** A thread list beside a transcript, resume by clicking, `thread_id` in
  the URL so a refresh survives, and a link from each reply to its turn in the traces pane.
- **`miku threads`.** The listing is shared, so the terminal gets it for the cost of a subcommand.
  This is the second time the peer-gateway rule pays out.
- **Removing a conversation.** A sidebar that lists every abandoned thread by its identifier
  creates the mess it then has to offer a way to clear. Removal deletes thread state and nothing
  else -- remembered facts and recorded traces survive it, because they are keyed differently --
  and the interface says so rather than implying it forgot.

Three behaviours were found by measuring the real database rather than assumed, and each shapes a
requirement:

- **A transcript must filter.** A turn that calls a tool stores an `AIMessage` with empty content
  and two `tool_calls`, then the `ToolMessage`s, then the real reply. Rendering the raw list
  produces empty bubbles.
- **Tool results are already prose.** `"Remembered: Dang likes Detective Conan"`,
  `"Created: Buy ticket to go home on 2026-08-28 at 15:00"`. They are shown as quiet inline lines
  between bubbles, which is what makes this a cockpit transcript rather than a chat window.
- **History grows without bound.** `nodes.py:187` sends `[SystemMessage, *state["messages"]]` every
  turn, with no `trim_messages`, no summarisation, and no prompt caching. One existing thread is
  already 19 messages. A conversation screen invites long threads, so the thread list shows each
  thread's message count — the cost is made visible in this change and fixed in a later one.

## Capabilities

### New Capabilities

- `conversation-history`: what a stored conversation is when read back — how threads are
  enumerated and ordered, how one is titled and sized, which stored messages are shown and which
  are structural, how a conversation is resumed and identified, and what removing one does and
  does not reach.

### Modified Capabilities

- `runtime-inspection`: the read-only surface now also reports conversations, and receives a
  checkpointer handle alongside its settings and store handle. Reading checkpointed state is
  explicitly permitted; modifying it remains forbidden.
- `web-gateway`: a session exposes what a gateway needs through named accessors rather than
  through its internals, conversation data is served through the inspection surface like every
  other view, and a conversation can be removed through the session the way a turn is run through
  it.
- `cli-gateway`: the terminal can list conversations.

## Impact

**Code**

| File | Change |
|---|---|
| `miku/runtime/inspect.py` | `ThreadView`, `thread_list`, `conversation_view` |
| `miku/runtime/session.py` | `checkpointer`, `tools`, `store` accessors; `Session` holds the checkpointer |
| `miku/gateway/web.py` | two GET endpoints, one DELETE; existing reaches move onto accessors |
| `miku/gateway/cli.py` | `miku threads` |
| `miku/gateway/static/*` | thread list, transcript, URL identity, trace links |
| `evals/deterministic/` | endpoint cases, inspection cases, node cases for the transcript |

**Not changed, deliberately:** `Session.run_turn`, the graph, the nodes, the tracer, the event
shape, the store, the checkpointer module. No new dependency.

`Session` gains one write method, `delete_conversation`. That is not a new kind of thing: a gateway
already causes a write by calling `Session.run_turn`. The rule a gateway must not break is that it
reads no source directly, and calling a session method breaks none of it.

**Out of scope, and which phase owns it**

| Deferred | Why, and where it goes |
|---|---|
| Token-by-token streaming | Requires `stream_mode="messages"` inside `run_turn`, the one function the CLI, the web gateway, and every eval share. Its own phase. |
| Renaming a conversation | Needs a stored title field, which would falsify `checkpointer.py`'s standing claim that a sidebar needs no new data model. Its own phase, if ever. |
| Removing a conversation's traces or its remembered facts | There is no `thread_id` in either store, verified. Making removal complete means building a link that does not exist; the boundary is stated instead of blurred. |
| Undo for removal | Removal is one checkpointer call with no soft-delete. A trash tier is a data-model decision. |
| Message trimming or summarisation | A decision about memory behaviour, not about a screen. This change measures and displays the cost; a later one acts on it. |
| Markdown rendering | Needs a library, which means a build step or a CDN — both forbidden by existing tests, and a dependency needs discussion. `pre-wrap` is the answer for now. |
| Searching conversations | No measured need at 15 threads. |
| Model-generated thread titles | A variable-latency model call on a random interaction, which is the thing consolidation is forbidden to do. The first user message is the title. |
| Indexing the checkpoint table | Listing scans all checkpoints to group them (272 rows for 15 threads today). Recorded with its number; revisited when the number justifies it. |
