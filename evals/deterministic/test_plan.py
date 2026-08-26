"""Plan validation — the gate between what a model proposes and what gets written.

Pure functions, no store, no model, no credentials. Every rule is asserted in
isolation, because each one is the only thing standing between a bad plan and a
memory the user cannot get back.
"""

from __future__ import annotations

from miku.memory.plan import (
    CLAIMED_TWICE,
    EMPTY_SELECTION,
    EXPIRE_ONE_FACT,
    MERGE_NEEDS_SOURCES,
    MERGE_NEEDS_TEXT,
    NEEDS_WINNER,
    SUPERSEDE_BACKWARDS,
    SUPERSEDE_ONE_STALE,
    TAKES_NO_WINNER,
    UNKNOWN_INDEX,
    WINNER_IN_SELECTION,
    Operation,
    Plan,
    validate_plan,
)
from miku.memory.store import LiveFact


def facts(*texts: str) -> list[LiveFact]:
    """Live facts one day apart, oldest first — so direction is unambiguous."""
    return [
        LiveFact(key=f"k{index}", fact=text, created_at=f"2026-08-{index + 1:02d}T09:00:00+00:00")
        for index, text in enumerate(texts)
    ]


THREE = facts("mornings", "afternoons", "in Hanoi this week")


def only(plan_operations: list[Operation]) -> Plan:
    return Plan(operations=plan_operations)


# --- the happy shapes ------------------------------------------------------


def test_a_forward_supersession_is_applicable():
    plan = only([Operation(kind="supersede", stale=[1], winner=2)])
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert dropped == []


def test_a_duplicate_may_name_several_stale_facts():
    plan = only([Operation(kind="duplicate", stale=[2, 3], winner=1)])
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert dropped == []


def test_a_merge_needs_no_winner():
    plan = only([Operation(kind="merge", stale=[1, 2], fact="mornings, then afternoons")])
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert dropped == []


def test_an_expire_names_one_fact_and_no_winner():
    plan = only([Operation(kind="expire", stale=[3])])
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert dropped == []


def test_an_empty_plan_is_valid():
    applicable, dropped = validate_plan(Plan(), THREE)
    assert applicable == []
    assert dropped == []


# --- the direction guard ---------------------------------------------------


def test_a_backwards_supersession_is_rejected():
    """The guard that matters: retiring the newer fact for the older one would
    reintroduce the exact bug consolidation exists to fix."""
    plan = only([Operation(kind="supersede", stale=[2], winner=1)])
    applicable, dropped = validate_plan(plan, THREE)
    assert applicable == []
    assert [drop.reason for drop in dropped] == [SUPERSEDE_BACKWARDS]


def test_a_supersession_between_facts_of_equal_age_is_rejected():
    """Not strictly newer is not newer. Ties give no evidence of direction."""
    same = [
        LiveFact(key="a", fact="one", created_at="2026-08-01T09:00:00+00:00"),
        LiveFact(key="b", fact="two", created_at="2026-08-01T09:00:00+00:00"),
    ]
    plan = only([Operation(kind="supersede", stale=[1], winner=2)])
    applicable, dropped = validate_plan(plan, same)
    assert applicable == []
    assert [drop.reason for drop in dropped] == [SUPERSEDE_BACKWARDS]


def test_direction_is_not_checked_for_other_kinds():
    """Only supersede claims a correction happened, so only supersede is timed."""
    plan = only([Operation(kind="duplicate", stale=[2], winner=1)])
    applicable, _ = validate_plan(plan, THREE)
    assert len(applicable) == 1


# --- index and claim rules -------------------------------------------------


