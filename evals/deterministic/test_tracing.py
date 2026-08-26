"""Tracing: shape, durability, redaction, and never breaking a turn."""

from __future__ import annotations

import io
import json

import pytest

from miku.ops.traceview import (
    branches_under,
    build_tree,
    parents_of,
    read_records,
)
from miku.ops.tracing import REDACTED, Tracer, new_turn_id


@pytest.fixture
def tracer(tmp_path):
    return Tracer(traces_dir=tmp_path / "traces", turn_id="turn-a")


def read_lines(tracer):
    return [json.loads(line) for line in tracer.path.read_text(encoding="utf-8").splitlines()]


def test_every_line_parses_as_json(tracer):
    tracer.event("node", node="assemble")
    tracer.event("node", node="agent")
    assert len(read_lines(tracer)) == 2


def test_events_carry_turn_kind_node_and_timestamp(tracer):
    tracer.event("node", node="agent", iteration=1)
    record = read_lines(tracer)[0]

    assert record["turn_id"] == "turn-a"
    assert record["kind"] == "node"
    assert record["node"] == "agent"
    assert record["iteration"] == 1
    assert record["ts"]


def test_events_are_attributable_to_one_turn(tracer):
    tracer.event("node", node="agent")
    other = tracer.for_turn("turn-b")
    other.event("node", node="agent")

    turns = {record["turn_id"] for record in read_lines(tracer)}
    assert turns == {"turn-a", "turn-b"}
    assert len([r for r in read_lines(tracer) if r["turn_id"] == "turn-a"]) == 1


def test_events_append_across_tracers_without_truncating(tmp_path):
    first = Tracer(traces_dir=tmp_path / "traces", turn_id="one")
    first.event("node", node="agent")

    second = Tracer(traces_dir=tmp_path / "traces", turn_id="two")
    second.event("node", node="agent")

    assert len(read_lines(second)) == 2


def test_a_failed_tool_event_is_marked_failed(tracer):
    tracer.tool_event("create_event", ok=False, error="boom")
    record = read_lines(tracer)[0]

    assert record["kind"] == "tool"
    assert record["tool"] == "create_event"
    assert record["ok"] is False


def test_a_successful_tool_event_is_distinguishable(tracer):
    tracer.tool_event("create_event", ok=True)
    assert read_lines(tracer)[0]["ok"] is True


def test_an_api_key_in_a_payload_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY_ENV", "vn--supersecretvalue123")
    tracer = Tracer(traces_dir=tmp_path / "traces", secret_env_names=("FAKE_KEY_ENV",))

    tracer.event("node", node="agent", detail="calling with vn--supersecretvalue123 attached")

    raw = tracer.path.read_text(encoding="utf-8")
    assert "vn--supersecretvalue123" not in raw
    assert REDACTED in raw
    # Still valid JSON, and the untouched fields survive.
    record = json.loads(raw.splitlines()[0])
    assert record["node"] == "agent"


