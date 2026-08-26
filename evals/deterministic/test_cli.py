"""The gateway: thread selection, clean exits, and errors as sentences."""

from __future__ import annotations

import builtins

import pytest

from miku.gateway.cli import main, new_thread_id, parse_args, print_tool_activity
from miku.runtime.providers import GREENNODE


def test_default_launch_starts_a_fresh_thread():
    assert parse_args([]).thread_id is None
    assert new_thread_id() != new_thread_id()


def test_a_named_thread_can_be_resumed():
    assert parse_args(["--thread", "work"]).thread_id == "work"


def test_missing_key_is_reported_as_a_sentence(monkeypatch, capsys, tmp_path):
    """No traceback, non-zero status, and the variable is named."""
    monkeypatch.delenv(GREENNODE.key_env, raising=False)
    monkeypatch.setenv("MIKU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(builtins, "input", lambda _="": "hello")

    status = main([])

    assert status == 2
    error = capsys.readouterr().err
    assert GREENNODE.key_env in error
    assert "Traceback" not in error


def test_tool_activity_is_printed(capsys):
    print_tool_activity("tool_call", {"tool": "create_event", "args": {"title": "Tennis"}})
    assert "create_event" in capsys.readouterr().out


def test_no_tool_activity_line_for_other_events(capsys):
    print_tool_activity("node", {"node": "agent"})
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("word", ["exit", "quit", "EXIT", ":q"])
async def test_exit_words_end_the_session(monkeypatch, tmp_path, word):
    from miku.gateway.cli import chat
    from miku.runtime import session as session_module

    monkeypatch.setenv("MIKU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(builtins, "input", lambda _="": word)
    monkeypatch.setattr(session_module, "open_session", _fake_session)

    import miku.gateway.cli as cli_module

    monkeypatch.setattr(cli_module, "open_session", _fake_session)
    assert await chat("t1") == 0


async def test_end_of_input_ends_the_session(monkeypatch, tmp_path):
    from miku.gateway.cli import chat

    def raise_eof(_=""):
        raise EOFError

    monkeypatch.setenv("MIKU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(builtins, "input", raise_eof)

    import miku.gateway.cli as cli_module

    monkeypatch.setattr(cli_module, "open_session", _fake_session)
    assert await chat("t1") == 0


def test_interrupt_exits_cleanly(monkeypatch, capsys, tmp_path):
    import miku.gateway.cli as cli_module

    monkeypatch.setenv("MIKU_STATE_DIR", str(tmp_path / "state"))

    async def interrupted(_thread_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "chat", interrupted)
    assert main([]) == 0
    assert "Traceback" not in capsys.readouterr().err


# --- helpers ----------------------------------------------------------------


class _FakeSession:
    async def run_turn(self, message, thread_id, on_event=None):  # pragma: no cover - unused
        raise AssertionError("no turn should run in these tests")


def _fake_session(*_args, **_kwargs):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def cm():
        yield _FakeSession()

    return cm()


# --- fan-out progress ------------------------------------------------------
# A fan-out is six model calls inside a tool. Without these lines the terminal
# goes quiet for all of them.


def test_a_branch_result_names_its_branch(capsys):
    print_tool_activity(
        "node",
        {
            "node": "generate",
            "branch": 2,
            "ok": True,
            "angle": "quietest day",
            "day": "2026-08-27",
            "start_time": "10:00",
        },
    )
    out = capsys.readouterr().out
    assert "[2]" in out
    assert "quietest day" in out
    assert "2026-08-27" in out


def test_a_failed_branch_says_so_without_pretending(capsys):
    print_tool_activity("node", {"node": "generate", "branch": 1, "ok": False})
    assert "no usable slot" in capsys.readouterr().out


def test_the_width_of_a_fanout_is_announced(capsys):
    print_tool_activity("node", {"node": "plan_angles", "branches": 5})
    assert "5 options" in capsys.readouterr().out


def test_a_clamp_is_visible_rather_than_silent(capsys):
    print_tool_activity(
        "clamp", {"node": "plan_angles", "asked": 9, "using": 5, "reason": "angles"}
    )
    out = capsys.readouterr().out
    assert "5 of 9" in out
    assert "angles" in out


def test_a_selection_reports_what_it_chose_from(capsys):
    print_tool_activity(
        "node", {"node": "select_best", "candidates": 4, "chosen": 2, "judged": True}
    )
    out = capsys.readouterr().out
    assert "option 2 of 4" in out
    assert "judged" in out


def test_a_single_candidate_is_not_reported_as_judged(capsys):
    print_tool_activity(
        "node", {"node": "select_best", "candidates": 1, "chosen": 0, "judged": False}
    )
    assert "only option" in capsys.readouterr().out


def test_a_budget_stop_is_visible(capsys):
    print_tool_activity("budget", {"node": "agent", "spent": 24, "limit": 24})
    assert "budget" in capsys.readouterr().out


def test_ordinary_node_events_stay_quiet(capsys):
    """The terminal reports work, not every state transition."""
    for node in ("assemble", "agent", "tools", "format"):
        print_tool_activity("node", {"node": node})
    assert capsys.readouterr().out == ""


def test_every_progress_line_is_ascii(capsys):
    """Windows consoles mangle anything else."""
    print_tool_activity("tool_call", {"tool": "propose_slots", "args": {"task": "review"}})
    print_tool_activity("node", {"node": "plan_angles", "branches": 5})
    print_tool_activity(
        "node",
        {"node": "generate", "branch": 0, "ok": True, "angle": "a", "day": "d", "start_time": "t"},
    )
    print_tool_activity(
        "clamp", {"node": "plan_angles", "asked": 9, "using": 5, "reason": "budget"}
    )
    print_tool_activity("node", {"node": "select_best", "candidates": 2, "chosen": 0})
    print_tool_activity("budget", {"node": "agent"})

    out = capsys.readouterr().out
    assert out.strip()
    out.encode("ascii")  # raises if anything slipped in
