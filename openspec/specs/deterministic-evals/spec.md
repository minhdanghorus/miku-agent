# Deterministic Evals

## Purpose

The test suite that pins the agent's behaviour.

Cases drive the real compiled graph through a single task function, so the suite exercises
what ships rather than a reimplementation of it. Every evaluator asserts on which tool ran
and what was persisted, never on how a reply is worded - small models phrase things
differently on every run, and stored rows do not.

Cases needing a live provider are separable from those that do not, so the offline subset
runs without credentials or spend.

## Requirements

### Requirement: The graph is evaluated through one task function

Evaluations SHALL drive the agent through a single async task function that takes case inputs
and returns the turn's result, so that `pydantic-evals` cases exercise the real graph rather
than a reimplementation of it. Cases SHALL NOT call model clients or tools directly.

#### Scenario: A case runs a real turn

- **WHEN** a case is evaluated
- **THEN** the graph runs assemble-context, agent, and any tool nodes as it would in the CLI
- **AND** the result reflects that turn's tool calls and reply

#### Scenario: Each case runs in isolated state

- **WHEN** two cases run in the same suite
- **THEN** neither sees the other's persisted events, facts, or thread history
- **AND** case order does not change the outcome

### Requirement: Deterministic evaluators assert on observable state, not prose

Evaluators SHALL assert on facts that are stable across model wording: which tools were called
with which arguments, and what was persisted. Assertions on exact reply text SHALL be avoided.

#### Scenario: Tool selection is asserted

- **WHEN** a case asks to book an activity at a stated time
- **THEN** the evaluator passes only if `create_event` was called
- **AND** the assertion does not depend on the reply's phrasing

#### Scenario: Persisted state is asserted

- **WHEN** a case asks to book an activity at a stated time
- **THEN** the evaluator reads the stored event and checks its title, date, and start time
- **AND** the case fails if no event was stored

#### Scenario: A no-tool case asserts that no tool ran

- **WHEN** a case asks a question answerable without tools
- **THEN** the evaluator passes only if no tool was called

### Requirement: The suite covers the loop's contract, not just the happy path

The suite SHALL include cases for: correct tool selection, correct persisted event fields,
relative-date resolution against a fixed reference date, memory recall across threads, and
termination at the iteration cap.

#### Scenario: Relative dates are covered with a fixed reference date

- **WHEN** a case supplies a fixed current date and asks for a weekday by name
- **THEN** the evaluator asserts the stored absolute date
- **AND** the case yields the same verdict when run on a different real-world day

#### Scenario: Cross-thread recall is covered

- **WHEN** a case remembers a fact on one thread and then runs a turn on another
- **THEN** the evaluator asserts the fact was available to the second turn

#### Scenario: The iteration cap is covered without live model calls

- **WHEN** a case drives the loop with a stubbed model that always requests a tool
- **THEN** the turn ends at the configured cap
- **AND** the evaluator asserts termination rather than waiting indefinitely

### Requirement: The suite is runnable and reports per-case outcomes

The suite SHALL be runnable by a single documented command and SHALL report a pass or fail
verdict per case. Cases requiring a live provider SHALL be separable from cases that do not, so
the latter can run without credentials or spend.

#### Scenario: Running the suite reports per-case results

- **WHEN** the documented command is run with credentials available
- **THEN** each case's verdict is reported
- **AND** the command's exit status is non-zero if any case failed

#### Scenario: Running without credentials still exercises offline cases

- **WHEN** the suite is run with no provider credentials
- **THEN** cases that need a live provider are skipped with a stated reason
- **AND** cases driven by a stubbed model still run and report verdicts

### Requirement: Fan-out is asserted on trace structure, not on wording

Evaluators SHALL be able to assert the shape of a turn from its trace: how many branches ran,
what caused them, which angle each carried, and where selection happened. These assertions SHALL
be drivable by the stubbed model, without provider credentials. No evaluator SHALL assert on the
wording of a recommendation.

#### Scenario: Branch count is asserted without credentials

- **WHEN** a fan-out case runs against the stubbed model
- **THEN** the evaluator asserts the number of branch steps from the trace
- **AND** the case runs with no provider credentials present

#### Scenario: Diversity is asserted structurally

- **WHEN** a fan-out case completes
- **THEN** the evaluator asserts that each branch carried a distinct angle
- **AND** the assertion reads recorded angles rather than generated text

#### Scenario: Selection order is asserted

- **WHEN** a fan-out case completes
- **THEN** the evaluator asserts exactly one selection step occurred
- **AND** that it is recorded as caused by the same step that caused the branches

#### Scenario: Budget exhaustion is asserted from the trace

- **WHEN** a case runs with a request budget too small to complete the fan-out
- **THEN** the evaluator asserts a budget exhaustion event is present
- **AND** asserts no model request was recorded after it

### Requirement: Tool routing has live cases

Because tool boundaries are expressed in tool descriptions rather than in code, the suite SHALL
include live cases asserting which tool the model selects for a given request, so that editing a
description in a way that breaks routing fails a test. These cases SHALL be skipped when
credentials are absent.

#### Scenario: A request stating a day and time routes to booking

- **WHEN** a live case sends a request naming both a day and a start time
- **THEN** the evaluator asserts the event-creation tool was called
- **AND** asserts the proposal tool was not called

#### Scenario: A request with no time routes to proposal

- **WHEN** a live case sends a request naming a task but no time
- **THEN** the evaluator asserts the proposal tool was called

#### Scenario: Routing cases skip without credentials

- **WHEN** the suite runs with no provider credentials
- **THEN** the routing cases are skipped rather than failed
