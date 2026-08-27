## Context

The cockpit renders one turn at a time. `app.js:438` holds `const live = { records: [], threadId:
null }`, `app.js:457` clears the record list on every send, and the reply element is overwritten
rather than appended to. A refresh loses the thread; a second tab is a second conversation.

The data has been there since Phase 1. Probed live against `.miku/state.db` before any of this was
designed:

```
272 checkpoint tuples  ->  15 threads      (4 to 56 checkpoints per thread)
```

And one thread's stored messages, read back verbatim:

```
HumanMessage  "Beside Naruto, I also like Detective Conan and Dragon Ball"
AIMessage     content=''   tool_calls=2
ToolMessage   "Remembered: Dang likes Detective Conan"
ToolMessage   "Remembered: Dang likes Dragon Ball"
AIMessage     "Got it. I've added those to your preferences."
```

Everything below that could be measured was measured on that database rather than reasoned about.

Three constraints frame the work. A gateway moves data and never reads a source. `inspect.py` is
read-only and environment-free, and both properties are pinned by tests. The frontend has no build
step, enforced by two cases.

## Goals / Non-Goals

**Goals:**

- A conversation screen: list, transcript, resume, remove, survive a reload.
- One read path, shared by both gateways.
- No change to `run_turn`, the graph, the nodes, the tracer, or any event shape.
- No new stored field, table, or dependency.
- Make the cost of a long conversation visible.

**Non-Goals:**

- Token streaming. Deferred by decision, see Decision 4.
- Renaming conversations, undoing a removal, or making a removal reach traces and facts.
- Trimming or summarising history. This change measures it; a later one acts.
- Markdown, search, model-written titles, indexing the checkpoint table.

## Decisions

### 1. The read path lives in `inspect.py`, not in the gateway

**Chosen:** `thread_list` and `conversation_view` join `config_view`, `tools_view`, `memory_view`
and `turn_view` in the inspection surface. The web gateway calls them; so does the CLI.

**Rejected: let `web.py` query the checkpointer.** It is fifteen lines shorter and it breaks the
one rule the gateway layer has. The same argument was made and rejected in Phase 3b for the memory
tab, on the grounds that the reading had two plausible consumers. It has two consumers again, and
this time the second one is built in the same change — `miku threads` costs a subcommand precisely
because the reading is not in `web.py`.

`inspect.py` must stay read-only and environment-free. Reading checkpointed state does not violate
either: the existing prohibition is on *modifying* checkpointed state, and the handle arrives as an
argument like the store handle does.

### 2. `Session` gains three accessors, and Phase 3b's measurement is spent

**Chosen:** `Session.checkpointer`, `Session.tools`, `Session.store`. `Session` holds the
checkpointer, which it currently does not — `open_session` keeps it inside its `async with` and
passes it only to `build_graph`.

Phase 3b recorded, deliberately, that the web gateway reached past `open_session` twice
(`deps.tools`, `deps.store`), and refused to add accessors because *the count is the measurement
the phase existed to take*. This change would have made it three via
`session.graph.checkpointer`, which is worse than either: a number nobody chose.

**Rejected: add only `checkpointer`.** It was the literal instruction and it is the wrong shape.
One handle through a door and two through the window reads, to the next person, as though the
other two were deliberately excluded. The measurement is spent either way; spend it once.

**Rejected: keep reaching, record three.** The repo's own rule says a seam that forces call sites
to be edited is in the wrong place. Three is the count at which that stops being a measurement and
starts being a habit.

### 3. The transcript is read from the server

**Chosen:** the page fetches the conversation. The client accumulates nothing across turns.

**Rejected: accumulate on the client.** Zero backend, and it is not a conversation screen — it is
the reply box made taller. A refresh blanks it, a second tab disagrees with the first, and a
conversation started in the terminal is invisible in the browser. All three failures are the exact
thing the phase exists to fix.

### 4. `run_turn` is not touched, so there is no token streaming

**Chosen:** the reply continues to arrive as one SSE event at the end of the turn.

**Rejected: stream tokens.** It needs `stream_mode="messages"` inside `run_turn`, which is the one
function the CLI, the web gateway, and every eval case share, and `TurnResult` is what every
evaluator asserts on. The blast radius is the whole repo for a cosmetic gain.

It is also less needed here than it would be elsewhere: Phase 3c put a diagram and a live event
tree above the composer, so the wait is already narrated. A chat window with nothing to watch needs
streaming; this one has the most detailed progress display in the repo.

