## MODIFIED Requirements

### Requirement: Trace events carry enough context to reconstruct a turn

Each event SHALL identify the turn it belongs to, the kind of event, the node involved, and a
timestamp. Tool events SHALL additionally record the tool name and whether it succeeded.

Each event SHALL also carry its own identifier and the identifier of the event that caused it,
so that a turn is reconstructed as a tree from those links rather than from the order of lines in
the file. Events belonging to a fan-out branch SHALL additionally record which branch they belong
to. The file SHALL remain one JSON object per line, append-only, with no event requiring a later
event in order to be written.

#### Scenario: Events of one turn can be isolated

- **WHEN** two turns have run
- **THEN** each event can be attributed to exactly one turn by its recorded turn identifier

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
