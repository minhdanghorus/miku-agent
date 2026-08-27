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

### Requirement: Secrets are redacted at the trace sink

The sink SHALL redact API keys and other configured secret values before writing, so that no
secret can reach a trace file regardless of what a caller passes in.

Redaction SHALL apply to every consumer of an event, not only to the file. An observer
watching a running turn SHALL be shown exactly what was written and never the payload as the
caller passed it, so that attaching an observer cannot become a second route by which a
secret escapes.

#### Scenario: A secret value in an event payload is redacted

- **WHEN** an event payload contains the active provider's API key
- **THEN** the written line contains a redaction marker in its place
- **AND** the key does not appear anywhere in the file

#### Scenario: An observer is shown the redacted event

- **WHEN** an event payload containing the active provider's API key is recorded while an
  observer is attached
- **THEN** the observer receives the redaction marker in its place
- **AND** the observer never receives the secret value

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

### Requirement: A running turn is observable through the sink

The system SHALL allow a caller to observe a turn's events as they are recorded, so that a
gateway can show progress while a turn runs rather than only its final reply. Observation
SHALL be per turn, so that concurrent turns do not deliver each other's events.

Observation SHALL be the only mechanism a gateway uses to watch a turn. Events produced
inside delegated work — a subgraph reached through a tool, invisible to the parent graph's
own update stream — SHALL reach an observer by this route like any other event.

An observer SHALL NOT be able to affect the turn. If observation raises, the turn SHALL
continue and produce its reply, on the same terms as a failed write.

#### Scenario: Delegated work reaches an observer

- **WHEN** a turn delegates to a fan-out subgraph while an observer is attached
- **THEN** the observer receives the subgraph's branch events
- **AND** each carries the same causing event and a distinct branch identifier

#### Scenario: Concurrent turns do not cross observers

- **WHEN** two turns run at once, each with its own observer
- **THEN** no observer receives an event carrying the other turn's identifier

#### Scenario: A failing observer does not break the turn

- **WHEN** an attached observer raises on every event
- **THEN** the turn still completes and returns a reply
- **AND** the trace file still contains the turn's events
