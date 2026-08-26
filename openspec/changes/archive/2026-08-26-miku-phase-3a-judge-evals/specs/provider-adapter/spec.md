## ADDED Requirements

### Requirement: A role mapping is justified by measurement, not by assumption

Which model a descriptor maps to a role SHALL be a recorded decision backed by measurement of
that model on the work the role does. A role MAY resolve to the same model id as another role
when that is the measured choice; distinctness between roles is not itself a requirement.

This exists because the reverse was assumed and turned out to be wrong. The `judge` role was
mapped to a different model than `main` on the sound principle that a model grading its own
output tends to flatter it. Measurement then found that model returning a constant verdict on any
dimension requiring temporal reasoning — an evaluator that cannot grade is worse than a biased
one, because a biased score is still a signal and a constant one is not.

#### Scenario: Two roles may resolve to one model

- **WHEN** the active descriptor maps two roles to the same model id
- **THEN** `resolve_model` returns that id for both roles
- **AND** no error or warning is raised on the basis of the roles matching

#### Scenario: The role seam is unaffected by which model a role names

- **WHEN** a role's model id is changed in the descriptor
- **THEN** no call site outside the descriptor requires modification
- **AND** callers continue to request the model by role

### Requirement: The evaluation role is never resolved by production code

No graph node, tool, or gateway path SHALL resolve the `judge` role. `judge` names the model that
grades evaluations and is expected to move whenever a better evaluator becomes available; a
production decision resolving it would move with it, silently. Work done on a user's behalf that
needs a model other than `main` SHALL name its own role.

This is a requirement rather than a convention because it was already violated once: the
fan-out's slot selection resolved `judge`, so remapping the evaluator changed which model chose a
user's appointment time.

#### Scenario: Remapping the evaluator leaves user-facing behaviour alone

- **WHEN** the `judge` role is pointed at a different model id
- **THEN** the model used by the fan-out's selection step is unchanged
- **AND** no other runtime path resolves a different model than before

#### Scenario: The fan-out resolves its own role

- **WHEN** a session builds the model the fan-out selects with
- **THEN** it resolves the `select` role
- **AND** it does not resolve the `judge` role

## MODIFIED Requirements

### Requirement: Models are requested by role, never by model id

The system SHALL expose five model roles — `main`, `fast`, `judge`, `select`, and `embed` — and
callers SHALL request a model by role. The descriptor maps each role to a concrete model id. A
role with no mapping SHALL fail with an error naming the role and provider.

#### Scenario: The graph requests the main model

- **WHEN** the agent node asks the adapter for the `main` role
- **THEN** it receives a chat model configured with the model id that the active provider's
  descriptor maps to `main`

#### Scenario: A role is unmapped for the active provider

- **WHEN** a caller requests a role that the active provider's descriptor does not map
- **THEN** the adapter raises an error naming both the role and the provider
- **AND** the error does not fall back to a different role silently

#### Scenario: A role's model id is overridden by configuration

- **WHEN** configuration supplies an explicit model id for a role
- **THEN** that id is used instead of the descriptor default
- **AND** the descriptor's capability flags for that provider still apply

#### Scenario: The fan-out requests its own selection role

- **WHEN** a session builds the model the fan-out's selection step uses
- **THEN** it requests the `select` role
- **AND** the model it receives is unaffected by which model the `judge` role names
