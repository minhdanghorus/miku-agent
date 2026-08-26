"""Tracing — one JSON object per line, one line per node transition.

Four properties are load-bearing:

  * **Causality lives in `parent`, not in line order.** Every event carries its
    own `span` and the `parent` that caused it. A linear turn reads back as a
    chain either way; five concurrent fan-out branches only read back correctly
    because the links are explicit. Line order records arrival.

  * **Redaction happens here, in the sink.** Not at call sites. A caller that
    forgets cannot leak a key, because no caller is trusted to remember.
  * **Write failures never break a turn.** Observability is not a correctness
    dependency; a full disk must not cost the user their answer.
  * **The event shape is `{kind, ...}`** — the same shape a browser dashboard
    consumes. A later phase can feed a UI from the same events without
    reshaping them. `listener` is that seam already in use: the CLI watches a
    fan-out through it, because a subgraph running inside a tool is invisible to
    the parent graph's own update stream.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

REDACTED = "[REDACTED]"

# Values shorter than this are not worth masking — redacting "3" would blank out
# half of every payload.
MIN_SECRET_LENGTH = 8


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def new_span_id() -> str:
    return uuid.uuid4().hex[:8]


def _collect_secrets(env_names: tuple[str, ...]) -> tuple[str, ...]:
    values = []
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if len(value) >= MIN_SECRET_LENGTH:
            values.append(value)
    # Longest first, so a secret containing another is masked whole.
    return tuple(sorted(set(values), key=len, reverse=True))


def _redact(value, secrets: tuple[str, ...]):
    """Walk a payload, masking any secret found inside a string."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    return value


@dataclass
class Tracer:
    """Appends events to .miku/traces/<date>.jsonl."""

    traces_dir: Path
    turn_id: str = field(default_factory=new_turn_id)
    secret_env_names: tuple[str, ...] = ()
    # What an event caused by this tracer hangs under, unless told otherwise.
    parent: str | None = None
    # Set on every event this tracer writes. Fan-out branches use it; nothing
    # else does, so it stays out of the record when it is None.
    branch: int | None = None
    # Called with every redacted record, for a gateway watching progress. It
    # sees exactly what the file sees -- never the raw payload -- so a listener
    # cannot become a second way to leak a key.
    listener: Callable[[dict], None] | None = None
    warn_stream = sys.stderr

    def __post_init__(self) -> None:
        self._secrets = _collect_secrets(self.secret_env_names)
        self._warned = False

    def for_turn(self, turn_id: str) -> Tracer:
        """A tracer writing to the same place, tagged with a different turn.

        A new turn is a new root: it inherits no parentage from the tracer it
        was cloned from.
        """
        return Tracer(
            traces_dir=self.traces_dir,
            turn_id=turn_id,
            secret_env_names=self.secret_env_names,
            listener=self.listener,
        )

    def child(self, parent: str, branch: int | None = None) -> Tracer:
        """A tracer whose events hang under `parent`.

        This is how parentage crosses a boundary the graph cannot carry state
        across — into a tool, and from there into a subgraph. Deliberately a
        clone rather than a mutable "current span" global: two branches running
        at once must not be able to see each other's position.
        """
        clone = Tracer(
            traces_dir=self.traces_dir,
            turn_id=self.turn_id,
            secret_env_names=self.secret_env_names,
            parent=parent,
            branch=branch,
            listener=self.listener,
        )
        return clone

    @property
    def path(self) -> Path:
        return self.traces_dir / f"{date.today().isoformat()}.jsonl"

    def event(self, kind: str, node: str = "", parent: str | None = None, **payload) -> str:
        """Record one event and return its span id. Never raises.

        The returned span is what a caller passes on as the `parent` of whatever
        it does next — that is the whole mechanism by which a turn becomes a
        tree. `parent` defaults to this tracer's own, which is what makes a
        child tracer work without every call site remembering.
        """
        span = new_span_id()
        record = {
            "turn_id": self.turn_id,
            "span": span,
            "parent": self.parent if parent is None else parent,
            "kind": kind,
            "node": node,
            "ts": datetime.now(UTC).isoformat(),
            **payload,
        }
        if self.branch is not None:
            record["branch"] = self.branch
        visible = _redact(record, self._secrets)
        try:
            line = json.dumps(visible, ensure_ascii=False, default=str)
            self.traces_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as error:  # noqa: BLE001 - tracing must never break a turn
            self._warn(error)

        if self.listener is not None:
            try:
                self.listener(visible)
            except Exception as error:  # noqa: BLE001 - a watcher cannot break a turn
                self._warn(error)
        # Returned even when the write failed: a broken sink must not also break
        # the parentage the caller is threading through the graph.
        return span

    def tool_event(
        self, tool: str, ok: bool, node: str = "tools", parent: str | None = None, **payload
    ) -> str:
        return self.event("tool", node=node, parent=parent, tool=tool, ok=ok, **payload)

    def _warn(self, error: Exception) -> None:
        if self._warned:
            return
        self._warned = True
        print(f"[miku] tracing disabled for this session: {error}", file=self.warn_stream)


class NullTracer(Tracer):
    """Drops everything. For tests that do not care about traces."""

    def __init__(self) -> None:
        super().__init__(traces_dir=Path("."))

    def event(
        self, kind: str, node: str = "", parent: str | None = None, **payload
    ) -> str:  # noqa: D102
        # Still mints a span, so a caller threading parentage behaves the same
        # whether or not anything is being written.
        return new_span_id()
