# Agent Loop

## Purpose

The reasoning cycle: assemble context, call the model, run the tools it asks for, repeat
until it answers.

The loop is hand-built rather than summoned from a prebuilt agent constructor, because its
control flow is the thing this project exists to make readable. It is bounded by a hard
iteration cap and degrades rather than crashes: a tool failure becomes a result the model
can see and recover from.

## Requirements

### Requirement: The loop is a hand-built state graph of three nodes

The agent SHALL be a hand-built LangGraph `StateGraph` with exactly three nodes — assemble
context, agent, and tools — wired so that the agent node routes conditionally to the tools node
when the model requests tool calls, and the tools node routes back to the agent node. The
implementation SHALL NOT use `create_react_agent` or other prebuilt agent constructors, so that
the control flow is readable in the graph definition itself.

#### Scenario: A turn needing no tools ends after one model call

- **WHEN** the user sends a message the model answers directly
- **THEN** the graph runs assemble context, then the agent node, and then finishes
- **AND** the tools node is not entered

#### Scenario: A turn needing a tool loops back through the agent

- **WHEN** the model responds with a tool call
- **THEN** the graph enters the tools node, executes the requested tool, and returns to the
  agent node with the tool result in state
- **AND** the agent node produces the final reply on a later iteration

#### Scenario: Multiple tool calls in one response all execute

- **WHEN** the model returns more than one tool call in a single response
- **THEN** every requested tool call is executed before control returns to the agent node
- **AND** each result is attributed to the tool call that produced it

### Requirement: Working memory is assembled before every model call

The assemble-context node SHALL build the model input from three parts: the persona system
prompt, facts recalled from long-term memory, and the thread's message history. The agent node
SHALL NOT read persona or memory directly.

#### Scenario: The persona is present in every turn

- **WHEN** any turn runs
- **THEN** the persona system prompt is included in the model input
- **AND** it appears as system content rather than as a user message

#### Scenario: Recalled facts are included when they exist

- **WHEN** long-term memory holds facts for the active user
- **THEN** those facts are included in the assembled context
- **AND** the reply may reflect them

#### Scenario: An empty memory produces a valid turn

- **WHEN** long-term memory holds no facts
- **THEN** the context is assembled without a facts section
- **AND** the turn completes normally

### Requirement: The loop is bounded by a hard iteration cap

The graph SHALL enforce a configurable maximum number of agent-node iterations per turn. On
reaching the cap the turn SHALL end and return a reply that states the limit was reached,
rather than continuing or raising an unhandled error.

#### Scenario: A turn that would loop forever terminates

- **WHEN** the model keeps requesting tool calls without ever producing a final reply
- **THEN** the turn ends once the configured iteration cap is reached
- **AND** the user receives a reply stating that the limit was reached
- **AND** the cap event is recorded in the trace

#### Scenario: A normal turn is unaffected by the cap

- **WHEN** a turn completes in fewer iterations than the cap
- **THEN** no cap-related message appears in the reply

### Requirement: Tool failures are returned to the model, not raised to the user

When a tool raises, the tools node SHALL convert the failure into a tool result describing the
error and return control to the agent node, so the model can recover or explain. The turn SHALL
NOT crash on a tool error.

#### Scenario: A tool raises during execution

- **WHEN** an executed tool raises an exception
- **THEN** an error result for that tool call is placed in state and control returns to the
  agent node
- **AND** the turn produces a reply rather than propagating the exception to the gateway
- **AND** the failure is recorded in the trace

#### Scenario: The model requests a tool that does not exist

- **WHEN** the model requests a tool name that is not registered
- **THEN** an error result naming the unknown tool is returned to the agent node
- **AND** no registered tool is executed as a substitute

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
