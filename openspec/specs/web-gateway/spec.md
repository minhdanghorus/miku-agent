# Web Gateway

## Purpose

A second gateway: a local web application that serves the cockpit and streams a turn to
the browser as it runs.

It is a peer of the terminal gateway, not a layer above it. Neither imports the other, and
both reach the agent only through the shared session entry point. The gateway moves data
between HTTP and the session and holds no agent logic of its own - anything it renders about
runtime state comes from the runtime inspection surface.

Delivering progress to a browser is never allowed to affect the turn producing it.

## Requirements

### Requirement: A console command starts a local web gateway

The system SHALL provide a console entry point, separate from the terminal gateway, that
serves a local web application over HTTP. The two gateways SHALL be peers: neither imports
the other, and each reaches the agent only through the shared session entry point.

The server SHALL bind a loopback interface by default. It SHALL NOT authenticate, and it
SHALL NOT be designed for exposure beyond the local machine.

#### Scenario: The web gateway starts without the terminal gateway

- **WHEN** the web gateway module is imported
- **THEN** no module belonging to the terminal gateway is imported as a result

#### Scenario: The terminal gateway is unaffected

- **WHEN** the terminal gateway command is launched
- **THEN** a conversation starts exactly as it did before the web gateway existed

#### Scenario: Missing optional dependencies are reported as a sentence

- **WHEN** the web gateway is launched without its optional dependencies installed
- **THEN** a single message naming what to install is printed
- **AND** the process exits with a non-zero status without a traceback

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

### Requirement: A turn is streamed to the browser as it runs

The system SHALL expose an endpoint that accepts a user message and a thread identifier,
runs exactly one turn, and streams that turn's progress to the client as a sequence of
server-sent events over a single connection.

Each streamed event SHALL be the same `{kind, ...}` record the trace sink produces, without
reshaping. The stream SHALL end with a terminating event carrying the turn's reply, its turn
identifier, and the number of model requests it spent.

#### Scenario: A plain turn streams progress and then a reply

- **WHEN** a message is posted that the agent answers with one tool call
- **THEN** the response content type is a server-sent event stream
- **AND** the client receives at least one progress event before the terminating event
- **AND** the terminating event carries the reply, the turn identifier, and the request count

#### Scenario: Delegated work is visible in the stream

- **WHEN** a turn delegates to a fan-out subgraph
- **THEN** the stream contains one record per branch
- **AND** every branch record names the same causing event
- **AND** each branch record carries a distinct branch identifier

#### Scenario: Events reach the client with causal links intact

- **WHEN** a turn streams to completion
- **THEN** every streamed record except the first names a causing event that also appeared
  in the same stream

#### Scenario: One request runs exactly one turn

- **WHEN** a single message is posted
- **THEN** exactly one turn is recorded against the given thread identifier

#### Scenario: A turn that fails mid-stream reports the failure as an event

- **WHEN** a turn raises after the response headers have been sent
- **THEN** the client receives an event identifying the failure
- **AND** the connection closes without the client waiting indefinitely

### Requirement: A slow or absent client never affects the turn

Delivering progress to a client SHALL NOT block, delay, or fail the turn producing it. If
the client disconnects mid-turn, the turn SHALL run to completion and its results SHALL be
recorded as normal.

#### Scenario: The client disconnects mid-turn

- **WHEN** a client closes the connection while a turn is in flight
- **THEN** the turn still completes
- **AND** its stored state and trace records are the same as for an uninterrupted turn

### Requirement: Concurrent turns do not interfere

The gateway SHALL serve turns on distinct threads concurrently from a single session. Each
turn SHALL have its own request allowance and its own turn identifier, and no turn's events
SHALL be attributed to another.

#### Scenario: Two turns run at once

- **WHEN** two messages on distinct thread identifiers are posted concurrently
- **THEN** both replies are produced
- **AND** each turn's recorded request count reflects only its own model requests
- **AND** no event produced by one turn carries the other turn's identifier

### Requirement: The cockpit is served as static assets with no build step

The system SHALL serve the cockpit's HTML, stylesheet, and scripts as static files from the
same server. The repository SHALL NOT require a JavaScript package manager, bundler, or
build step to produce them.

#### Scenario: The cockpit loads from a clean checkout

- **WHEN** the web gateway is started from a checkout with only Python dependencies
  installed
- **THEN** the cockpit page and its assets are served successfully

#### Scenario: No frontend build tooling is required

- **WHEN** the repository is examined
- **THEN** it declares no JavaScript package manifest, lockfile, or bundler configuration

### Requirement: The web gateway is testable without a network listener

The application SHALL be constructible against an injected session, so that its endpoints
can be exercised in-process with a stubbed model and a frozen clock, without binding a port
and without provider credentials.

#### Scenario: Endpoints are exercised in-process

- **WHEN** the application is constructed with a stubbed session and a turn is posted
- **THEN** the stream is produced and asserted on without any network listener being bound
- **AND** no provider credential is required

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
