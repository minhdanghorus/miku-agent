## ADDED Requirements

### Requirement: A terminal command reports external tool servers

The terminal SHALL provide a command that reports the configured external tool servers: each
server's name, whether it is enabled, which transport it uses, and the tools it contributed or the
reason it did not connect.

It SHALL obtain that report from the runtime inspection surface, the same surface the web gateway
reads, and SHALL NOT read the configuration file or contact a server itself.

The command SHALL work when the connector is disabled and when no configuration file exists,
reporting that state as a sentence rather than as an error. "Nothing is configured" and "something
is broken" must be distinguishable without reading a traceback.

Its output SHALL be plain ASCII, as every other terminal output in this system is, because Windows
consoles mangle anything else.

#### Scenario: Configured servers are listed in the terminal

- **WHEN** servers are configured and the reporting command is run
- **THEN** each is reported with its name, whether it is enabled, its transport, and its tools

#### Scenario: A server that failed to connect is reported with its reason

- **WHEN** a configured server did not connect and the reporting command is run
- **THEN** it is listed
- **AND** the reason it did not connect is reported

#### Scenario: Nothing configured is a sentence, not an error

- **WHEN** no configuration file exists and the reporting command is run
- **THEN** it reports that no external tool servers are configured
- **AND** it exits successfully

#### Scenario: Disabled is distinguishable from absent

- **WHEN** a configuration file exists but the connector is disabled
- **THEN** the report says the connector is disabled
- **AND** the configured servers are still listed

#### Scenario: The report reads through the inspection surface

- **WHEN** the terminal gateway's source is examined
- **THEN** it obtains the report from the runtime inspection surface
- **AND** it does not read the configuration file directly

#### Scenario: The report is plain ASCII

- **WHEN** the reporting command produces output
- **THEN** every character in it is ASCII
