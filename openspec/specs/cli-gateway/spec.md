# CLI Gateway

## Purpose

The terminal entry point.

A gateway moves text and nothing else: it assembles no prompts, calls no models, executes no
tools, and reads no memory. It picks which conversation is in scope and prints what the
session reports. That constraint is what makes a second gateway cheap to add later.

Failures reach the user as sentences, never as tracebacks.

## Requirements

### Requirement: A terminal command starts a conversation

The system SHALL provide a console entry point that reads user messages from the terminal,
runs one turn of the agent per message, prints the reply, and continues until the user exits.

#### Scenario: A single exchange

- **WHEN** the user launches the command and types a message
- **THEN** the reply is printed to the terminal
- **AND** the prompt returns for the next message

#### Scenario: Exiting ends the session cleanly

- **WHEN** the user issues the exit command or sends end-of-input
- **THEN** the process terminates with a success status
- **AND** no traceback is printed

#### Scenario: Interrupting a running turn does not corrupt state

- **WHEN** the user interrupts the process while a turn is in flight
- **THEN** the process exits without a traceback
- **AND** a subsequent run of the same thread starts from consistent state

### Requirement: The gateway selects the thread but holds no agent logic

The gateway SHALL choose the thread identifier for the session — defaulting to a new thread and
accepting an option to resume a named one — and SHALL otherwise only move text between the
terminal and the graph. It SHALL NOT assemble prompts, call models, execute tools, or read
memory directly.

#### Scenario: Resuming a named thread

- **WHEN** the user launches the command with a thread identifier that already has history
- **THEN** that thread's history is in scope for the session
- **AND** a question depending on earlier messages is answered correctly

#### Scenario: Default launch starts a fresh thread

- **WHEN** the user launches the command with no thread identifier
- **THEN** a new thread identifier is used
- **AND** no prior conversation is in scope

### Requirement: Tool activity is visible in the terminal

While a turn runs, the gateway SHALL print which tools are being called, so the user can see
what the agent did rather than only its final reply.

#### Scenario: A turn that calls a tool

- **WHEN** a turn calls `create_event`
- **THEN** the terminal shows that `create_event` was called before the final reply is printed

#### Scenario: A turn that calls no tools

- **WHEN** a turn is answered directly by the model
- **THEN** no tool activity lines are printed

### Requirement: Configuration errors are reported as messages, not tracebacks

When the session cannot start because of missing or invalid configuration, the gateway SHALL
print a single actionable message naming what is missing and exit with a failure status.

#### Scenario: Missing API key

- **WHEN** the required provider key environment variable is unset and the command is launched
- **THEN** a message naming the missing variable is printed
- **AND** the process exits with a non-zero status without a traceback
