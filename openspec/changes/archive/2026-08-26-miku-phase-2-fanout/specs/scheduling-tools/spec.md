## ADDED Requirements

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
