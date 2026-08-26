# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## Project state

miku-agent is a local-first personal assistant agent. **Phases 1 and 2 are implemented**: a
CLI conversation on a hand-built LangGraph `StateGraph`, with scheduling tools, two-tier
memory, JSONL tracing, a deterministic eval suite, and best-of-N fan-out behind a tool. The
design constraint is legibility — explicit, readable code over framework indirection.

Planning lives in OpenSpec. Completed changes are under `openspec/changes/archive/`, and the
architecture reasoning behind every decision — including the live spike and measurement
results — is in `openspec/explorations/2026-08-25-miku-agent-architecture.md`. Read the
exploration before proposing structural changes: most of the obvious alternatives were already
considered and rejected there for stated reasons.

## Architecture map (box -> file)

- `miku/gateway/cli.py` — the terminal. Moves text only: no prompt assembly, no model calls,
  no tool execution, no memory reads. That constraint is what makes a second gateway cheap.
- `miku/runtime/config.py` — every knob, `MIKU_`-prefixed. **Nothing else reads the
  environment**, except `providers.py` reading the provider key.
- `miku/runtime/providers.py` — the provider adapter. Roles
  (`main`/`fast`/`judge`/`select`/`embed`),
  per-model capability flags, and two builders: `chat_model(role)` for LangChain,
  `judge_model()` for pydantic-evals.
- `miku/runtime/session.py` — one session: store + checkpointer + tools + model + tracer +
  compiled graph. Nothing is a module-level global; the eval suite runs many sessions per
  process.
- `miku/graph/build.py` — the loop, wired by hand. `miku/graph/nodes.py` — the three nodes,
  plus `Deps` (session-lived) and `TurnContext` (turn-lived, delivered as `Runtime.context`).
- `miku/graph/fanout.py` — the best-of-N subgraph: `plan_angles` -> `Send` x N -> `generate`
  -> `select_best` -> `format`. Reached only through the `propose_slots` tool, so the main
  graph is still three nodes.
- `miku/runtime/budget.py` — one request allowance per turn, shared by reference with any
  delegated subgraph.
- `miku/memory/checkpointer.py` — thread state. `miku/memory/store.py` — cross-thread facts,
  including the tombstone fields and the `live_facts` view the pass reads.
- `miku/memory/plan.py` — what a model may propose about memory, and `validate_plan`, the pure
  gate every write goes through. `miku/memory/consolidate.py` — the pass itself: a plain async
  function, deliberately not a subgraph.
- `miku/tools/` — `create_event` / `list_events` / `remember` / `propose_slots`, plus
  `registry.py` and the injectable `clock.py`. `proposals.py` is the delegating tool; it needs
  the whole session, so `open_session` appends it after `Deps` exists.
- `miku/ops/tracing.py` — JSONL sink with redaction inside the sink, `span`/`parent` parentage,
  and a `listener` seam the gateway watches. `miku/ops/traceview.py` — reading a trace back as
  a tree.
- `miku/SOUL.md` — the persona: name and tone only.
- `evals/deterministic/` — tests. `evals/task.py` is the single task function cases drive;
  `evals/evaluators.py` holds the evaluators; `evals/helpers.py` has the stub model. All but one
  evaluator reads tool calls and stored rows; `JudgedHonest` is the exception, and the reason it
  is allowed to read a reply is in the rule below.
- Runtime state lives in `.miku/` (state.db, traces/) — gitignored.

## Environment and commands

Python 3.13, managed with `uv`.

```bash
uv sync --extra dev        # install
uv run miku                # talk to Miku (new thread)
uv run miku --thread work  # resume a named conversation
uv run pytest              # the whole suite
uv run pytest -k live      # only the cases that call the real provider (judged cases included)
uv run pytest evals/deterministic/test_fanout.py   # fan-out shape, no credentials
uv run miku consolidate            # show what tidying memory would do (writes nothing)
uv run miku consolidate --apply    # actually resolve them
uv run ruff check .        # lint (must be clean)
```

Tests live under `evals/`, not `tests/` — `testpaths` in `pyproject.toml` reflects that.

## Rules

- **Do not add `create_react_agent` or other prebuilt agents.** The hand-built graph is the
  point of the repo, not an accident.
- **Do not hand-roll wrappers over streaming, tool binding, or retry.** LangChain provides
  them. The provider adapter abstracts *configuration*, never wire formats.
- **Adding a provider means adding a descriptor to `PROVIDERS`.** If a change requires
  editing a call site, the seam is in the wrong place.
- **Capability flags are declared, never inferred.** No `if model == "qwen..."` anywhere. An
  unprobed capability is `"unknown"` and counts as unsupported.
- **Evaluators assert on tool calls and stored rows, never on reply wording.** Small models
  phrase things differently every run; a stored row does not. One judged evaluator exists,
  `JudgedHonest`, and only because the claim it checks — that Miku never says it scheduled,
  remembered, or looked up something no tool returned — lives in the reply and nowhere else. A
  judged evaluator is never added where a deterministic one can carry the claim: a bad judge
  fails *correct* code, so every judged assertion is a place a future judge regression can waste
  an afternoon. It is handed the turn's tool calls rather than asked to infer them, which is what
  keeps the dimension close to objective.
