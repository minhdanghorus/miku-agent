# Cockpit Diagram

## Purpose

What the cockpit shows about the agent's own shape, and about when a turn's steps happened.

The trace view answers "where did this turn go?". It cannot answer "what is this agent?", because
parentage between records carries no structure - a node entered three times reads as three
unrelated rows, concurrent work reads as sequential, and work that never became a graph node is
absent entirely.

This capability covers the diagram that answers the second question: what it depicts, why it is
written by hand rather than derived from the compiled graph, how it is kept from drifting away from
the code it describes, what each of its numbers counts, and how a turn's records are shown against
it while they arrive.

## Requirements

### Requirement: The cockpit renders the agent's architecture, not only a turn's trace

The cockpit SHALL render a diagram of the agent above the pane that shows a running turn. The
diagram SHALL be present and complete before any turn has been run, because it describes what the
agent is rather than what a turn did.

The diagram SHALL depict steps that the compiled graph does not expose as nodes — work performed
inside a node, and work delegated behind a tool — because those are part of the architecture a
reader needs and are absent from the execution graph by deliberate design.

#### Scenario: The diagram is present before any turn

- **WHEN** the cockpit is opened and no turn has been run
- **THEN** the diagram is rendered
- **AND** every box it declares is present with no box marked as having run

#### Scenario: Work performed inside a node is depicted

- **WHEN** the diagram is rendered
- **THEN** it depicts the fact recall and persona load that occur inside the assemble step
- **AND** those are depicted as belonging to that step rather than as steps of the graph

### Requirement: A hand-authored diagram is guarded against drifting from the graph

The diagram SHALL be authored by hand rather than derived from the compiled graph, because the
architecture it describes includes work the compiled graph cannot report.

Because it is hand-authored, the set of graph nodes the diagram claims SHALL be checked against
the set of nodes actually registered with the graph builders. Registering a node that the diagram
does not depict SHALL fail that check, and depicting a node that is not registered SHALL fail it
equally.

The check SHALL name the offending node, so that a failure states what is missing rather than that
two sets differ.

#### Scenario: A node added to the graph but not to the diagram is caught

- **WHEN** a node is registered with a graph builder and the diagram is not updated
- **THEN** the check fails
- **AND** the failure names the node that is missing from the diagram

#### Scenario: A node drawn in the diagram but absent from the graph is caught

- **WHEN** the diagram declares a graph node that no builder registers
- **THEN** the check fails
- **AND** the failure names the node that the graph does not have

#### Scenario: The diagram and the graph agreeing passes

- **WHEN** the diagram's declared graph nodes are exactly those the builders register
- **THEN** the check passes

### Requirement: Work that is not drawn is attributed to the step that performs it

The diagram MAY leave work undrawn when a box of its own does not earn the space. Undrawn work
SHALL be attributed to the step it runs inside, so that records belonging to it mark that step
rather than nothing at all. A delegating turn SHALL therefore change the diagram while the
delegation is in progress.

Not drawing a node SHALL NOT reduce what the drift guard checks. A node the diagram accounts for
without depicting it is still a node the diagram is answerable for.

#### Scenario: Records from undrawn work mark the step that performs it

- **WHEN** a turn delegates to a subgraph that has no box of its own
- **THEN** the step that delegated it shows how many steps ran inside it
- **AND** that quantity is shown separately from what the step itself counts, because one tool
  call that fans out five ways is one call and several steps
- **AND** no box is added for any node inside it

#### Scenario: Undrawn nodes remain within the drift guard's scope

- **WHEN** the diagram accounts for a node without depicting it
- **THEN** that node is still compared against the nodes the builders register
- **AND** removing it from the graph without removing it from the diagram fails the check

### Requirement: A box declares what its number counts

Each box MAY declare what its number counts, as the kind of record that means one occurrence of
the thing being counted. A box that declares nothing SHALL show no number.

A number SHALL be displayed with the unit it counts, because different boxes count different
things and two bare numbers side by side would read as comparable when they are not.

A declared count SHALL correspond to a quantity the system computes without the diagram's
involvement, so that the number can be checked against something other than the code that renders
it. A box counting how many records mention it is not such a quantity: one step of the graph emits
a record on entry, a record per request it makes and a record per result, so records mentioning it
and work it did are different numbers.

