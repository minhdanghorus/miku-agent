"""The consolidation plan: what a model may propose, and what survives checking.

Two halves, deliberately in one file and deliberately free of any model call.
The model's whole influence over memory arrives as a `Plan`, and every write the
pass performs is authorised by an `Operation` that got through `validate_plan`.
Keeping that gate pure is what makes it assertable without credentials.

**One shape for four operations.** Every operation says the same thing — these
facts stop counting, and (sometimes) this one takes over:

    supersede   stale=[old]        winner=new     a later fact corrects an earlier one
    duplicate   stale=[b, c]       winner=a       restatements collapse into the original
    merge       stale=[a, b, c]    winner=None    fragments become the new `fact` text
    expire      stale=[x]          winner=None    a time-bound fact whose window passed

`kind` is therefore not what makes an operation executable — the fields are. It
records intent, which is what a trace needs to be readable and what the
per-kind rules below are keyed on.

**Indices, not keys.** The model counts facts in a numbered list, 1-based, and
never sees a uuid. Resolving indices to keys happens in the pass, at write time,
which is also what makes a fact written by a live session mid-run harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from miku.memory.store import LiveFact

KIND_SUPERSEDE = "supersede"
KIND_DUPLICATE = "duplicate"
KIND_MERGE = "merge"
KIND_EXPIRE = "expire"

KINDS = (KIND_SUPERSEDE, KIND_DUPLICATE, KIND_MERGE, KIND_EXPIRE)


class Operation(BaseModel):
    """One proposed resolution. Field meanings vary by `kind`; see the module docstring."""

    kind: Literal["supersede", "duplicate", "merge", "expire"]
    stale: list[int] = Field(
        default_factory=list,
        description="1-based numbers of the facts that stop counting.",
    )
    winner: int | None = Field(
        default=None,
        description="1-based number of the fact that replaces them, when one already exists.",
    )
    fact: str = Field(
        default="",
        description="For merge only: the single fact the fragments become.",
    )
    why: str = Field(default="", description="One short sentence of justification.")


class Plan(BaseModel):
    """Everything the model proposes for one run. An empty plan is a valid answer."""

    operations: list[Operation] = Field(default_factory=list)


@dataclass(frozen=True)
class Dropped:
    """An operation that failed validation, and the machine-readable reason."""

    operation: Operation
    reason: str


# Reasons are constants rather than prose so that a test, a trace, and a CLI line
# can all agree on what happened without matching on a sentence.
UNKNOWN_INDEX = "unknown_index"
CLAIMED_TWICE = "claimed_twice"
EMPTY_SELECTION = "empty_selection"
WINNER_IN_SELECTION = "winner_in_selection"
NEEDS_WINNER = "needs_winner"
TAKES_NO_WINNER = "takes_no_winner"
SUPERSEDE_ONE_STALE = "supersede_needs_exactly_one_stale"
SUPERSEDE_BACKWARDS = "supersede_points_backwards"
MERGE_NEEDS_SOURCES = "merge_needs_at_least_two_sources"
MERGE_NEEDS_TEXT = "merge_needs_text"
EXPIRE_ONE_FACT = "expire_needs_exactly_one_fact"


def _shape_reason(operation: Operation, count: int) -> str | None:
    """Everything checkable without looking at timestamps."""
    indices = list(operation.stale) + ([] if operation.winner is None else [operation.winner])

    if any(index < 1 or index > count for index in indices):
        return UNKNOWN_INDEX
    if not operation.stale:
        return EMPTY_SELECTION
    if operation.winner is not None and operation.winner in operation.stale:
        return WINNER_IN_SELECTION

    if operation.kind == KIND_SUPERSEDE:
        if len(operation.stale) != 1:
            return SUPERSEDE_ONE_STALE
        if operation.winner is None:
            return NEEDS_WINNER
    elif operation.kind == KIND_DUPLICATE:
        if operation.winner is None:
            return NEEDS_WINNER
    elif operation.kind == KIND_MERGE:
        if operation.winner is not None:
            return TAKES_NO_WINNER
        if len(operation.stale) < 2:
            return MERGE_NEEDS_SOURCES
        if not operation.fact.strip():
            return MERGE_NEEDS_TEXT
    elif operation.kind == KIND_EXPIRE:
        if operation.winner is not None:
            return TAKES_NO_WINNER
        if len(operation.stale) != 1:
            return EXPIRE_ONE_FACT

    return None


def validate_plan(plan: Plan, facts: list[LiveFact]) -> tuple[list[Operation], list[Dropped]]:
    """Split a proposed plan into what may be applied and what may not.

    Never raises. A model that returns nonsense costs us the nonsense, not the
    run — which is the same rule tool failures follow: errors degrade, they do
    not crash.

    Operations are considered in order, so an earlier one wins a contested fact.
    """
    applicable: list[Operation] = []
    dropped: list[Dropped] = []
    claimed: set[int] = set()

    for operation in plan.operations:
        reason = _shape_reason(operation, len(facts))

        if reason is None:
            touched = set(operation.stale)
            if operation.winner is not None:
                touched.add(operation.winner)
            if touched & claimed:
                reason = CLAIMED_TWICE

        if reason is None and operation.kind == KIND_SUPERSEDE:
            # The guard worth having. The plausible model error here is not
            # inventing a contradiction, it is getting the direction wrong --
            # retiring this month's preference in favour of last month's, which
            # would reintroduce the exact bug consolidation exists to fix. A
            # timestamp comparison settles it, so a timestamp comparison does.
            stale = facts[operation.stale[0] - 1]
            winner = facts[operation.winner - 1]  # type: ignore[index]
            if not winner.created_at > stale.created_at:
                reason = SUPERSEDE_BACKWARDS

        if reason is not None:
            dropped.append(Dropped(operation=operation, reason=reason))
            continue

        applicable.append(operation)
        claimed.update(operation.stale)
        if operation.winner is not None:
            claimed.add(operation.winner)

    return applicable, dropped
