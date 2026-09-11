## MODIFIED Requirements

### Requirement: A read-only view of runtime state is available to any gateway

The system SHALL provide a runtime surface that reports what is currently configured, which
tools exist and where each came from, which external tool servers are configured and how they
fared, what long-term memory holds, which conversations are held, what one conversation
contains, and what a past turn did. Every gateway SHALL obtain such data from this surface rather
than reading those sources itself.

#### Scenario: Configuration is reportable

- **WHEN** the inspection surface is asked for the active configuration
- **THEN** it reports the resolved provider, the model chosen for each role, and the
  configured limits

#### Scenario: The registered tools are reportable

- **WHEN** the inspection surface is asked which tools exist
- **THEN** it reports every registered tool by name, with the description the model is given

#### Scenario: A tool reports where it came from

- **WHEN** the inspection surface reports the registered tools
- **THEN** each one is reported as built into this system or as contributed by a named external
  server
- **AND** a tool contributed by a server names that server

#### Scenario: External tool servers are reportable

- **WHEN** the inspection surface is asked which external tool servers are configured
- **THEN** it reports each configured server, whether it is enabled, whether it connected, how
  many tools it contributed, and the reason if it did not connect

#### Scenario: Reporting servers starts nothing

- **WHEN** the inspection surface is asked about external tool servers
- **THEN** no server process is started in order to answer
- **AND** configured state is reported even for servers that were never connected

#### Scenario: Live facts are reportable

- **WHEN** the inspection surface is asked what memory holds
- **THEN** it reports the live facts for the active user
- **AND** facts that have been superseded are excluded

#### Scenario: Held conversations are reportable

- **WHEN** the inspection surface is asked which conversations exist
- **THEN** it reports one entry per thread identifier held in persisted state
- **AND** a conversation started by an earlier process is included

#### Scenario: One conversation is reportable as exchanges

- **WHEN** the inspection surface is asked for one conversation by its identifier
- **THEN** it reports that conversation's exchanges in order
- **AND** a conversation that does not exist is reported as absent rather than as an error

#### Scenario: A past turn is reportable as a tree

- **WHEN** the inspection surface is asked for a recorded turn by its identifier
- **THEN** it reports that turn's events reconstructed from their causal links
- **AND** a turn that was never recorded is reported as absent rather than as an error

## ADDED Requirements

### Requirement: Inspection never starts a process

Inspection SHALL NOT start, spawn, or connect to anything in order to answer a question. The
surface is safe to call at any moment, including during a turn, and a function that could launch a
browser to render a page is not.

Where a report has both a configured half and a live half, the configured half SHALL be derived
from configuration and the live half SHALL be taken from a session that already holds it. An
absent session SHALL yield the configured half with the live half reported as unknown, never an
error and never an attempt to obtain it.

#### Scenario: Server state is reportable with no session

- **WHEN** the inspection surface is asked about external tool servers and no session exists
- **THEN** it reports what is configured
- **AND** reports connection state as unknown rather than connecting to find out
- **AND** starts no process
