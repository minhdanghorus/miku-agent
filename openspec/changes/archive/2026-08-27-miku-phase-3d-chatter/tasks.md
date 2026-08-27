## 1. The session exposes what a gateway needs

- [x] 1.1 Hold the checkpointer on `Session`, passed by `open_session` alongside the graph.
- [x] 1.2 Add `Session.checkpointer`, `Session.tools`, `Session.store` accessors.
- [x] 1.3 Move the web gateway's two existing reaches (`session.deps.tools`, `session.deps.store`)
      onto the accessors.
- [x] 1.4 Case: each accessor reports the same handle the session was built with.
- [x] 1.5 Case: the web gateway's source contains no `.deps.` reach.
- [x] 1.6 Record in `design.md` that Phase 3b's reach measurement is spent here, not mislaid.

## 2. Reading conversations back

- [x] 2.1 `ThreadView` dataclass: `thread_id`, `title`, `message_count`, `updated_at`.
- [x] 2.2 `thread_list(checkpointer)` — group `alist(None)` by `thread_id`, newest activity
      first, one entry per conversation. **Deviation:** no `settings` parameter. Nothing in the
      listing needs one — `memory_view` takes it to build the user's namespace, and a
      checkpointer is keyed by thread alone — so carrying it for symmetry would be an unused
      argument in a repo that reads its own signatures.
- [x] 2.3 Derive the title from the first `HumanMessage`, truncated. No stored field, no model call.
- [x] 2.4 A conversation with no derivable title is still listed, identified by its thread id.
- [x] 2.5 `conversation_view(checkpointer, thread_id)` — ordered exchanges, not checkpoints.
- [x] 2.6 Filter: drop `AIMessage` with empty content; keep `AIMessage` with content as assistant;
      keep `ToolMessage` as a distinct tool entry, verbatim.
- [x] 2.7 Absence is data: unknown thread and empty database both read as empty, never an error.
- [x] 2.8 Case: a tool-calling turn yields user + assistant exchanges and no empty assistant entry,
      with the fixture built from the real stored shape recorded in `design.md`.
- [x] 2.9 Case: listing reports one entry per conversation, not one per checkpoint.
- [x] 2.10 Case: ordering is by last activity, newest first.
- [x] 2.11 Case: `message_count` equals the number of stored messages.
- [x] 2.12 Case: every conversation-reading function leaves persisted state unchanged and writes no
      checkpoint.
- [x] 2.13 Case: the existing environment-free pin still holds with the checkpointer handle added.
- [x] 2.14 Case: nothing in a conversation view exposes checkpoint or channel structure.

## 3. Serving conversations

- [x] 3.1 `GET /api/threads` through `inspect.thread_list`.
- [x] 3.2 `GET /api/threads/{thread_id}` through `inspect.conversation_view`.
- [x] 3.3 Confirm `POST /api/turn` needs no change, and say so in the case rather than assuming it.
- [x] 3.4 Case: both endpoints in-process, stubbed model, frozen clock, no port bound.
- [x] 3.5 Case: an unknown thread responds successfully with no exchanges.
- [x] 3.6 Case: two turns on one `thread_id` produce four exchanges in order through the endpoint.
- [x] 3.7 Case: the import-edge assertion between the two gateways still holds.

## 4. The conversation screen

- [x] 4.1 Thread list as a sidebar, not a sixth tab: title, message count, last activity, plus a
      "new conversation" control.
- [x] 4.2 A conversation with no messages is listed by its identifier rather than hidden.
- [x] 4.3 `renderTranscript(exchanges)` as an exported pure function, escaping through `escape()`.
- [x] 4.4 Assistant and user exchanges as bubbles; tool entries as quiet inline lines between them,
      always shown — no toggle, no fold.
- [x] 4.5 `white-space: pre-wrap`. No markdown, no library, no CDN.
- [x] 4.6 Selecting a conversation loads its transcript and points the composer at it.
- [x] 4.7 `thread_id` in the URL fragment; a reload restores the same conversation.
- [x] 4.8 A completed turn re-reads *this one conversation*, and does not append the reply
      locally. **Deviation, with the measurement behind it:** a `tool` trace record carries the
      tool's name and whether it worked, never the sentence it returned, so an appended
      transcript would be missing exactly the tool lines 4.4 exists to show, and would differ
      from the same conversation reloaded a second later. Putting that sentence on the event is
      an event-shape change this phase does not make. One GET, and Decision 3 holds: the server
      is the only account of what was said.
