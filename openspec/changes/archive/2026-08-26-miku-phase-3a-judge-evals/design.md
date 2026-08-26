## Context

The suite has 233 tests, every one asserting on tool calls, stored rows, or trace structure. That
rule is deliberate and measured — small models phrase things differently every run, a stored row
does not. It leaves exactly one thing uncovered: `SOUL.md` promises Miku will *"never claim to
have scheduled, remembered, or looked up anything unless a tool actually returned a result saying
so"*, and that promise is made in prose. An evaluator that refuses to read prose cannot check it.

`judge_model()` and the `judge` role have existed since Phase 1, unused. Before building on them,
the judge was measured — the exploration doc records the full run under *"Measured: the judge
could not judge"*. 18 cases across three dimensions, three correct and three deliberately wrong
each, two replications per model, 72 live calls through pydantic-evals' own `judge_input_output`:

| | date | habit | plain | total |
| --- | --- | --- | --- | --- |
| `openai/gpt-4o-mini` ×2 | 3/6 | 5/6 | 6/6 | 14/18 |
| `google/gemma-4-31b-it` ×2 | 6/6 | 6/6 | 6/6 | 18/18 |

The 3/6 is not partial credit. Raw verdicts on the date dimension were `[False ×6]` in both runs:
gpt-4o-mini failed every case, correct and incorrect alike, which is exactly the accuracy of a
constant function. Its stated reasons show why — it computed "the following week" from
2026-08-26 as 2026-09-05, a Saturday, and ruled confidently on that.

## Goals / Non-Goals

**Goals:**

- Cover the honesty claim `SOUL.md` makes and nothing currently tests.
- Put the `judge` role on a model measured able to do the work.
- Leave every existing deterministic evaluator untouched and every existing assertion intact.
- Keep the offline suite green and unchanged in cost.
- Repair the two comments the remap falsifies, so the code does not state the opposite of what it
  does.

**Non-Goals:**

- Subjective judging (tone, helpfulness, voice). Unmeasured by construction — see Decision 5.
- Replacing any deterministic assertion with a judged one.
- The cockpit UI (3b), selection (2.5b), or episodic memory (deferred).
- Making judged cases part of the default offline run.

## Decisions

### 1. `judge` moves to `google/gemma-4-31b-it`

