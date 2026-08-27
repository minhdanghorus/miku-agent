## ADDED Requirements

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
