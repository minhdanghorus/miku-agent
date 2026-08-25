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
