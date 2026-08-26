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

### Fan-out routing: what makes it happen (spike, 2026-08-25, verified live)

The `Send` diagram above says *how* a fan-out runs. It does not say *what triggers one*. Three
candidate answers, and they differ in where the decision lives:

| | Mechanism | Cost | Determinism |
|---|---|---|---|
| 1. User forces it | a CLI flag or keyword | none | total, but it is not product behaviour |
| 2. Router node | a classifier node before `agent` | +1 LLM call every turn | routing logic drifts from the model that must honour it |
| 3. Fan-out is a tool | `propose_slots` bound alongside the others | none | the model decides by picking a tool, which it already does |

**Decided: 3.** Choosing a tool *is already* the model's decision-making surface; a router node
reinvents it. It also leaves the Phase 1 graph untouched: `agent -> tools -> agent` and
`route_after_agent` do not change, so none of the seven capability specs are disturbed. And
the cost question answers itself, because a model simply does not reach for the tool when
greeted with "hi".

The `Send` machinery still gets built and still gets learned — the tool's body is a compiled
**subgraph** that fans out internally:

```
   MAIN GRAPH (unchanged from Phase 1)
   assemble --> agent <==> tools --> END
                            |
                            | tool "propose_slots"
                            v
   SUBGRAPH (new in Phase 2)
   plan_angles --Send xN--> [generate] --> gather --> select_best
```

One consequence to design around: `make_tools_node` coerces every result with `str(output)`,
and a subgraph returns a state dict. The subgraph must format its own answer before returning,
rather than the tools node learning to tell two kinds of tool apart. Keeping that knowledge
out of the node is what preserves its one-screen legibility.

#### What the spike measured

`gemma-4-31b-it`, the three real Phase 1 tools plus a stubbed `propose_slots`, ten prompts,
three runs each, two wordings of the tool description. **The model returned an identical
choice on all three runs of every case** — tool selection here is not a coin flip.

On unambiguous prompts, routing was correct every time: prompts carrying a day and a time went
to `create_event`, prompts with no time went to `propose_slots`, a greeting called nothing.

Two apparent failures were not routing failures, which is only visible by reading the replies
rather than the tool names:

- *"book tennis with Raj saturday 8am"* returned no tool call and said: "I can't do that.
  You've told me you dislike meetings before 9am. Are you sure you want it at 8am?" A stored
  fact suppressed the action. That is the behaviour we want.
- *"give me 5 options for lunch with Anna"* was a bad test case — the model read "options" as
  restaurants, not time slots, and declined to invent restaurants.

One genuine miss, and the fix locates the real design lever:

| Tool description | *"when should I schedule the dentist next week?"* |
|---|---|
| States only what the tool does | no tool call — "I don't have access to your dentist's availability" |
| Also states when **not** to use it | calls `propose_slots` |

The added sentence was: "Do NOT use this when the user already said when - use create_event
for that." **The boundary between two overlapping tools lives in prose, not in control flow.**
That is a good place for it: prose is editable in seconds and assertable against a stub model
with no credentials.

Three things to carry into the Phase 2 design:

1. Any tool whose scope overlaps another **must say when not to use it**. Worth a rule in
   CLAUDE.md.
2. Long-term memory can *suppress* a tool call, not merely inform one. With fan-out, decide
   deliberately whether facts apply when choosing the tool, when scoring candidates in
   `select_best`, or both.
3. Live evals for fan-out will not be as noisy as feared.

What the spike does **not** establish: one model, ten prompts, N=3. The stub was never
executed, so argument quality is unmeasured. Two fan-out tools competing was not tried, and
neither was a fully underspecified request ("set up coffee with Nam").

### Tracing a fan-out: two fields, not a new format

A flat line-per-event log carries causality in its line order. That holds for a linear loop
(`assemble -> agent -> tools -> agent`) and breaks the moment five branches run concurrently:
five identical `generate` lines, no way to tell which belongs to which branch, and line order
now records arrival, not causation.

Interleaving is the only symptom. There is no write race — LangGraph is single-threaded
asyncio, so `Send` gives concurrency, not parallelism, and no two writes overlap.

The fix keeps the format and adds parentage: `span` (this event's id) and `parent` (the id of
the event that caused it), plus `branch` on fan-out nodes. The tree is not stored; it is
*reconstructed* from the parent links, so out-of-order arrival is harmless.

```jsonl
{"span":"b0","parent":"a3","node":"plan_angles","angles":5}
{"span":"b1","parent":"b0","node":"generate","branch":0,"angle":"early morning"}
{"span":"b3","parent":"b0","node":"generate","branch":2,"angle":"avoid busy days"}
{"span":"b2","parent":"b0","node":"generate","branch":1,"angle":"after lunch"}
{"span":"b6","parent":"b0","node":"select_best","chose":2}
```