Whether a box has run SHALL be determined separately from what its number counts. A step that ran
without producing the counted kind SHALL still be shown as having run.

#### Scenario: A single tool call counts as one

- **WHEN** a turn makes one tool call
- **THEN** the step that ran it shows a count of one
- **AND** the count is unchanged by that step also recording its entry and the call's result

#### Scenario: Each count matches what the turn reported

- **WHEN** a turn completes
- **THEN** the count shown for the model step equals the number of iterations the turn reported
- **AND** the count shown for the tool step equals the number of tool calls the turn reported

#### Scenario: A step that produced nothing countable still reads as having run

- **WHEN** a turn ends by hitting a limit, so the step records the limit instead of its usual event
- **THEN** that step is shown as having run
- **AND** its count is zero

#### Scenario: A number is shown with its unit

- **WHEN** two boxes counting different things both show a number
- **THEN** each number is accompanied by what it counts

#### Scenario: Every declared rule names a kind that occurs

- **WHEN** a turn runs
- **THEN** every kind a box declares as its counted kind appears in that turn's records for that
  box, so a renamed kind cannot leave a badge silently reading zero

#### Scenario: An event describing part of a step is not counted as a step

- **WHEN** a step records an event describing something that happened during it rather than the
  step itself
- **THEN** that event is not counted as an occurrence of the step

### Requirement: Records are joined to the diagram by the node name they already carry

A record SHALL be attributed to a box by the node name the record already records. No new field
SHALL be added to a trace event, and no event SHALL be reshaped, in order to render the diagram.

A record naming a node the diagram does not know SHALL be ignored rather than causing the diagram
to fail to render.

#### Scenario: Records mark the boxes for the nodes they name

- **WHEN** a turn's records are painted onto the diagram
- **THEN** exactly the boxes whose nodes appear among those records are marked as having run

#### Scenario: An unknown node does not break the diagram

- **WHEN** a record names a node the diagram does not declare
- **THEN** the diagram still renders every box it declares
- **AND** no box is marked on account of that record

### Requirement: Painting is a pure function of the diagram and the records

The diagram SHALL be painted by a function of the topology and the set of records, with no state
carried between calls. Painting SHALL NOT accumulate: a record arriving does not modify a
previously painted result, it produces a new one from the whole set.

One painting function SHALL serve both a turn in progress and a turn read back from a trace file.

#### Scenario: Arrival order does not change the painted result

- **WHEN** the same records are painted in two different orders
- **THEN** the two painted results are identical

#### Scenario: Painting record by record ends where painting all at once ends

- **WHEN** a turn's records are painted one at a time, each call receiving every record seen so far
- **AND** the same records are painted in a single call
- **THEN** the final results are identical

#### Scenario: A finished turn is painted by the same function

- **WHEN** a turn is read back from a trace file and painted
- **THEN** it is painted by the function that paints a turn in progress

### Requirement: Each record shows when it happened, relative to its turn

Each record displayed in the cockpit SHALL show an offset computed as the difference between that
record's timestamp and the timestamp of the first record of the same turn.

The offset SHALL NOT be computed from the gap to the preceding record, because the events of a
turn do not mark the boundaries of work: one step records its event before doing its work so that
the work it causes has a parent, while the others record theirs after. A gap between adjacent
records is therefore not the duration of anything for most steps.

Records SHALL continue to be displayed in the tree their causal links describe; the offset is
additional information on a record, not a reordering of them.

#### Scenario: Offsets are measured from the start of the turn

- **WHEN** a turn's records are displayed
- **THEN** each record's offset equals its timestamp minus the first record's timestamp

#### Scenario: The tree structure is unchanged by showing offsets

- **WHEN** offsets are displayed
- **THEN** the parent and child relationships rendered are the same as those rendered without them

#### Scenario: Offsets appear for a turn read back from a file

- **WHEN** a finished turn is opened from the traces view
- **THEN** its records show offsets computed the same way as a turn in progress

#### Scenario: A record with no usable timestamp shows no offset

- **WHEN** a record without a parseable timestamp appears among a turn's records
- **THEN** that record is displayed without an offset
- **AND** the offsets of every other record are unchanged by its presence

