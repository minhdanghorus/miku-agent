## Context

miku-agent is scaffolding: `main.py` prints a string, `pyproject.toml` declares no
dependencies, and there is no package, test suite, or lint config. The architecture was
explored and agreed in `openspec/explorations/2026-08-25-miku-agent-architecture.md`, which is
the source of the decisions restated here.

This is a learning repo. The stated design constraint is legibility: explicit, readable code
over framework indirection. That constraint is in tension with adopting LangGraph, and most of
the decisions below are about resolving that tension in a specific direction — use the
framework's runtime, hand-write the control flow.

Two constraints come from outside the repo:

- **The provider menu is small and uneven.** A live spike against GreenNode's
  OpenAI-compatible endpoint found exactly five models: `google/gemma-4-31b-it`,
  `qwen/qwen3-5-27b`, `openai/gpt-4o-mini`, and two embedding models (`baai/bge-m3`,
  `gemini/gemini-embedding-001`, the latter verified at 3072 dims). All three chat models
  handle tool calling, parallel tool calls, and streaming. `qwen` **rejects** native structured
  output (`400: 'messages' must contain the word 'json'`) while the other two accept it. Prompt
  caching was not probed and is treated as unavailable.
- **Two LLM stacks are unavoidable.** The agent runs on LangChain; `pydantic-evals`'s
  `LLMJudge` resolves its model through pydantic-ai. Judge evals are Phase 3, but the seam is
  designed now so it is not discovered later.

## Goals / Non-Goals

**Goals:**

- A CLI conversation that reasons, calls scheduling tools, remembers facts, persists thread
  state, traces every node transition, and is covered by deterministic evals.
- A loop whose control flow is readable from the graph definition — a newcomer should see the
  cycle without reading framework internals.
- A provider seam where adding OpenRouter, DeepSeek, or Anthropic means adding a descriptor,
  not editing call sites.
- Enough test coverage that Phase 2's fan-out work has something to regress against.

**Non-Goals:**

- Fan-out subagents, budget caps, node caching, retrieval gate, consolidation (Phase 2).
- Any web UI, SSE streaming transport, or judge evals (Phase 3).
- OTel spans, guardrails, semantic search over memory, additional gateways (later).
- `.ics` export, multi-user support, voice or TTS.
- Registering any provider other than GreenNode.

## Decisions

### Hand-build the `StateGraph`; do not use `create_react_agent`

Three nodes — assemble context, agent, tools — with a conditional edge from agent to tools and
an unconditional edge back. This is more code than the prebuilt, and that is the point: the
loop is the thing being learned, and it must be visible in the graph definition. The prebuilt
would hide exactly the part worth reading.

*Alternative considered:* `create_react_agent` for Phase 1, hand-rolling later. Rejected —
Phase 2 adds a supervisor with `Send` fan-out, which means rewriting the graph anyway. Better
to own the shape from the start.

*Alternative considered:* No framework at all, like waku-agent. Rejected — using a modern
framework is an explicit goal of the repo, and LangGraph's checkpointer and `Send` API are what
Phases 2 and 3 are built on.

### The provider adapter abstracts configuration, not wire formats

LangChain already normalizes wire formats: OpenAI-compatible providers share one `ChatOpenAI`
differing only by `base_url`, and Anthropic-native would be `ChatAnthropic` — both are
`BaseChatModel`. So the adapter does **not** translate messages, tool schemas, or streaming
chunks. waku-agent's `loop/models.py` exists only because it has no framework underneath.

What the adapter does own: a `Provider` descriptor (name, wire family, key/base-url env var
names, role-to-model mapping, capability flags, limits) plus two builders —
`chat_model(role)` returning a `BaseChatModel`, and `judge_model()` returning a pydantic-ai
model. Four roles: `main`, `fast`, `judge`, `embed`.

The capability flags are not speculative. The spike proved `qwen` refuses native structured
output while the other two models accept it; without a declared home, that fact becomes
`if model == "qwen..."` scattered across the codebase. Unverified capabilities (prompt caching)
are recorded as unknown and treated as unsupported.

Only GreenNode is registered in Phase 1, but the shape and both builders exist from the start
so later providers are purely additive.

*Alternative considered:* Call `ChatOpenAI(...)` directly at each site and defer the adapter.
Rejected — the capability-flag problem is real today, and later providers are already expected.

*YAGNI line held:* no hand-rolled abstraction over streaming, tool binding, or retry. LangChain
provides them; re-wrapping for symmetry is where this repo would first lose its legibility.

### Two memory tiers, deliberately not unified

`SqliteSaver` checkpointer holds per-thread conversation state. A SQLite-backed LangGraph
`Store` holds cross-thread facts under a user-scoped namespace. These are never mixed: the
checkpointer is not used for facts, the store is not used for message history.

The store supplies storage only; recall reads it directly. The interesting parts — a gate
deciding *whether* to remember and a consolidation pass deciding *what* to keep — are deferred
to Phase 2 on purpose: tuning them before real data has accumulated would be tuning in a
vacuum. Phase 1 writes facts only through an explicit `remember` tool, so the system never
decides on its own what to persist.

