# Exploration: miku-agent architecture

**Date:** 2026-08-25
**Mode:** `/opsx:explore` — thinking only, no implementation.
**Status:** decisions below are agreed but not yet turned into an OpenSpec change.

## What miku-agent is

A personal AI assistant agent, Hatsune Miku as the persona (name + tone of voice in a
`SOUL.md`-style system prompt — nothing more; no TTS/voice in scope). This is a learning
repo: the goal is to build an agent harness on a modern framework stack and understand
agent internals while doing it.

Framework choices (agreed):

| Concern | Choice |
|---|---|
| Orchestration | LangGraph — **hand-built `StateGraph`**, not `create_react_agent` |
| Eval | `pydantic-evals` — deterministic evaluators + `LLMJudge` |
| LLM providers | OpenAI-compatible: GreenNode (current), OpenAI, OpenRouter |
| Observability | JSONL trace sink (default); OTel spans later, low priority |
| Flagship task | Scheduling (same teaching task as waku-agent) |

## Reference repos consulted

| Repo | What we take from it |
|---|---|
| `D:\waku-agent` | Pillar layout (gateway / runtime / loop / memory / tools / ops), dashboard SSE pattern, JSONL tracing, `deterministic` vs `judge` eval split, `SOUL.md` persona idea |
| `D:\langchain\production-course-main-code` | LangGraph patterns: `supervisor_agent.py`, `parallel_agents.py`, `hierarchical_agents.py`, `checkpointing.py`, `cycles_loops.py`, `error_handling.py`, `cost_optimization.py` |
| `D:\lang-production-api` | Production layer reference: `cache.py`, `security.py`, `monitoring.py`, slowapi rate limiting |
| `D:\odoo\HR_Document_Processing\.env` | Real GreenNode config shape (base_url + api_key, model tiers, timeout, max-concurrency, `LLM_STRUCTURED_OUTPUT_MODE`) |

Also relevant: `handoff-waku-agent-architecture-qa.md` at repo root — a prior session that
traced waku's SSE streaming end to end and concluded the `{kind, ...}` event shape is
framework-agnostic (LangGraph would feed it from `astream_events`).

## Spike results — GreenNode x LangChain (2026-08-25, verified live)

Throwaway probe against `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1` using
`langchain_openai.ChatOpenAI(base_url=..., api_key=...)`. **Everything the Phase 1 design
depends on works.**

Catalog (`GET /models`) — 5 models, this is the whole menu:

```
qwen/qwen3-5-27b              chat
google/gemma-4-31b-it         chat (the model the HR project uses)
openai/gpt-4o-mini            chat
baai/bge-m3                   embeddings
gemini/gemini-embedding-001   embeddings (3072 dims, verified working)
```

Per-model capability matrix:

| | tool calling | parallel tool calls | streaming deltas | native structured output |
|---|---|---|---|---|
| `google/gemma-4-31b-it` | yes | yes (2 calls) | yes (22 chunks) | yes |
| `qwen/qwen3-5-27b` | yes | yes (2 calls) | yes (161 chunks) | **no** — 400: `'messages' must contain the word 'json'` |
| `openai/gpt-4o-mini` | yes | yes (2 calls) | yes (62 chunks) | yes |

Consequences:

- **No wire-format adapter is needed** — but a provider adapter still is. See
  "Provider adapter: where the seam goes" below.
- **`openai/gpt-4o-mini` is available through GreenNode.** This is likely what was meant by
  "I have an OpenAI key" — there is no separate `OPENAI_API_KEY` in the HR `.env`; every key
  there is a GreenNode key. Practical upshot: the judge model can be a *different* model
  than the agent model without a second vendor account, which avoids the
  model-grades-itself bias.
- **Embeddings are available locally to GreenNode** (`bge-m3`, `gemini-embedding-001`) — so
  semantic search over long-term memory is open later without a new vendor.
- **Structured output must not be assumed per-model.** qwen rejects native json_schema mode.
  The HR project's `LLM_STRUCTURED_OUTPUT_MODE=prompt` is the same scar. Any structured-output
  use should be a per-model capability flag in the provider registry, not a global.
- **Unknown, not probed:** prompt caching support. Assume none until proven.

## Design decisions taken

### Provider adapter: where the seam goes

An adapter layer is wanted from Phase 1 even though only GreenNode is wired, because
OpenRouter / DeepSeek / Anthropic are expected later. The question is what it should
abstract — LangChain already handles the hard part.

Three levels of variation:

| Level | Varies | Who handles it |
|---|---|---|
| 1 | Same OpenAI wire, different config — GreenNode, OpenRouter, DeepSeek, Gemini-compat | LangChain: one `ChatOpenAI`, differing `base_url` / key / model id |
| 2 | Different wire entirely — Anthropic native | LangChain: `ChatAnthropic`, still a `BaseChatModel` |
| 3 | Config resolution, role-to-model mapping, capability flags, limits | **Ours. This is the adapter.** |

