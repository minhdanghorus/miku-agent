## 1. Fact shape and recall

- [x] 1.1 Add the tombstone fields to the stored fact value: `superseded_by`, `superseded_at`, and `derived_from`. Absent means live — no migration, no backfill.
- [x] 1.2 Add `supersede_fact(store, settings, key, successor_key)` writing the marker onto an existing row without touching its `fact` text.
- [x] 1.3 Add `merge_facts(store, settings, text, source_keys)` writing a new row carrying `derived_from`, then tombstoning each source against the new key.
- [x] 1.4 Add `expire_fact(store, settings, key)` — a tombstone naming no successor.
- [x] 1.5 Change `recall_facts` to drop rows carrying a supersession timestamp, keeping the existing signature and ordering so both call sites are unchanged.
- [x] 1.6 Add `live_facts(store, settings)` returning `(key, fact, created_at)` triples for the pass, which needs keys and timestamps that `recall_facts` deliberately discards.
- [x] 1.7 Update the `store.py` module docstring: it currently states that nothing rewrites, merges, or expires a stored fact, and that consolidation is a later phase.
- [x] 1.8 Tests: a superseded row keeps byte-identical text; recall excludes it; a merge links provenance both ways; a row with no marker is live; an expired row names no successor.

## 2. Configuration

- [x] 2.1 Add `max_requests_per_consolidation: int = Field(default=4, ge=1)` to `Settings`.
- [x] 2.2 Add the consolidation model role resolution — `main`, per design decision 9 — with no new role in `ROLES`.
- [x] 2.3 Tests: the knob reads from `MIKU_MAX_REQUESTS_PER_CONSOLIDATION`; a value below 1 is rejected at startup.

## 3. The plan: shape and validation

- [x] 3.1 Define the plan as Pydantic models: a `Plan` holding `Supersede`, `Duplicate`, `Merge`, and `Expire` operations, all referencing facts by 1-based index.
- [x] 3.2 Implement `validate_plan(plan, facts)` returning `(applicable, dropped)` where each dropped entry carries a machine-readable reason.
- [x] 3.3 Reject any index outside the live set.
- [x] 3.4 Reject any fact claimed by two operations in one plan; keep the first, drop the rest.
- [x] 3.5 Reject a merge with fewer than two sources or empty resulting text.
- [x] 3.6 Reject a supersession whose replacement is not strictly newer by `created_at` — the direction guard from design decision 6.
- [x] 3.7 Tests: each rejection rule in isolation; a plan mixing valid and invalid operations keeps the valid ones; validation never raises.

## 4. The pass

- [x] 4.1 Create `miku/memory/consolidate.py` as a plain async function, not a subgraph.
- [x] 4.2 Refuse at startup with a named error when the resolved model's declared `native_structured_output` capability is not `"yes"`.
- [x] 4.3 Build the prompt: live facts numbered with their `created_at`, today's date, and prose describing the four operations and when each does *not* apply.
- [x] 4.4 Request the plan through LangChain structured output against the Pydantic model.
- [x] 4.5 Claim each request from a `Budget(limit=settings.max_requests_per_consolidation)` constructed for the run; on exhaustion, stop and report rather than raise.
- [x] 4.6 Map validated indices to keys, then apply — `supersede`, `duplicate`, `merge`, `expire` — skipping any operation whose key has vanished since the read.
- [x] 4.7 Return a result object carrying the applied operations, the dropped ones with reasons, and the live fact count before and after.
- [x] 4.8 Support `apply=False` on the same code path, computing and validating the plan and returning the same result object with nothing written.
- [x] 4.9 Tests: a stub model returning a fixed plan drives every operation kind; a dry run leaves the store untouched; dry run and real run apply the same operations; an empty plan writes nothing; an exhausted budget reports instead of raising.
- [x] 4.10 Test idempotence: run the pass twice over the same facts and assert the second run applies nothing and adds no second marker.
- [x] 4.11 Test that only live facts are presented to the model when the store already holds superseded rows.

## 5. Tracing

- [x] 5.1 Open the run with `tracer.for_turn(new_turn_id())` so it is a root distinct from any turn, and emit events with `kind="consolidate"`.
- [x] 5.2 Trace the read (live fact count), the plan (proposed operation count), each dropped operation with its reason, and the outcome (applied count, live count after).
- [x] 5.3 Confirm no change to the trace format or to `traceview.py` — the `{kind, ...}` shape absorbs this.
- [x] 5.4 Tests: a completed run reads back under one run id; a dropped operation records its reason; a failing trace sink does not stop the pass.

## 6. CLI

- [x] 6.1 Add argument parsing for a `consolidate` subcommand, leaving the no-subcommand invocation starting a conversation exactly as before.
- [x] 6.2 Default to reporting; require an explicit `--apply` before anything is written.
- [x] 6.3 Print the plan as ASCII only: one line per operation, showing the facts involved and, for a merge, the resulting text.
- [x] 6.4 Print dropped operations with their reasons, so a rejected direction guard is visible rather than silent.
- [x] 6.5 Report "nothing to consolidate" and exit successfully when the plan is empty.
- [x] 6.6 Watch the run through the existing tracer `listener` seam, as the CLI already does for fan-out.
- [x] 6.7 Keep consolidation logic out of the gateway: parse, call, print.
- [x] 6.8 Tests: the default invocation writes nothing; `--apply` applies; output is ASCII; a bare invocation still starts a conversation.

## 7. Evals

- [x] 7.1 Add a stub consolidation model to `evals/helpers.py` returning a fixed plan, alongside the existing stub chat model.
- [x] 7.2 Dropped on inspection: the existing evaluators are `Evaluator[TurnInputs, TurnOutput]` for driving a `Dataset` over `run_turn`, and consolidation is not a turn. Assertions land directly on stored rows instead. Recorded as design decision 13.
- [x] 7.3 Add `evals/deterministic/test_consolidate.py` covering the four operations, the four validation rules, dry run, idempotence, and budget exhaustion.
- [x] 7.4 Assert no case compares merged text to an expected string.
- [x] 7.5 Verify the whole consolidation suite passes with the provider key blanked.

## 8. Verification and documentation

- [x] 8.1 Run `uv run ruff check .` clean.
- [x] 8.2 Run the full suite, then re-run it with the provider key blanked, and record both counts.
- [x] 8.3 Live dry check. The real `.miku/state.db` turned out to hold zero facts (every spike used a temp state dir), so the command was verified against it end to end and the judgment measurement ran on a seeded scratch database instead. Recorded under "Measured during implementation".
- [x] 8.4 Live apply check, in a scratch database rather than the repo's own (which holds no facts). Run with a control arm: unconsolidated the agent books the stale preference 3/3, consolidated it books the survivor 3/3. Recorded under "Measured during implementation".
- [x] 8.5 Record measured request count and wall-clock for a consolidation run in `design.md`, in the style of Phase 2's "Measured during implementation".
- [x] 8.6 Update `CLAUDE.md`: the architecture map gains `miku/memory/consolidate.py`, the known-limits entry about no consolidation is replaced, and the commands section gains `uv run miku consolidate`.
- [x] 8.7 Update the roadmap in the exploration doc: Phase 2.5 is now two changes, and this is the first.
- [x] 8.8 Note any behaviour that surprised the implementation, especially anything the direction guard caught on real data.
