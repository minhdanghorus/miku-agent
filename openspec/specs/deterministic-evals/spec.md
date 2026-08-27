# Deterministic Evals

## Purpose

The test suite that pins the agent's behaviour.

Cases drive the real compiled graph through a single task function, so the suite exercises
what ships rather than a reimplementation of it. Every evaluator asserts on which tool ran
and what was persisted, never on how a reply is worded - small models phrase things
differently on every run, and stored rows do not.

Cases needing a live provider are separable from those that do not, so the offline subset
runs without credentials or spend.

## Requirements

### Requirement: The graph is evaluated through one task function

Evaluations SHALL drive the agent through a single async task function that takes case inputs
and returns the turn's result, so that `pydantic-evals` cases exercise the real graph rather
than a reimplementation of it. Cases SHALL NOT call model clients or tools directly.

#### Scenario: A case runs a real turn

- **WHEN** a case is evaluated
- **THEN** the graph runs assemble-context, agent, and any tool nodes as it would in the CLI
- **AND** the result reflects that turn's tool calls and reply

#### Scenario: Each case runs in isolated state

- **WHEN** two cases run in the same suite
- **THEN** neither sees the other's persisted events, facts, or thread history
- **AND** case order does not change the outcome

### Requirement: Deterministic evaluators assert on observable state, not prose

Evaluators SHALL assert on facts that are stable across model wording: which tools were called
with which arguments, and what was persisted. Assertions on exact reply text SHALL be avoided.

#### Scenario: Tool selection is asserted

- **WHEN** a case asks to book an activity at a stated time
- **THEN** the evaluator passes only if `create_event` was called
- **AND** the assertion does not depend on the reply's phrasing

#### Scenario: Persisted state is asserted

- **WHEN** a case asks to book an activity at a stated time
- **THEN** the evaluator reads the stored event and checks its title, date, and start time
- **AND** the case fails if no event was stored

#### Scenario: A no-tool case asserts that no tool ran

- **WHEN** a case asks a question answerable without tools
- **THEN** the evaluator passes only if no tool was called

### Requirement: The suite covers the loop's contract, not just the happy path

The suite SHALL include cases for: correct tool selection, correct persisted event fields,
relative-date resolution against a fixed reference date, memory recall across threads, and
termination at the iteration cap.

#### Scenario: Relative dates are covered with a fixed reference date

- **WHEN** a case supplies a fixed current date and asks for a weekday by name
- **THEN** the evaluator asserts the stored absolute date
- **AND** the case yields the same verdict when run on a different real-world day

#### Scenario: Cross-thread recall is covered

- **WHEN** a case remembers a fact on one thread and then runs a turn on another
- **THEN** the evaluator asserts the fact was available to the second turn

#### Scenario: The iteration cap is covered without live model calls

- **WHEN** a case drives the loop with a stubbed model that always requests a tool
- **THEN** the turn ends at the configured cap
- **AND** the evaluator asserts termination rather than waiting indefinitely

### Requirement: The suite is runnable and reports per-case outcomes

The suite SHALL be runnable by a single documented command and SHALL report a pass or fail
verdict per case. Cases requiring a live provider SHALL be separable from cases that do not, so
the latter can run without credentials or spend.

#### Scenario: Running the suite reports per-case results

- **WHEN** the documented command is run with credentials available
- **THEN** each case's verdict is reported
- **AND** the command's exit status is non-zero if any case failed

#### Scenario: Running without credentials still exercises offline cases

- **WHEN** the suite is run with no provider credentials
- **THEN** cases that need a live provider are skipped with a stated reason
- **AND** cases driven by a stubbed model still run and report verdicts

### Requirement: Fan-out is asserted on trace structure, not on wording

Evaluators SHALL be able to assert the shape of a turn from its trace: how many branches ran,
what caused them, which angle each carried, and where selection happened. These assertions SHALL
be drivable by the stubbed model, without provider credentials. No evaluator SHALL assert on the
wording of a recommendation.

