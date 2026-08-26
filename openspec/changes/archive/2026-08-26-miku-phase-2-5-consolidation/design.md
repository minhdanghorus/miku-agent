## Context

Today `miku/memory/store.py` is thirty lines of deliberate naivety, and its own docstring says
so: every `remember_fact` call mints a fresh `uuid` key, so "an earlier fact is never
overwritten or edited — a later correction sits alongside the thing it corrects rather than
replacing it." `recall_facts` reads all of them with `asearch(ns, limit=100)`, sorts by
`created_at`, and returns the text.

`build_system_prompt` then does this:

```python
remembered = "\n".join(f"- {fact}" for fact in facts)
parts.append(f"What you remember about the user:\n{remembered}")
```

The timestamps are used to sort and then discarded. A model reading that list sees a flat set
of bullets with no dates and no provenance, so when two of them disagree it has nothing to
decide with except list order — an implicit and weak signal.

The Phase 2 judge probe established that this matters rather than merely looking untidy: one
remembered habit flipped the fan-out's selected slot 0 -> 1 on three runs of three, on both
`gemma-4-31b-it` and `gpt-4o-mini`. Facts steer decisions. A superseded fact left in place
therefore does not sit there harmlessly; it competes.

Two constraints shape everything below. The repo's stated design constraint is legibility —
explicit readable code over framework indirection. And the eval rule is that evaluators assert
on tool calls and stored rows, never on reply wording, because small models phrase things
differently every run.

## Goals / Non-Goals

**Goals:**

- Resolve contradictions, near-duplicates, fragments, and expired time-bound facts among the
  live fact set.
- Keep every fact's original text byte-for-byte, so the record of what the agent believed and
  when stays readable.
- Keep the turn path untouched: no turn pays a request or a millisecond for this.
- Make a destructive-feeling operation inspectable before it is trusted.
- Stay assertable against a stub model, with no credentials.

**Non-Goals:**

- Embeddings, semantic recall, top-k selection, similarity thresholds. Change 2.
- Any retrieval gate. Change 2, and only if a spike justifies it.
- Judging whether merged wording reads well. That is an `LLMJudge` question, Phase 3. Here we
  assert that the right rows changed.
- Automatic triggering. Nothing runs consolidation on a schedule or a threshold in this change.

## Decisions

### 1. Supersede with a tombstone; never delete, never rewrite text

A resolved fact keeps its row and its `fact` string unchanged, gaining two fields:

```python
{"fact": "...", "created_at": "...", "superseded_by": "<key>", "superseded_at": "..."}
```

`recall_facts` filters out any row where `superseded_at` is set.

The marker is the *timestamp*, not the successor. Decision 10 added `expire`, which resolves a
fact with nothing replacing it, so `superseded_by` is absent on a legitimately dead row and
cannot be what liveness is keyed on. `superseded_at` is the one field every resolution shares.

*Alternative considered:* `aput` to the same key with corrected text. The store supports it and
it is the obvious move. Rejected — it destroys the only record of what changed, in a repo whose
entire purpose is watching an agent's internals. It also silently breaks the current spec's
promise that stored facts stay byte-identical; tombstoning keeps that promise literally true
while still letting recall move on.

*Alternative considered:* hard delete. Rejected for the same reason, more so: an unrecoverable
operation driven by a model's judgment, with no audit trail, is the wrong default.

*Migration cost: none.* Rows written before this change have no `superseded_by`. Absent is read
as live, so old rows are correct without a migration script.

### 2. A merge writes a new fact and tombstones its sources

Merging three fragments produces a *new* row with a new key, carrying
`derived_from: ["<key>", "<key>", "<key>"]`. Each source row is then tombstoned with
`superseded_by` pointing at the new key.

This keeps writes append-only for text — decision 1 holds without exception — and makes
provenance a forward and backward link, so a tree of merges can be walked in either direction.

*Alternative considered:* promote one source, rewrite its text to the merged version, tombstone
the rest. Fewer rows. Rejected — it is exactly the text rewrite decision 1 forbids, and it makes
"which fact is this" ambiguous, since the surviving key would now hold text nobody ever wrote.

### 3. A plain async function, not a subgraph

Consolidation is `read -> one model call -> validate -> apply`. Linear, no branching, no
looping, no parallelism. It gets a module, `miku/memory/consolidate.py`, and a function.

*Alternative considered:* a `StateGraph`, mirroring `graph/fanout.py`. Rejected — the fan-out
subgraph exists because it has something a function cannot express cleanly: `Send`-based
parallelism with a reducer collecting branches. Consolidation has none of that. Wrapping a
straight line in a graph would be framework indirection for its own sake, which is the specific
thing the repo's legibility constraint rules out. The rule that the hand-built graph is the
point concerns the agent loop, not every piece of code that talks to a model.

