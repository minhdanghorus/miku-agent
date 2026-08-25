"""Evaluators — every one asserts on behaviour or stored state.

Nothing here looks at how the reply is phrased. Small models word things
differently on every run; which tool ran and what landed in the database do not.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from evals.task import TurnInputs, TurnOutput


@dataclass
class CalledTool(Evaluator[TurnInputs, TurnOutput]):
    """The named tool was called during the turn."""

    tool: str

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        return ctx.output.called(self.tool)


@dataclass
class CalledNoTools(Evaluator[TurnInputs, TurnOutput]):
    """No tool ran at all."""

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        return not ctx.output.tool_calls


@dataclass
class StoredEvent(Evaluator[TurnInputs, TurnOutput]):
    """An event with these exact fields is in the database.

    `title_contains` rather than an exact title: the model chooses the wording of
    the title, and that is not the thing under test.
    """

    day: str
    start_time: str
    title_contains: str = ""

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        for event in ctx.output.events:
            matches = event["day"] == self.day and event["start_time"] == self.start_time
            if matches and self.title_contains.lower() in event["title"].lower():
                return True
        return False


@dataclass
class StoredNothing(Evaluator[TurnInputs, TurnOutput]):
    """No event was persisted — for cases where guessing would be wrong."""

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        return not ctx.output.events


@dataclass
class ToolArgEquals(Evaluator[TurnInputs, TurnOutput]):
    """A specific argument the tool was called with."""

    tool: str
    arg: str
    expected: str

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        return str(ctx.output.args_for(self.tool).get(self.arg, "")) == self.expected


@dataclass
class MentionsAny(Evaluator[TurnInputs, TurnOutput]):
    """The reply contains one of these substrings.

    The one place text is inspected, and only for facts a reply cannot paraphrase
    away — a remembered cat's name is either there or the recall failed.
    """

    options: tuple[str, ...]

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        reply = ctx.output.reply.lower()
        return any(option.lower() in reply for option in self.options)


@dataclass
class StoppedAtCap(Evaluator[TurnInputs, TurnOutput]):
    """The turn terminated at the iteration cap rather than running away."""

    cap: int

    def evaluate(self, ctx: EvaluatorContext[TurnInputs, TurnOutput]) -> bool:
        return ctx.output.iterations == self.cap