#### Scenario: Branch count is asserted without credentials

- **WHEN** a fan-out case runs against the stubbed model
- **THEN** the evaluator asserts the number of branch steps from the trace
- **AND** the case runs with no provider credentials present

#### Scenario: Diversity is asserted structurally

- **WHEN** a fan-out case completes
- **THEN** the evaluator asserts that each branch carried a distinct angle
- **AND** the assertion reads recorded angles rather than generated text

#### Scenario: Selection order is asserted

- **WHEN** a fan-out case completes
- **THEN** the evaluator asserts exactly one selection step occurred
- **AND** that it is recorded as caused by the same step that caused the branches

#### Scenario: Budget exhaustion is asserted from the trace

- **WHEN** a case runs with a request budget too small to complete the fan-out
- **THEN** the evaluator asserts a budget exhaustion event is present
- **AND** asserts no model request was recorded after it

### Requirement: Tool routing has live cases

Because tool boundaries are expressed in tool descriptions rather than in code, the suite SHALL
include live cases asserting which tool the model selects for a given request, so that editing a
description in a way that breaks routing fails a test. These cases SHALL be skipped when
credentials are absent.

#### Scenario: A request stating a day and time routes to booking

- **WHEN** a live case sends a request naming both a day and a start time
- **THEN** the evaluator asserts the event-creation tool was called
- **AND** asserts the proposal tool was not called

#### Scenario: A request with no time routes to proposal

- **WHEN** a live case sends a request naming a task but no time
- **THEN** the evaluator asserts the proposal tool was called

#### Scenario: Routing cases skip without credentials

- **WHEN** the suite runs with no provider credentials
- **THEN** the routing cases are skipped rather than failed

### Requirement: Consolidation is asserted on stored rows, not on merged wording

The suite SHALL assert consolidation's behaviour by inspecting stored fact rows and the plan
that was applied — which rows became superseded, which key they name as successor, which keys a
merged fact was derived from, and which operations were dropped and why. It SHALL NOT assert on
the wording a model produced for a merged fact.

These cases SHALL run against a stub model returning a fixed plan, so they require no provider
credentials and produce the same result on every run.

#### Scenario: Supersession is asserted by row state

- **WHEN** a case supplies two contradicting facts and a stub plan superseding the older
- **THEN** the older row is asserted to carry a supersession marker naming the newer row
- **AND** the newer row is asserted to carry none

#### Scenario: Merged wording is not asserted

- **WHEN** a case exercises a merge
- **THEN** the assertions cover the source keys, the successor links, and the live fact count
- **AND** no assertion compares the merged text to an expected string

#### Scenario: The suite runs without credentials

- **WHEN** the consolidation cases run with no provider key configured
- **THEN** they execute against the stub model and report per-case outcomes

### Requirement: Judged evaluation covers only what deterministic assertion cannot reach

The suite SHALL support evaluators backed by a model judge, and their use SHALL be confined to
dimensions for which no deterministic assertion is possible. Where a tool call or a stored row
can carry the assertion, a deterministic evaluator SHALL be used instead. A judged evaluator
SHALL NOT be added alongside a deterministic one that already covers the same claim.

The dimension this exists for is honesty: `SOUL.md` requires that Miku never claim to have
scheduled, remembered, or looked up anything unless a tool returned a result saying so. That
claim lives in the reply and is invisible to an evaluator that reads only tool calls and stored
rows.

#### Scenario: A claimed action with no tool behind it fails

- **WHEN** a case runs a turn whose reply asserts that something was scheduled or saved
- **AND** the turn's recorded tool calls contain no call that performed it
- **THEN** the judged evaluator returns a failing verdict
- **AND** the failure is attributable to the absence of the tool call, not to the reply's phrasing

#### Scenario: An honest refusal passes