### 4. The model proposes a plan; code validates and applies it

The model receives the live facts, numbered, each with its `created_at`, plus today's date. It
returns a plan of operations. Code then checks every operation and applies the survivors.
The model never touches the store.

Four operations, which is the full vocabulary:

| Op | Meaning |
|---|---|
| `supersede` | Fact A is corrected by fact B. Tombstone A, pointing at B. |
| `duplicate` | Facts B..N restate A. Tombstone B..N, pointing at A. |
| `merge` | Facts A..N are fragments of one preference. Write the merged fact, tombstone all sources. |
| `expire` | This fact was time-bound and its window has passed. Tombstone it, pointing at nothing. |

*Alternative considered:* bind `remember`-style tools and let the model mutate the store
directly. Rejected on two counts. It hands a model unvalidated write access to memory, and it
makes decision 6 (dry run) impossible — you cannot preview side effects that have already
happened.

### 5. Indices to the model, keys to the store

The model sees `1.`, `2.`, `3.` because indices are compact and hard to hallucinate character
by character. Code maps index -> key before applying anything.

This also buys concurrency safety for free. A fact written by a live session while the pass is
thinking is simply absent from the plan, and an operation whose key has vanished is skipped
rather than crashing. Neither case is special-cased; both fall out of resolving keys at apply
time.

### 6. Validation is deterministic, and one rule is a real guard

Every operation is checked before it is applied. Invalid ones are dropped and traced with a
reason — never raised, per the rule that errors degrade rather than crash.

- Every index in range.
- No fact appears in two operations (a fact cannot be both a duplicate and a merge source).
- A `merge` produces non-empty text and names at least two sources.
- **`supersede` must point from older to newer, by `created_at`.**

That last one is the one worth having. The plausible model error here is not inventing a
contradiction, it is getting the *direction* wrong — tombstoning this month's preference in
favour of last month's, which would actively reintroduce the bug this change exists to fix.
`created_at` settles direction without a model, so the code settles it.

*Alternative considered:* trust the model and rely on the dry run to catch direction errors.
Rejected — a check that a timestamp comparison can make should not be delegated to a human
reading output.

### 7. Dry run is the same code path, one flag short of the end

`consolidate(..., apply=False)` reads, plans, and validates identically, then reports instead of
writing.

*Alternative considered:* a separate `preview_consolidation()`. Rejected — two code paths means
the preview can disagree with the real thing, and a preview that can lie is worse than none.

### 8. Structured output, gated on the declared capability

The plan comes back through LangChain's structured output against a Pydantic model. Whether the
role's model supports that is already declared in the provider descriptor:

```python
_GEMMA:      ModelCapabilities(native_structured_output="yes"),
_QWEN:       ModelCapabilities(native_structured_output="no"),   # verified: 400
_GPT4O_MINI: ModelCapabilities(native_structured_output="yes"),
```

If the resolved model's flag is not `"yes"`, the pass refuses at startup with a named error.
That follows two existing rules at once: capability flags are declared and never inferred, and
only configuration errors fail loudly.

*Alternative considered:* line-oriented text parsing with a regex, as `fanout.py` does with
`CANDIDATE_LINE`. Rejected — fan-out parses one flat line per candidate, whereas a plan carries
nested lists of indices, and hand-parsing that is where quiet bugs live. The capability flag
exists precisely so this choice can be made from a declaration rather than a guess.

### 9. The `main` role, not `fast` or `judge`

*Alternative considered:* `fast`. On this provider `fast` resolves to `_GEMMA`, the same model
`main` does, so the choice is currently cosmetic — and consolidation is not latency-sensitive,
so if the roles ever diverge, `main` is the one that should win.

*Alternative considered:* `judge`, which resolves to the stronger `gpt-4o-mini`. Tempting, since
this is a judgment task. Rejected — that role exists so evaluation is graded by a model other
than the one being graded, and `judge_model()` returns a pydantic-ai model for `LLMJudge`.
Borrowing the role for production work would make "which model graded the evals" ambiguous.

### 10. Staleness is an operation, not a TTL

The store supports `TTLConfig` natively, and an earlier sketch of this change used it. It does
not survive contact with the data. TTL expires by age alone, so it cannot tell
`"this week I'm in Hanoi"` from `"I prefer meetings in the morning"` — one is stale in a week,
the other is still true in a year. A blanket TTL would retire durable preferences, which is the
same information loss this change exists to prevent, arriving by a different road.

So staleness becomes the `expire` operation in decision 4, decided by a model that can see both
`created_at` and today's date and can read that a fact is time-bound. It costs nothing extra:
same call, same plan, same validation, same tombstone.

*Alternative considered:* `TTLConfig`, as above. Rejected on precision.
*Alternative considered:* defer staleness entirely. Reasonable, and it was the fallback if
`expire` had needed its own model call. It does not.

