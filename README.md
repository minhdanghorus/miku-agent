# miku-agent

Miku in your area! Miku Agent is a local first AI agent harness you actually own, including
loop, memory, eval. All in code built to stay legible as it grows.

**Phase 1 — the CLI agent.** One terminal conversation that reasons, calls tools, remembers
across conversations, leaves a readable trace, and is covered by tests. The loop is a
hand-built LangGraph `StateGraph`: three nodes and one cycle, written out rather than
summoned from a prebuilt, because the loop is the part worth reading.

**Phase 2 — best-of-N fan-out.** Ask *when* rather than *book this*, and Miku explores five
candidate slots in parallel, each from a different angle, then judges them against what it
knows about you. It is `Send`-based map-reduce in a subgraph behind a tool — so the model
decides to fan out by choosing that tool, and the main loop is still three nodes.

## Quickstart

```bash
uv sync --extra dev          # install
cp .env.example .env         # then paste your provider key into it
uv run miku                  # talk to Miku
uv run miku --thread work    # resume a named conversation
```

Try it: *"Remember that I dislike meetings before 9am."* Quit. Start again.
*"Book a catch-up with Alex on Saturday."* The fact is still there — it lives in
`.miku/state.db`, which is yours to open.

Then ask something with no time in it — *"find me a good time for a 1-hour design review
this week"* — and watch five branches come back out of order:

```
  > propose_slots(end_day='2026-08-30', start_day='2026-08-25', task='1-hou...)
    ... exploring 5 options in parallel
    [1] after lunch: 2026-08-25 13:30
    [3] beside existing work: 2026-08-26 10:30
    [0] early morning: 2026-08-25 09:00
    [2] quietest day: 2026-08-25 09:00
    [4] late in the window: 2026-08-30 14:00
    ... picked option 1 of 5 (judged)
miku> I recommend today, August 25th, at 09:00. Would you like me to book that?
```

It proposed; it did not book. And it did not offer you 08:00, because you told it not to.

## The map

```
      $ miku
        │
  ┌─────▼───────┐
  │ gateway/cli │  moves text; no prompts, no models, no tools, no memory
  └─────┬───────┘
        ▼
  ┌──────────── graph/ ────────────────────────────────────────────┐
  │                                                                │
  │   ┌──────────┐    ┌───────┐  tool calls?  ┌───────┐            │
  │   │ assemble │───▶│ agent │──────────────▶│ tools │            │
  │   └──────────┘    └───────┘               └───┬───┘            │
  │        ▲               ▲                      │                │
  │        │               └──────────────────────┘                │
  │   SOUL.md + facts + history        (capped at MIKU_MAX_ITERATIONS)
  └───────────────────────────────────────────────│────────────────┘
        │                          propose_slots  │
        │                                         ▼
        │        ┌──────── graph/fanout.py ─────────────────────────┐
        │        │  plan_angles ──Send x N──▶ generate (concurrent) │
        │        │                                │                 │
        │        │       format ◀── select_best ◀─┘  (LLM as judge) │
        │        └──────────────────────────────────────────────────┘
        ▼                                    ▼
  state.db: threads + facts + events   .miku/traces/<date>.jsonl
```

The fan-out is a subgraph reached through a tool, not a fourth node. That is deliberate:
choosing a tool is already how a model decides things, so "should I fan out?" needs no new
machinery — and a plain "book it Saturday at 8am" costs one request instead of eight.