- [x] 4.9 Diagram keeps following the running turn and is not reset by the transcript.
- [x] 4.10 Trace link on replies produced in this session, using the `turn_id` already on the reply
      event; no link where none is known.
- [x] 4.11 Sidebar folds below 48rem, following the existing media query.
- [x] 4.12 Node case: a tool-calling conversation renders two bubbles and two tool lines, no empty
      bubble.
- [x] 4.13 Node case: escaping, empty conversation, exchange missing a field.
- [x] 4.14 Case: the no-build-step assertions still hold — no `package.json`, no `http://` in the
      page.

## 5. Removing a conversation

- [x] 5.1 `Session.delete_conversation(thread_id)` over `checkpointer.adelete_thread`. A session
      method — not an inspection function, and not a new write module. Same shape as `run_turn`.
- [x] 5.2 `DELETE /api/threads/{thread_id}` calling that method. An unknown id succeeds.
- [x] 5.3 Remove control in the sidebar, behind a confirmation that names all three outcomes:
      conversation gone, remembered facts kept, recorded traces kept.
- [x] 5.4 Label it "remove conversation" — never "delete", never "forget". No undo, and the
      confirmation says so.
- [x] 5.5 Removing the open conversation clears the transcript and the URL fragment.
- [x] 5.6 Case: removal drops it from the listing, and reading it afterwards is absent.
- [x] 5.7 Case: a fact remembered during a removed conversation is still live memory afterwards.
      This is the asymmetry the wording exists for — assert it rather than trusting the copy.
- [x] 5.8 Case: a removed conversation's turns are still reportable by their `turn_id`.
- [x] 5.9 Case: removing one conversation leaves every other one intact.
- [x] 5.10 Case: the gateway's removal endpoint calls the session, not the checkpointer.

## 6. The terminal keeps up

- [x] 6.1 `miku threads` through the same `inspect.thread_list`.
- [x] 6.2 Plain ASCII output; no conversations is a sentence and exit code 0.
- [x] 6.3 Case: the listing matches what the web endpoint reports for the same state.
- [x] 6.4 Case: `cli.py` queries no checkpointer directly.
- [x] 6.5 No removal flag in the terminal. A destructive flag deserves its own argument, and the
      listing is what was shared, not the write.

## 7. Recording what this cost

- [x] 7.1 `CLAUDE.md`: remove the "No conversation screen" limit; describe the new read path, the
      three accessors, and the one session write method in the architecture map.
- [x] 7.2 `CLAUDE.md` known limits: listing scans every checkpoint (272 rows for 15 conversations,
      measured); conversation history is unbounded and re-sent every turn (`nodes.py:187`, no
      trimming, no caching, one thread already at 19 messages); trace links exist only for turns
      from the current session; removal reaches thread state alone — facts are keyed by user and
      traces by turn, with no `thread_id` in either, so two thirds of a conversation survive it.
- [x] 7.3 `CLAUDE.md`: strengthen the loopback/no-authentication wording now that the cockpit holds
      conversation history and can destroy it.
- [x] 7.4 Exploration doc: record the live probe — 272 checkpoints across 15 threads, the stored
      message shapes, the absence of any trimming, and the three-key asymmetry removal runs into —
      and update the roadmap.
- [x] 7.5 `uv run pytest` clean, `uv run ruff check .` clean.
- [ ] 7.6 Open the cockpit in a browser: list conversations, resume one, send a turn, follow a trace
      link, and remove a conversation. The check no test replaces. Phase 3c's equivalent is still
      unticked; do not tick this one on its behalf.
      Two things only a person can settle, both from Decision 16: whether the sticky composer stays
      reachable on the 22-message conversation without covering the reply it just produced, and
      whether losing it on the other four tabs is felt as a loss.
