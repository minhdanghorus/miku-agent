## ADDED Requirements

### Requirement: Consolidation is invoked explicitly, never during a turn

The system SHALL expose consolidation as an explicitly invoked pass over one user's stored
facts. A conversational turn SHALL NOT trigger consolidation, whether directly, on a fact
count threshold, or on session open or close. The pass SHALL NOT be reachable as a tool the
model can call.

#### Scenario: A turn never consolidates

- **WHEN** any number of turns run, including turns that call `remember`
- **THEN** no fact acquires a supersession marker as a result of those turns
- **AND** the turn's request count is unchanged from what it would be without this capability

#### Scenario: The pass is not a model-callable tool

- **WHEN** the tool registry is built for a session
- **THEN** no registered tool exposes consolidation

#### Scenario: The pass runs on demand

- **WHEN** consolidation is invoked for a user with stored facts
- **THEN** it reads that user's live facts and produces a result without a turn being active

### Requirement: The model proposes a plan; code applies it

Consolidation SHALL obtain a plan of operations from a model, and the system SHALL apply that
plan itself. The model SHALL NOT write to the store, and SHALL NOT be given tools that write
to the store.

The plan SHALL support exactly four operation kinds:

- `supersede` — one fact is corrected by a later fact
- `duplicate` — one or more facts restate another fact
- `merge` — several facts are fragments of one preference and become a single new fact
- `expire` — a time-bound fact whose window has passed

#### Scenario: The plan drives the writes

- **WHEN** the model returns a plan naming facts to supersede
- **THEN** exactly the named facts are marked superseded
- **AND** no fact absent from the plan is modified

#### Scenario: An empty plan writes nothing

- **WHEN** the model returns a plan with no operations
- **THEN** no stored fact is modified
- **AND** the pass reports that nothing changed

#### Scenario: The pass refuses a model that cannot return structured output

- **WHEN** the resolved model's declared `native_structured_output` capability is not `yes`
- **THEN** the pass fails with an error naming the model and the role
- **AND** no stored fact is modified

### Requirement: Resolution is recorded, never destructive

A resolved fact SHALL retain its row and its original fact text byte-for-byte. Resolution SHALL
be recorded by setting a supersession marker and a supersession timestamp on that row. The pass
SHALL NOT delete a stored fact and SHALL NOT rewrite the text of an existing fact.

#### Scenario: A superseded fact keeps its text

- **WHEN** a fact is superseded by a later one
- **THEN** the superseded row still exists in the store
- **AND** its fact text is identical to what was originally written
- **AND** it carries a supersession marker and timestamp

#### Scenario: Nothing is deleted

- **WHEN** a pass applies operations of every kind
- **THEN** the number of rows in the store is greater than or equal to the number before the
  pass

#### Scenario: An expired fact is tombstoned, not removed

- **WHEN** a fact is expired
- **THEN** its row remains with its original text and a supersession timestamp
- **AND** it names no replacement

### Requirement: A merge records its sources

A `merge` operation SHALL write a new fact with a new key, and that new fact SHALL record the
keys of every source it was derived from. Each source SHALL then be marked as superseded by the
new fact's key.

#### Scenario: Provenance is traversable in both directions

- **WHEN** three facts are merged into one
- **THEN** the new fact records all three source keys
- **AND** each of the three source rows names the new fact's key as its successor

#### Scenario: A merge names at least two sources

- **WHEN** a plan contains a merge naming fewer than two sources
- **THEN** that operation is not applied

### Requirement: Operations are validated before they are applied

Every proposed operation SHALL be validated before it is applied. An operation that fails
validation SHALL be dropped with a recorded reason, and SHALL NOT abort the pass or raise to
the caller.

Validation SHALL reject an operation that references a fact outside the live set, an operation
that references a fact already claimed by another operation in the same plan, a merge whose
resulting text is empty, and a supersession whose replacement is not strictly newer than the
fact it replaces.

#### Scenario: A backwards supersession is rejected

- **WHEN** a plan proposes that a fact be superseded by an older fact
- **THEN** that operation is not applied
- **AND** neither fact is modified
- **AND** a reason is recorded

#### Scenario: An unknown fact reference is dropped

- **WHEN** a plan references a fact index outside the live set
- **THEN** that operation is not applied
- **AND** the remaining valid operations are still applied

#### Scenario: A fact claimed twice is rejected

- **WHEN** a plan names the same fact in two operations
- **THEN** at most one of those operations is applied

#### Scenario: A partly invalid plan still makes progress

- **WHEN** a plan contains both valid and invalid operations
- **THEN** the valid operations are applied
- **AND** the pass completes without raising

### Requirement: A dry run reports without writing

The pass SHALL support a mode that produces and validates the same plan but writes nothing.
The reporting mode and the applying mode SHALL share one code path up to the point of writing.

#### Scenario: A dry run leaves the store untouched

- **WHEN** the pass runs in reporting mode over facts that contain a contradiction
- **THEN** the reported plan names the operation that would be applied
- **AND** no stored fact is modified

#### Scenario: A dry run and a real run agree

- **WHEN** the pass runs in reporting mode and then in applying mode over the same facts with
  the same model output
- **THEN** the operations applied are the operations the dry run reported

### Requirement: The pass is bounded by a request budget

A consolidation run SHALL claim its model requests from a request budget belonging to that run
alone. When the budget is exhausted the pass SHALL stop making requests and SHALL report what
it completed rather than raising.

#### Scenario: A run cannot exceed its allowance

- **WHEN** a consolidation run is configured with an allowance of N requests
- **THEN** the run issues no more than N model requests

#### Scenario: Two runs do not share an allowance

- **WHEN** two consolidation runs execute in sequence
- **THEN** the second run starts with a full allowance

### Requirement: Consolidation is idempotent

Running the pass again over an already-consolidated fact set SHALL produce no further changes.

#### Scenario: A second run is a no-op

- **WHEN** the pass runs over a fact set, and then runs again over the resulting live facts
- **THEN** the second run applies no operations
- **AND** no fact acquires a second supersession marker

#### Scenario: Superseded facts are not reconsidered

- **WHEN** the pass runs over a store that already contains superseded rows
- **THEN** only live facts are presented to the model

### Requirement: A consolidation run is traced as its own root

The pass SHALL write trace events using the same JSONL sink and the same `{kind, ...}` event
shape as a turn, under a run identifier of its own. Events SHALL record the operations
proposed, the operations applied, and the reason for each operation dropped.

#### Scenario: The run is readable back from the trace

- **WHEN** a consolidation run completes
- **THEN** the trace contains events under a single run identifier distinct from any turn
- **AND** the counts of proposed and applied operations are recorded

#### Scenario: A dropped operation records why

- **WHEN** an operation fails validation
- **THEN** a trace event records that operation and the reason it was dropped

#### Scenario: Tracing failure does not stop the pass

- **WHEN** the trace sink cannot be written during a run
- **THEN** the pass completes and reports its result
