## ADDED Requirements

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

#### Scenario: Events of one turn can be isolated

- **WHEN** two turns have run
- **THEN** each event can be attributed to exactly one turn by its recorded turn identifier

#### Scenario: A failed tool call is distinguishable from a successful one

- **WHEN** a tool raises during a turn
- **THEN** the corresponding trace event records the tool name and marks it as failed

#### Scenario: Hitting the iteration cap is visible in the trace

- **WHEN** a turn ends because the iteration cap was reached
- **THEN** the trace contains an event recording that the cap terminated the turn

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
