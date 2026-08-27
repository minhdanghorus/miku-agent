## 1. Time, first (ships on its own)

- [x] 1.1 Add `elapsed(records)` to `app.js`: returns the offset of each record from the first
      record's `ts`, keyed by span. Pure, exported, no DOM.
- [x] 1.2 Clamp negative offsets to zero, and take the first record present as the origin so a
      truncated trace still measures.
- [x] 1.3 Render the offset on each row in `renderNodes`, with the absolute clock in a `title`
      attribute. Label neutrally (`+2.3s`) -- never "started".
- [x] 1.4 Style the offset column in `style.css` so it does not compete with the record's text.
- [x] 1.5 Confirm by inspection that the traces pane gets offsets too, since it shares
      `renderTree`. Do not add a second path.
- [x] 1.6 Cases in `test_cockpit.py`: offsets equal `ts` minus the first `ts` for known
      timestamps; a gap-to-previous computation would fail them; a clock step clamps to zero; the
      rendered tree's parent/child structure is unchanged by showing offsets.

## 2. The topology, as data

- [x] 2.1 Add an exported `TOPOLOGY` to `app.js`: boxes in order, the steps drawn inside each box,
      the edges between them (including the `tools -> agent` back edge), and for each box the
      graph node names it claims.
- [x] 2.2 Depict inside `assemble` the two steps the compiled graph cannot report: the persona
      load and the fact recall. Mark them as steps-within, not as graph nodes.
- [x] 2.3 Declare the delegated subgraph as one box hanging off `tools`, labelled with the tool
      that reaches it, claiming the node names `plan_angles`, `generate`, `select_best`, `format`.
- [x] 2.4 Add `nodesClaimed(topology)` -- the flat set of graph node names the topology declares.
      Exported; the drift guard is its only consumer.

## 3. The drift guard

- [x] 3.1 Case in `test_cockpit.py`: run `node` to dump `nodesClaimed(TOPOLOGY)`, build the real
      graphs via `build_graph` and `build_fanout_graph`, take `get_graph().nodes` from each, filter
      `__start__`/`__end__`, and assert the two sets are equal.
- [x] 3.2 Make the failure name the offending node in each direction -- missing from the diagram,
      and unknown to the graph -- rather than printing two sets.
- [x] 3.3 Confirm the case carries the existing `needs_node` skip marker, and that the skip reason
      says the guard did not run.
- [x] 3.4 Control case: a deliberately wrong topology fixture fails the guard. A guard that cannot
      fail is not a guard.
- [x] 3.5 Verify the guard builds its own graph and that no gateway code gains access to
      `session.graph`.

## 4. Painting

- [x] 4.1 Add `paint(topology, records)` to `app.js`: pure, returns which boxes are marked and how
      many records marked each. No state between calls, no accumulation.
- [x] 4.2 Mark the delegated box when any record names a node it claims.
- [x] 4.3 Ignore records naming unknown nodes rather than failing.
- [x] 4.4 Add `renderDiagram(target, topology, painted)` -- HTML and CSS only, no SVG coordinates
      and no layout algorithm. Escape everything a record contributes.
- [x] 4.5 Cases in `test_cockpit.py`: arrival order does not change the result; painting
      record-by-record converges to painting all at once; a fan-out turn marks the delegated box;
      a non-delegating turn leaves it unmarked; an unknown node marks nothing and breaks nothing;
      a box entered three times reports a count of three.

## 5. Wiring

- [x] 5.1 Add the diagram container above the live turn pane in `index.html`.
- [x] 5.2 Render the diagram on load, unmarked, before any turn has run.
- [x] 5.3 Repaint from `live.records` on each arriving event in `sendTurn`, calling `paint` with
      the whole set rather than updating in place.
- [x] 5.4 Paint the diagram in the traces pane when a finished turn is opened, using the same
      `paint` and the same renderer.
- [x] 5.5 Style marked, unmarked and delegated states in `style.css`, and the back edge as a
      visible return to `agent`.
- [x] 5.6 Case: the served page references the diagram container and the module still imports
      without a DOM.

## 6. Documentation and specs

- [x] 6.1 Update the architecture map in `CLAUDE.md` for what `app.js` now holds -- the topology,
      the guard's existence, and that the diagram is hand-authored on purpose.
- [x] 6.2 Add the known limits: appearance is still unverified by machine and is now the largest
      such surface; the steps drawn inside a node are the unguarded half of the diagram; the drift
      guard skips without `node`; level-2 replay is unbuilt.
- [x] 6.3 Record in the exploration document why the diagram is not derived from `get_graph()`,
      including that the derived design needed a spike which choosing to hand-author removed.
- [x] 6.4 Re-read the three delta specs against what was actually built and add anything the
      implementation revealed, before archiving. The Phase 3a and 3b delta specs were both
      incomplete at this point.

## 7. Verification

- [x] 7.1 `uv run pytest` -- whole suite green.
- [x] 7.2 `uv run pytest evals/deterministic/test_cockpit.py` -- the new cases, including the
      control that must fail on a wrong topology.
- [x] 7.3 `uv run ruff check .` -- clean.
- [ ] 7.4 Open the cockpit in a browser and run one plain turn and one fan-out turn. This is the
      one check no test replaces, and the phase says so in its own known limits.