**Alternative considered: keep `gpt-4o-mini` and scope judging to the `plain` dimension**, where
it scored 6/6. Rejected on inspection of what that dimension contains. The two dimensions it
fails — date resolution and habit adherence — are already asserted *deterministically*, by
`args_for("create_event")` and `StoredEvent`. So the scope-down would have cost nothing and was
briefly the recommended path. What killed it is that "honesty" is not cleanly separable: the
honest-refusal case ("I can't set reminders yet") and the false-claim case ("Done! I've saved
that reminder") both sit inside `plain`, but a realistic false claim is usually *about a
scheduling action*, and grading it drags temporal context back in. Choosing a judge that inverts
its verdict the moment a date appears is choosing a landmine.

**Alternative considered: per-dimension judges** — gemma for objective, gpt-4o-mini for
subjective. Rejected: it breaks one-role-one-model and would need `judge_objective` /
`judge_subjective` in `ROLES`, which is real architectural cost for a subjective dimension this
change does not build.

### 2. The anti-self-grading principle is traded knowingly, not forgotten

The descriptor's original rationale is correct and stays correct: a model grading its own output
tends to flatter it. It is traded because the alternative measured worse — a flattering judge
still carries signal, a constant judge carries none.

Two things bound the trade. First, it is one line in `PROVIDERS` with no call site touched, so it
reverses the moment GreenNode's catalog offers a third capable chat model. Second, the dimensions
this change actually judges are the ones where flattery has the least room: whether a tool call
backs a claim is close to objective, and the judge is given the turn's tool calls as input rather
than being asked to intuit them.

**Alternative considered: an external provider for the judge only.** Rejected — it would put a
second set of credentials into a repo whose whole configuration story is one provider descriptor,
and "no new dependencies without discussion" extends to new API accounts.

### 3. Judged evaluators sit beside deterministic ones, never instead of them

A judged evaluator is added only where no deterministic assertion is possible. This is not
stylistic: the measured failure mode of a bad judge is **false-fail**, which produces red tests
over correct code. Every judged assertion is therefore a place where a future judge regression
can waste an afternoon, and the fewer of them there are, the smaller that surface is.

**Alternative considered: judge everything, keep deterministic evaluators as a cross-check.**
Rejected for cost and for the same reason — it maximises exactly the surface worth minimising,
and buys nothing where a string comparison is already exact.

### 4. Judged cases skip without credentials, following the existing live-case pattern

The suite already distinguishes offline cases from live ones (`pytest -k live`). Judged cases join
the live set. No new mechanism.

**Alternative considered: a recorded-response fixture so judged cases run offline.** Rejected —
it would test the plumbing while asserting nothing about the judge, and the judge is the part
whose behaviour is uncertain. A frozen judge verdict is a fixture pretending to be a measurement.

### 5. Subjective judging is deliberately not built

The spike measured **capability, not bias**. Every one of its 18 cases has an objective answer —
2026-09-01 either is a Tuesday or is not — so self-grading bias had nowhere to appear. With
`judge` now resolving to the same model as `main`, gemma grades gemma, and that is precisely the
configuration where bias would show. Building subjective evaluators on an unmeasured bias
assumption would produce numbers that look like evidence.

Measuring it needs a different instrument: paired outputs where one is known better on a
subjective axis, ideally with a human label. That is its own change.

### 6. The two stale comments are repaired, not deleted

`providers.py`'s judge entry currently explains a distinctness the code will no longer have, and
`consolidate.py` justifies `CONSOLIDATION_ROLE = "main"` partly by rejecting `judge` as reserved
for grading. The consolidation decision remains right — `main` is the role that should keep doing
that work — but the stated reason stops distinguishing anything once both resolve to gemma.

Deleting either comment would lose a real reason. Both are rewritten to say what is now true,
including why the mapping changed, so the next reader finds the measurement rather than a
contradiction.

## Risks / Trade-offs

- **Gemma grades gemma; self-grading bias is untested** → Confined to dimensions where flattery
  has least room, and the judge is handed the turn's tool calls rather than asked to infer them.
  Recorded as a Known limit, not implied to be solved.
- **A false-fail from the judge produces a red test over correct code** → Minimised by keeping the
  judged surface as small as possible (Decision 3), and by surfacing the judge's reason with every
  verdict so a wrong verdict is diagnosable in one read rather than an afternoon.
- **Four of five roles now resolve to gemma, so the role seam carries less real variation** →
  Not a defect. The seam exists so a second provider plugs in at `resolve_model`; that is
  unaffected. Worth recording that nothing currently exercises role divergence.
- **The fan-out's selection model changed as a side effect** → Split into its own role so it
  cannot happen again, and recorded as a Known limit because the new behaviour is likely better
  but unmeasured. Anyone wanting the old behaviour sets `MIKU_MODEL_SELECT`.
- **Judged cases cost one request each and are non-deterministic** → They are live-only and
  opt-in. The offline suite's cost and determinism are unchanged.
- **The measurement is 18 cases on one day against one provider** → Bounded claim. It is enough to
  disqualify a constant-verdict judge; it is not a general ranking of the two models.

### 7. `select` is split out of `judge` (decided during implementation)

The verification task that grepped for runtime uses of the `judge` role found one:
`session.py` built the fan-out's selection model with `chat_model(settings, "judge")`, so
remapping the judge moved a user-facing choice. The proposal had claimed the opposite.

**Alternative considered: accept it and document.** Rejected — it leaves the overload in place, so
the next judge change moves scheduling behaviour again, silently, for the same reason. The trap
is the defect, not this instance of it.

**Alternative considered: point the fan-out at `main`.** One line, and it stops production
following the evaluator. Rejected because it deletes a seam that was placed deliberately: the
comment in `session.py` shows selection was *meant* to be separately steerable from the agent.
Collapsing it to `main` solves the coupling by removing the flexibility rather than by naming it.

Splitting the role costs about fifteen lines and makes the rule true rather than aspirational.
That both roles name gemma today is not an argument against it — that is exactly the state in
which an overload hides.

## Migration Plan

No data migration. One runtime change: the fan-out's slot selection moves from `gpt-4o-mini` to
gemma, because it was resolving `judge`. See Decision 7 and the proposal's Runtime impact.

Rollback for the judge is one line: point `judge` back at `openai/gpt-4o-mini`. Because `select`
is now its own role, that rollback no longer drags scheduling behaviour with it — which is the
property Decision 7 exists to buy.

Rollback is one line: point `judge` back at `openai/gpt-4o-mini`. The judged cases would then fail
on date-adjacent content, which is the measured behaviour and would be visible immediately rather
than silently.

## Open Questions

- Whether gemma flatters its own output on subjective dimensions. Needs paired outputs with a
  known-better side, which the objective cases cannot provide. Out of scope here; recorded so the
  gap is not mistaken for coverage.
- Whether `MIKU_MODEL_JUDGE` should be documented in `.env.example` as the supported way to try a
  different judge without editing the descriptor. It already works — the spike used it — but it is
  currently undocumented.