| Path | What lives there |
|---|---|
| `miku/gateway/cli.py` | the terminal. Text in, text out, nothing else |
| `miku/runtime/config.py` | every knob, read once. Nothing else touches the environment |
| `miku/runtime/providers.py` | the provider adapter — roles, capability flags, two builders |
| `miku/runtime/session.py` | one session: store, checkpointer, tools, model, tracer, graph |
| `miku/graph/build.py` | the loop, wired by hand — three nodes, one cycle |
| `miku/graph/nodes.py` | assemble context · agent · tools, plus `Deps` and `TurnContext` |
| `miku/graph/fanout.py` | the best-of-N subgraph — `Send`, a reducer, and a judge |
| `miku/runtime/budget.py` | one request allowance per turn, shared with the fan-out |
| `miku/memory/checkpointer.py` | thread state (short-term) |
| `miku/memory/store.py` | facts across threads (long-term) |
| `miku/tools/` | `create_event` · `list_events` · `remember` · `propose_slots` |
| `miku/ops/tracing.py` | JSONL sink, with redaction where it cannot be forgotten |
| `miku/ops/traceview.py` | reading a trace back as a tree, for evals and eyeballs |
| `miku/SOUL.md` | who Miku is — name and tone, nothing else |
| `evals/deterministic/` | the tests. `evals/task.py` is the one function cases drive |

## The provider adapter

Every supported provider speaks the OpenAI wire, so LangChain already handles message
formats, tool schemas, and streaming. What this repo owns instead:

- **roles, not model ids.** Callers ask for `main` / `fast` / `judge` / `embed`; the
  descriptor maps each to a concrete model.
- **capability flags.** Not theoretical — `qwen/qwen3-5-27b` refuses native structured
  output while `gemma-4-31b-it` and `openai/gpt-4o-mini` accept it. An unprobed capability
  is recorded as `unknown` and treated as unsupported.
- **two builders.** `chat_model(role)` for the graph, `judge_model()` for pydantic-evals —
  same descriptor, so the agent and the judge cannot drift onto different providers.

Adding OpenRouter, DeepSeek, or Anthropic means adding a descriptor to `PROVIDERS`. It must
never mean editing a call site.

## Memory is two things, kept apart

| | Thread state | Long-term facts |
|---|---|---|
| What | this conversation's messages | what Miku knows about you |
| Where | LangGraph checkpointer | LangGraph `Store` |
| Scope | one `thread_id` | every thread |
| Written by | the graph, automatically | the `remember` tool, only when asked |

Phase 1 has no retrieval gate and no consolidation: every fact rides along in every turn,
and nothing is ever rewritten. Fine at tens of facts, wrong at thousands — the gate is the
first Phase 2 item, and it wants real accumulated data to tune against.

## Evals

```bash
uv run pytest                      # everything
uv run pytest -k live              # the cases that call the real provider
uv run pytest evals/deterministic/test_graph.py   # the loop's contract, stubbed
```

Two halves. The **live** cases drive the real provider and skip with a stated reason when no
key is set. Everything else runs offline against a stubbed model — including the
runaway-turn case, which must terminate at the iteration cap without spending a token.

Every evaluator asserts on which tool ran and what landed in the database, never on how the
reply is worded. Small models phrase things differently on every run; stored rows do not.

## Configuration

See `.env.example`. The only required value is the provider API key.

| Variable | Default | What it does |
|---|---|---|
| `MIKU_PROVIDER` | `greennode` | which descriptor to use |
| `MIKU_MODEL_MAIN` | descriptor default | override the agent's model |
| `MIKU_MAX_ITERATIONS` | `8` | hard stop on agent laps per turn (depth) |
| `MIKU_FANOUT_BRANCHES` | `5` | candidates a fan-out explores (width) |
| `MIKU_MAX_REQUESTS_PER_TURN` | `24` | model requests per turn, fan-out included |
| `MIKU_STATE_DIR` | `.miku` | where `state.db` and `traces/` live |
| `MIKU_USER_ID` | `local` | whose facts the store holds |

## What is not here yet

No retrieval gate and no fact consolidation — every remembered fact still rides along in
every turn, which is fine at tens and wrong at thousands (Phase 2.5). No web UI and no
judge evals (Phase 3). No OTel, no guardrails, no semantic search over memory, no `.ics`
export.

No node cache either, and that one is a decision rather than a delay: it was planned for
Phase 2 and dropped on inspection, because branches are deliberately given different angles,
so no two branch inputs are ever identical and there is nothing for a cache to hit.
