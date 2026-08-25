## 1. Project scaffolding

- [x] 1.1 Add dependencies to `pyproject.toml`: `langgraph`, `langchain-openai`, `langgraph-checkpoint-sqlite`, `pydantic-settings`, `python-dotenv`; add a `dev` extra with `pytest`, `pydantic-evals`, `ruff`. Run `uv sync` and commit `uv.lock`.
- [x] 1.2 Create the `miku/` package skeleton — `gateway/`, `runtime/`, `graph/`, `tools/`, `memory/`, `ops/` — each with an `__init__.py`. Create `evals/deterministic/`.
- [x] 1.3 Declare the `miku` console script entry point in `pyproject.toml` and configure `ruff` (line length 100, matching the reference repos).
- [x] 1.4 Add `.env.example` documenting the GreenNode variables (base URL, key, provider name, model overrides, iteration cap). Add `.miku/` and `.env` to `.gitignore`.
- [x] 1.5 Delete the `main.py` stub.

## 2. Runtime: configuration and the provider adapter

- [x] 2.1 Implement `miku/runtime/config.py` with `pydantic-settings`: provider name, per-role model overrides, iteration cap, state directory, request timeout, retry count, max concurrency. Loads `.env`.
- [x] 2.2 Implement the `Provider` descriptor in `miku/runtime/providers.py`: name, wire family, key/base-url env var names, `{role: model_id}` for `main`/`fast`/`judge`/`embed`, capability flags (`native_structured_output`, `prompt_cache`, `embeddings`), and limits.
- [x] 2.3 Register the GreenNode descriptor with the model ids confirmed by the spike (`google/gemma-4-31b-it`, `qwen/qwen3-5-27b`, `openai/gpt-4o-mini`, `baai/bge-m3` / `gemini/gemini-embedding-001`) and per-model capability flags — `qwen` must declare native structured output unsupported.
- [x] 2.4 Implement `chat_model(role) -> BaseChatModel`, applying timeout, retry, and max-concurrency from config; raise a named error for an unknown provider, unmapped role, or missing key, before any request is attempted.
- [x] 2.5 Implement `judge_model()` returning a pydantic-ai model built from the same descriptor and credentials.
- [x] 2.6 Write offline tests in `evals/deterministic/test_providers.py`: unknown provider, missing key, unmapped role, role override honored, capability flag readable, judge and chat resolve from one config. No network calls.

## 3. Memory

- [x] 3.1 Implement `miku/memory/checkpointer.py`: build a `SqliteSaver` over `.miku/state.db`, keyed by `thread_id`.
- [x] 3.2 Implement `miku/memory/store.py`: a SQLite-backed LangGraph `Store` with a user-scoped namespace, plus `remember_fact()` and `recall_facts()`. Recall is a direct read — no gate, no extra model call.
- [x] 3.3 Write offline tests: new thread starts empty, two threads stay isolated, a thread resumes after reopening the database, a fact written on one thread is recalled on another, facts survive reopening, stored facts are never rewritten.

## 4. Scheduling tools

- [x] 4.1 Implement `miku/tools/calendar_store.py`: create the events table in `.miku/state.db` (title, date, start time, created-at) with insert and query-by-date functions.
- [x] 4.2 Implement `miku/tools/scheduling.py` — `create_event` and `list_events` as LangChain tools taking absolute dates, with `list_events` returning results ordered by start time.
- [x] 4.3 Implement `miku/tools/clock.py`: the injectable current-date source used to resolve relative dates, defaulting to the real date.
- [x] 4.4 Implement `miku/tools/memory.py` — the `remember` tool wrapping `remember_fact()`.
- [x] 4.5 Implement `miku/tools/registry.py`: the registered tool list the graph binds, and lookup by name that raises a named error for an unknown tool.
- [x] 4.6 Write offline tests: an event round-trips through the store, two events on one day both persist and list in time order, listing an empty day returns empty, events survive reopening the database, an unknown tool name raises.

## 5. Tracing

