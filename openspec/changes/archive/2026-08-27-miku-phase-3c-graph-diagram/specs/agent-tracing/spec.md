## MODIFIED Requirements

### Requirement: Trace events carry enough context to reconstruct a turn

Each event SHALL identify the turn it belongs to, the kind of event, the node involved, and a
timestamp. Tool events SHALL additionally record the tool name and whether it succeeded.

Timestamps within one turn SHALL be directly comparable and SHALL NOT decrease along a causal
chain, so that the elapsed position of any event within its turn can be computed by subtraction
without consulting anything outside the turn. A timestamp SHALL NOT be read as the boundary of a
step's work: one node records its event before performing its work so that the work it causes has
a parent to hang under, while the others record theirs after, and nothing in the file distinguishes
the two.

A tool SHALL be traced twice: once when its invocation is requested, recording the arguments
it was asked to run with, and once when it has run, recording whether it succeeded. Both
SHALL be linked to the step that caused them, so that a request and its outcome reconstruct
under the same parent. No tool invocation SHALL be reported to an observer by any route that
does not also write it to the file.

Each event SHALL also carry its own identifier and the identifier of the event that caused it,
so that a turn is reconstructed as a tree from those links rather than from the order of lines in
the file. Events belonging to a fan-out branch SHALL additionally record which branch they belong
to. The file SHALL remain one JSON object per line, append-only, with no event requiring a later
event in order to be written.

#### Scenario: Events of one turn can be isolated

- **WHEN** two turns have run
- **THEN** each event can be attributed to exactly one turn by its recorded turn identifier

#### Scenario: Elapsed position within a turn is computable from the file alone

- **WHEN** a turn's events are read back
- **THEN** subtracting the first event's timestamp from any event's timestamp yields that event's
  elapsed position within the turn
- **AND** no event in a causal chain carries a timestamp earlier than the event that caused it

#### Scenario: A requested tool call records its arguments

- **WHEN** a turn calls a tool
- **THEN** the trace contains an event recording that the call was requested, the tool name,
  and the arguments it was given
- **AND** that event names a causing event that appears in the same turn

#### Scenario: A failed tool call is distinguishable from a successful one

- **WHEN** a tool raises during a turn
- **THEN** the corresponding trace event records the tool name and marks it as failed

#### Scenario: Hitting the iteration cap is visible in the trace

- **WHEN** a turn ends because the iteration cap was reached
- **THEN** the trace contains an event recording that the cap terminated the turn

#### Scenario: A linear turn reconstructs as a chain

- **WHEN** a turn runs without delegation
- **THEN** every event except the first names a causing event that appears in the same turn
- **AND** following those links yields a single chain

#### Scenario: Concurrent branches reconstruct as a tree

- **WHEN** a turn delegates to a fan-out subgraph
- **THEN** every branch event names the same causing event
- **AND** each branch event records a distinct branch identifier

#### Scenario: Out-of-order arrival does not corrupt the tree

- **WHEN** branch events are written in an order different from the order the branches started
- **THEN** the reconstructed tree is unchanged
- **AND** no branch is attributed to the wrong causing event

#### Scenario: Every line remains independently parseable

- **WHEN** a turn is interrupted before it completes
- **THEN** every event already written parses as a standalone JSON object
