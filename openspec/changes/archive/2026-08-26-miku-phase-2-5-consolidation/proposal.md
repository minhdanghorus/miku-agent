## Why

Long-term memory only grows. `remember_fact` mints a new key on every call and nothing ever
merges, corrects, or retires what was written, so after enough turns the store holds facts
that contradict each other — and `build_system_prompt` renders them as a bare bulleted list
with the timestamps stripped, leaving the model no way to tell which one is current.

This is not a hypothetical cost. The Phase 2 judge probe measured a single remembered habit
flipping the fan-out's chosen slot 0 -> 1, three runs out of three, on both `gemma-4-31b-it`
and `gpt-4o-mini`. Facts demonstrably steer decisions, so a stale fact sitting beside its own
correction turns every scheduling decision into a coin toss. Contradiction hurts at tens of
facts, which is the scale this repo is already at — unlike retrieval selection, which only
starts to matter at thousands.

## What Changes

- A **consolidation pass** that reads every live fact for a user and resolves four defects it
  finds among them: contradictions (a later fact supersedes an earlier one), near duplicates
  (repeats collapse to one), fragments (several partial statements of one preference merge
  into a single coherent fact), and expiry (a time-bound fact whose window has passed).
- **Supersession is recorded, never destructive.** A resolved fact keeps its row and its
  original `fact` text byte-for-byte, gaining `superseded_by` and `superseded_at`. Nothing is
  deleted, so the history of what the agent believed and when remains readable.
- **`recall_facts` filters superseded rows out**, so the turn path sees only live facts.
  Every existing caller — the `assemble` node and the `propose_slots` tool — benefits without
  changing its call.
- **The pass runs outside the turn**, invoked by an explicit `miku consolidate` command. No
  turn ever pays its latency or its requests, and its own decisions are traced as a run that
  can be read back on its own.
- **A dry-run mode** prints what would be superseded and merged without writing, so a
  destructive-feeling operation can be inspected before it is trusted.
- **Staleness is an operation in the same pass**, not a TTL. The store's native `TTLConfig`
  expires by age alone and so cannot tell `"this week I'm in Hanoi"` from `"I prefer meetings
  in the morning"`; it would retire durable preferences, which is the same information loss
  this change exists to prevent. Expiry therefore rides along in the plan the pass already
  produces, at no extra cost.
- **BREAKING (spec-level):** `agent-memory` currently guarantees that stored facts are never
  merged, summarized, or deleted automatically. That guarantee is replaced by a narrower and
  more honest one: stored fact *text* is never rewritten, and recall reflects supersession.

## Capabilities

### New Capabilities

- `memory-consolidation`: reading the live fact set, deciding which facts supersede, duplicate,
  or merge into which, recording those decisions as tombstones rather than deletions, bounding
  the pass's own request usage, and exposing the whole thing as an inspectable dry run.

### Modified Capabilities

- `agent-memory`: the requirement "No retrieval gate or consolidation in this phase" is
  rewritten. Consolidation becomes permitted and specified; the no-retrieval-gate half stays,
  because that is the second change's subject. Recall gains the obligation to exclude
  superseded facts, and the stored-fact shape gains the two tombstone fields.
- `cli-gateway`: gains a second command. The gateway still holds no agent logic — it parses
  arguments, calls a runtime entry point, and prints what came back.
- `deterministic-evals`: gains a coverage contract for consolidation, mirroring the one Phase 2
  added for fan-out. Assertions land on stored rows and applied operations, never on the wording
  of a merged fact.

## Impact

- `miku/memory/store.py` — the fact row shape, `recall_facts` filtering, and new supersede /
  merge writes.
- `miku/memory/consolidate.py` (new) — the pass itself.
- `miku/gateway/cli.py` — argument parsing for the `consolidate` subcommand, plus ASCII-only
  reporting of the result.
- `miku/runtime/config.py` — one knob, the pass's request bound.
- `miku/runtime/providers.py` — no new role; the pass uses an existing one.
- `miku/ops/tracing.py` — no format change. A consolidation run is a root like a turn.
- `evals/deterministic/` — a new suite asserting on stored rows, per the repo's rule that
  evaluators never assert on prose.
- No new dependencies.

## Out of scope

Deferred to **change 2 of Phase 2.5** (`memory-selection`), which is gated on a spike
measuring whether similarity retrieval works on facts of this shape:

- Embeddings, `embedding_model()` in the provider adapter, and `SqliteIndexConfig` indexing.
- Semantic top-k selection at recall time, and the similarity threshold that would give
  gating for free.
- Any retrieval gate, including the model-call gate that was considered and set aside because
  this provider's `fast` role resolves to the same model as `main`.

Deferred to **Phase 3**: judge-based evaluation of consolidation quality. This change asserts
that the right rows changed, not that the merged wording is good.
