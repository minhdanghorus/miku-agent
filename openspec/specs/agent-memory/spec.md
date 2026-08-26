# Agent Memory

## Purpose

Two tiers of memory, deliberately kept apart.

Thread state is this conversation's message history, persisted per thread and resumable
across restarts. Long-term facts are what the agent knows about the user, visible from every
thread. Conflating the two is the easiest way to make an agent's memory illegible, so the
requirements below keep each out of the other's storage.

Facts are written only when explicitly asked for; nothing infers what is worth keeping. What
was kept is not rewritten in place either - an explicit consolidation pass resolves which
facts are still true, but it never deletes a row or changes the text of one.

## Requirements

### Requirement: Thread state is persisted by a checkpointer

Conversation state SHALL be persisted per thread by a SQLite-backed LangGraph checkpointer,
keyed by a thread identifier. A turn SHALL be able to continue an existing thread's history
across process restarts.

#### Scenario: A thread continues after restart

- **WHEN** a conversation has several turns, the process exits, and a new process resumes the
  same thread identifier
- **THEN** the earlier messages are present in the assembled context
- **AND** the agent can answer a question that depends on them

#### Scenario: Two threads do not see each other's history

- **WHEN** two different thread identifiers are used in turn
- **THEN** neither thread's assembled context contains the other's messages

#### Scenario: A new thread starts empty

- **WHEN** a previously unused thread identifier is used
- **THEN** the assembled context contains no prior messages
- **AND** the turn completes normally

### Requirement: Long-term facts are stored across threads

Facts SHALL be persisted in a SQLite-backed LangGraph `Store` under a namespace scoped to the
user, so that they are visible from every thread. The checkpointer SHALL NOT be used for
long-term facts, and the store SHALL NOT be used for message history.

A stored fact SHALL carry its text and the time it was written. It MAY additionally carry a
supersession marker naming the fact that replaced it, the time it was superseded, and — when
it was produced by merging others — the keys it was derived from.

#### Scenario: A fact learned in one thread is available in another

- **WHEN** a fact is remembered during one thread and a different thread later runs a turn
- **THEN** the fact appears in the second thread's assembled context

#### Scenario: Facts survive process restart

- **WHEN** a fact is remembered, the process exits, and a new process starts
- **THEN** the fact is still recalled into the assembled context

#### Scenario: Supersession is stored beside the fact, not in the checkpointer

- **WHEN** a fact is marked superseded
- **THEN** the marker is stored on that fact's row in the store
- **AND** no thread's checkpointed state is modified

### Requirement: Remembering is an explicit tool call

The system SHALL expose a `remember` tool that writes one fact to long-term memory. Facts SHALL
be written only through this tool in this phase; the system SHALL NOT decide autonomously which
statements to persist.

#### Scenario: The user asks to be remembered

- **WHEN** the user states a preference and asks for it to be remembered
- **THEN** the agent calls `remember` with that fact
- **AND** one fact entry is persisted
- **AND** the reply confirms what was remembered

#### Scenario: Ordinary conversation does not write facts

- **WHEN** a turn contains no request to remember anything and the agent does not call
  `remember`
- **THEN** no new fact entries are persisted

#### Scenario: A remembered preference influences a later turn

- **WHEN** a preference about scheduling has been remembered, and a later turn schedules
  something where that preference applies
- **THEN** the recalled fact is present in the assembled context for that turn

### Requirement: Recall excludes superseded facts

Recall SHALL return only live facts — those carrying no supersession marker. A fact marked as
superseded SHALL NOT appear in the assembled context, and SHALL NOT be returned to any other
caller of recall.

A fact row written before supersession existed carries no marker and SHALL be treated as live,
so no migration is required.

#### Scenario: A superseded fact leaves the assembled context

- **WHEN** a fact has been marked superseded, and a later turn assembles context
- **THEN** that fact is absent from the assembled context
- **AND** the fact that superseded it is present

#### Scenario: Every recall path filters alike

- **WHEN** facts are recalled during context assembly and during a proposal tool call
- **THEN** neither result contains a superseded fact

#### Scenario: Rows written before supersession existed are live

- **WHEN** a fact row carries no supersession marker
- **THEN** it is recalled

### Requirement: No retrieval gate in this phase

Recall SHALL read the live facts for the active user directly, without a model-driven gate
deciding whether to retrieve, and without a similarity ranking selecting a subset. Retrieval
selection is deferred so it can be measured against real accumulated data before being built.

#### Scenario: Recall requires no extra model call

- **WHEN** context is assembled for a turn
- **THEN** live facts are read from the store without an additional model request

#### Scenario: Every live fact is recalled

- **WHEN** context is assembled for a turn and the user has several live facts
- **THEN** all of them appear in the assembled context, up to the configured limit
