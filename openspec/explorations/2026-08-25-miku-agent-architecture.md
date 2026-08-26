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
| 2.5a | Fact consolidation: supersede / duplicate / merge / expire, tombstoned, behind `miku consolidate` |
| 2.5b | Selection: embeddings (`bge-m3`), top-k recall, `miku reindex` + index fingerprint. No gate and no threshold - the pollution spike closed that question |
| 3 | Judge evals (`LLMJudge`) · **UI, two surfaces** (see below) |
| later | OTel spans · other gateways · guardrails · semantic search over memory |

### Measured: an unresolved contradiction books the wrong time

Phase 2.5a's motivating claim, tested end to end rather than argued. Two facts, one scratch
database, the same scheduling question put to the real fan-out three times per arm, on
`google/gemma-4-31b-it`.

Seed: *"I prefer meetings in the morning"* (dated March) and *"I prefer meetings in the
afternoon"* (dated August). Neither announces itself as the newer one.

| | Facts in context | Recommendation | Runs |
|---|---|---|---|
| Unconsolidated | 2 | Mon 31 Aug, **09:00** | 3 of 3 |
| Consolidated | 1 | Mon 31 Aug, **14:00** | 3 of 3 |

Unconsolidated, the agent books the **stale** March preference, deterministically. That is the
prompt-shape argument confirmed: `build_system_prompt` renders facts as bare bullets with the
timestamps stripped, so a flat contradiction leaves the model nothing to resolve it with and it
settles on the wrong one. After consolidation the survivor drives the answer and the reply names
the reason unprompted.

**The bound matters as much as the result.** A first attempt found nothing, and it is recorded
in the change's design doc because it says where consolidation is and is not load-bearing. That
seed used *"I've switched to afternoons for meetings now; mornings are for deep work"* — which
dates itself in its own wording. Both arms answered 14:00, 3 of 3. The model resolves a
correction that announces itself, without help.

So: consolidation matters exactly when facts do **not** date themselves, which is most of how
people phrase preferences out loud. When a correction is explicit, consolidation is only
housekeeping.

### Why Phase 2.5 became two changes

The original line bundled three things that turned out to have different urgency and
different risk.

**Contradiction hurts now; selection does not.** The Phase 2 judge probe measured one
remembered habit flipping the fan-out's chosen slot 0 -> 1, 3/3 on both models. So a stale
fact sitting beside its correction competes for every scheduling decision, and it does that at
*tens* of facts — the scale this repo is already at. Top-k selection only starts to matter at
thousands. Consolidation therefore goes first, and it needs no embeddings, no new dependency,
and no spike.

**The retrieval gate was dropped, then partly reinstated as a question.** The waku-style gate —
a small model deciding whether a turn needs memory — does not survive this provider's catalog:
`fast` resolves to the same `google/gemma-4-31b-it` that `main` does, so the gate is not a cheap
model gating an expensive one, it is gemma called twice. It taxes every turn, most visibly the
cheap ones it exists to protect, and its failure modes are asymmetric: loading facts
unnecessarily is mild and visible, while skipping facts the turn needed is rare, invisible, and
the worst thing a memory system can do.

Two cheaper replacements were on the table — a similarity threshold, where recall returns
nothing if nothing scores above it, and memory-as-a-tool, where facts are absent from the prompt
until the model calls `recall_memory`. The spike below measured the problem all three were meant
to solve and did not find it, so all three are dropped. The measurement is recorded in the next
section.

**Staleness stopped being a TTL.** `TTLConfig` was the plan and did not survive contact with
the data: it expires by age alone, so it cannot tell "this week I'm in Hanoi" from "I prefer
meetings in the morning". A blanket TTL retires durable preferences, which is the same
information loss consolidation exists to prevent, arriving by a different road. Expiry became
the fourth operation in the plan the pass already produces — same call, same validation, same
tombstone, no extra cost, and it can read that a fact is time-bound.

### Measured: context pollution is real, reproducible, and harmless (2026-08-26)

Carrying facts into a turn that does not need them was the argument for a retrieval gate. It was
never measured, so it was measured. 40 live turns on GreenNode: two arms × ten harmless turns
× two replications.

Arm A ran against a store seeded with 15 realistic facts (morning meetings, tennis on Tuesdays, a
manager named Linh, a 09:30 standup). Arm B was the control — an empty store, same code path.
Every turn got its own `thread_id`, so the ten turns could not contaminate one another and the
only variable between the arms was the presence of facts. The turns were deliberately trivial:
`hello`, `what is 2+2?`, `thanks!`, `what is the capital of Japan?`, `bye`. `good morning` was a
lexical trap set against the `I prefer meetings in the morning` fact.

Every hard metric came back clean:

| Metric | Result over all 40 turns |
| --- | --- |
| Tool calls | **0** |
| Requests per turn | **1**, no turn deviated |
| Facts surfacing in a reply (16 distinctive probes) | **0** |
| Errors | **0** |

The worst case the spike existed to catch — a turn about arithmetic calling `create_event` — did
not occur once. Neither did the mild case of the agent volunteering memory unprompted. The
`good morning` trap produced byte-identical replies in both arms.

**The replication is what makes the result readable, and it nearly went the other way.** A single
run showed the arms differing on 2 of 10 turns, which reads as noise and would have been reported
as such. But `chat_model` pins `temperature=0`, so the noise floor is measurably zero:

