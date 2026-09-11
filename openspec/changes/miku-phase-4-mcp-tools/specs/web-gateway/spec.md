## ADDED Requirements

### Requirement: The cockpit distinguishes built-in tools from contributed ones

The cockpit's tool view SHALL group the registered tools by where they came from, showing which are
built into this system and which were contributed by which external server.

It SHALL also show the configured external tool servers, including any that contributed nothing,
with the reason a server did not connect.

A built-in tool is in the repository; a contributed one depends on a process that may not start
tomorrow. When a tool is missing, that difference is the whole diagnosis, and a view that cannot
express it sends the user to read source.

Both SHALL be obtained from the runtime inspection surface. The gateway SHALL NOT read the
configuration file, contact a server, or inspect a session's internals to render this.

#### Scenario: Tools are grouped by origin

- **WHEN** the tool view is requested and contributed tools are registered
- **THEN** built-in tools and contributed tools are reported as distinct groups
- **AND** each contributed tool names the server it came from

#### Scenario: A server that contributed nothing is still shown

- **WHEN** a configured server did not connect
- **THEN** it appears in the view
- **AND** the reason it did not connect is shown

#### Scenario: The view reads through the inspection surface

- **WHEN** the web gateway's source is examined
- **THEN** the tool and server data are obtained from the runtime inspection surface
- **AND** no configuration file is read and no server is contacted by the gateway

#### Scenario: Nothing configured renders as a state, not a failure

- **WHEN** the connector is inactive and the tool view is requested
- **THEN** the built-in tools are shown
- **AND** the absence of external servers is stated rather than reported as an error
