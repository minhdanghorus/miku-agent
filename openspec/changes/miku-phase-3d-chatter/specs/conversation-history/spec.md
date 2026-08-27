## ADDED Requirements

### Requirement: Conversations are enumerable without running a turn

The system SHALL be able to report which conversations exist, from persisted thread state alone,
without invoking a model, running a turn, or requiring the conversation to have been started in
the current process.

Conversations SHALL be reported newest activity first, so that the list answers "what was I doing"
rather than "what exists".

Enumeration SHALL report one entry per conversation. Persisted state records many checkpoints per
conversation, and a listing that reported checkpoints would grow with turns taken rather than with
conversations held.

#### Scenario: Conversations from an earlier process are listed

- **WHEN** turns are run against two thread identifiers, the process exits, and a new process asks
  which conversations exist
- **THEN** both thread identifiers are reported
- **AND** no model request is made

#### Scenario: One entry per conversation, not per checkpoint

- **WHEN** a conversation with several turns is listed
- **THEN** it appears exactly once

#### Scenario: The most recently active conversation is first

- **WHEN** one conversation receives a turn after another
- **THEN** it is reported before the other

#### Scenario: No conversations reads as empty

- **WHEN** nothing has ever been persisted and the conversations are listed
- **THEN** the result is empty rather than an error

### Requirement: A conversation is named and sized from what it already holds

A listed conversation SHALL carry a human-readable title derived from the first thing the user said
in it, and SHALL report how many stored messages it holds.

No new stored field SHALL be introduced to satisfy this. A title SHALL NOT be produced by a model
call, because that would place a variable-latency, variable-cost request on an interaction that did
not ask for one.

A conversation whose title cannot be derived SHALL still be listed, identified by its thread
identifier.

#### Scenario: The title comes from the opening message

- **WHEN** a conversation is started with a user message and then listed
- **THEN** its title is derived from that message

#### Scenario: The title survives later turns

- **WHEN** further turns are added to that conversation and it is listed again
- **THEN** its title is still derived from the opening message

#### Scenario: Listing makes no model request

- **WHEN** conversations are listed
- **THEN** no model request is recorded

#### Scenario: Size is reported

- **WHEN** a conversation holding a known number of stored messages is listed
- **THEN** the reported count equals that number

### Requirement: A transcript reports what was said, not how it was stored

Reading one conversation SHALL report an ordered sequence of exchanges suitable for display, and
SHALL NOT report the persisted checkpoint structure itself. The persistence format belongs to the
graph library; a surface that exposed it would bind every consumer to that library's shape.

A stored assistant message with no content SHALL NOT be reported as something the assistant said.
Such a message exists to carry requested tool calls, and reporting it produces an empty turn in the
display that corresponds to nothing the user experienced.

Each reported exchange SHALL identify who produced it.

#### Scenario: A tool-calling turn produces no empty exchange

- **WHEN** a conversation contains a turn in which the assistant requested tools before replying
- **THEN** the reported exchanges include the user's message and the assistant's reply
- **AND** no reported assistant exchange has empty content

#### Scenario: Order is preserved

- **WHEN** a conversation with several turns is read
- **THEN** the reported exchanges are in the order they were stored

#### Scenario: The persistence structure is not exposed

- **WHEN** a conversation is read
- **THEN** the report contains no checkpoint, channel, or versioning structure

### Requirement: Tool activity appears as its own kind of line

Work the assistant performed during a turn SHALL be reportable as entries distinct from what the
assistant said, so that a display can render them differently or omit them.

This is possible because tool results in this system are already written as readable sentences
rather than as serialised data. Reporting them is therefore a filtering decision, not a formatting
one, and no tool result is reformatted for display.

#### Scenario: A tool result is reported distinctly

- **WHEN** a conversation containing a completed tool call is read
- **THEN** the tool's result is reported as an entry marked as tool activity
- **AND** it is not reported as something the assistant said

#### Scenario: Tool results are reported verbatim