- **A role names the work a model is chosen for, not the model.** `main`, `fast`, `judge` and
  `select` all resolve to gemma today; that is a measured choice, not an oversight, and it does
  not make them interchangeable. `judge` grades evals and moves the moment a better evaluator
  exists — so nothing a user runs may resolve it. This rule has already been paid for once:
  `select` exists because the fan-out picked slots on `judge`, and remapping the judge moved a
  production choice as a side effect. Two roles naming one model is not two names for one thing.
- **A stored fact is never deleted, and its text is never rewritten.** Resolution is recorded
  by stamping `superseded_at` onto the row. `superseded_at` — not `superseded_by` — is what
  makes a row dead, because an expired fact has no successor to point at. Absent means live,
  which is why the pre-consolidation database needed no migration.
- **A model proposes changes to memory; code applies them.** The consolidation model never
  touches the store and is never given a tool that could. Its plan goes through `validate_plan`
  first, and one of those checks is not stylistic: a supersession must point from older to
  newer by `created_at`. The plausible model error is direction, and getting it wrong would
  reinstate the exact bug consolidation exists to fix.
- **Keep the two memory tiers apart.** The checkpointer is not for facts; the store is not for
  message history.
- **A tool that overlaps another tool's scope must say when *not* to use it.** Measured, not
  stylistic: adding that one sentence to a description is what fixed the only genuine misroute
  in the routing spike. Tool boundaries live in prose, never in routing code — no classifier
  node decides which tool to use.
- **Session-lived things go in `Deps`; turn-lived things go in `TurnContext`.** A session serves
  many turns and will serve them concurrently. A budget or tracer in `Deps` is a bug waiting
  for the web gateway.
- **Per-turn context reaches a tool through the invocation config, never as a model argument.**
  A model must not be able to declare its own limit, and the field must not appear in a schema
  the model reads.
- **Never write back to a field with a reducer.** `candidates` uses `operator.add`, so writing
  a reordered copy appends rather than replaces. A live run found this the hard way; the ranked
  order lives in its own field.
- **Errors degrade, they do not crash.** Tool failures become tool results the model can see.
  Trace-write failures warn. Only configuration errors fail loudly, at startup.
- **CLI output stays ASCII.** Windows consoles mangle em dashes and bullets.
- No new dependencies without discussion.

## Known limits (deliberate, not oversights)

- No selection: every live fact rides along in every turn. Fine at tens, wrong at thousands, and
  Phase 2.5b's only remaining justification is that scale. There is deliberately no retrieval
  gate either — the pollution spike measured 40 live turns with and without facts and found 0
  stray tool calls, 0 facts volunteered unprompted, and differences confined to wording, so all
  three proposed gates were solving a harm that does not occur here.
- The judge is the same model as the agent. `gemma` grades `gemma`, which is the configuration
  self-grading bias lives in, and the spike that chose it could not detect that: all 18 of its
  cases had objective answers, so flattery had nowhere to appear. It measured capability, not
  bias. Judged evaluation is therefore kept to claims with defensible answers, and subjective
  scoring is unbuilt rather than untrusted.
- Four of five roles now resolve to one model, so nothing exercises role divergence. The seam
  still earns its place — `resolve_model` is where a second provider plugs in — but the fact that
  roles *can* differ is currently asserted only by tests that override one.
- Moving the fan-out's selection from `gpt-4o-mini` to gemma was a side effect of the judge
  remap, caught during implementation rather than designed. It is very likely an improvement — a
  slot picker was running on the one catalog model measured twice to be unable to reason about
  dates — but *likely* is the honest word. It is unmeasured.
- Consolidation never runs on its own. No threshold, no schedule, no tool — someone types
  `miku consolidate`. Automatic triggering would put a variable-latency, variable-cost model
  call into a random turn, which is the hardest kind of behaviour to debug.
- Consolidation reads the whole live fact set in one model call. True at tens, untested at
  hundreds. The run's own budget bounds the damage; chunking is unbuilt.
- `merge` is exercised only against the stub. Three live runs over a nine-fact seed proposed
  `supersede`, `duplicate`, and `expire` and never a merge, so whether gemma reaches for it on
  real data is unknown.
- The supersession direction guard has never fired in the wild — 0 dropped across three live
  runs. Asserted in tests, unproven in practice. Kept anyway: one timestamp comparison against
  a failure that would be silent.
- No prompt caching — unverified on GreenNode, so full context is re-sent each turn.
- No node cache. It was planned for Phase 2 and then dropped on inspection: branch inputs are
  deliberately never identical, so there is nothing for it to hit.
- The budget counts requests, not tokens or cost. GreenNode's usage metadata is unprobed.
- Distinct angles can converge on the same slot, so five branches do not guarantee five
  options. Arguably a feature; untuned either way.
- `main` defaults to `google/gemma-4-31b-it`. Measured, not assumed: `openai/gpt-4o-mini`
  fails the weekday-resolution cases (it books "Saturday" as a Thursday). `gemma` resolves
  fan-out windows correctly too ("next week" -> the right Monday).
- A fan-out turn costs 8 requests and ~13 trace lines, against 2 and ~5 for a plain turn.
