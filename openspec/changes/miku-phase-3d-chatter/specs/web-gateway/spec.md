## ADDED Requirements

### Requirement: A session exposes what a gateway needs by name

A session SHALL expose the handles a gateway legitimately needs — its registered tools, its store,
and its checkpointer — as named accessors. A gateway SHALL NOT reach through a session's internal
structure to obtain them.

The reason is a measurement rather than a preference. The web gateway reached past its session
twice, and that count was left visible on purpose as the price of the phase that introduced it. A
third reach would have turned a recorded cost into a habit, so the accessors are added and the
measurement is spent.

#### Scenario: A gateway obtains handles through accessors

- **WHEN** the web gateway's source is examined
- **THEN** it obtains tools, store, and checkpointer handles through named session accessors
- **AND** it does not reach through the session's dependency structure to reach them

#### Scenario: The accessors report the session's own handles

- **WHEN** a session is opened and each accessor is read
- **THEN** each reports the same handle the session's own graph and tools were built with

### Requirement: Conversations are served as read endpoints

The web gateway SHALL serve the list of conversations and the contents of one conversation as
read-only endpoints. Both SHALL obtain their data from the runtime inspection surface.

Starting a turn SHALL continue to accept the conversation identifier it already accepts. No new
request shape SHALL be required to continue an existing conversation.

#### Scenario: The conversation list is served

- **WHEN** the conversations endpoint is requested
- **THEN** it responds with one entry per held conversation

#### Scenario: One conversation is served

- **WHEN** the endpoint for a specific conversation is requested
- **THEN** it responds with that conversation's exchanges in order

#### Scenario: An unknown conversation is served as empty

- **WHEN** the endpoint is requested for a conversation that does not exist
- **THEN** it responds successfully with no exchanges

#### Scenario: Continuing a conversation needs no new request shape

- **WHEN** a turn is started for an existing conversation
- **THEN** it is accepted through the same request shape a new conversation uses
- **AND** the turn's messages are stored in that conversation

### Requirement: A conversation is removed through the session

The web gateway SHALL offer removal of one conversation, and SHALL perform it by calling the
session rather than by deleting persisted state itself.

This is the same shape running a turn already has: the gateway calls a session method and the
session writes. The constraint the gateway is held to is that it reads no source directly, which a
session call does not breach. Removal SHALL NOT be routed through the inspection surface, which is
read-only.

#### Scenario: Removal is served

- **WHEN** removal is requested for an existing conversation
- **THEN** the request succeeds
- **AND** that conversation is no longer returned by the conversation list endpoint

#### Scenario: Removal goes through the session

- **WHEN** the web gateway's source is examined
- **THEN** its removal endpoint calls a session method
- **AND** it does not call the checkpointer directly

#### Scenario: Removing an unknown conversation succeeds

- **WHEN** removal is requested for a conversation that does not exist
- **THEN** the request succeeds rather than failing

## MODIFIED Requirements

### Requirement: The web gateway holds no agent logic

The web gateway SHALL move data between HTTP and the session and nothing else. It SHALL NOT
assemble prompts, call models, execute tools, or read memory directly. Any read of runtime
state that a rendered view needs SHALL be obtained from the runtime inspection surface
rather than performed by the gateway.

#### Scenario: No prompt assembly or model access in the gateway

- **WHEN** the web gateway's source is examined
- **THEN** it contains no prompt construction, no model client construction, and no direct
  model invocation

#### Scenario: Views read through the inspection surface

- **WHEN** an endpoint serves configuration, tool, memory, or conversation data
- **THEN** the data is obtained from the runtime inspection surface
- **AND** the gateway does not query the store or the checkpointer directly
