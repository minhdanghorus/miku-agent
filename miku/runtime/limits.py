"""Concurrency limiting for model calls.

The descriptor's max_concurrency has to be enforced somewhere, and it cannot be
a constructor argument — LangChain has no such knob. So model calls go through
one semaphore per event loop. In Phase 1 a single CLI turn never exceeds it;
this exists because Phase 2's best-of-N fan-out will.
"""

from __future__ import annotations

import asyncio

_semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}


def model_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    """The semaphore for this event loop and limit, created on first use.

    Keyed by loop as well as limit so tests that spin up their own loops do not
    inherit a semaphore bound to a dead one.
    """
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, max_concurrency)
    if key not in _semaphores:
        _semaphores[key] = asyncio.Semaphore(max_concurrency)
    return _semaphores[key]
