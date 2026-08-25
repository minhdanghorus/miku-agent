"""Tracing: shape, durability, redaction, and never breaking a turn."""

from __future__ import annotations

import io
import json

import pytest

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