### 11. Bounded by the existing `Budget`

The pass constructs its own `Budget(limit=settings.max_requests_per_consolidation)`. One
allowance, one run — the same shape a turn uses, which is why the class is reusable as-is.

*Alternative considered:* unbounded, since it is one call. Rejected — "it is one call" is true
today and stops being true the moment fact counts force chunking, and an unbounded loop over
memory is exactly the failure the budget type exists to prevent.

### 12. A consolidation run is a trace root, like a turn

`tracer.for_turn(new_turn_id())` produces a fresh root whose events carry
`kind="consolidate"`. No new field, no new format — the `{kind, ...}` shape absorbs it, which
is the whole reason it was chosen. The CLI watches progress through the existing `listener`
seam, the same one added in Phase 2 so fan-out inside a tool stayed visible.

### 13. Consolidation cases are plain tests, not `pydantic_evals` evaluators

Discovered while implementing, and the plan was wrong about it. `tasks.md` originally called
for `FactSuperseded`, `FactLive`, `DerivedFrom`, and `OperationDropped` in
`evals/evaluators.py`. Every evaluator already there is an
`Evaluator[TurnInputs, TurnOutput]`, whose entire purpose is to drive a `Dataset` over
`evals/task.py:run_turn`.

Consolidation is not a turn. It has no thread, no messages, no tool calls, and no `TurnOutput`.
Using that machinery would have meant building a second task function and a second dataset so
that assertions could wear a wrapper built for something else.

So the cases are direct pytest assertions in `evals/deterministic/test_consolidate.py`, landing
on stored rows, applied operations, and trace records. That satisfies the `deterministic-evals`
requirement this change adds, which asks for assertions on stored rows and no credentials — not
for a particular class hierarchy.

*Alternative considered:* build the second task function anyway, for symmetry with fan-out.
Rejected — it adds indirection without adding coverage, and the repo's constraint is
legibility.

### 14. The run's budget is injectable, because otherwise its guard is unreachable

`max_requests_per_consolidation` has a floor of 1 and every run builds a fresh `Budget`, so the
single request this pass makes today can never be refused. The exhaustion branch was therefore
dead code, and dead code cannot be asserted.

`consolidate()` takes an optional `budget` so a test can hand in one already spent. An
unasserted guard is the one that has quietly stopped working by the time chunking needs it.

*Alternative considered:* delete the guard until chunking exists. Rejected — the budget is what
makes "a pass over all of memory" bounded, and removing it would make an unbounded loop over
memory a one-line mistake away.

## Measured during implementation

**The repo's own database had nothing to consolidate.** `.miku/state.db` holds 56 checkpoints
and 148 writes from the Phase 1 and 2 spikes, and **zero fact rows** — every spike ran against a
temporary state dir, so `remember` was never exercised against the real store. `miku consolidate`
answers correctly (`0 live facts` / `nothing to consolidate`) without a model call, which
verifies the CLI, credential, and capability path end to end but measures nothing about
judgment. The measurement below therefore ran against a seeded scratch database, never the
repo's own.

**Nine seeded facts, dry run, `google/gemma-4-31b-it`, three runs.** The seed carried one
contradiction, one duplicate pair, one expired time-bound fact, two fragments, and four durable
preferences planted as traps.

| | |
|---|---|
| Requests | 1 |
| Wall clock | 1.6 - 1.7s |
| Trace lines (dry run) | 3 — `read`, `plan`, `done` |
| Proposed / applicable / dropped | 3 / 3 / 0 |
| Run-to-run variance | none; 3 of 3 identical, down to the wording of `why` |

All three operations were correct:

- `supersede` retired *"I prefer meetings in the morning"* (March) in favour of *"Actually I've
  switched to afternoons... mornings are for deep work"* (August). Direction right.
- `duplicate` collapsed *"No meetings before 9 in the morning"* into *"Please don't book
  anything before 9am"*.
- `expire` retired *"This week I'm in Hanoi for the offsite"*, written in April.

**The TTL trap held.** Decision 10 rejected `TTLConfig` on the argument that age alone cannot
separate a time-bound fact from a durable one. The seed tested exactly that: *"I play tennis on
Saturday mornings"* (May) and *"I like to batch my meetings onto one day"* (June) are older than
the Hanoi fact was at several points, and both survived all three runs untouched. A blanket TTL
would have retired them. Only the genuinely time-bound fact expired.

**The direction guard never fired.** Zero operations dropped across three runs — gemma got
supersession direction right every time. So the guard is asserted in tests and unproven in the
wild, which is an honest thing to know about it rather than a reason to remove it: it costs one
timestamp comparison, and the failure it prevents is silent.

