"""The request budget for one turn.

`max_iterations` bounds how *deep* a turn goes: how many times the agent node
runs. It says nothing about how *wide* a turn goes, and a best-of-N fan-out is
entirely width. Eight iterations each delegating to a five-branch fan-out is
forty-eight model requests, with both of those limits reporting themselves
satisfied. This is the number that bounds depth times width.

Two properties make it work:

  * **One counter per turn, shared by reference.** The main loop and any
    delegated subgraph spend from the same object. Copies would reintroduce
    exactly the blind spot above, one level down.
  * **Per turn, not per session.** A session serves many turns, and from the
    web gateway onwards it serves them concurrently. A budget that outlived its
    turn would let one conversation spend another's allowance.

Spending is a claim, not a report: `spend()` refuses rather than going over, so
a caller that checks the return value cannot overshoot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    """A model-request allowance. Mutable on purpose — see the module docstring."""

    limit: int
    spent: int = 0

    def spend(self, count: int = 1) -> bool:
        """Claim `count` requests. Returns False and claims nothing if that
        would exceed the limit, so a refused caller has not been charged."""
        if self.spent + count > self.limit:
            return False
        self.spent += count
        return True

    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining() == 0

    def for_turn(self) -> Budget:
        """A fresh allowance with the same limit.

        Mirrors `Tracer.for_turn`: one object per turn, cloned from a
        session-lived template, never reset in place. Resetting in place is how
        two concurrent turns end up sharing a counter.
        """
        return Budget(limit=self.limit)
