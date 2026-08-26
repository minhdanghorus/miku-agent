## ADDED Requirements

### Requirement: Cases needing an optional capability degrade to skipped

The suite SHALL remain green on an installation that has only the core and eval
dependencies. Where a case requires something optional — a dependency installed by an
extra, or a tool provided by the developer's machine rather than by this project — that
case SHALL be skipped with a reason naming what is missing, and its absence SHALL NOT fail
the run.

This extends the existing credential rule to a second and third axis. Credentials gate the
live cases; an optional extra gates the cases that exercise the web gateway; and a system
tool gates the cases that exercise the cockpit's browser-side logic. All three are the same
bargain: an optional capability may be untested on a given machine, but it may never turn a
correct checkout red.

#### Scenario: The suite is green without the optional extras

- **WHEN** the suite runs on an installation without the web extra
- **THEN** every case requiring it reports as skipped
- **AND** the run's exit status is unaffected by their absence

#### Scenario: A skip names what is missing

- **WHEN** a case is skipped for a missing optional capability
- **THEN** the reported reason names the capability and how to obtain it

#### Scenario: A system tool that is not a project dependency

- **WHEN** the suite runs on a machine without the tool the browser-side cases use
- **THEN** those cases report as skipped
- **AND** no project dependency is added to make them run

### Requirement: The web gateway is asserted on event shape and stored state

Cases covering the web gateway SHALL drive the real application in-process, without binding
a network port and without provider credentials. They SHALL assert on the shape of the
streamed events and on state the turn stored, never on rendered markup or reply wording —
the same rule the rest of the suite follows, for the same reason: a cockpit that renders
differently is a UI change, while a stream that loses a causal link is a defect.

#### Scenario: A turn is exercised without a listener on a port

- **WHEN** a turn is posted to the application in a case
- **THEN** the stream is produced and asserted on with no port bound
- **AND** no provider credential is required

#### Scenario: Delegated work is asserted structurally

- **WHEN** a case covers a turn that delegates to a fan-out
- **THEN** it asserts one record per branch, a shared causing event, and distinct branch
  identifiers
- **AND** it asserts nothing about the wording of any branch's output

### Requirement: Concurrent turns are asserted to have actually overlapped

A case asserting that two turns do not interfere SHALL also establish that the two turns
were in flight at the same time. Isolation between turns that ran one after the other is
not evidence of isolation, and a fixture that fails to overlap reports success while
testing nothing.

The overlap SHALL be forced rather than left to timing, so that failure to overlap is a
deterministic failure rather than an intermittent one.

#### Scenario: A single turn cannot satisfy the overlap

- **WHEN** the concurrency fixture is driven by one turn alone
- **THEN** the case fails rather than passing

#### Scenario: Two turns overlap and stay isolated

- **WHEN** two turns are run concurrently against one session
- **THEN** both produce a reply
- **AND** each reports only the model requests it made itself
- **AND** neither turn's events are recorded against the other's turn identifier
