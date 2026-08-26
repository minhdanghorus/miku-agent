## ADDED Requirements

### Requirement: A terminal command consolidates stored facts

The system SHALL provide a console subcommand that runs one consolidation pass over the active
user's stored facts and prints what it did. The subcommand SHALL default to reporting without
writing, and SHALL require an explicit flag before any fact is modified.

The gateway SHALL hold no consolidation logic: it parses arguments, calls a runtime entry
point, and prints the result. It SHALL NOT decide which facts to resolve, call a model, or
write to the store itself.

#### Scenario: The default invocation writes nothing

- **WHEN** the user runs the consolidate subcommand with no flags
- **THEN** the planned operations are printed
- **AND** no stored fact is modified

#### Scenario: An explicit flag applies the plan

- **WHEN** the user runs the consolidate subcommand with the apply flag
- **THEN** the valid operations are applied
- **AND** what was applied is printed

#### Scenario: Nothing to do is reported as such

- **WHEN** the pass finds no operation to perform
- **THEN** a message says so
- **AND** the process exits with a success status

#### Scenario: Starting a conversation is unaffected

- **WHEN** the user launches the command with no subcommand
- **THEN** a conversation starts exactly as before

### Requirement: Consolidation output is plain ASCII

Everything the consolidate subcommand prints SHALL be ASCII, so that Windows consoles render it
without mangling.

#### Scenario: Reported operations render on a Windows console

- **WHEN** the consolidate subcommand prints a plan
- **THEN** every character printed is ASCII
