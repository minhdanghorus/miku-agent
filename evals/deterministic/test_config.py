"""The knobs. Defaults are asserted because they are the documented contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from miku.runtime.config import load_settings


def test_fanout_and_budget_have_documented_defaults():
    settings = load_settings()
    assert settings.fanout_branches == 5
    assert settings.max_requests_per_turn == 24


def test_both_knobs_are_overridable():
    settings = load_settings(fanout_branches=2, max_requests_per_turn=3)
    assert settings.fanout_branches == 2
    assert settings.max_requests_per_turn == 3


def test_the_environment_can_set_them(monkeypatch):
    monkeypatch.setenv("MIKU_FANOUT_BRANCHES", "7")
    monkeypatch.setenv("MIKU_MAX_REQUESTS_PER_TURN", "9")
    settings = load_settings()
    assert settings.fanout_branches == 7
    assert settings.max_requests_per_turn == 9


@pytest.mark.parametrize("field", ["fanout_branches", "max_requests_per_turn"])
def test_neither_knob_accepts_zero(field):
    """A zero-width fan-out or a zero-request budget is a misconfiguration, and
    configuration errors fail loudly at startup rather than degrading."""
    with pytest.raises(ValidationError):
        load_settings(**{field: 0})


def test_the_budget_bounds_more_than_the_iteration_cap():
    """The two limits are not interchangeable: max_iterations bounds depth,
    the budget bounds depth x breadth, so the budget must be the larger."""
    settings = load_settings()
    assert settings.max_requests_per_turn > settings.max_iterations


def test_the_consolidation_budget_has_a_default():
    assert load_settings().max_requests_per_consolidation == 4


def test_the_consolidation_budget_is_overridable(monkeypatch):
    monkeypatch.setenv("MIKU_MAX_REQUESTS_PER_CONSOLIDATION", "2")
    assert load_settings().max_requests_per_consolidation == 2


def test_the_consolidation_budget_rejects_zero():
    with pytest.raises(ValidationError):
        load_settings(max_requests_per_consolidation=0)


def test_a_consolidation_run_does_not_borrow_the_turn_allowance():
    """Two separate knobs on purpose: a run is not a turn, and a pass over all
    of memory must not be able to spend what a conversation was allotted."""
    settings = load_settings()
    moved = load_settings(max_requests_per_consolidation=11)
    assert moved.max_requests_per_consolidation == 11
    assert moved.max_requests_per_turn == settings.max_requests_per_turn