- [x] 5.1 Implement `miku/ops/tracing.py`: append one JSON object per line to `.miku/traces/<date>.jsonl`, each carrying turn id, event kind, node, and timestamp; tool events also carry tool name and success flag.
- [x] 5.2 Add secret redaction inside the sink — replace configured secret values with a marker before writing, keeping each line valid JSON.
- [x] 5.3 Make write failures non-fatal: warn and continue so a turn always completes.
- [x] 5.4 Write offline tests: every line parses as JSON, events append across reopens without truncating, events are attributable to one turn, a failed tool event is marked failed, an API key in a payload is redacted and the line stays valid JSON, an unwritable destination does not raise.

## 6. The graph

- [x] 6.1 Define the state shape in `miku/graph/state.py`: messages, iteration counter, turn id, recalled facts, and the reference date.
- [x] 6.2 Add the persona at `miku/SOUL.md` — Miku's name and tone of voice only, no capability claims.
- [x] 6.3 Implement the assemble-context node: persona system prompt + recalled facts + thread history. The agent node must not read persona or memory directly.
- [x] 6.4 Implement the agent node: bind the registered tools to `chat_model("main")` and invoke it on the assembled context.
- [x] 6.5 Implement the tools node: execute every tool call in the response, convert exceptions and unknown tool names into error results rather than raising, and attribute each result to its originating call.
- [x] 6.6 Wire the graph in `miku/graph/build.py`: assemble → agent, conditional agent → tools when tool calls are present, tools → agent. Compile with the checkpointer and store. No `create_react_agent`.
- [x] 6.7 Enforce the iteration cap: end the turn at the configured limit with a reply stating the limit was reached, and emit a cap event to the trace.
- [x] 6.8 Emit a trace event on every node transition.
- [x] 6.9 Write offline tests with a stubbed model: a no-tool turn skips the tools node, a tool-call turn loops back through the agent, multiple tool calls in one response all execute, a raising tool still yields a reply, an unknown tool name yields an error result without executing a substitute, and the cap terminates a runaway turn.

## 7. CLI gateway

- [x] 7.1 Implement `miku/gateway/cli.py`: a read-eval-print session that runs one turn per message, owns the single `asyncio.run`, and moves text only.
- [x] 7.2 Add thread selection — default to a new thread, accept an option to resume a named one.
- [x] 7.3 Print tool activity as it happens, before the final reply.
- [x] 7.4 Handle exit, end-of-input, and interrupt cleanly — no traceback, correct exit status.
- [x] 7.5 Report configuration errors as a single actionable message naming the missing variable, exiting non-zero without a traceback.
- [x] 7.6 Wire `miku/__main__.py` to the console script.

## 8. Deterministic eval suite

- [x] 8.1 Implement the single async task function wrapping the compiled graph, plus per-case isolated state (fresh temporary state directory per case).
- [x] 8.2 Implement evaluators asserting on tool calls and persisted rows — never on reply wording.
- [x] 8.3 Add the live-provider cases: correct tool selection, correct persisted event fields, relative-date resolution against a fixed reference date, cross-thread recall, and a no-tool case asserting no tool ran.
- [x] 8.4 Add the stubbed-model cap case that asserts termination without live calls.
- [x] 8.5 Make live cases skip with a stated reason when credentials are absent, so the offline subset runs without them.
- [x] 8.6 Document the run command and add it to the README; confirm exit status is non-zero when a case fails.

## 9. Verification and wrap-up

- [x] 9.1 Run the full suite with credentials present; record which model was used for the `main` role and whether any case is flaky across two runs.
- [x] 9.2 Run the suite with credentials absent; confirm live cases skip and offline cases still pass.
- [x] 9.3 Manually exercise the CLI end to end: create an event, list it, remember a preference, resume the thread in a new process and confirm both the event and the fact are still there.
- [x] 9.4 Inspect a trace file by hand — confirm one line per node transition, no secrets present, and that a turn can be reconstructed from it.
- [x] 9.5 Run `ruff` clean.
- [x] 9.6 Update `README.md` with quickstart, the architecture map, and the command table; update `CLAUDE.md` to replace the "scaffolding stage" description with the real layout and commands.
- [x] 9.7 Answer the design's open question on the `main` role default by comparing suite results between `gemma-4-31b-it` and `openai/gpt-4o-mini`; record the choice in the design doc.
- [x] 9.8 Fill in `context:` in `openspec/config.yaml` now that the stack and conventions are settled.
