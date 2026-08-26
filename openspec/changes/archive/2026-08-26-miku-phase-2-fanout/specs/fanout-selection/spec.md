## ADDED Requirements

### Requirement: Candidates are generated in parallel by a fan-out subgraph

A delegated subgraph SHALL generate N candidate answers concurrently using LangGraph's `Send`
API, collect them through a reducer field, and reduce them to one selection. The number of
branches SHALL be configurable. The subgraph SHALL NOT be implemented with a prebuilt agent
constructor.

#### Scenario: N branches run for one delegation

- **WHEN** the fan-out subgraph is invoked with a branch count of N
- **THEN** exactly N generate steps run for that invocation
- **AND** every generate step records the same parent step in the trace

#### Scenario: All branches complete before selection runs

- **WHEN** the branches finish out of order
- **THEN** the selection step runs exactly once
- **AND** it runs only after every branch that completed has been collected

#### Scenario: The branch count follows configuration

- **WHEN** the configured branch count is changed
- **THEN** the number of generate steps in a delegation changes to match

### Requirement: Each branch receives a distinct angle

Branch diversity SHALL be structural, not produced by sampling temperature. The subgraph SHALL
assign each branch a distinct angle drawn from a default list held in code. A caller MAY supply
its own list of angles, in which case that list SHALL be used instead of the default.

#### Scenario: Default angles are distinct per branch

- **WHEN** a delegation runs with no angles supplied
- **THEN** each branch is recorded in the trace with an angle
- **AND** no two branches of that delegation share the same angle

#### Scenario: Supplied angles override the default list

- **WHEN** a delegation is invoked with an explicit list of angles
- **THEN** the branches are assigned the supplied angles
- **AND** none of the default angles appear for that delegation

#### Scenario: More branches are requested than angles exist

- **WHEN** the requested branch count exceeds the number of available angles
- **THEN** the number of branches is reduced to the number of distinct angles
- **AND** the reduction is recorded in the trace

### Requirement: One selection step chooses among the collected candidates

A single selection step SHALL choose exactly one candidate from those collected, using a model
call, and SHALL record which candidate it chose. Selection SHALL choose among the candidates it
was given and SHALL NOT compute new answers of its own.

#### Scenario: The selection identifies one candidate

- **WHEN** the selection step completes
- **THEN** the trace records the index of the chosen candidate
- **AND** the chosen index refers to a candidate that was collected

#### Scenario: A single candidate needs no model call

- **WHEN** only one candidate was collected
- **THEN** that candidate is selected without a model call

### Requirement: A partial fan-out still produces a result

If some branches do not produce a candidate — because the request budget was exhausted or a
branch failed — the subgraph SHALL select from the candidates that did arrive rather than fail.
If no candidate arrived, the subgraph SHALL return a result explaining that, not raise.

#### Scenario: Some branches fail

- **WHEN** at least one branch raises and at least one produces a candidate
- **THEN** selection runs over the successful candidates
- **AND** the delegation returns a result rather than propagating an exception

#### Scenario: No branch produces a candidate

- **WHEN** every branch fails or is skipped
- **THEN** the delegation returns a result stating that no candidate was produced
- **AND** the calling turn still produces a reply

### Requirement: The subgraph result crosses the tool boundary as text

The subgraph SHALL format its own final result into the text form a tool result takes. The node
that executes tools SHALL NOT require knowledge of whether a tool is backed by a subgraph or by
a plain function.

#### Scenario: A subgraph-backed tool result is indistinguishable in shape

- **WHEN** a subgraph-backed tool completes
- **THEN** the tool result placed in state has the same shape as any other tool result
- **AND** it is attributed to the tool call that requested it