So we do **not** write message-format translation, tool-schema translation, or streaming-chunk
translation — waku's `loop/models.py` only exists because it uses no framework. What we write
is level 3:

```
runtime/providers.py

  Provider (descriptor)              Role -> model resolution
  ---------------------              ------------------------
  name          "greennode"          main   -> the agent model
  wire          "openai"             fast   -> gate / summarize (cheap)
  key_env       GREENNODE_API_KEY    judge  -> evals + select_best
  base_url_env  GREENNODE_BASE_URL   embed  -> semantic search (later)
  models        {role: model_id}
  caps          native_structured_output: bool
                prompt_cache: bool | unknown
                embeddings: bool
  limits        timeout, retry, max_concurrent

  chat_model(role)  -> BaseChatModel        (for LangGraph)
  judge_model()     -> pydantic-ai Model    (for LLMJudge)
```

The capability flags are not speculative: the spike proved qwen rejects native structured
output while gemma and gpt-4o-mini accept it. Without a declared home, that fact turns into
`if model == "qwen..."` scattered through the codebase.

Phase 1 registers **only GreenNode**, but adding a provider must mean adding a descriptor,
never editing call sites. Two consumers from day one — LangChain for the agent, pydantic-ai
for the judge — which is why `judge_model()` belongs in the same module.

YAGNI line to hold: do **not** hand-roll abstractions over streaming, tool binding, or retry.
LangChain provides them; re-wrapping for symmetry is where this repo would first lose its
legibility.

### Memory: two distinct things, do not conflate

```
LangGraph checkpointer            |  Long-term memory (waku-style)
(per-thread, short-term)          |  (cross-thread knowledge)
----------------------------------+----------------------------------
message history of one thread     |  semantic facts
resume after crash                |  episodic summaries
time travel / branching           |  procedural (SKILL.md)
-> SqliteSaver, automatic         |  -> retrieval gate + consolidation
                                  |  -> LangGraph Store for storage,
                                  |     the gate/consolidation logic is ours
```

Decision: **use LangGraph `Store` as the storage layer; hand-write the retrieval gate and
consolidation on top.** Store gives `put(namespace, key, value)` / `search(namespace, query=)`
and an optional embedding index, but the interesting decisions — *whether* to remember and
*what* to keep — stay ours. That is the part worth learning.

Bonus: `thread_id` from the checkpointer is exactly the primary key for the ChatGPT-style
conversation-list UI wanted later. Two requirements, one mechanism.

### Security: reframed as guardrails, and out of Phase 1

`lang-production-api/security.py` is an input sanitizer + PII masker + rate limiter, built for
a multi-tenant public API. Copied verbatim into a local personal assistant it would block the
*user* (typing "ignore previous instructions" is a legitimate thing to type) while missing the
real risks. The real risks for a local agent are:

| Risk | Where it lives |
|---|---|
| Injection via **tool output** (web search, file, email returning instructions) | the tool -> LLM boundary, not user -> LLM |
| Genuinely dangerous tools (write file, shell, send message) | needs a permission gate / confirm-before-execute |
| Secret leakage into traces (API keys, PII landing in `.jsonl`, then git) | a redaction layer in the tracing sink |
| Cost runaway from subagent fan-out | budget / step cap inside the loop |

Not in Phase 1. When it arrives it should be scoped to these four, not to PII regex.

### Cache: three different things sharing one word

1. **Response cache** — `hash(query) -> answer` with TTL (what `lang-production-api` does).
   Near-useless for a stateful agent: same question + different memory must give a different
   answer. Lowest priority.
2. **Provider prompt cache** — real 60-90% cost cut, but GreenNode support is unverified.
3. **LangGraph node cache** — cache an expensive deterministic node by input key. This is the
   one that matters, and it matters *because of* best-of-N fan-out (6 LLM calls per turn).

Priority order: (2) -> (3) -> (1).

### Fan-out: best-of-N, using LangGraph `Send`

The motivating example: "tell me a developer joke" -> generate 5 candidates in parallel ->
judge -> return the funniest.

```
                    +-----------------+
                    |   supervisor    |
                    +--------+--------+
                             |  Send() x5   (map)
        +--------+-----------+-----------+--------+
        v        v           v           v        v
      [gen]    [gen]       [gen]       [gen]    [gen]
        +--------+-----------+-----------+--------+
                             v  (reduce: Annotated[list, operator.add])
                    +----------------+
                    |  select_best   |  <- LLM-as-judge, at runtime
                    +--------+-------+
                             v
                          answer
```

Nice property: `select_best` is LLM-as-judge inside the product loop, mirroring the same
concept in the eval pillar. One idea, two places.

Three open design questions on this, deliberately deferred to Phase 2:

- **Diversity.** The same prompt x5 at high temperature yields five similar jokes, which
  destroys the point of best-of-N. Real fan-out gives each branch a different angle
  (5 personas / 5 topics / 5 comedy styles). Designing that angle *is* the supervisor node.
