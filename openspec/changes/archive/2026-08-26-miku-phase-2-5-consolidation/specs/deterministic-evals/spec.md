## ADDED Requirements

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
