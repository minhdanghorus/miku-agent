## ADDED Requirements

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

#### Scenario: A fact learned in one thread is available in another

- **WHEN** a fact is remembered during one thread and a different thread later runs a turn
- **THEN** the fact appears in the second thread's assembled context

#### Scenario: Facts survive process restart

- **WHEN** a fact is remembered, the process exits, and a new process starts
- **THEN** the fact is still recalled into the assembled context

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

### Requirement: No retrieval gate or consolidation in this phase

Recall SHALL read the stored facts for the active user directly, without a model-driven gate
deciding whether to retrieve and without a consolidation pass rewriting or compacting stored
facts. These behaviors are deferred so they can be tuned against real accumulated data.

#### Scenario: Recall requires no extra model call

- **WHEN** context is assembled for a turn
- **THEN** facts are read from the store without an additional LLM request

#### Scenario: Stored facts are not rewritten

- **WHEN** many facts accumulate over many turns
- **THEN** existing fact entries remain byte-identical to what was written
- **AND** no fact is merged, summarized, or deleted automatically
