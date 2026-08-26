"""Reading a trace back.

The sink writes a flat file; causality lives in `parent`. This is the other half
of that bargain — the code that turns the flat file back into the tree it
describes. Evaluators use it to assert the *shape* of a turn (how many branches
ran, what caused them, what each carried) rather than the wording of a reply.

Deliberately dependency-free and read-only: a Phase 3 dashboard wants exactly
this, and so does anyone debugging with a file and a terminal.

Line order is arrival order and is never trusted for structure. A record whose
parent is missing from the same turn is reported as a root, not dropped — a
truncated file should still be readable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def read_records(path: Path, turn_id: str | None = None) -> list[dict]:
    """Every parseable record in the file, optionally for one turn only.

    An unparseable line is skipped rather than raised on: a turn killed
    mid-write must not make the whole file unreadable.
    """
    if not path.exists():
        return []

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if turn_id is None or record.get("turn_id") == turn_id:
            records.append(record)
    return records


@dataclass
class TraceNode:
    """One event plus whatever it caused."""

    record: dict
    children: list[TraceNode] = field(default_factory=list)

    @property
    def span(self) -> str:
        return self.record.get("span", "")

    @property
    def node(self) -> str:
        return self.record.get("node", "")

    @property
    def kind(self) -> str:
        return self.record.get("kind", "")

    def walk(self):
        """This node and every descendant, parents before children."""
        yield self
        for child in self.children:
            yield from child.walk()

    def describe(self, depth: int = 0) -> str:
        """An indented rendering, for eyeballing a turn in the terminal."""
        label = f"{self.kind}:{self.node}" if self.node else self.kind
        branch = self.record.get("branch")
        if branch is not None:
            label += f" [branch {branch}]"
        lines = ["  " * depth + label]
        lines.extend(child.describe(depth + 1) for child in self.children)
        return "\n".join(lines)


def build_tree(records: list[dict]) -> list[TraceNode]:
    """The roots of the forest these records describe.

    Children keep the order they were written in, which is arrival order — the
    tree's *shape* comes from the parent links, and only the ordering among
    siblings reflects who finished first.
    """
    by_span = {record["span"]: TraceNode(record) for record in records if "span" in record}

    roots = []
    for record in records:
        span = record.get("span")
        if span is None:
            continue
        node = by_span[span]
        parent = record.get("parent")
        if parent in by_span and parent != span:
            by_span[parent].children.append(node)
        else:
            roots.append(node)
    return roots


def branches_under(records: list[dict], node_name: str) -> list[dict]:
    """Every record for `node_name` that carries a branch number."""
    return [
        record
        for record in records
        if record.get("node") == node_name and record.get("branch") is not None
    ]


def parents_of(records: list[dict], node_name: str) -> set[str | None]:
    """The distinct parents of every record for `node_name`.

    A fan-out is correct when this is a single span: all branches were caused by
    the same step.
    """
    return {record.get("parent") for record in records if record.get("node") == node_name}
