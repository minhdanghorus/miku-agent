## ADDED Requirements

### Requirement: External MCP servers contribute tools without code changes

The system SHALL be able to connect to Model Context Protocol servers described in configuration,
and SHALL offer the tools those servers expose to the model alongside the tools built into this
repository.

Connecting a server SHALL require no change to any source file. Adding a server is an edit to a
configuration file, in the same way that adding a provider is an edit to a descriptor. If
connecting a new server ever required editing a call site, the seam would be in the wrong place.

A contributed tool SHALL be indistinguishable to the loop from a built-in one: it is looked up by
name and invoked through the same path, and the loop SHALL NOT branch on where a tool came from.

#### Scenario: A configured server's tools become callable

- **WHEN** a reachable server is configured and the connector is enabled
- **THEN** each tool that server offers is registered among the session's tools
- **AND** the model is offered it with the description the server gave

#### Scenario: Connecting a server changes no source file

- **WHEN** a server is added to the configuration file and a session is opened
- **THEN** its tools are registered
- **AND** no file under the graph package differs from its state before the server was added

#### Scenario: A contributed tool is invoked like any other

- **WHEN** the model requests a tool contributed by a server
- **THEN** it is located by name and invoked through the same path a built-in tool uses
- **AND** its result is returned to the model as a tool result

### Requirement: The connector is off unless both gates are open

The connector SHALL activate only when it is explicitly enabled **and** a configuration file is
present. Either condition alone SHALL leave it inactive.

Enablement SHALL default to inactive. A configuration file that exists but is not wanted SHALL be
disableable without being moved or deleted, because a configuration kept across months outlives
the intention behind it, and the failure of a forgotten file is silent: processes spawned on every
session of a repository someone returned to.

Enabling the connector with no configuration file present SHALL NOT be an error. Absence is the
inactive state, not a misconfiguration.

#### Scenario: Inactive by default

- **WHEN** a session is opened with no MCP configuration touched
- **THEN** only the built-in tools are registered
- **AND** no external process is started

#### Scenario: A present file with the connector disabled stays inactive

- **WHEN** a valid configuration file exists and the connector is not enabled
- **THEN** no server is contacted
- **AND** no external process is started

#### Scenario: Enabled with no file is not an error

- **WHEN** the connector is enabled and no configuration file exists
- **THEN** the session opens normally with only its built-in tools

### Requirement: Server configuration lives in a file, not in the environment

Server descriptions SHALL be read from a configuration file. Configuration SHALL hold only the
location of that file and whether to read it; it SHALL NOT hold the servers themselves.

A server description SHALL be able to express: a name, how the server is started, its arguments,
the environment it is started with, the working directory it is started in, the transport to use,
whether it is enabled, and which of its tools to bind.

The working directory SHALL be expressible. A server that lives in its own project, with its own
dependencies and relative imports, cannot be started from this project's directory, and such
servers are the motivating case rather than an edge one.

The file's default location SHALL be the runtime state directory, which is not tracked by version
control, because a server description carries the credentials that server is started with. A
template SHALL be tracked instead, so that the capability is discoverable without the secrets
being tracked with it.

#### Scenario: A server is described entirely in the file

- **WHEN** the configuration file describes a server with a command, arguments, environment,
  working directory, and tool selection
- **THEN** that server is started accordingly
- **AND** no environment variable other than the location and the enabling flag was consulted

#### Scenario: A server is started in its own directory

- **WHEN** a server description names a working directory
- **THEN** the server process is started in that directory

#### Scenario: A malformed configuration file is reported, not swallowed

- **WHEN** the configuration file is not valid or a server description is missing what it needs
- **THEN** the problem is reported naming the file and what is wrong
- **AND** the session still opens with its built-in tools

### Requirement: Which tools are bound is controlled at two grains

A server SHALL be disableable individually without removing its description. A server description
SHALL also be able to name which of its tools to bind, and when it does, only those tools SHALL be
bound.

Omitting a tool selection SHALL bind every tool the server offers. Selection is opt-in narrowing,
not a required field.

Selection SHALL be an allowlist rather than a denylist, so that a server's later release cannot add
tools to the model's prompt without a decision being made.

This exists because tool count is a cost, not a convenience. The model chosen for the main role was
measured against four tools; a single real server in use here offers twenty-nine.

#### Scenario: A disabled server contributes nothing

- **WHEN** a server description is marked disabled
- **THEN** it is not contacted
- **AND** none of its tools are registered
- **AND** it is still reported as configured