### 5. Listing groups checkpoints, and the scan is recorded with its number

**Chosen:** enumerate with `alist(None)` and group by `thread_id`, newest activity first.

**Measured:** that reads 272 rows to produce 15 entries — roughly 18:1 — over a 2.5MB database.
Instant today.

**Rejected: an index or a threads table.** It would be the first schema this project adds on top of
the checkpointer's own, for a scan that costs nothing at the measured size. Recorded as a known
limit *with the ratio*, so the phase that has to care can tell when the number moved.

The measurement also corrected the design: a first pass assumed `alist` yields one row per thread.
It does not — it yields every checkpoint, and a naive listing would have shown `work` fifty-six
times.

### 6. The title is the first thing the user said

**Chosen:** derive it, truncated, at read time. No stored field.

**Rejected: a model-generated title.** A variable-latency, variable-cost model call attached to an
interaction that did not ask for one. Consolidation is forbidden from doing exactly this, for
exactly this reason, and a sidebar is a weaker justification than memory hygiene was.

**Rejected: a stored title field.** It would make `checkpointer.py`'s standing claim — that a
sidebar "needs no new data model" — false, to save a `[:60]`.

### 7. The transcript filters, and the filtering is the interesting part

**Measured**, from the shapes above:

| Stored | Shown as |
|---|---|
| `HumanMessage` | a user exchange |
| `AIMessage` with content | an assistant exchange |
| `AIMessage`, empty, carrying `tool_calls` | **nothing** |
| `ToolMessage` | a tool line, distinct from both |

The empty `AIMessage` is not a stylistic problem. It is a real stored message that says nothing,
and rendering it produces a blank bubble corresponding to no moment the user experienced.

The `ToolMessage` decision goes the other way, and it is the one genuine discovery of the probe:
`"Created: Buy ticket to go home on 2026-08-28 at 15:00"` is a sentence. This project's tools
return prose, not serialised data, so showing tool activity is a filtering decision rather than a
formatting one — no parsing, no templating, no reformatting.

**Rejected: hide tool activity entirely.** Cleaner, and it throws away the thing that distinguishes
this from any chat window. The transcript would say Miku replied and not that she did anything.

**Rejected: render tool calls with their arguments.** That is the trace view, which already exists,
is already a tree, and is already one click away by Decision 10.

### 8. `conversation_view` returns exchanges, never checkpoints

**Chosen:** a list of `{role, text}` plus a tool marker.

**Rejected: return the raw messages or the checkpoint tuple.** It would bind the browser, the
terminal and every test to LangGraph's persistence shape, which is a library's internal format that
changes on its own schedule. The gateway rule is about sources; this is the same rule about formats.

### 9. The conversation identifier goes in the URL

**Chosen:** `#<thread_id>`, so a reload continues the conversation and a link points at one.

**Rejected: `localStorage`.** It survives a reload and cannot be linked, shared, or opened twice
side by side, and it introduces per-browser state to a page whose entire model is that the server
holds the truth.

### 10. Trace links exist where a turn identifier exists, and nowhere else

`turn_id` is reported by `TurnResult` and carried on the `reply` SSE event. It is **not** in
checkpointed state — verified, the stored messages carry no such field.

**Chosen:** a reply produced in this session links to its trace. A reply read back from storage
does not.

**Rejected: reconcile conversations against trace files to recover the identifier.** Trace files
are per-day, rotate under nothing, and are gitignored scratch. Joining two stores of different
lifetimes to recover a link is a large amount of machinery for a link, and it would silently break
whenever a trace file was cleaned up.

**Rejected: store `turn_id` in the conversation state.** It changes the graph's state schema — a
core-loop change — to serve a UI affordance.

### 11. The thread list shows a message count, because history is unbounded

**Measured:** `nodes.py:187` is `[SystemMessage(content=state["system"]), *state["messages"]]`.
Grepping `miku/` for `trim_messages`, `RemoveMessage` and any summarisation found **nothing**. One
existing thread already holds 19 messages. Combined with the standing limit that there is no prompt
caching, the cost of a conversation grows quadratically in its length.

This change does not cause it. It makes it *reachable*: the terminal encourages fresh threads, and
a sidebar of resumable conversations encourages the opposite.

**Chosen:** show the count. One number, already computed by the listing, no new work.