Load-bearing side effect: checkpointer `thread_id` is exactly the primary key the Phase 3
ChatGPT-style conversation sidebar needs. No separate conversation data model will be required.

*Alternative considered:* Hand-written memory layer over raw SQLite, as waku does. Rejected —
`Store` gives namespacing and an optional embedding index for free, and the part worth writing
by hand is the gate/consolidation logic, which stays ours.

### Scheduling in SQLite only, with an injectable reference date

Events live in a SQLite table with title, date, start time, and creation timestamp — chosen so
evals can assert on stored rows rather than on reply prose, which varies by model wording.
waku's `.ics` file is a convenience feature, not architecture; deferred.

Relative dates ("Saturday", "tomorrow") resolve to absolute dates before persistence, against
an **injectable** current date. Without injection, a case asserting "next Saturday" changes
verdict depending on the day the suite runs.

### Deterministic evals assert state, not text

`pydantic-evals` is a standalone package and `Dataset.evaluate_sync(task_fn)` accepts any async
function, so it does not require a pydantic-ai agent and does not collide with LangChain. The
graph is wrapped once as `async def turn(inputs) -> ...`; every case drives that.

Evaluators assert on which tools were called with which arguments and on what was persisted —
never on exact wording. The iteration-cap case uses a stubbed model that always requests a
tool, so it neither spends tokens nor risks hanging. Cases needing a live provider are
separable from those that do not, so the offline subset runs without credentials.

### Errors degrade rather than crash

Tool failures become tool results fed back to the model, so the model can explain or recover;
the turn produces a reply instead of a traceback. The iteration cap ends a runaway turn with a
reply that says so. Trace-write failures warn and let the turn finish — observability is not a
correctness dependency. Configuration errors are the exception: they fail loudly at startup,
before any request, naming the missing variable.

### Tracing: JSONL now, redaction at the sink

One JSON object per line per node transition, in date-partitioned files under `.miku/traces/`.
Redaction happens inside the sink, not at call sites, so no caller can leak a key by
forgetting. The `{kind, ...}` event shape is chosen to match what waku's dashboard frontend
already consumes, so Phase 3's UI can reuse that rendering logic and be fed from
`astream_events` instead.

OTel is deferred; the JSONL sink is designed as one sink among several rather than the only
possible one.

### Async throughout

LangGraph is async-first and Phase 2's fan-out is inherently concurrent. Writing the graph and
tools async now avoids a sync-to-async rewrite later. The CLI is the only sync surface, and it
owns the single `asyncio.run`.

## Risks / Trade-offs

- **Small model menu; gemma/qwen are weaker at tool use than frontier models.** → Assert on
  tool calls and stored state, never on prose, so eval verdicts are not hostage to phrasing
  quality. `openai/gpt-4o-mini` is available through the same endpoint as a stronger fallback
  for the `main` role if gemma proves unreliable.
- **Judge quality is unverified.** `openai/gpt-4o-mini` as judge over a gemma/qwen agent may not
  be meaningfully stronger on the dimension being judged. → Judge evals are Phase 3; the `judge`
  role exists now so the question can be answered by swapping a descriptor field.
- **LangGraph's abstraction can swallow the legibility goal.** → Hand-built graph, no prebuilt
  agents, and a hard no on wrapping framework features we already get.
- **Evals cost real tokens on every run.** → Keep the live case count small; drive the cap case
  with a stub; keep the offline subset runnable without credentials.
- **`Store` semantics may not fit gate/consolidation cleanly in Phase 2.** → Recall is a single
  narrow read in Phase 1, so replacing the storage layer later touches one module.
- **Deferring the retrieval gate means every turn carries all known facts.** Fine at ten facts,
  not at a thousand. → Acceptable for Phase 1; the gate is the first Phase 2 item.
- **Two LLM stacks (LangChain + pydantic-ai) is real duplication.** → Confined to the provider
  adapter, which is the module whose job is exactly that.
- **No prompt caching means full context is re-sent every turn.** → Small contexts in Phase 1;
  revisit when GreenNode support is confirmed.

## Migration Plan

No migration — there is no prior behavior and no persisted data. `main.py` is deleted and
replaced by the `miku` console entry point. Rollback is `git revert`; `.miku/` can be deleted
freely since nothing depends on it yet.

## Open Questions

- Is `openai/gpt-4o-mini` actually a stronger judge than gemma/qwen on the dimensions the judge
  evals will score? Unanswered until Phase 3 has cases to compare.
- Does GreenNode support prompt caching? Unprobed; assumed no.
- ~~Which model should the `main` role default to?~~ **Resolved: `google/gemma-4-31b-it`.**
  Measured with the live suite against both. gemma passes 6/6 cases across three consecutive
  runs. `openai/gpt-4o-mini` passes 4/6: it gets weekday arithmetic wrong, booking
  "Saturday" as 2026-08-27 — a Thursday — while stating "Saturday, August 27" in the reply.
  The expectation that the frontier-branded model would be stronger at multi-step tool use
  did not survive contact with the suite, which is the point of having one.
- `openspec/config.yaml` still has `context:` commented out. Once this phase lands, the stack
  and conventions are settled enough to fill in.
