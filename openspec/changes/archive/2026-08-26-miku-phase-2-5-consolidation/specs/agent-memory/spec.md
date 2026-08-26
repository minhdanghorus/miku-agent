## ADDED Requirements

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

## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: No retrieval gate or consolidation in this phase

**Reason**: The requirement bundled two deferrals that have now diverged. Consolidation is the
subject of this change and is specified by the `memory-consolidation` capability, so a blanket
prohibition on it is no longer true. Its promise that stored facts are never "merged,
summarized, or deleted automatically" also over-committed: consolidation merges and retires
facts, though it still never deletes a row or rewrites the text of one.

**Migration**: The retrieval-gate half is preserved verbatim as the new requirement "No
retrieval gate in this phase", which continues to defer selection to the next change. The
non-destructive guarantee is preserved in a narrower and now accurate form by the
`memory-consolidation` requirement "Resolution is recorded, never destructive", which keeps
every row and every original fact text intact. Recall's new obligation is covered by "Recall
excludes superseded facts".
