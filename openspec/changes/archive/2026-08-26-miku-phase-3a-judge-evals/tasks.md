## 1. Remap the judge role

- [x] 1.1 Point `judge` at `_GEMMA` in `GREENNODE.models` in `miku/runtime/providers.py`
- [x] 1.2 Rewrite the comment on that entry: it currently explains a distinctness the code no
      longer has. Say what is true instead — the role was remapped because the previous judge
      returned a constant verdict on any dimension needing temporal reasoning (3/6 twice against
      gemma's 18/18), and name the exploration section holding the measurement
- [x] 1.3 Rewrite the `CONSOLIDATION_ROLE` rationale in `miku/memory/consolidate.py`. The
      decision stays `main`; the reason that rejected `judge` no longer distinguishes anything
      once both resolve to gemma. Keep the `fast` half of that comment, which is still true
- [x] 1.4 Replace `test_judge_defaults_to_a_different_model_than_the_agent` in
      `evals/deterministic/test_providers.py`. It asserts a policy the `provider-adapter` spec
      never stated. The replacement pins what the spec does require — that `judge` resolves to a
      declared id and that two roles resolving to one model is not an error
- [x] 1.5 Add a test that `judge_model()` returns a model for the remapped id, and that
      `MIKU_MODEL_JUDGE` still overrides it (the spike relied on that override)
- [x] 1.6 Run `uv run pytest evals/deterministic/test_providers.py` and confirm green

## 2. Judged evaluators

- [x] 2.1 Add a `JudgedHonest` evaluator to `evals/evaluators.py`, typed
      `Evaluator[TurnInputs, TurnOutput]` like the rest. It receives the turn's reply and its
      recorded tool calls, and fails when the reply asserts an action no tool performed
- [x] 2.2 Build its rubric so the judge is *given* the tool calls rather than asked to infer
      them — this is what keeps the dimension close to objective, per design decision 2
- [x] 2.3 Surface the judge's stated reason on the evaluator's output. A verdict with no reason
      is not reviewable, and the reason is where the spike's constant-verdict judge exposed
      itself
- [x] 2.4 Make a judge request failure produce a failing case rather than an aborted run, matching
      the repo's rule that errors degrade
- [x] 2.5 Confirm no judged evaluator duplicates a claim a deterministic evaluator already makes

## 3. Judged cases

- [x] 3.1 Add a case where the turn performs a real scheduling action and the reply reports it —
      the judged verdict should pass, and the existing deterministic evaluators keep asserting
      the tool call and stored row alongside it
- [x] 3.2 Add a case for an honest refusal of an unsupported request, asserting a passing verdict
      with no tool called
- [x] 3.3 Mark judged cases as live, so they skip without credentials the way the existing live
      cases do
- [x] 3.4 Run `uv run pytest -k live` with credentials and confirm the judged cases pass
- [x] 3.5 Run the full suite with `GREENNODE_API_KEY` blanked and confirm the judged cases report
      as skipped and the run stays green

## 4. Documentation

- [x] 4.1 Note `MIKU_MODEL_JUDGE` in `.env.example` as the supported way to try another judge
      without editing the descriptor
- [x] 4.2 Add the judged-evaluation line to `CLAUDE.md`'s architecture map and commands, and
      extend the evaluator rule so it reads correctly now that one evaluator does read prose:
      deterministic assertions stay the default, judged ones cover only what they cannot reach
- [x] 4.3 Add two Known limits to `CLAUDE.md`: gemma grades gemma so self-grading bias on
      subjective dimensions is untested by construction, and three of four roles now resolve to
      one model so nothing exercises role divergence

## 5. Verification

- [x] 5.1 `uv run ruff check .` clean
- [x] 5.2 Full suite green with credentials; full suite green with the key blanked, judged cases
      skipped
- [x] 5.3 Confirm no runtime path changed: grep that nothing outside `judge_model()` resolves the
      `judge` role, so no graph node, tool, or CLI command behaves differently.
      **Disconfirmed.** `session.py` built the fan-out's selection model with
      `chat_model(settings, "judge")`, so the remap moved a production choice

## 6. Split `select` out of `judge` (added after 5.3 failed)

- [x] 6.1 Add `select` to `ROLES` and to `GREENNODE.models`, with the reason it is separate from
      `judge` even though both name gemma today
- [x] 6.2 Add the `model_select` override knob to `config.py`
- [x] 6.3 Rename `Deps.judge_model` to `select_model`; update `session.py` to build it from the
      `select` role and `fanout.py` to call it
- [x] 6.4 Test that pointing `judge` elsewhere leaves `select` where it is, and that the fan-out
      wires to `select_model` rather than `judge_model`
- [x] 6.5 Document the new role in `.env.example` and `CLAUDE.md`, including why the two are apart
- [x] 6.6 Correct `proposal.md` and `design.md`, which claimed no runtime change, and add the
      spec requirement that production never resolves `judge`
- [x] 6.7 Record as a Known limit that the fan-out's selection model changed as a side effect and
      the new behaviour, though likely better, is unmeasured
- [x] 5.4 Re-read the two rewritten comments against the code they describe and confirm neither
      now states something false
