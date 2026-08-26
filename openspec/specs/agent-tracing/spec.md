# Agent Tracing

## Purpose

The observability sink: one JSON object per line, one line per node transition.

Two properties are load-bearing. Redaction happens inside the sink rather than at call
sites, so no caller can leak a secret by forgetting. And a tracing failure never breaks a
turn - observability is not a correctness dependency.

The event shape is chosen so a turn can be reconstructed from the file alone.

## Requirements

### Requirement: Every node transition is traced to a JSONL file

The system SHALL append one JSON object per line to a trace file for each node transition of
every turn. Trace files SHALL live under the runtime state directory, partitioned by date.

#### Scenario: A turn produces a readable trace

- **WHEN** a turn runs to completion
- **THEN** the day's trace file contains one line per node transition of that turn
- **AND** every line parses as JSON on its own

#### Scenario: Traces accumulate across turns and runs

- **WHEN** several turns run, the process restarts, and another turn runs on the same day
- **THEN** all turns' events are present in the same day's file, appended in order
- **AND** no earlier content is overwritten

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

### Requirement: Secrets are redacted at the trace sink

The sink SHALL redact API keys and other configured secret values before writing, so that no
secret can reach a trace file regardless of what a caller passes in.

#### Scenario: A secret value in an event payload is redacted

- **WHEN** an event payload contains the active provider's API key
- **THEN** the written line contains a redaction marker in its place
- **AND** the key does not appear anywhere in the file

#### Scenario: Redaction does not corrupt the line

- **WHEN** a payload containing a secret is redacted
- **THEN** the written line is still valid JSON
- **AND** the event's other fields are unchanged

### Requirement: Tracing failures never break a turn

If writing a trace event fails, the turn SHALL continue and produce its reply. Tracing is
observability, not a dependency of correctness.

#### Scenario: The trace file cannot be written

- **WHEN** the trace destination is not writable during a turn
- **THEN** the turn still completes and returns a reply
- **AND** the failure surfaces as a warning rather than an exception to the user
