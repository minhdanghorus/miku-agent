## Why

miku-agent is currently a `print()` stub with no dependencies and no package layout. The
architecture has been explored and agreed (see
`openspec/explorations/2026-08-25-miku-agent-architecture.md`), and a live spike has confirmed
the provider stack works, but none of it exists in code. Phase 1 turns the agreed design into
the smallest thing that is genuinely an agent: one CLI conversation that reasons, calls tools,
remembers, leaves a readable trace, and is covered by tests.

Everything past that — fan-out subagents, the web UI, judge evals, guardrails — is deliberately
out of scope, because each needs a working loop underneath it first.

## What Changes

- **New package layout.** `miku/` replaces the `main.py` stub: `gateway/`, `runtime/`,
  `graph/`, `tools/`, `memory/`, `ops/`, plus `evals/` at the repo root. Runtime state lives
  in a gitignored `.miku/`.
- **Dependencies added** (currently zero): `langgraph`, `langchain-openai`,
  `langgraph-checkpoint-sqlite`, `pydantic-settings`, `python-dotenv`; dev/eval extra gets
  `pydantic-evals` and `pytest`.
- **A provider adapter, not a wire adapter.** A `Provider` descriptor carries
  key/base-url env names, role-to-model mapping (`main` / `fast` / `judge` / `embed`), declared
  capability flags, and limits. Two builders: `chat_model(role)` returning a LangChain
  `BaseChatModel` for the graph, and `judge_model()` returning a pydantic-ai model for later
  eval work. Only the GreenNode descriptor is registered; adding OpenRouter/DeepSeek/Anthropic
  later must mean adding a descriptor, never editing call sites.
- **A hand-built LangGraph `StateGraph`** — three nodes (assemble context → agent → tools)
  with a conditional edge back to the agent and a hard iteration cap. `create_react_agent`
  and other prebuilts are explicitly not used; the point is that the loop is readable.
- **Scheduling as the flagship task.** Tools `create_event` and `list_events` over a SQLite
  table. No `.ics` export in this phase.
- **Two-tier memory, kept distinct.** `SqliteSaver` checkpointer for per-thread conversation
  state; a LangGraph SQLite `Store` for cross-thread facts, written by an explicit `remember`
  tool. No retrieval gate and no consolidation yet — those need accumulated real data to tune
  against.
- **A Miku persona** as a `SOUL.md` system prompt: name and tone of voice only.
- **JSONL tracing.** One event per node transition appended to `.miku/traces/<date>.jsonl`,
  with secret redaction at the sink.
- **Deterministic evals** with `pydantic-evals`: the graph is wrapped as an async task function
  and driven by `Dataset` cases asserting tool selection and event correctness. No judge evals.

No breaking changes — there is no existing behavior to break beyond deleting the `main.py` stub.

## Capabilities

### New Capabilities

- `provider-adapter`: resolving configuration into ready-to-use model clients — provider
  descriptors, role-to-model mapping, declared per-model capability flags, timeouts and
  concurrency limits, and the two builder entry points (LangChain chat model, pydantic-ai
  judge model).
- `agent-loop`: the hand-built `StateGraph` — state shape, the three nodes, the
  conditional tool edge, the iteration cap, working-memory assembly (persona + recalled facts
  + thread history), and behavior when the cap or an error is hit.
- `scheduling-tools`: the flagship task surface — creating and listing calendar events,
  their schemas, natural-language time resolution expectations, and the SQLite storage shape.
- `agent-memory`: the two memory tiers — thread state via checkpointer (including resuming an
  existing thread) and cross-thread facts via `Store` written through a `remember` tool, plus
  how recalled facts enter working memory.
- `cli-gateway`: the terminal entry point — starting a session, continuing or resuming a
  thread, rendering tool activity and replies, and exiting. The gateway moves text only and
  holds no agent logic.
- `agent-tracing`: the JSONL observability sink — event shape, one event per node transition,
  file layout under `.miku/traces/`, and secret redaction.
- `deterministic-evals`: the `pydantic-evals` harness — how the graph is wrapped as a task
  function, what a case asserts, which behaviors are covered, and how the suite is run.

### Modified Capabilities

None — `openspec/specs/` is empty; this is the first change.

## Impact

- **Code**: `main.py` stub removed; new `miku/` package and `evals/` tree created.
- **Dependencies**: `pyproject.toml` goes from zero dependencies to the LangGraph/LangChain
  stack plus a dev/eval extra. `uv.lock` is created.
- **Config**: a `.env.example` documenting the GreenNode variables; `.gitignore` gains
  `.miku/` and `.env`.
- **External services**: the GreenNode OpenAI-compatible endpoint. Evals that call it need a
  key and cost real tokens, so cases must be kept few and cheap.
- **Tooling**: `pytest` becomes the test runner, with tests living under `evals/` rather than
  `tests/`.
- **Out of scope, and depending on this change**: fan-out subagents and budget caps (Phase 2),
  web UI and judge evals (Phase 3), OTel, guardrails, semantic search over memory.