```
A1 vs B1  (signal?)     differs 2/10   ['thanks!', 'ocean fact']
A2 vs B2  (signal?)     differs 2/10   ['thanks!', 'ocean fact']   <- the same two
B1 vs B2  (pure noise)  differs 0/10   []
A1 vs A2  (pure noise)  differs 0/10   []
```

Same two turns, byte-identical across replications. So the difference is not sampling variance;
it is a real, reproducible effect of 1.5k tokens of facts sitting ahead of the question. On
`thanks!` arm A added a sentence arm B did not. On the ocean question the two arms returned
different true facts.

So pollution exists, and it moves **wording, not behaviour**. Since this repo asserts on tool
calls and stored rows and never on reply wording, the thing it perturbs is the thing already
declared unassertable.

**What it settles.** All three protective mechanisms — the waku-style model gate, the similarity
threshold, and memory-as-a-tool — were proposed to prevent a harm that does not occur at this
scale. They are solutions to a problem that is not there, and 2.5b becomes selection alone. This
is a happy outcome for memory-as-a-tool in particular: it was the one option that pushed facts
through a tool result into the checkpointer, blurring the line that *"keep the two memory tiers
apart"* draws, and dropping it keeps that rule intact at no cost.

It also strengthens the embedding-index decision in the next section. If selection is a
**scale optimisation and not a correctness mechanism**, then degrading to full recall on a
fingerprint mismatch costs latency and context and gives up nothing in answer quality.

**And it reframes why 2.5b happens at all.** The remaining justification is scale, not quality —
and the real store holds 0 facts today (every spike to date ran against a temporary state dir).
Selection is therefore infrastructure for a problem two orders of magnitude away. That may still
be worth building, because this repo exists to learn how agents work, but the reason on the
record has to be *"to learn how retrieval is built"* rather than *"to fix something that hurts"*.
Recording the wrong reason is how a Known limit gets misread a year later.

**Bounds on the claim.** 15 facts, not 500 — at thousands, facts dominate the prompt and the
measured shift may stop being benign; this says nothing about that regime. Ten turns chosen by
hand, not sampled from real logs. And the facts were clean, which is the post-consolidation
state; contradictory facts may behave differently.

### Changing the embedding model: the index is derived, so rebuild it

An embedding is a cache, not data. The fact text is the source of truth, the vector is a pure
function of it, and no fact is lost when the function changes — so this is a detection-and-rebuild
problem, not a migration problem. That is also why re-embedding does not collide with the rule
that a stored fact is never rewritten: a rebuild touches the vector table and never the row.

Two failure modes, and the safe one is the loud one:

- **Different dimensions.** `sqlite-vec` fixes `dims` when the table is created, so inserts and
  queries fail outright. Noisy, immediate, harmless.
- **Same dimensions, different model.** Nothing fails. The stored vectors and the query vector
  live in unrelated spaces, so cosine similarity returns a meaningless number and top-k returns
  arbitrary facts. This is the dangerous case, and it is the same asymmetry that killed the
  retrieval gate: skipping facts the turn needed is rare, invisible, and the worst failure a
  memory system has.

Rebuild-from-scratch **is** the migration strategy at this scale. A hundred facts at ~20 tokens
is one batched call — dual-writing or shadow-indexing would be technique applied to a problem
that does not exist. Three decisions follow:

1. **The fingerprint lives in a JSON file under `.miku/`, not in the store.** It is runtime
   metadata about an index, not a fact about the user, and putting it in a store namespace would
   mix the two. A file is also readable by eye when something is wrong, which is when it matters.
   Contents: provider, model id, dims, and the prompt convention — `bge-m3` embeds text raw while
   the `e5`/`gte` families need `query:`/`passage:` prefixes, so changing convention alone breaks
   recall just as thoroughly as changing model.
2. **`miku reindex` is its own command, not a flag on `consolidate`.** The two solve unrelated
   problems, and `consolidate` already owns a dry-run/apply distinction that would collide.
   Ordering matters though: consolidate first, reindex second, so nothing pays to be embedded on
   its way to a tombstone. A reindex embeds live facts only — a tombstoned row can never be
   revived, so there is no reason to carry it in the index.
3. **A mismatch degrades to full recall; it does not crash and does not search anyway.** The
   fallback already exists and is already proven: `recall_facts` today returns every live fact,
   and 2.5b adds selection *on top of* it. So the failure path is to remove the top layer and
   keep running — slower, more context, still correct — with one warning line pointing at
   `miku reindex`. This generalizes into a constraint on 2.5b as a whole: **selection must be a
   removable layer, not something woven into the read path.** A design with no route back to
   full recall converts an index incident into a memory incident.

The string comparison runs when a session opens and costs nothing. The case it cannot catch is a
provider silently repointing `baai/bge-m3` at a new checkpoint, where every field of the
fingerprint still matches. That needs a canary — one fixed sentence, embedded and hashed — and a
canary costs a live embedding call. Charging every session for an event that may never happen is
the wrong trade, so the canary belongs behind `miku reindex --check`: voluntary, run when someone
suspects drift, never automatic.

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

- ~~The pollution spike~~ **closed 2026-08-26** — measured, see above. No gate in 2.5b. What
  remains open is the same question at scale: the spike ran on 15 clean facts, and nothing has
  measured 500 contradictory ones.
- **Embedding checkpoint drift, unprobed.** `miku reindex --check` is the agreed home for a
  canary, but nothing yet measures whether GreenNode ever repoints a model id at a new
  checkpoint. Until something does, the risk is acknowledged and unquantified: the fingerprint
  catches a deliberate model change and misses a silent one.
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