def test_redaction_reaches_nested_payloads(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY_ENV", "vn--supersecretvalue123")
    tracer = Tracer(traces_dir=tmp_path / "traces", secret_env_names=("FAKE_KEY_ENV",))

    tracer.event("tool", args={"headers": ["Bearer vn--supersecretvalue123"]})

    assert "supersecretvalue" not in tracer.path.read_text(encoding="utf-8")


def test_short_env_values_are_not_treated_as_secrets(tmp_path, monkeypatch):
    """Masking a two-character value would blank out unrelated text."""
    monkeypatch.setenv("FAKE_KEY_ENV", "ok")
    tracer = Tracer(traces_dir=tmp_path / "traces", secret_env_names=("FAKE_KEY_ENV",))

    tracer.event("node", node="agent", detail="booking is ok")
    assert "booking is ok" in tracer.path.read_text(encoding="utf-8")


def test_an_unwritable_destination_does_not_raise(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("I am a file", encoding="utf-8")

    tracer = Tracer(traces_dir=blocked)
    tracer.warn_stream = io.StringIO()

    tracer.event("node", node="agent")  # must not raise
    tracer.event("node", node="tools")


def test_unserialisable_payloads_do_not_raise(tracer):
    class Opaque:
        pass

    tracer.event("node", node="agent", weird=Opaque())
    assert read_lines(tracer)[0]["kind"] == "node"


def test_turn_ids_are_unique():
    assert new_turn_id() != new_turn_id()


# --- parentage -------------------------------------------------------------
# Line order records arrival. Structure comes from `parent`, and these are the
# tests that hold that distinction in place.


def test_every_event_carries_its_own_span(tracer):
    first = tracer.event("node", node="assemble")
    second = tracer.event("node", node="agent")

    spans = [record["span"] for record in read_lines(tracer)]
    assert spans == [first, second]
    assert len(set(spans)) == 2


def test_a_root_event_has_no_parent(tracer):
    tracer.event("node", node="assemble")
    assert read_lines(tracer)[0]["parent"] is None


def test_a_linear_turn_reconstructs_as_one_chain(tracer):
    root = tracer.event("node", node="assemble")
    agent = tracer.event("node", node="agent", parent=root)
    tools = tracer.event("node", node="tools", parent=agent)
    tracer.event("node", node="agent", parent=tools)

    roots = build_tree(read_lines(tracer))
    assert len(roots) == 1
    chain = [node.node for node in roots[0].walk()]
    assert chain == ["assemble", "agent", "tools", "agent"]


def test_a_child_tracer_hangs_events_under_its_parent(tracer):
    parent = tracer.event("node", node="tools")
    tracer.child(parent).event("node", node="plan_angles")

    records = read_lines(tracer)
    assert records[1]["parent"] == parent
    assert records[1]["turn_id"] == records[0]["turn_id"]


def test_concurrent_branches_share_one_parent_and_differ_by_branch(tracer):
    parent = tracer.event("node", node="plan_angles")
    for index in (0, 1, 2):
        tracer.child(parent, branch=index).event("node", node="generate")

    records = read_lines(tracer)
    generated = branches_under(records, "generate")
    assert len(generated) == 3
    assert parents_of(records, "generate") == {parent}
    assert sorted(record["branch"] for record in generated) == [0, 1, 2]


def test_out_of_order_arrival_reconstructs_identically(tracer):
    """Branches finish in whatever order they finish. The tree must not care."""
    parent = tracer.event("node", node="plan_angles")
    children = [tracer.child(parent, branch=i) for i in (0, 1, 2)]
    for index in (2, 0, 1):  # deliberately not the order they were created in
        children[index].event("node", node="generate")

    roots = build_tree(read_lines(tracer))
    assert len(roots) == 1
    assert len(roots[0].children) == 3
    assert {child.record["branch"] for child in roots[0].children} == {0, 1, 2}


def test_a_branchless_tracer_writes_no_branch_field(tracer):
    tracer.event("node", node="agent")
    assert "branch" not in read_lines(tracer)[0]


def test_an_orphaned_event_is_reported_as_a_root(tracer):
    """A file read mid-write can reference a parent that is not there yet."""
    tracer.event("node", node="generate", parent="never-written")
    roots = build_tree(read_lines(tracer))
    assert len(roots) == 1
    assert roots[0].node == "generate"


def test_a_truncated_line_does_not_break_the_reader(tracer):
    tracer.event("node", node="assemble")
    with tracer.path.open("a", encoding="utf-8") as handle:
        handle.write('{"turn_id": "turn-a", "sp\n')  # a killed write

    records = read_records(tracer.path)
    assert len(records) == 1
    assert records[0]["node"] == "assemble"


def test_a_new_turn_does_not_inherit_parentage(tracer):
    parent = tracer.event("node", node="tools")
    fresh = tracer.child(parent).for_turn("turn-b")
    fresh.event("node", node="assemble")

    later = [r for r in read_lines(tracer) if r["turn_id"] == "turn-b"]
    assert later[0]["parent"] is None


def test_a_span_is_returned_even_when_the_write_fails(tmp_path):
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("occupied", encoding="utf-8")
    tracer = Tracer(traces_dir=blocked, turn_id="turn-a")
    tracer.warn_stream = io.StringIO()

    span = tracer.event("node", node="agent")
    assert span  # parentage must survive a broken sink
