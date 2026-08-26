## ADDED Requirements

### Requirement: One request budget bounds a whole turn

A turn SHALL have a single budget counting model requests, and every model request made during
that turn — by the main loop or by any delegated subgraph — SHALL be counted against it. The
limit SHALL be configurable.

#### Scenario: Delegated requests count against the same budget

- **WHEN** a turn makes a model request and then delegates to a subgraph that makes further
  requests
- **THEN** the budget's spent count equals the total number of requests made by both
- **AND** no separate per-subgraph count governs the turn

#### Scenario: Breadth multiplied by depth stays bounded

- **WHEN** a model repeatedly requests a delegating tool across several iterations
- **THEN** the total number of model requests in the turn does not exceed the configured limit

### Requirement: A budget belongs to exactly one turn

A budget SHALL be created for each turn and SHALL NOT be shared between turns, including turns
running concurrently in one process. A later turn SHALL begin with its full allowance regardless
of what an earlier turn spent.

#### Scenario: A second turn starts with a full allowance

- **WHEN** one turn spends part of its budget and a second turn then runs
- **THEN** the second turn's spent count starts at zero

#### Scenario: Concurrent turns do not share an allowance

- **WHEN** two turns run concurrently in one session
- **THEN** neither turn's spending reduces the allowance available to the other

### Requirement: Exhaustion stops further requests and is visible

When the budget is exhausted, no further model request SHALL be made for that turn. The turn
SHALL still produce a reply, and the trace SHALL record that the budget stopped the work.

#### Scenario: The budget is exhausted mid-turn

- **WHEN** the budget reaches its limit while work remains
- **THEN** no further model request is made for that turn
- **AND** the trace contains an event recording budget exhaustion
- **AND** the turn produces a reply rather than raising

#### Scenario: A normal turn is unaffected

- **WHEN** a turn completes using fewer requests than the limit
- **THEN** no budget exhaustion event appears in the trace

### Requirement: The budget is not under model control

The budget SHALL NOT be exposed as a model-facing tool argument, and a model SHALL NOT be able
to raise, reset, or bypass it. Per-turn budget context SHALL reach tools through the invocation
configuration rather than through the arguments the model supplies.

#### Scenario: The limit does not appear in a tool schema

- **WHEN** the tool schemas bound to the model are inspected
- **THEN** no schema exposes a budget or limit parameter

#### Scenario: A delegating tool still receives the budget

- **WHEN** a delegating tool executes
- **THEN** it counts its requests against the turn's budget without the model having passed it
