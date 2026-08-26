## Why

Every evaluator in the suite asserts on tool calls and stored rows, which is the right default
and covers everything with a defensible answer. It leaves one gap: nothing checks whether a reply
is *honest* — whether Miku claimed to schedule, remember, or look something up that no tool
returned. `SOUL.md` makes that promise explicitly and no test enforces it, because the claim
lives in prose and prose is what the deterministic evaluators refuse to read.

An LLM judge is the tool for that gap, and the judge role was measured before being trusted with
it. `openai/gpt-4o-mini`, the configured judge, does not grade — on any dimension requiring
temporal reasoning it returned "fail" for every case, correct and incorrect alike, 3/6 twice with
identical misses. Its 3/6 is exactly the accuracy of a constant function. `gemma-4-31b-it` scored
18/18 twice on the same cases. The measurement is in
`openspec/explorations/2026-08-25-miku-agent-architecture.md` under *"Measured: the judge could
not judge"*.

## What Changes

- Remap the `judge` role in the `GREENNODE` descriptor from `openai/gpt-4o-mini` to
  `google/gemma-4-31b-it`.
- **Split `select` out of `judge`.** Found during implementation, not planned: the fan-out's
  `select_best` step resolves the `judge` role, so remapping the judge silently moved a
  *production* choice. `select` becomes a fifth role, `Deps.judge_model` becomes
  `Deps.select_model`, and grading a test is no longer the same decision as picking a slot for a
  real person.
- **BREAKING (test-level, not runtime)**: `test_judge_defaults_to_a_different_model_than_the_agent`
  asserts `resolve_model("judge") != resolve_model("main")` and will fail. The `provider-adapter`
  spec never required that — its scenario is conditional ("*may* differ"). The test was
  over-asserting a policy relative to the spec it covers, and it is replaced by one that pins the
  measured reason for the current mapping instead.
- Add judge-backed evaluation to `evals/`, alongside the deterministic evaluators rather than
  replacing any of them. Scoped to honesty and answer-relevance — dimensions with defensible
  answers that the deterministic evaluators structurally cannot reach.
- Judge cases are skipped without credentials, like the existing live cases, so the offline suite
  stays green.
- Fix two code comments the remap turns into falsehoods:
  - `providers.py` — the `judge` entry says *"A different model than main, on purpose: a model
    grading its own output is a well-known way to get flattering scores."* After the remap this
    describes the opposite of what the line does.
  - `consolidate.py` — `CONSOLIDATION_ROLE = "main"` is justified partly by rejecting `judge`
    because *"borrowing it for production work would make that claim ambiguous."* The decision
    stays correct; the reason stops distinguishing anything once `judge` resolves to `main`'s
    model, so it needs restating rather than deleting.

## Capabilities

### New Capabilities

None. This extends how the suite evaluates; it introduces no new area of behaviour.

### Modified Capabilities

- `deterministic-evals`: gains a requirement that judged evaluation is available for dimensions
  the deterministic evaluators cannot reach, that it never replaces a deterministic assertion
  where one is possible, and that judged cases skip cleanly without credentials.
- `provider-adapter`: gains a requirement that a role mapping is justified by measurement rather
  than by assumption, and that a role may resolve to the same model as another role when that is
  the measured choice. No existing requirement is removed — the "judge may differ" scenario stays
  true as written.

## Impact

**Code**

- `miku/runtime/providers.py` — two entries in `GREENNODE.models` (`judge` remapped, `select`
  added), `ROLES`, plus the stale comment.
- `miku/runtime/config.py` — `model_select` knob.
- `miku/graph/nodes.py`, `miku/graph/fanout.py`, `miku/runtime/session.py` — `judge_model` →
  `select_model` on `Deps` and at its one call site.
- `miku/memory/consolidate.py` — the `CONSOLIDATION_ROLE` rationale comment only. No behaviour
  change: consolidation already runs on `main` and continues to.
- `evals/evaluators.py` — new judged evaluators beside the existing ones.
- `evals/deterministic/` — new judged cases; one existing provider test rewritten.

**Dependencies**

None. `pydantic-evals` and `pydantic-ai` are already declared and already used by
`judge_model()`.

**Runtime**

The fan-out's slot selection moves from `openai/gpt-4o-mini` to gemma.

This corrects what this section said when the change was proposed — that no runtime path resolves
`judge`. It was wrong: `session.py` built the fan-out's selection model with
`chat_model(settings, "judge")`. The task that grepped for it is what caught it, which is the
argument for keeping that kind of verification task in a plan rather than assuming the answer.

The move is very likely an improvement — the slot picker was running on the one model in the
catalog measured twice to be unable to reason about dates, choosing among *dates* — but it is
unmeasured, and it is recorded as a Known limit rather than claimed as a fix.

**Cost**

Judged cases each cost one extra model request beyond the turn they grade. They are opt-in via
credentials, so the offline suite cost is unchanged.

## Out of scope

- **The cockpit UI.** Phase 3b owns it, including FastAPI, uvicorn, SSE, and the static
  frontend. Nothing here touches a gateway.
- **Changing how deterministic evaluators work.** They keep asserting on tool calls and stored
  rows. A judged evaluator is added where no deterministic one is possible, never instead of one
  that is.
- **Subjective-dimension judging** — tone, helpfulness, "does this sound like Miku". The spike
  could not validate it: every case it ran had an objective answer, so self-grading bias had
  nowhere to appear. Gemma now grades gemma, which is exactly where that bias would live, and it
  stays unmeasured until a change designed to measure it.
- **Selection and embeddings** (Phase 2.5b) and **episodic memory** (deferred), both unaffected.
