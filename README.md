# miku-agent

Miku in your area! Miku Agent is a local first AI agent harness you actually own, including
loop, memory, eval. All in code built to stay legible as it grows.

**Phase 1 — the CLI agent.** One terminal conversation that reasons, calls tools, remembers
across conversations, leaves a readable trace, and is covered by tests. The loop is a
hand-built LangGraph `StateGraph`: three nodes and one cycle, written out rather than
summoned from a prebuilt, because the loop is the part worth reading.

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
  └────────────────────────────────────────────────────────────────┘
        │                                    │
        ▼                                    ▼
  state.db: threads + facts + events   .miku/traces/<date>.jsonl
```

| Path | What lives there |
|---|---|
| `miku/gateway/cli.py` | the terminal. Text in, text out, nothing else |
| `miku/runtime/config.py` | every knob, read once. Nothing else touches the environment |
| `miku/runtime/providers.py` | the provider adapter — roles, capability flags, two builders |
| `miku/runtime/session.py` | one session: store, checkpointer, tools, model, tracer, graph |
| `miku/graph/build.py` | the loop, wired by hand — three nodes, one cycle |
| `miku/graph/nodes.py` | assemble context · agent · tools |
| `miku/memory/checkpointer.py` | thread state (short-term) |
| `miku/memory/store.py` | facts across threads (long-term) |
| `miku/tools/` | `create_event` · `list_events` · `remember`, plus the registry |
| `miku/ops/tracing.py` | JSONL sink, with redaction where it cannot be forgotten |
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
| `MIKU_MAX_ITERATIONS` | `8` | hard stop on agent laps per turn |
| `MIKU_STATE_DIR` | `.miku` | where `state.db` and `traces/` live |
| `MIKU_USER_ID` | `local` | whose facts the store holds |

## What Phase 1 is not

No fan-out subagents, no budget caps, no node cache (Phase 2). No web UI and no judge evals
(Phase 3). No OTel, no guardrails, no semantic search over memory, no `.ics` export. Each
one needs a working loop underneath it first, and now there is one.
