# Provider Adapter

## Purpose

Turns configuration into ready-to-use model clients.

Every supported provider speaks the OpenAI wire, so LangChain already handles message
formats, tool schemas, and streaming. This capability owns the part it does not: which
provider is active and where its credentials come from, which concrete model serves which
role, what each model can actually do, and the request limits applied to all of them.

It serves two stacks from one configuration - LangChain for the agent, pydantic-ai for the
evaluation judge - so the two cannot drift onto different providers by accident.

## Requirements

### Requirement: Provider descriptors declare configuration, not code paths

The system SHALL represent each LLM provider as a declarative descriptor holding: a provider
name, its wire family, the environment variable names for its API key and base URL, a
role-to-model mapping, declared capability flags, and request limits. Adding a provider SHALL
require only registering a new descriptor; it SHALL NOT require editing any call site that
requests a model.

#### Scenario: GreenNode is the registered provider

- **WHEN** the runtime loads configuration with the GreenNode provider selected
- **THEN** it resolves the API key and base URL from the environment variable names declared in
  the GreenNode descriptor
- **AND** the resolved base URL and key are never written to logs or traces

#### Scenario: An unknown provider name is rejected early

- **WHEN** configuration names a provider that has no registered descriptor
- **THEN** startup fails with an error naming the unknown provider and listing the registered
  provider names
- **AND** no LLM request is attempted

#### Scenario: A missing API key fails before any request

- **WHEN** the selected provider's key environment variable is unset or empty
- **THEN** startup fails with an error naming the missing environment variable
- **AND** no LLM request is attempted

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

### Requirement: Capability flags are declared per model, not assumed

The descriptor SHALL declare, per model, whether native structured output is supported, whether
prompt caching is supported, and whether the model serves embeddings. Callers SHALL consult
these flags rather than probing behavior or branching on model id strings.

#### Scenario: A model without native structured output is not asked for it

- **WHEN** a caller needs structured output from a model whose descriptor declares native
  structured output unsupported
- **THEN** the adapter does not issue a native json-schema request for that model
- **AND** the caller can detect the unsupported capability from the flag before calling

#### Scenario: Capability is unknown rather than false

- **WHEN** a capability has not been verified for a model — prompt caching, for example
- **THEN** the descriptor records it as unknown rather than supported
- **AND** callers treat unknown as unsupported

### Requirement: Two builders serve two stacks from one configuration

The adapter SHALL expose `chat_model(role)` returning a LangChain `BaseChatModel` for graph use,
and `judge_model()` returning a pydantic-ai model for evaluation use. Both SHALL read the same
provider configuration so that the two stacks cannot drift apart.

#### Scenario: The graph and the judge resolve from one configuration

- **WHEN** the graph obtains a chat model and an evaluation obtains a judge model in the same
  process
- **THEN** both are built from the same active provider descriptor and the same resolved
  credentials
- **AND** changing the configured provider changes both

#### Scenario: The judge model may differ from the agent model

- **WHEN** the descriptor maps the `judge` role to a different model id than the `main` role
- **THEN** `judge_model()` returns a model for the `judge` id
- **AND** `chat_model("main")` is unaffected

### Requirement: Limits are applied to every model the adapter builds

The adapter SHALL apply the descriptor's declared request timeout, retry policy, and maximum
concurrency to every model it builds. Callers SHALL NOT need to pass these per call.

#### Scenario: Timeout comes from configuration

- **WHEN** a model is built for any role
- **THEN** the configured request timeout is applied to it
- **AND** a request exceeding it fails with a timeout error rather than hanging

#### Scenario: Concurrency is bounded

- **WHEN** more concurrent model requests are issued than the configured maximum
- **THEN** the excess requests wait rather than being sent
- **AND** all requests eventually complete or fail on their own terms

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