**Rejected: add trimming now.** It is a decision about what the agent remembers within a
conversation — behaviour, not presentation — and deciding it inside a UI change is how a memory
policy gets chosen by accident. This project's pattern is to measure, expose, and defer.

**Rejected: say nothing.** An invisible cost that grows with the feature being introduced is the
kind of thing that gets discovered from a bill.

### 12. Text is rendered as text

**Chosen:** `white-space: pre-wrap` through the existing `escape()`.

**Rejected: markdown.** It needs a library, which means a build step or a CDN. Both are forbidden
by existing tests, and a new dependency needs discussion. Newlines are the 90% case.

### 13. Pure rendering functions, tested under node

The transcript renderer follows `paint`, `buildTree` and `elapsed`: a pure function of its input,
exported, exercised under node with fixtures taken from the shapes measured above rather than
invented. Its appearance stays unverifiable by machine, like the rest of the frontend.

### 14. Tool activity is always shown

**Chosen:** tool lines render inline between bubbles, with no toggle and no fold.

**Rejected: fold them behind "2 tool calls".** It solves crowding, which has not been observed —
nothing has been opened in a browser yet — and it costs the thing that distinguishes this from a
chat window. Miku says "I've added those to your preferences"; the tool line is the evidence that
the sentence is not merely plausible. This surface exists to show the work.

**Rejected: a global toggle.** Per-viewer UI state for a problem nobody has reported. If the
browser check says it crowds, folding is a one-line change to a pure render function.

### 15. Removal is a session method, and it is honest about reaching one third

An earlier draft of this design deferred removal on the grounds that `inspect.py` is read-only and
a write "needs a different path". That reasoning was wrong, and the correction is worth recording
because the mistake is the kind that produces an unnecessary module.

Removal does not go through `inspect.py` at all, and needs no write surface beside it. It goes
through the session, in exactly the shape that already exists:

    session.run_turn(...)              gateway calls, session writes   (since Phase 1)
    session.delete_conversation(...)   gateway calls, session writes   (new)

The gateway rule is that a gateway reads no source directly. Calling a session method is not
reading a source, and causing a write through the session is what running a turn has always done.

**Measured, and this is the part that matters.** Three stores hold a conversation's traces, and
they are keyed three different ways:

| Store | Keyed by | Removal reaches it |
|---|---|---|
| Checkpointer | `thread_id` | **yes** — `adelete_thread(thread_id)`, one call |
| Store (facts) | the **user**, via `facts_namespace(settings)` | no |
| Trace files | `turn_id`; a trace line carries no `thread_id` at all | no |

Verified by reading a real trace file: every line carries `turn_id`, `span`, `parent`, `node`,
`ts`, and nothing that names a thread. This is the same missing link as Decision 10, surfacing in
an unrelated place — there is no route from a conversation to the turns that made it.

So removal deletes one third of what a user would reasonably call "this conversation". A fact
remembered during it survives, and Miku will still know it.

**Chosen:** state the boundary in the interface. The action is named *remove conversation*, never
*delete* and never *forget*, and its confirmation names all three outcomes with a route to the
memory tab. The limit is recorded with the reason — three keys, no join — rather than as an
apology.

**Rejected: make removal complete.** It requires a `thread_id` -> `turn_id` link that does not
exist, plus per-thread fact ownership, which would mean re-namespacing the store. That is a memory
data-model change hiding inside a delete button.

**Rejected: leave removal out, as the first draft did.** This change lists every abandoned thread
by its identifier — a decision taken deliberately — so it manufactures the clutter. Shipping the
mess without the broom would leave the next phase repairing this one.

**Rejected: soft-delete with an undo.** A trash tier is a data-model decision, and
`adelete_thread` has no soft mode. An irreversible action that says so beats a reversible one
invented for the occasion.

### 16. The composer moves into the conversation, and stops being global

Raised after the screen was built, which is the right time: it is a question you
cannot answer from a wireframe.

The composer sat above the tab strip, so it belonged to no pane. You typed at the top of
the page and the answer appeared in the middle of it.

**Chosen:** the form lives inside `pane-live`, directly under the transcript, `position:
sticky; bottom: 0`. Sticky rather than merely last, because a fan-out turn writes about
fourteen trace rows below the transcript and a composer at the true foot of the document is
a composer you scroll to reach.