- **WHEN** a case runs a turn that declines an unsupported request and offers an alternative
- **AND** no tool was called
- **THEN** the judged evaluator returns a passing verdict

#### Scenario: A deterministic assertion is preferred where one exists

- **WHEN** a case asserts which tool ran or what was persisted
- **THEN** that assertion is made by a deterministic evaluator
- **AND** no judged evaluator duplicates it

### Requirement: Judged cases degrade to skipped without credentials

Judged evaluation requires a live provider request. Cases that use it SHALL be skipped when no
provider credential is configured, in the same way the existing live cases are, and their absence
SHALL NOT fail the run.

#### Scenario: The offline suite stays green

- **WHEN** the suite runs with no provider API key in the environment
- **THEN** every judged case reports as skipped
- **AND** the run's exit status is unaffected by their absence

#### Scenario: A judge request failure does not crash the run

- **WHEN** a judged evaluator's model request raises
- **THEN** the case reports a failure rather than aborting the suite

### Requirement: A judged verdict is recorded with the reason the judge gave

A judged evaluator SHALL surface the judge's stated reason alongside its verdict. A pass or fail
with no reason attached is not reviewable, and the judge-strength spike found that the reason is
where an unusable judge reveals itself — a constant verdict looks like a score until the
reasoning is read.

#### Scenario: A failing verdict carries its reason

- **WHEN** a judged evaluator returns a failing verdict
- **THEN** the judge's reason is available in the case's reported output

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

### Requirement: A hand-authored description of the system is asserted against the system

Where the project states something about itself in a place that code does not generate — a
diagram, a declared inventory, a list a reader is meant to trust — the suite SHALL assert that the
statement matches what the code actually does, rather than relying on the author to keep them in
step.

Such an assertion SHALL compare identifiers, never prose. It SHALL report which identifier is
missing or unexpected, so that a failure tells the reader what to change.

#### Scenario: The cockpit diagram's node inventory is asserted against the graph builders

- **WHEN** the suite runs
- **THEN** the set of graph nodes the cockpit diagram declares is compared with the set the graph
  builders register
- **AND** the case fails if either set contains a node the other does not

#### Scenario: A drift failure names the node

- **WHEN** the diagram and the builders disagree
- **THEN** the failure message contains the name of the node they disagree about

#### Scenario: The comparison cannot pass by comparing nothing

- **WHEN** the inventory comparison runs
- **THEN** the case asserts that both sets are non-empty before comparing them

#### Scenario: The comparison reads identifiers rather than rendered text

- **WHEN** the inventory is compared
- **THEN** the assertion reads node identifiers
- **AND** it does not read any label, caption, or description shown to a user

### Requirement: Frontend logic with more than one caller is asserted directly

Logic in the cockpit that serves more than one surface — a turn in progress and a turn read back
from a file — SHALL be asserted by calling it, not by inspecting a rendered page. Such cases SHALL
skip when the runtime needed to call it is absent, in the same way cases requiring credentials or
an optional extra skip.

Assertions on such logic SHALL cover the properties that a rendered page cannot demonstrate:
independence from arrival order, and equality between an incremental sequence of calls and a
single call over the same input.

#### Scenario: Painting is asserted to be independent of arrival order

- **WHEN** the same records are painted in two different orders
- **THEN** the case asserts the two results are identical

#### Scenario: Incremental painting is asserted to converge

- **WHEN** records are painted one at a time, each call receiving every record seen so far
- **THEN** the case asserts the final result equals painting all of them in one call

#### Scenario: The offset computation is asserted against known timestamps

- **WHEN** records with known timestamps are given to the offset computation
- **THEN** the case asserts each offset equals that record's timestamp minus the first record's
- **AND** the case fails if any offset is derived from the preceding record instead

#### Scenario: These cases skip without the runtime they need

- **WHEN** the runtime required to call the frontend logic is not installed
- **THEN** those cases are reported as skipped with the reason
- **AND** the remainder of the suite runs and passes
