## ADDED Requirements

### Requirement: A registered tool may be backed by a subgraph

A tool MAY be implemented by a compiled graph rather than a plain function. The node that
executes tools SHALL treat both kinds identically: it SHALL NOT inspect a tool to decide how to
call it, and SHALL NOT branch on whether a tool delegates. The main loop SHALL remain the three
nodes it already is, with no node added for delegation and no change to how the agent node routes.

#### Scenario: Delegation adds no node to the main graph

- **WHEN** a turn calls a subgraph-backed tool
- **THEN** the main graph's nodes and edges are the same as for a turn that calls a plain tool
- **AND** the agent node routes on the presence of tool calls exactly as before

#### Scenario: The tools node does not distinguish tool kinds

- **WHEN** the tools node executes a subgraph-backed tool and a plain tool in the same step
- **THEN** both are invoked the same way
- **AND** both results are placed in state attributed to their own tool call

#### Scenario: A failing subgraph-backed tool behaves like any failing tool

- **WHEN** a subgraph-backed tool raises
- **THEN** an error result for that tool call is placed in state and control returns to the
  agent node
- **AND** the turn produces a reply rather than propagating the exception

### Requirement: Per-turn context reaches tools through the invocation configuration

Context that belongs to a turn rather than to a session — the turn's trace parentage and its
request budget — SHALL be supplied to a tool at invocation time through the invocation
configuration, not captured when the tool is built and not passed as a model-supplied argument.
Tools that do not need per-turn context SHALL be unaffected.

#### Scenario: A delegating tool receives the turn's context

- **WHEN** the tools node invokes a tool that needs per-turn context
- **THEN** the tool receives the current turn's trace parentage and budget
- **AND** events it emits are attributed to the current turn

#### Scenario: Two turns in one session receive different context

- **WHEN** two turns in the same session each invoke the same delegating tool
- **THEN** each invocation receives its own turn's context
- **AND** neither invocation observes the other's budget or trace parentage

#### Scenario: Per-turn context is invisible to the model

- **WHEN** the tool schemas bound to the model are inspected
- **THEN** no schema exposes trace parentage or budget as a parameter
