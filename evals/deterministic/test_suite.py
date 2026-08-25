"""The pydantic-evals suite.

Two halves, separated on purpose:

  * **Live cases** drive the real provider. They cost tokens and need a key, so
    they skip cleanly without one.
  * **The cap case** drives a stubbed model. It costs nothing, needs no key, and
    must never hang — which is exactly the property being tested.
"""

from __future__ import annotations

import pytest
from pydantic_evals import Case, Dataset

from evals.evaluators import (
    CalledNoTools,
    CalledTool,
    MentionsAny,
    StoppedAtCap,
    StoredEvent,
    StoredNothing,
    ToolArgEquals,
)
from evals.helpers import SKIP_REASON, StubModel, has_credentials, wants
from evals.task import TurnInputs, TurnOutput, run_turn

# 2026-08-25 is a Tuesday, so "Saturday" is unambiguously 2026-08-29.
TODAY = "2026-08-25"
NEXT_SATURDAY = "2026-08-29"


LIVE_CASES = [
    Case(
        name="books_a_stated_time",
        inputs=TurnInputs(
            message="Book a tennis game with Raj this Saturday at 8am.", today=TODAY
        ),
        evaluators=[
            CalledTool(tool="create_event"),
            StoredEvent(day=NEXT_SATURDAY, start_time="08:00", title_contains="tennis"),
        ],
    ),
    Case(
        name="resolves_a_weekday_to_an_absolute_date",
        inputs=TurnInputs(message="Put a haircut on Saturday at 14:00.", today=TODAY),
        evaluators=[
            ToolArgEquals(tool="create_event", arg="day", expected=NEXT_SATURDAY),
            StoredEvent(day=NEXT_SATURDAY, start_time="14:00"),
        ],
    ),
    Case(
        name="lists_a_day_without_inventing_events",
        inputs=TurnInputs(message="What do I have on 2026-12-25?", today=TODAY),
        evaluators=[CalledTool(tool="list_events"), StoredNothing()],
    ),
    Case(
        name="asks_rather_than_guessing_a_missing_time",
        inputs=TurnInputs(message="Book a dentist appointment on 2026-09-01.", today=TODAY),
        evaluators=[StoredNothing()],
    ),
    Case(
        name="answers_without_tools_when_none_are_needed",
        inputs=TurnInputs(message="Hello, who are you?", today=TODAY),
        evaluators=[CalledNoTools()],
    ),
    Case(
        name="recalls_a_fact_across_threads",
        inputs=TurnInputs(
            setup_message="Please remember that my cat is called Mochi.",
            setup_thread_id="thread-one",
            message="What is my cat called?",
            thread_id="thread-two",
            today=TODAY,
        ),
        evaluators=[MentionsAny(options=("Mochi",))],
    ),
]


@pytest.mark.skipif(not has_credentials(), reason=SKIP_REASON)
def test_live_suite():
    """The live half. Reports per case; fails the run if any case fails."""
    dataset = Dataset(name="miku-phase-1", cases=LIVE_CASES)
    report = dataset.evaluate_sync(run_turn, max_concurrency=2)
    report.print(include_input=False, include_output=False)

    failures = {
        case.name: [name for name, passed in case.assertions.items() if not passed.value]
        for case in report.cases
        if not all(assertion.value for assertion in case.assertions.values())
    }
    assert not failures, f"cases failed: {failures}"


def test_cap_case_without_credentials(tmp_path):
    """The runaway-turn case: stubbed model, no key, no spend, no hang."""
    from miku.runtime.config import load_settings
    from miku.runtime.session import open_session
    from miku.tools.clock import Clock

    cap = 3

    async def capped_turn(inputs: TurnInputs) -> TurnOutput:
        settings = load_settings(
            state_dir=tmp_path / inputs.thread_id, user_id="eval", max_iterations=cap
        )
        model = StubModel([wants("list_events", {"day": inputs.today})])
        async with open_session(settings, model=model, clock=Clock.fixed(inputs.today)) as session:
            result = await session.run_turn(inputs.message, thread_id=inputs.thread_id)
        return TurnOutput(
            reply=result.reply, tool_calls=result.tool_calls, iterations=result.iterations
        )

    dataset = Dataset(
        name="miku-phase-1-offline",
        cases=[
            Case(
                name="terminates_at_the_iteration_cap",
                inputs=TurnInputs(message="loop forever", today=TODAY, thread_id="cap"),
                evaluators=[StoppedAtCap(cap=cap), MentionsAny(options=("step limit",))],
            )
        ],
    )

    report = dataset.evaluate_sync(capped_turn)
    assertions = report.cases[0].assertions
    assert all(a.value for a in assertions.values()), assertions
