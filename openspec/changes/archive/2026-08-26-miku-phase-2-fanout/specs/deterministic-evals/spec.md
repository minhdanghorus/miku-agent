## ADDED Requirements

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