Rejected alternatives:

- **A file per branch.** Loses global ordering between files, costs descriptors, and a turn
  stops being readable with one `cat`.
- **A nested JSON document per turn.** Reads nicely, but the tree must be held in memory until
  the turn ends. That forfeits append-only, which forfeits streaming, which forfeits the
  Phase 3 dashboard — it consumes events one at a time. A crashed turn would also leave
  nothing on disk.
- **OpenTelemetry now.** Still deferred.

Worth stating plainly: `span` + `parent` *is* OpenTelemetry's data model in miniature. That is
not a coincidence, it is the right model for the problem. The payoff is that a later OTel
export becomes a field mapping rather than a redesign, and two hand-written fields today are
far cheaper than the SDK.

The gain is not only legibility — it makes fan-out **assertable against a stub model**, with no
credentials: exactly N `generate` spans sharing one parent, each with a distinct `branch`
(structural proof of diversity, rather than reading the prose), exactly one `select_best`
running only after all branches, and zero spans after a `cap` event.

Two known costs, to be measured rather than assumed: a fan-out turn writes roughly 6x the
lines, and `event()` currently does a synchronous open/write/close inside the event loop —
invisible when the loop is sequential, a per-write stall exactly when branches are meant to
overlap.

### Budget: one mutable counter, passed down (informed by pydantic-ai)

Pydantic-ai's agent-delegation guide independently arrives at the same shape as the routing
decision above — the delegate is invoked *from inside a tool* — and it answers the accounting
question in one argument:

```python
@joke_selection_agent.tool
async def joke_factory(ctx: RunContext, count: int) -> list[str]:
    r = await joke_generation_agent.run(f'Please generate {count} jokes.', usage=ctx.usage)
    return r.output
```

`usage=ctx.usage` shares one accumulator **by reference**; parent and delegate add to the same
object, and `UsageLimits` (`cost_limit`, `request_limit`, `total_tokens_limit`,
`tool_calls_limit`) applies to the whole run rather than per agent. Notably the counter lives
neither in graph state nor in the tool's arguments.

Three candidates were considered for miku:

| | Why not / why |
|---|---|
| Cap as a tool argument | Rejected outright: it makes the model declare its own limit. It also adds a field to a schema the routing spike showed to be delicately balanced — the model would omit it or pass 999. |
| Subgraph owns its own cap | Rejected: local caps do not compose. Parent `max_iterations=8` and fan-out `5+1` both report "within limits" while the turn spends 48 calls. Two counters, one blind spot. |
| A mutable counter in `Deps` | Correct in substance — one counter, shared by reference, which is what `ctx.usage` is. |

The third needs one correction, and the repo already contains the pattern for it. `Deps` is
built once per **session**, not per turn. On the CLI that is invisible; the Phase 3 web server
runs concurrent turns over one `Deps`, where one turn's fan-out would eat another's budget —
a relative of the bug that forced `iterations` to reset in `assemble`.

`Tracer` already solved this exact lifecycle: it lives in `Deps`, is per-turn, and gets there
through `for_turn(turn_id)`. **Budget should follow the tracer's lifecycle exactly** — same
clone-per-turn shape, same route into the subgraph. Concurrent turns stay isolated by
construction rather than by the CLI happening to run one at a time, no manual reset is needed,
and there is one wiring problem to solve instead of two. One pattern, two uses.

`iterations` stays in graph state. It is a property of the parent graph, read only by the
parent graph, and it is already right; sharing a name with "limit" is not a reason to share a
mechanism.

Phase 2 counts **one** dimension: LLM requests per turn. `max_iterations` already bounds depth;
what is missing is breadth (5 branches) multiplied by depth. Tokens and cost are the
economically truer dimensions but require reading GreenNode's usage metadata, which is
unverified — do not make Phase 2 depend on it.

One further note from the same guide: *"Agent delegation doesn't need to use the same model for
each agent."* Branches could run on `fast` while `select_best` runs on `judge` — cheaper, and
the first real use for the four-role system built in Phase 1 but never exercised. It also
reopens model-diversity as a variant of the diversity question rather than a dead end.

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
| 2 | Best-of-N fan-out (`propose_slots` tool wrapping a `Send` subgraph) · per-turn budget · node cache · span/parent tracing |
| 2.5 | Retrieval gate · fact consolidation · embeddings (`bge-m3`) |
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
- Where the fan-out subgraph gets its tracer and budget. `Deps` reaches the parent graph's
  nodes; the subgraph sits inside a tool, so either the tool closure receives them at build
  time or `make_tools_node` passes its own span down — the latter is cleaner but touches the
  tool signature.
- How the angles are produced: a fixed list in code (deterministic, assertable) as the
  default, with the model free to override them as a tool argument. Not yet settled.