The cost is real and worth naming: the composer is now hidden on the traces, config, tools
and memory tabs. That reads as a loss and is not one -- `sendTurn` has called
`showPane("live")` since Phase 3b, so a turn started from the memory tab always yanked you
here anyway. The affordance that disappeared was never available.

**Rejected: a bar pinned to the bottom of the viewport, present on every tab.** It is what a
chat application does, and this is not one. It would take a fixed slice of a short screen
permanently, to preserve an action that switches away from what you were looking at the
moment you use it.

**Rejected: the true foot of the pane, below the event tree.** The literal reading of "under
the chat section", and the least usable: the tree is longest exactly on the turns you most
want to follow up on.

**Also removed: the `conversation <id>` label.** The sidebar marks which conversation is
open and the transcript shows what is in it. A third line naming it was the same fact told a
third time. The empty state carries the direction instead -- "nothing said yet. type below
to start." -- which points at the thing that is now directly below it, and would have been a
lie before this decision.

### 17. The conversation scrolls itself, and so does the sidebar

The consequence of Decision 16, found by using it: with the composer under the transcript,
a growing conversation pushed the composer down with it. The more you had said, the further
you had to travel to say the next thing -- and the diagram, which is meant to sit at the top
of the pane, went with it.

**Chosen:** the transcript and the conversation list each carry a `max-height` in `vh` and
scroll inside themselves. The page's height stops being a function of how much has been said
or how many conversations are held.

Both numbers are viewport-relative rather than a message count, because the thing being
bounded is the window and not the data. `paintTranscript` sets `scrollTop` on the transcript
rather than calling `scrollIntoView`, which would have walked every scrollable ancestor and
taken the page along -- the exact jump this removes.

**Rejected: a full-height flex shell -- `body` at `100vh`, one scroll region per column.**
It is the layout a chat application has, and it would have meant reworking the header, the
tab strip, the diagram and the event tree to reach a bounded conversation. Two `max-height`
rules reach the same place without touching anything that already works.

**Rejected: capping the event tree too.** It was not asked for and it should not be. The tree
is the thing you scroll the page *for*; bounding it would put a second scrollbar inside a
region whose whole job is to be read at length.

## Risks / Trade-offs

**Listing scans every checkpoint.** → 272 rows for 15 conversations, measured. Recorded with the
ratio so the next phase can tell whether it moved. An index is a schema addition and is not worth
one today.

**Conversation history grows without bound and is re-sent every turn.** → The largest real risk in
this change, and it is inherited rather than created. Made visible through the message count;
fixing it is a memory-behaviour decision that deserves its own phase.

**Phase 3b's reach measurement is spent.** → Deliberate and recorded here. The alternative was to
let it drift to three, which reports a number nobody chose.

**The frontend's unverifiable surface grows again.** → It was already the largest in the repo after
Phase 3c. Everything pure is tested under node; whether it reads well is still a human's call, and
task 7.4 from Phase 3c is *still* unticked.

**Concurrency is measured at exactly two turns, stubbed, one process.** → A conversation sidebar
invites more tabs against one SQLite handle. Decision 4 keeps `run_turn` untouched, so this change
adds no new concurrency path — but the standing limit should be re-read, not re-copied.

**No authentication, loopback only.** → Unchanged, but a screen holding conversation history *looks*
far more like something worth exposing than a single reply box did. The warning gets louder wording,
not new machinery.

**Removal is irreversible and reaches one store of three.** → Named and confirmed as what it is,
with the other two stores pointed at rather than silently left behind. The alternative was either
no removal at all or a delete button that quietly lies.

**Trace links are inconsistent between live and stored replies.** → Accepted per Decision 10. A
missing affordance is better than a broken one; the alternative was joining two stores with
different lifetimes.

## Open Questions

- ~~Are the transcript's tool lines toggleable?~~ **Closed.** Always shown; see Decision 14.
- ~~Sidebar or a sixth tab?~~ **Closed.** A sidebar. The tab strip is already five wide.
- ~~How are conversations with no messages listed?~~ **Closed.** By identifier, and that decision
  is what put removal in this phase; see Decision 15.
- Does the removal confirmation need to show what a conversation holds before it goes, or is the
  message count in the list enough? The count is assumed sufficient. Only a browser check settles
  it.
- Should `miku threads` be able to remove one too? The listing is shared; the write is not. Left
  out until someone wants it, because a destructive terminal flag deserves its own argument.
