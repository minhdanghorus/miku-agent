## ADDED Requirements

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

The system SHALL expose four model roles — `main`, `fast`, `judge`, and `embed` — and callers
SHALL request a model by role. The descriptor maps each role to a concrete model id. A role
with no mapping SHALL fail with an error naming the role and provider.

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