- **WHEN** a tool result is reported
- **THEN** its text is the text the tool returned, unmodified

### Requirement: A conversation is resumed by its identifier and nothing else

Continuing a conversation SHALL require only its thread identifier. No new state SHALL be
established to resume one, and resuming SHALL work for a conversation this process did not start.

The identifier of the conversation being viewed SHALL be recoverable after the view is reloaded, so
that a reload continues the conversation rather than silently starting a new one.

#### Scenario: A conversation started elsewhere is continued

- **WHEN** a conversation is created, the process exits, and a new process sends a message with the
  same identifier
- **THEN** the assembled context contains the earlier messages

#### Scenario: A reload continues the same conversation

- **WHEN** a conversation is open and the view is reloaded
- **THEN** the same conversation is shown
- **AND** a message sent afterwards is stored in that conversation

#### Scenario: Sending without an identifier starts a new conversation

- **WHEN** a message is sent with no conversation identified
- **THEN** a new identifier is established and reported back

### Requirement: Reading a conversation never changes it

Enumerating conversations and reading one SHALL leave persisted state byte-for-byte unchanged. No
read SHALL create a conversation, advance one, or write a checkpoint.

#### Scenario: Reads leave state unchanged

- **WHEN** every conversation-reading function is called against populated state
- **THEN** the persisted state afterwards is identical to the state before

#### Scenario: Reading an unknown conversation creates nothing

- **WHEN** a conversation that does not exist is read
- **THEN** the result is empty
- **AND** no conversation with that identifier exists afterwards

### Requirement: A reply can be traced to the turn that produced it

Where the turn identifier that produced a reply is known, the display SHALL offer a route from that
reply to that turn's recorded trace.

The turn identifier is not part of persisted conversation state; it is reported when a turn runs.
A reply for which no turn identifier is known SHALL be displayed without such a route rather than
with a broken one.

#### Scenario: A reply from this session links to its trace

- **WHEN** a turn completes and its reply is displayed
- **THEN** the reply offers a route to that turn's trace, identified by the turn identifier the
  turn reported

#### Scenario: A reply read back from storage offers no broken route

- **WHEN** a conversation is read back and no turn identifier is known for a reply
- **THEN** that reply is displayed without a trace route

### Requirement: A conversation can be removed, and the removal states its own boundary

The system SHALL provide a way to remove one conversation, deleting its persisted thread state so
that it is no longer enumerated and no longer resumable.

Removal SHALL NOT be presented as forgetting. Facts remembered during a conversation are stored
against the user rather than against the thread, and recorded traces are keyed by turn rather than
by thread, so neither is reachable from a conversation identifier. Removal therefore reaches
persisted thread state and nothing else, and the interface SHALL say so before the removal happens
rather than after.

Removal SHALL be confirmed before it takes effect, and SHALL be irreversible once it does. No
soft-deleted or recoverable state SHALL be introduced.

Removing a conversation SHALL NOT affect any other conversation.

#### Scenario: A removed conversation is gone from the listing

- **WHEN** a conversation is removed
- **THEN** it is no longer reported by the listing
- **AND** reading it reports it as absent

#### Scenario: Removal is confirmed first

- **WHEN** removal is requested
- **THEN** it does not take effect until confirmed
- **AND** the confirmation states that remembered facts and recorded traces are not removed

#### Scenario: Remembered facts survive removal

- **WHEN** a conversation in which a fact was remembered is removed
- **THEN** that fact is still reported as live memory

#### Scenario: Recorded traces survive removal

- **WHEN** a conversation whose turns were traced is removed
- **THEN** those turns are still reportable by their turn identifiers

#### Scenario: Other conversations are untouched

- **WHEN** one of several conversations is removed
- **THEN** every other conversation is still listed with its exchanges intact

#### Scenario: Removing an unknown conversation is not an error

- **WHEN** removal is requested for a conversation that does not exist
- **THEN** the request succeeds
- **AND** no conversation is created
