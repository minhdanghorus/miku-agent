"""The clock — one place that knows what day it is.

Tool inputs carry absolute dates, so something has to turn "Saturday" into
2026-08-29. That resolution happens in the model, which means the model has to be
told today's date; this module is what tells it.

It exists as a seam rather than a bare date.today() call so an eval can pin the
reference date. Without that, a case asserting "next Saturday" changes verdict
depending on the day the suite happens to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Clock:
    """Today, according to whoever is asking."""

    today: date

    @classmethod
    def real(cls) -> Clock:
        return cls(today=date.today())

    @classmethod
    def fixed(cls, iso_date: str) -> Clock:
        """A frozen clock, for evals: Clock.fixed("2026-08-25")."""
        return cls(today=date.fromisoformat(iso_date))

    def describe(self) -> str:
        """How the date is stated to the model."""
        return f"{self.today.isoformat()} ({self.today.strftime('%A')})"