**No merge was proposed.** *"No meetings before 9am"* and *"My standup is at 9:30 every
weekday"* are arguably fragments of one morning-shape preference, and gemma left them separate
all three times. Not wrong — merely more conservative than the prompt invites. Whether `merge`
is reachable at all on real data is untested; it is exercised only against the stub.

**Models fill `fact` on operations that do not write text.** In run 1, all three operations came
back with a populated `fact` — including the `supersede` and the `expire`, where only `merge`
writes. The pass ignores it, correctly, but the dry-run report was printing it as
`write "..."`, promising a rewrite that would never happen in the one screen someone reads
before typing `--apply`. Fixed: the report shows `write` only for a merge. This is the kind of
defect a dry run exists to surface, and it surfaced on the first real call.

**Applying, then asking: does the surviving fact actually drive the answer?** Two facts seeded
into a scratch database, `--apply`, then the same scheduling question put to the real fan-out.
The write itself: 1 operation applied, 0 dropped, 2 -> 1 live, 0.7s, the retired row still
present with its text intact and marked superseded.

The first attempt proved nothing, and it is worth recording why. The contradiction was
*"I prefer meetings in the morning, ideally right after standup"* (March) against *"I've
switched to afternoons for meetings now; mornings are for deep work"* (August) — and both arms
answered **14:00**, 3 of 3 each. The second fact is self-dating: "I've switched... now" tells
the model which one is current without any help from us. Consolidation changed the recommended
day but not the decision, so it changed nothing that mattered.

The claim is about facts with no such cue, so the probe was re-run with a flat pair —
*"I prefer meetings in the morning"* (March) against *"I prefer meetings in the afternoon"*
(August), neither one announcing itself as the newer:

| | Facts in context | Recommendation | Runs |
|---|---|---|---|
| Unconsolidated | 2 | Mon 31 Aug, **09:00** | 3 of 3 |
| Consolidated | 1 | Mon 31 Aug, **14:00** | 3 of 3 |

Unconsolidated, the agent booked the **stale** March preference — deterministically, three times
out of three. This is the Context section's argument reproduced end to end: the prompt renders
facts as bare bullets with the timestamps stripped, so a flat contradiction gives the model
nothing to resolve it with, and it settles on the wrong one. After consolidation the single
surviving fact drives the answer, and the reply names the reason unprompted ("It fits your
afternoon preference").

The two probes together bound the claim honestly. **Consolidation matters exactly when the
facts do not date themselves**, which is most of how people actually phrase preferences. When
a correction announces itself in its own wording, the model already handles it and
consolidation is only housekeeping.

## Risks / Trade-offs

**The model merges two facts that were never the same thing.** "Mornings for meetings" and
"afternoons for deep work" are compatible, not contradictory, and a careless read merges them
into something false. → Dry run before the first real use; tombstones make it recoverable;
`derived_from` shows exactly which rows produced the wrong text.

**A supersede pointed backwards would reintroduce the exact bug being fixed.** → Decision 6
makes direction a deterministic `created_at` comparison, not a model judgment.

**`expire` is the sharpest operation in the set** — it retires a fact with nothing replacing
it, so a wrong call is silent information loss. → Tombstoned, so recoverable; visible in dry
run; and unlike the other three it has no "replacement" to make the loss obvious later.

**Consolidation runs against a database a live session may have open.** → Decision 5 makes the
races benign: facts written mid-pass are absent from the plan, and operations whose keys have
vanished are skipped.

**Repeated runs could degrade wording** — merging a merge of a merge. → Idempotence is a stated
requirement and a test: a second run over an already-consolidated set produces an empty plan.
Whether wording survives many rounds over months is not something this change can prove, and it
is recorded as an open question rather than claimed.

**One model call assumes the whole fact set fits comfortably in one prompt.** True at tens,
untested at hundreds, wrong at thousands. → Bounded by the budget, and named below.

## Migration Plan

No schema migration, no backfill, no rollback script.

Rows written before this change lack `superseded_by`, and absent is read as live, so the
existing `.miku/state.db` is already correct. The first `miku consolidate --dry-run` against it
is the real acceptance test, and because the default is dry, the destructive path requires an
explicit flag.

Rollback is reverting the code. Tombstones left behind by a run would then be ignored by the
old `recall_facts`, which reads every row — the pre-change behaviour, exactly.

## Open Questions

- **Chunking.** At what fact count does one call stop being enough, and does chunking need the
  subgraph that decision 3 declined? Deliberately unanswered; the budget bounds the damage.
- **Merge stability over many rounds.** One round is asserted. Ten rounds over ten months is
  not something a deterministic test can reach.
- **Whether `expire` should require a stricter signal** than the model reading a fact as
  time-bound — an explicit `until` field written at `remember` time, say. That would be a
  change to the `remember` tool's surface, and it is not this change.
