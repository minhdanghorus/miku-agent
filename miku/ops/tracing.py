"""Tracing — one JSON object per line, one line per node transition.

Three properties are load-bearing:

  * **Redaction happens here, in the sink.** Not at call sites. A caller that
    forgets cannot leak a key, because no caller is trusted to remember.
  * **Write failures never break a turn.** Observability is not a correctness
    dependency; a full disk must not cost the user their answer.
  * **The event shape is `{kind, ...}`** — the same shape a browser dashboard
    consumes. A later phase can feed a UI from the same events without
    reshaping them.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

REDACTED = "[REDACTED]"

# Values shorter than this are not worth masking — redacting "3" would blank out
# half of every payload.
MIN_SECRET_LENGTH = 8


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


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
    warn_stream = sys.stderr

    def __post_init__(self) -> None:
        self._secrets = _collect_secrets(self.secret_env_names)
        self._warned = False

    def for_turn(self, turn_id: str) -> Tracer:
        """A tracer writing to the same place, tagged with a different turn."""
        clone = Tracer(
            traces_dir=self.traces_dir,
            turn_id=turn_id,
            secret_env_names=self.secret_env_names,
        )
        return clone

    @property
    def path(self) -> Path:
        return self.traces_dir / f"{date.today().isoformat()}.jsonl"

    def event(self, kind: str, node: str = "", **payload) -> None:
        """Record one event. Never raises."""
        record = {
            "turn_id": self.turn_id,
            "kind": kind,
            "node": node,
            "ts": datetime.now(UTC).isoformat(),
            **payload,
        }
        try:
            line = json.dumps(_redact(record, self._secrets), ensure_ascii=False, default=str)
            self.traces_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as error:  # noqa: BLE001 - tracing must never break a turn
            self._warn(error)

    def tool_event(self, tool: str, ok: bool, node: str = "tools", **payload) -> None:
        self.event("tool", node=node, tool=tool, ok=ok, **payload)

    def _warn(self, error: Exception) -> None:
        if self._warned:
            return
        self._warned = True
        print(f"[miku] tracing disabled for this session: {error}", file=self.warn_stream)


class NullTracer(Tracer):
    """Drops everything. For tests that do not care about traces."""

    def __init__(self) -> None:
        super().__init__(traces_dir=Path("."))

    def event(self, kind: str, node: str = "", **payload) -> None:  # noqa: D102
        return