#### Scenario: A backwards clock step does not produce a negative offset

- **WHEN** a record's timestamp precedes the timestamp of the turn's first record
- **THEN** its offset is zero rather than negative

### Requirement: Showing the diagram adds nothing to the served surface

Rendering the diagram and the offsets SHALL require no additional HTTP endpoint, no additional
read of configuration, memory, or the store, and no additional access to the session beyond what
the gateway already has.

#### Scenario: No new endpoint is served

- **WHEN** the diagram and offsets are rendered
- **THEN** the set of API endpoints the gateway serves is unchanged

#### Scenario: The gateway reaches no further into the session

- **WHEN** the diagram and offsets are rendered
- **THEN** the gateway accesses no attribute of the session that it did not access before

### Requirement: The diagram renders text as data

Every piece of text the diagram places into the page SHALL be escaped, whether it originates in
the diagram's own description or in a record.

#### Scenario: Markup in the diagram's text is rendered as text

- **WHEN** the diagram's description contains characters that would otherwise be read as markup
- **THEN** the rendered output contains them escaped
- **AND** contains no element built from them

### Requirement: The diagram's layout encodes distance from the main path

The diagram SHALL lay its main path out along one axis, and SHALL draw a box that returns to an
earlier box within that earlier box's own group rather than as a further step along the main path.

A cycle SHALL be depicted as two distinct connectors, not one bidirectional connector, because the
two directions are not the same kind of thing: leaving the main path is a routing decision, and
returning to it is unconditional.

#### Scenario: A box that loops back is grouped with the box it returns to

- **WHEN** the diagram is rendered
- **THEN** the number of groups along the main path equals the number of boxes that do not loop
  back to another
- **AND** a box that loops back appears within the group of the box it returns to

#### Scenario: The cycle is drawn as two connectors

- **WHEN** a cycle is depicted
- **THEN** two connectors are rendered for it

### Requirement: Edges are drawn, not labelled

No connector in the diagram SHALL carry visible text. A connector governed by a routing decision
SHALL be visually distinguishable from one that has no condition, and the condition itself SHALL
remain available on the connector without occupying the page.

The reason is that a label naming the condition on every edge stated what the reader could already
see — a turn ends at the exit whatever the page says about it — while the distinction between a
decision and a certainty could not be seen at all.

#### Scenario: No connector carries text

- **WHEN** the diagram is rendered
- **THEN** no connector contains any visible text

#### Scenario: A conditional edge is distinguishable from an unconditional one

- **WHEN** the diagram renders an edge the router decides and an edge that has no condition
- **THEN** the two are rendered differently

#### Scenario: The condition is still reachable

- **WHEN** an edge has a condition
- **THEN** that condition is carried on the edge as supplementary information rather than dropped

### Requirement: The diagram shows how far a turn has got

While a turn's records are still arriving, the diagram SHALL mark the box owning the most recently
received record that names a node it knows. Exactly one box SHALL be marked this way at a time, and
a turn that has produced no placeable record SHALL have no box marked.

That mark SHALL NOT be presented as the step currently executing. The records do not support that
claim: one node records its event before doing its work and the others record theirs after, so the
most recent record names a started step in one case and a finished step in the others. The mark
therefore carries no label.

A turn read back from a trace file SHALL have no such mark, because a finished turn has no
position.

Marking how far a turn has got SHALL NOT replace the record of what has run: a box may be both.

#### Scenario: The mark follows the most recent placeable record

- **WHEN** records arrive one at a time
- **THEN** after each one, the marked box is the box owning that record
- **AND** exactly one box is marked

#### Scenario: A record naming an unknown node does not clear the mark

- **WHEN** the most recent record names a node the diagram does not know
- **THEN** the mark remains on the box owning the most recent record it can place

#### Scenario: A finished turn is rendered with no mark

- **WHEN** a turn is read back from a trace file and rendered
- **THEN** no box is marked as the turn's position
- **AND** the boxes that ran are still shown as having run

#### Scenario: The mark is not part of the order-independent painting

- **WHEN** the same records are supplied in two different orders
- **THEN** the painted counts are identical
- **AND** the marked box differs, because it is defined by which record arrived last
