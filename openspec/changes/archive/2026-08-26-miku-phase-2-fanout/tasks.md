## 1. Config knobs

- [x] 1.1 Add `fanout_branches` (default 5) and `max_requests_per_turn` to `Settings`, `MIKU_`-prefixed like every other knob
- [x] 1.2 Document both in `.env.example` with a line saying what each bounds
- [x] 1.3 Extend `evals/deterministic/test_providers.py` (or the config tests beside it) to cover the two new knobs and their defaults

## 2. Trace parentage

- [x] 2.1 Add `span` and `parent` to every trace record, minted in `Tracer.event`
- [x] 2.2 Add `Tracer.child(parent_span)` following the existing `for_turn` clone shape; do not introduce a global or a contextvar
- [x] 2.3 Thread parentage through the three existing nodes so a Phase 1 turn reconstructs as a chain
- [x] 2.4 Add a `branch` field, written only by fan-out nodes
- [x] 2.5 Write a small trace-reading helper for evaluators: read a turn's records and return the tree by parent links
- [x] 2.6 Extend `evals/deterministic/test_tracing.py`: a linear turn is a single chain, every non-root event names a parent in the same turn, out-of-order writes reconstruct identically, every line parses standalone
- [x] 2.7 Confirm the existing `test_every_node_transition_is_traced` still passes unchanged

## 3. Turn budget

- [x] 3.1 Add `miku/runtime/budget.py`: a mutable `Budget` with `spend()`, `remaining()`, `exhausted`, and a `for_turn` clone matching `Tracer`'s shape
- [x] 3.2 Create the budget per turn in `session.py`, alongside the per-turn tracer, and put it in `Deps`
- [x] 3.3 Count the agent node's model call against it; on exhaustion emit a `budget` trace event and end the turn with a reply, never an exception
- [x] 3.4 New `evals/deterministic/test_budget.py`: a second turn starts at zero, two concurrent turns do not share an allowance, exhaustion emits the event and still replies, a normal turn emits no budget event
- [x] 3.5 Assert `iterations` stayed in graph state and was not folded into the budget

## 4. Per-turn context into tools

- [x] 4.1 Pass a `RunnableConfig` from `make_tools_node` to `tool.ainvoke(args, config)` carrying the turn's span and budget under `configurable`
- [x] 4.2 Confirm live that a `StructuredTool` coroutine declaring a `config` parameter receives it, and that the parameter is absent from the model-facing schema
- [x] 4.3 Verify the three existing tools are untouched by the change and need no signature edit
- [x] 4.4 Extend `evals/deterministic/test_tools.py`: a config-declaring tool receives the current turn's span and budget, two turns in one session receive different ones, and no bound schema exposes either

## 5. The fan-out subgraph

- [x] 5.1 Add `miku/graph/fanout.py` with its own state: the request, the angle list, and a candidates field reduced with `operator.add`
- [x] 5.2 `plan_angles` — resolve the angle list (supplied or default), clamp the branch count to the number of distinct angles, trace the clamp, and emit one `Send` per branch
- [x] 5.3 `generate` — one model call on the `fast` role, returning a candidate with an absolute ISO date and HH:MM time; record the branch and its angle in the trace
- [x] 5.4 `select_best` — one model call on the `judge` role choosing among the collected candidates; skip the call when only one candidate arrived; trace the chosen index
- [x] 5.5 Format the subgraph's result to text in a final step, so nothing outside knows a subgraph ran
- [x] 5.6 Degrade on partial results: select among what arrived; with zero candidates return an explanatory result rather than raising
- [x] 5.7 Count every branch and selection call against the turn budget; stop launching branches once exhausted
- [x] 5.8 New `evals/deterministic/test_fanout.py`: N branches for a branch count of N, all share one parent, distinct angles per branch, supplied angles override defaults, clamping when angles run short, exactly one selection after the branches, one candidate skips the model call, partial and empty results both still return

## 6. The proposal tool

- [x] 6.1 Add `miku/tools/proposals.py`: `propose_slots(task, window, angles=None, config=...)` invoking the compiled subgraph
- [x] 6.2 Write the description so it states when *not* to use it, naming `create_event` as the alternative — the sentence the spike measured as the fix
- [x] 6.3 Sharpen `create_event`'s description to state it requires an absolute date and time
- [x] 6.4 Register the tool in `registry.py`
- [x] 6.5 Validate and reject unusable input the way `scheduling.py` rejects non-ISO dates
- [x] 6.6 Assert proposing writes nothing to the calendar table
- [x] 6.7 Extend `evals/deterministic/test_tools.py` for the tool's validation, its no-write guarantee, and both descriptions naming their boundary

## 7. Live measurement

- [x] 7.1 Probe argument quality against the real provider: does the model pass a sensible `task` and `window`? Record the result in the design doc's open questions
- [x] 7.2 Add live routing cases to the suite: day+time routes to `create_event`, no time routes to `propose_slots`, skipped without credentials
- [x] 7.3 Measure a fan-out turn end to end: wall time, request count, and trace line count. Record the numbers; do not optimise yet
- [x] 7.4 Sanity-check selection quality with the default `judge` model, given the Phase 1 measurement that it mishandles weekday arithmetic. If selection is poor, note that changing the judge role is a one-line descriptor edit

## 8. Wiring, CLI, and close-out

- [x] 8.1 Decide and implement how the CLI reports concurrent branch activity — `print_tool_activity` currently assumes sequential tool calls, and five branches will interleave. ASCII only
- [x] 8.2 Run the whole suite plus `ruff check .`; both clean
- [x] 8.3 Two-process end-to-end check: a proposal, then booking the recommended slot, surviving a restart
- [x] 8.4 Reconstruct one real fan-out trace into a tree by hand and confirm it reads correctly
- [x] 8.5 Confirm no provider key appears anywhere in the trace files
- [x] 8.6 Update `CLAUDE.md`: the architecture map gains the new modules, and add the rule that a tool overlapping another's scope must state when not to use it
- [x] 8.7 Update `README.md` with the fan-out example
- [x] 8.8 Answer the design doc's open questions with what was learned, or restate them as Phase 2.5/3 items
- [x] 8.9 Delete `handoff-phase-2.md` — this change replaces it