- **Cost.** 5 gen + 1 judge = 6 LLM calls per turn. Budget cap and fan-out cap must be
  first-class in the loop, not retrofitted.
**Domain — decided: scheduling.** The fan-out is demoed on the flagship task, not on jokes:
"propose 5 meeting slots, pick the best one given my habits." Same best-of-N pattern, one
tool domain instead of two, and the selection step naturally pulls long-term memory in.
The joke example remains the clearest way to *explain* the pattern, but it is not what gets
built.

## Phase 1 scope (agreed)

CLI only. No UI.

```
      $ miku
        |
        v
  +-------------+
  | gateway/cli |   stdin/stdout only, knows nothing about LLMs
  +------+------+
         v
  +---------------- graph/ (hand-built StateGraph) ----------------+
  |                                                                |
  |   +----------+    +-------+  tool_calls?  +-------+            |
  |   | assemble |--->| agent |-------------->| tools |            |
  |   | context  |    +-------+               +---+---+            |
  |   +----------+        ^                       |                |
  |        ^              +-----------------------+                |
  |        |                  (loop, capped at N iterations)       |
  |   SOUL.md + store facts + thread history                       |
  +----------------------------------------------------------------+
         |                              |
         v                              v
  SqliteSaver (thread)          tracing -> .miku/traces/*.jsonl
  Store (facts)
```

Modules:

- `runtime/` — config (pydantic-settings) + the provider adapter described above. Only the
  GreenNode descriptor is registered in Phase 1; the descriptor shape, role-to-model mapping,
  capability flags, and both builders (`chat_model(role)` / `judge_model()`) exist from the
  start so later providers are additive.
- `graph/` — hand-built `StateGraph`: 3 nodes, conditional edge, iteration cap. No prebuilts.
- `tools/` — scheduling: `create_event`, `list_events`, plus `remember`.
- `memory/` — `SqliteSaver` for thread state; `Store` (SQLite) for long-term facts.
  **No retrieval gate, no consolidation yet** — those need real accumulated data to tune
  against, so tuning them now would be tuning in a vacuum.
- `ops/tracing.py` — JSONL sink, one event per node transition.
- `evals/deterministic/` — `pydantic-evals`: correct tool selected, event created with the
  right time. Wrap the graph in `async def turn(inputs) -> str` and hand it to
  `Dataset.evaluate_sync`.

Scheduling storage: **SQLite table only.** Waku also writes an `.ics` file; that is a
convenience feature, not architecture. `.ics` export can come later. SQLite is easier to
assert against in evals.

Explicitly **not** in Phase 1: dashboard, subagents/fan-out, judge evals, OTel, cache,
security/guardrails, vector search, telegram/voice gateways.

### Note on the eval stack

`pydantic-evals` is a standalone package and `Dataset.evaluate_sync(task_fn)` takes any async
function — it does not require a pydantic-ai agent, so it does not collide with the LangChain
stack. But `LLMJudge` resolves its model through **pydantic-ai's** model layer, not LangChain's.
So the repo will have two paths to an LLM: `ChatOpenAI` for the agent, pydantic-ai's
`OpenAIModel` for the judge. The provider config must serve both — worth designing in from the
start rather than discovering while writing the first judge eval.

## Roadmap

| Phase | Content |
|---|---|
| 1 | CLI · hand-built StateGraph · provider registry · scheduling tools · SqliteSaver + Store (`remember`) · JSONL tracing · deterministic evals |
| 2 | Best-of-N fan-out (`Send` + reducer) · budget & fan-out caps · node cache · retrieval gate + consolidation |
| 3 | Judge evals (`LLMJudge`) · **UI, two surfaces** (see below) |
| later | OTel spans · other gateways · guardrails · semantic search over memory |

### UI requirement (Phase 3) — two surfaces, one server

Both live in the same local web app:

1. **Harness cockpit** — the waku-agent dashboard, reproduced: live step-by-step progress of
   the running turn, plus tabs per pillar (config, memory, loop, tools, ops/traces, data).
   This is the "watch it think" surface.
2. **Conversation screen** — ChatGPT-style: a sidebar listing past conversations, click one to
   open its transcript and keep chatting. Backed by checkpointer `thread_id`s.

Transport, per the earlier handoff analysis: SSE over a POST fetch, one connection per turn,
`{kind, ...}` JSON events. LangGraph is async-first, so waku's sync `http.server` does not
carry over — FastAPI/starlette, fed from `astream_events`. The frontend event-application
logic from waku transfers essentially unchanged, since it only cares about the event shape.

## Open questions

- Judge model choice: `openai/gpt-4o-mini` via GreenNode as judge against a
  gemma/qwen agent? Needs a sanity check that the judge is actually stronger than the agent
  on the dimension being judged.
- Does GreenNode support prompt caching? Unprobed.
- Nothing has been written to `openspec/config.yaml` yet — once the stack settles, `context:`
  is where the provider/framework conventions should be recorded (per CLAUDE.md).