def test_an_index_past_the_end_is_dropped():
    plan = only([Operation(kind="expire", stale=[9])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [UNKNOWN_INDEX]


def test_a_zero_index_is_dropped():
    """The numbering the model sees is 1-based; 0 means it lost count."""
    plan = only([Operation(kind="expire", stale=[0])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [UNKNOWN_INDEX]


def test_an_out_of_range_winner_is_dropped():
    plan = only([Operation(kind="supersede", stale=[1], winner=9)])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [UNKNOWN_INDEX]


def test_an_empty_selection_is_dropped():
    plan = only([Operation(kind="expire", stale=[])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [EMPTY_SELECTION]


def test_a_fact_cannot_win_and_lose_in_one_operation():
    plan = only([Operation(kind="supersede", stale=[1], winner=1)])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [WINNER_IN_SELECTION]


def test_a_fact_claimed_twice_keeps_the_first_operation():
    plan = only(
        [
            Operation(kind="supersede", stale=[1], winner=2),
            Operation(kind="expire", stale=[1]),
        ]
    )
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert applicable[0].kind == "supersede"
    assert [drop.reason for drop in dropped] == [CLAIMED_TWICE]


def test_a_winner_is_claimed_too():
    """Otherwise a later operation could retire the fact an earlier one promoted."""
    plan = only(
        [
            Operation(kind="supersede", stale=[1], winner=2),
            Operation(kind="expire", stale=[2]),
        ]
    )
    applicable, dropped = validate_plan(plan, THREE)
    assert len(applicable) == 1
    assert [drop.reason for drop in dropped] == [CLAIMED_TWICE]


# --- per-kind shape rules --------------------------------------------------


def test_a_supersession_naming_two_stale_facts_is_dropped():
    plan = only([Operation(kind="supersede", stale=[1, 3], winner=2)])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [SUPERSEDE_ONE_STALE]


def test_a_supersession_without_a_winner_is_dropped():
    plan = only([Operation(kind="supersede", stale=[1])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [NEEDS_WINNER]


def test_a_duplicate_without_a_winner_is_dropped():
    plan = only([Operation(kind="duplicate", stale=[1, 2])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [NEEDS_WINNER]


def test_a_merge_with_one_source_is_dropped():
    plan = only([Operation(kind="merge", stale=[1], fact="merged")])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [MERGE_NEEDS_SOURCES]


def test_a_merge_with_blank_text_is_dropped():
    plan = only([Operation(kind="merge", stale=[1, 2], fact="   ")])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [MERGE_NEEDS_TEXT]


def test_a_merge_naming_a_winner_is_dropped():
    """A merge writes a new fact. Nominating an existing one is a different op."""
    plan = only([Operation(kind="merge", stale=[1, 2], winner=3, fact="merged")])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [TAKES_NO_WINNER]


def test_an_expire_naming_two_facts_is_dropped():
    plan = only([Operation(kind="expire", stale=[1, 2])])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [EXPIRE_ONE_FACT]


def test_an_expire_naming_a_winner_is_dropped():
    plan = only([Operation(kind="expire", stale=[3], winner=1)])
    _, dropped = validate_plan(plan, THREE)
    assert [drop.reason for drop in dropped] == [TAKES_NO_WINNER]


# --- degrading, not crashing -----------------------------------------------


def test_a_partly_invalid_plan_still_makes_progress():
    plan = only(
        [
            Operation(kind="expire", stale=[99]),
            Operation(kind="supersede", stale=[1], winner=2),
            Operation(kind="merge", stale=[3], fact="too few sources"),
        ]
    )
    applicable, dropped = validate_plan(plan, THREE)
    assert [operation.kind for operation in applicable] == ["supersede"]
    assert [drop.reason for drop in dropped] == [UNKNOWN_INDEX, MERGE_NEEDS_SOURCES]


def test_validation_never_raises_on_an_empty_fact_set():
    plan = only([Operation(kind="supersede", stale=[1], winner=2)])
    applicable, dropped = validate_plan(plan, [])
    assert applicable == []
    assert [drop.reason for drop in dropped] == [UNKNOWN_INDEX]


def test_every_dropped_entry_carries_its_operation():
    """The CLI and the trace both need to show *what* was rejected, not just why."""
    operation = Operation(kind="expire", stale=[42], why="stale")
    _, dropped = validate_plan(only([operation]), THREE)
    assert dropped[0].operation == operation
