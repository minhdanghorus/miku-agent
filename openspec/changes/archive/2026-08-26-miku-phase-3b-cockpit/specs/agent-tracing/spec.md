## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Trace events carry enough context to reconstruct a turn

Each event SHALL identify the turn it belongs to, the kind of event, the node involved, and a
timestamp. Tool events SHALL additionally record the tool name and whether it succeeded.

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