#### Scenario: Only the selected tools are bound

- **WHEN** a server offering several tools names a subset of them
- **THEN** exactly that subset is registered
- **AND** the unnamed tools are absent from the session's tools

#### Scenario: No selection means every tool

- **WHEN** a server description names no tool selection
- **THEN** every tool the server offers is registered

#### Scenario: A selected tool the server does not offer is reported

- **WHEN** a selection names a tool the server does not offer
- **THEN** that is reported as a warning naming the server and the tool
- **AND** the tools that do exist are still registered

### Requirement: Contributed tools are namespaced by their server

A contributed tool SHALL be registered under a name that identifies both its server and the tool,
so that two servers offering the same tool name do not collide and so that a tool's origin is
legible in a trace and in a tool call.

A contributed tool whose namespaced name collides with a built-in tool SHALL be reported at startup
rather than resolved silently by precedence. Which of two same-named tools ran is exactly what
nobody can reconstruct afterwards.

#### Scenario: Two servers offering the same tool name do not collide

- **WHEN** two connected servers each offer a tool of the same name
- **THEN** both are registered under distinct names
- **AND** each name identifies the server it came from

#### Scenario: A collision with a built-in tool is reported

- **WHEN** a contributed tool's registered name matches a built-in tool's name
- **THEN** the collision is reported naming both
- **AND** it is not resolved silently

### Requirement: A broken or absent server degrades, it does not crash

A server that fails to start, fails to initialise, or does not respond within its timeout SHALL be
skipped with a warning that names it and the reason. The session SHALL open, and every other
server's tools and every built-in tool SHALL remain available.

A request for a tool whose server is unavailable SHALL return a tool result saying so, in the same
way any other tool failure becomes a result the model can see. It SHALL NOT raise to the user.

This is the existing rule, applied to a new class of failure: errors degrade, they do not crash.
Only configuration errors fail loudly, at startup.

#### Scenario: A server that cannot start does not prevent a session

- **WHEN** a server is configured with a command that cannot be executed
- **THEN** opening a session succeeds
- **AND** the failure is reported naming the server and the reason
- **AND** the built-in tools are registered as usual

#### Scenario: One broken server does not take down the others

- **WHEN** one of several configured servers fails to start
- **THEN** the remaining servers' tools are registered

#### Scenario: A failing contributed tool becomes a tool result

- **WHEN** a contributed tool raises or its server is unavailable
- **THEN** the loop records a tool result describing the failure
- **AND** the turn continues and produces a reply

### Requirement: Server processes live and die with the session

Connections SHALL be opened while a session is being opened, before the model is told which tools
exist, and SHALL be closed when the session closes.

After a session closes, no process it started SHALL survive. A child process that outlives its
parent is not an inconvenience on every platform this runs on, and the failure presents as a
machine accumulating browsers rather than as an error anyone sees.

Connections SHALL NOT be deferred until a tool is first called. The model is told what tools exist
when the graph is built, so a tool that has not been discovered by then cannot be requested.

#### Scenario: Tools are known before the first model call

- **WHEN** a session is opened with a reachable server configured
- **THEN** that server's tools are among the session's tools before any turn is run

#### Scenario: Closing the session leaves no process behind

- **WHEN** a session with a connected server is closed
- **THEN** the process that server was started as is no longer running

### Requirement: Only the tool surface of the protocol is reached

This capability SHALL cover MCP tools. Resources and prompts SHALL NOT be read, exposed, or
registered, and their absence SHALL be stated rather than left to be discovered.

Results that are not text SHALL be discarded and replaced by a placeholder describing what was
dropped. Accepting them would require a declared model capability for non-text input, and
capabilities in this system are declared, never inferred.

Only one transport SHALL be implemented. A server description SHALL nevertheless be able to name
its transport, defaulting to the implemented one, so that adding another later is an addition
rather than a migration of every existing configuration file. A description naming an
unimplemented transport SHALL be reported as a configuration error.

#### Scenario: Non-text results are replaced by a placeholder

- **WHEN** a contributed tool returns content that is not text
- **THEN** the tool result contains a placeholder describing what was dropped
- **AND** any text content in the same result is preserved

#### Scenario: An unimplemented transport is a configuration error

- **WHEN** a server description names a transport that is not implemented
- **THEN** it is reported naming the server and the transport
- **AND** the session still opens with its built-in tools

#### Scenario: Resources and prompts are not registered

- **WHEN** a connected server exposes resources or prompts as well as tools
- **THEN** only its tools are registered
