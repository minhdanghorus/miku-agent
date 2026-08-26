# Scheduling Tools

## Purpose

The flagship task surface: creating and listing calendar events.

Tool inputs carry absolute dates only. Resolving "Saturday" to a date happens before the
tool is called, against a reference date that can be pinned - otherwise an assertion about
"next Saturday" would change verdict depending on the day it ran.

Events live in a plain queryable table so that correctness can be checked against stored
rows rather than against how a reply was phrased.

## Requirements

### Requirement: Events can be created through a tool

The system SHALL expose a `create_event` tool that records a calendar event with a title, a
date, and a start time. The tool SHALL persist the event and return a confirmation identifying
what was stored.

#### Scenario: A scheduling request creates one event

- **WHEN** the user asks to book a named activity at a stated day and time
- **THEN** the agent calls `create_event` with the title, resolved date, and start time
- **AND** one event row is persisted
- **AND** the reply confirms the stored title, date, and time

#### Scenario: Two events requested in one message both persist

- **WHEN** the user asks to book two distinct activities in a single message
- **THEN** two events are persisted
- **AND** neither overwrites the other

#### Scenario: A request missing a time is not silently guessed

- **WHEN** the user asks to book something without stating a time, and no default is derivable
  from remembered preferences
- **THEN** the agent asks for the missing time rather than persisting an event
- **AND** no event row is created

### Requirement: Events can be listed through a tool

The system SHALL expose a `list_events` tool that returns the persisted events for a given
date, ordered by start time.

#### Scenario: Listing a day that has events

- **WHEN** the user asks what is scheduled on a date that has events
- **THEN** the agent calls `list_events` for that date
- **AND** the reply names each event with its start time, in ascending time order

#### Scenario: Listing a day with no events

- **WHEN** the user asks what is scheduled on a date with no events
- **THEN** the tool returns an empty result
- **AND** the reply states that nothing is scheduled rather than inventing an event

### Requirement: Relative dates are resolved against the current date

Tool inputs SHALL carry absolute dates. Relative expressions such as "Saturday" or "tomorrow"
SHALL be resolved to an absolute date before persistence, using the current date as the
reference point.

#### Scenario: A weekday name resolves to the next such weekday

- **WHEN** the user says "Saturday" and today is not Saturday
- **THEN** the persisted event date is the next occurring Saturday
- **AND** the stored date is absolute, not the literal word

#### Scenario: The reference date is injectable for testing

- **WHEN** an evaluation supplies a fixed current date
- **THEN** relative expressions resolve against that fixed date
- **AND** the assertion on the resulting absolute date is stable over time

### Requirement: Events are stored in a queryable local table

Events SHALL be persisted in a SQLite table under the runtime state directory, with columns
sufficient to assert on them directly: title, date, start time, and creation timestamp. No
calendar-file export is in scope for this phase.

#### Scenario: Stored events are readable without the agent

- **WHEN** an event has been created through the agent
- **THEN** it can be read by querying the SQLite table directly
- **AND** an evaluation can assert on the stored row rather than on the reply text

#### Scenario: State survives process restart

- **WHEN** events are created, the process exits, and a new process starts
- **THEN** the previously created events are still listed

### Requirement: Under-specified scheduling requests are served by a proposal tool

A tool SHALL be available for requests that name something to schedule without stating when.
It SHALL take the task and the window to search, propose several candidate slots, and return one
recommendation together with the alternatives considered. Every proposed slot SHALL carry an
absolute ISO date and a 24-hour start time, resolved before the recommendation is returned.

#### Scenario: A request without a time is served by the proposal tool

- **WHEN** the user asks for a good time for a task without naming a day and time
- **THEN** the proposal tool is called
- **AND** the event-creation tool is not called in the same step

#### Scenario: A request that already states a time is not fanned out

- **WHEN** the user names both a day and a start time
- **THEN** the event-creation tool is called
- **AND** the proposal tool is not called

#### Scenario: Recommended slots are absolute

- **WHEN** the proposal tool returns
- **THEN** every slot it names carries an absolute ISO date and a 24-hour start time
- **AND** no slot is expressed as a weekday name or a relative phrase

#### Scenario: Proposing does not book

- **WHEN** the proposal tool returns a recommendation
- **THEN** no event has been written to the calendar table
- **AND** booking requires a subsequent event-creation call

### Requirement: A tool whose scope overlaps another states when not to use it

Where two registered tools could plausibly serve the same request, each description SHALL state
the condition under which the other applies. Tool boundaries SHALL be expressed in the tool
descriptions rather than in routing code, and no classifier step SHALL be introduced to choose
between tools.

#### Scenario: Overlapping descriptions name their boundary

- **WHEN** the registered tool descriptions are inspected
- **THEN** the proposal tool's description states the condition under which the event-creation
  tool applies instead
- **AND** the event-creation tool's description states that it requires an absolute date and time

#### Scenario: No routing step precedes tool selection

- **WHEN** a turn selects between the scheduling tools
- **THEN** the selection is made by the model's tool choice
- **AND** no additional model call is made for the purpose of routing
