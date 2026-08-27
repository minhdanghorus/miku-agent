## ADDED Requirements

### Requirement: A terminal command lists conversations

The terminal SHALL provide a command that lists held conversations, reporting each one's
identifier, its derived title, how many stored messages it holds, and when it was last active.

It SHALL obtain that listing from the runtime inspection surface, the same surface the web gateway
reads, and SHALL NOT read persisted state itself. The command exists partly as evidence: a listing
built for one gateway that costs a subcommand to offer in the other is what the peer-gateway
constraint was for.

Its output SHALL be plain ASCII, as every other terminal output in this system is, because Windows
consoles mangle anything else.

#### Scenario: Held conversations are listed in the terminal

- **WHEN** conversations exist and the listing command is run
- **THEN** each is reported with its identifier, title, message count, and last activity
- **AND** the most recently active is reported first

#### Scenario: No conversations is a sentence, not an error

- **WHEN** nothing has been persisted and the listing command is run
- **THEN** it reports that there are no conversations
- **AND** it exits successfully

#### Scenario: The listing reads through the inspection surface

- **WHEN** the terminal gateway's source is examined
- **THEN** it obtains the listing from the runtime inspection surface
- **AND** it does not query the checkpointer directly

#### Scenario: The listing is plain ASCII

- **WHEN** the listing command produces output
- **THEN** every character in it is ASCII
