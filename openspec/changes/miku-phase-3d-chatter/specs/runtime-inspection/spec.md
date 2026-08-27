## MODIFIED Requirements

### Requirement: A read-only view of runtime state is available to any gateway

The system SHALL provide a runtime surface that reports what is currently configured, which
tools exist, what long-term memory holds, which conversations are held, what one conversation
contains, and what a past turn did. Every gateway SHALL obtain such data from this surface rather
than reading those sources itself.

#### Scenario: Configuration is reportable

- **WHEN** the inspection surface is asked for the active configuration
- **THEN** it reports the resolved provider, the model chosen for each role, and the
  configured limits

#### Scenario: The registered tools are reportable

- **WHEN** the inspection surface is asked which tools exist
- **THEN** it reports every registered tool by name, with the description the model is given

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

### Requirement: Inspection never mutates and never reads the environment

The inspection surface SHALL be read-only: it SHALL NOT write to the store, modify
checkpointed state, invoke a model, or open a session. It SHALL receive its configuration, its
store handle, and its checkpointer handle as arguments, so that configuration resolution stays in
one place.

Reading checkpointed state is permitted; modifying it is not. The distinction is deliberate: a
conversation view must read thread state, and the prohibition that matters is on writing it.

#### Scenario: Inspecting memory changes nothing

- **WHEN** every inspection function is called against a populated store
- **THEN** the stored facts afterwards are identical to those before
- **AND** no model request is recorded

#### Scenario: Inspecting conversations changes nothing

- **WHEN** every inspection function is called against populated thread state
- **THEN** the persisted thread state afterwards is identical to the state before
- **AND** no checkpoint is written

#### Scenario: Configuration is not resolved a second way

- **WHEN** the inspection module's source is examined
- **THEN** it reads no environment variable
