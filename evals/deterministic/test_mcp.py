"""External tool servers: the four claims, and the rules around them.

waku connects to MCP servers in ninety-five lines and asserts nothing about any
of it. Two of the claims here are the two it never made -- that a server which
cannot start does not prevent a session opening, and that no child process
survives the session that spawned it -- and they are the reason this file
exists at all rather than the port being taken on trust.

Every case that needs a live server talks to `evals/fixtures/mcp_echo_server.py`
over stdio, spawned with `sys.executable`. No npx, no network, no credentials.
An in-memory transport would be faster and is exactly what cannot make the
fourth claim, there being no process to outlive anything.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from evals.deterministic.test_inspect import code_only
from evals.helpers import MCP_SKIP_REASON, StubModel, has_mcp_extra
from miku.mcp import client as mcp_client
from miku.mcp import config as mcp_config
from miku.runtime import inspect as runtime_inspect
from miku.runtime.config import load_settings
from miku.runtime.session import open_session
from miku.tools.clock import Clock

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"
FIXED_CLOCK = Clock.fixed("2026-08-25")

needs_mcp = pytest.mark.skipif(not has_mcp_extra(), reason=MCP_SKIP_REASON)


# --- Helpers ----------------------------------------------------------------


def write_config(tmp_path: Path, *servers: dict) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": list(servers)}), encoding="utf-8")
    return path


def echo_server(name: str = "echo", **extra) -> dict:
    """The in-repo fixture, described as the configuration file would describe it."""
    return {"name": name, "command": sys.executable, "args": [str(FIXTURE)], **extra}


def settings_for(tmp_path: Path, config: Path | None, *, enabled: bool = True):
    return load_settings(
        state_dir=tmp_path / "state",
        user_id="tester",
        max_iterations=3,
        mcp_enabled=enabled,
        mcp_config=config if config is not None else tmp_path / "absent.json",
    )


def alive(pid: int) -> bool:
    """Whether a process id is still running.

    Written out rather than reached for through a dependency because the claim
    it serves is platform-specific: on Windows a child does not die with its
    parent, and `os.kill(pid, 0)` there is not a liveness probe but a request to
    terminate.
    """
    if os.name == "nt":
        query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == still_active
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def names(tools) -> set[str]:
    return {tool.name for tool in tools}


# --- The two gates ----------------------------------------------------------


async def test_nothing_is_configured_and_nothing_is_started(tmp_path):
    """The default. No flag, no file, no subprocess, four tools."""
    async with open_session(settings_for(tmp_path, None, enabled=False), model=StubModel([])) as s:
        assert s.mcp.tools == []
        assert s.mcp.states == []
        assert names(s.tools) == {"create_event", "list_events", "remember", "propose_slots"}


@needs_mcp
async def test_the_file_alone_does_not_open_the_gate(tmp_path):
    """waku's rule is the file's presence alone. This one has an off switch."""
    config = write_config(tmp_path, echo_server())
    settings = settings_for(tmp_path, config, enabled=False)

    async with open_session(settings, model=StubModel([])) as session:
        assert session.mcp.tools == []
        # Configured, though: reported without being contacted.
        assert [state.name for state in session.mcp.states] == ["echo"]
        assert not any(state.connected for state in session.mcp.states)


async def test_the_flag_alone_is_not_an_error(tmp_path):
    """Enabling the connector with no file is a state, not a misconfiguration.

    This feature is not important enough to fail a startup over.
    """
    async with open_session(settings_for(tmp_path, None), model=StubModel([])) as session:
        assert session.mcp.tools == []
        assert "create_event" in names(session.tools)


@needs_mcp
async def test_both_gates_open_binds_the_server_tools(tmp_path):
    """Claim 1: a reachable server's tools are in `deps.tools`, namespaced."""
    config = write_config(tmp_path, echo_server())

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert {"echo_echo", "echo_word_count", "echo_explode"} <= names(session.tools)
        # The built-in four are untouched.
        assert "create_event" in names(session.tools)
        assert "echo" not in names(session.tools)


# --- Degradation ------------------------------------------------------------


@needs_mcp
async def test_a_server_that_cannot_start_does_not_prevent_a_session(tmp_path):
    """Claim 2, which waku never asserted.

    The whole degradation rule in one case: a command that does not exist costs
    a warning and its own tools, and costs the session nothing.
    """
    config = write_config(tmp_path, {"name": "broken", "command": "no-such-command-anywhere"})

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert names(session.tools) == {
            "create_event",
            "list_events",
            "remember",
            "propose_slots",
        }
        state = session.mcp.states[0]
        assert state.name == "broken"
        assert not state.connected
        assert state.error


@needs_mcp
async def test_one_broken_server_does_not_cost_the_others_their_tools(tmp_path):
    config = write_config(
        tmp_path,
        {"name": "broken", "command": "no-such-command-anywhere"},
        echo_server("good"),
    )

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert "good_echo" in names(session.tools)
        states = {state.name: state for state in session.mcp.states}
        assert not states["broken"].connected
        assert states["good"].connected


@needs_mcp
async def test_a_failing_contributed_tool_becomes_a_tool_result(tmp_path):
    """Claim 3: the existing loop rule, extended to a borrowed tool.

    The fixture's `explode` raises inside the server. The turn must still reach
    a reply -- a tool failure is something the model reads, not something the
    user sees as a traceback.
    """
    config = write_config(tmp_path, echo_server())
    model = StubModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "echo_explode", "args": {}, "id": "call-1"}],
            ),
            AIMessage(content="That tool did not work."),
        ]
    )

    async with open_session(settings_for(tmp_path, config), model=model) as session:
        result = await session.run_turn("break something", thread_id="t")

    assert result.reply == "That tool did not work."
    assert result.called("echo_explode")


@needs_mcp
async def test_a_tool_result_reaches_the_model_as_prose(tmp_path):
    """An MCP result arrives as content blocks; `nodes.py` does `str(output)`.

    Without flattening, the model would be handed
    `[{'type': 'text', 'text': 'hi', 'id': 'lc_...'}]` -- brackets, quotes and a
    random uuid. This is the case that keeps that from being invisible.
    """
    config = write_config(tmp_path, echo_server())

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        tool = next(tool for tool in session.tools if tool.name == "echo_echo")
        assert await tool.ainvoke({"text": "hello"}) == "hello"

        counter = next(tool for tool in session.tools if tool.name == "echo_word_count")
        assert await counter.ainvoke({"text": "one two three"}) == "3 words"


# --- Choosing what gets bound -----------------------------------------------


@needs_mcp
async def test_an_allowlist_binds_exactly_its_subset(tmp_path):
    config = write_config(tmp_path, echo_server(tools=["echo"]))

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert "echo_echo" in names(session.tools)
        assert "echo_word_count" not in names(session.tools)
        assert "echo_explode" not in names(session.tools)


@needs_mcp
async def test_no_allowlist_binds_everything(tmp_path):
    config = write_config(tmp_path, echo_server())

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert session.mcp.states[0].tool_count == 3


@needs_mcp
async def test_a_selected_tool_the_server_does_not_offer_is_reported(tmp_path):
    """A typo in an allowlist would otherwise present as a tool that is never called."""
    config = write_config(tmp_path, echo_server(tools=["echo", "no_such_tool"]))

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert "echo_echo" in names(session.tools)
        assert any("no_such_tool" in problem for problem in session.mcp.problems)


@needs_mcp
async def test_a_disabled_server_is_not_contacted_and_is_still_reported(tmp_path):
    """"I turned it off" and "I never set it up" must not be the same sentence."""
    config = write_config(tmp_path, echo_server("off", enabled=False), echo_server("on"))

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert "on_echo" in names(session.tools)
        assert not any(name.startswith("off_") for name in names(session.tools))

        states = {state.name: state for state in session.mcp.states}
        assert states["off"].enabled is False
        assert states["off"].connected is False
        assert states["off"].tool_count == 0
        assert states["off"].error is None  # not broken -- switched off


@needs_mcp
async def test_two_servers_offering_the_same_tool_name_do_not_collide(tmp_path):
    config = write_config(tmp_path, echo_server("left"), echo_server("right"))

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert {"left_echo", "right_echo"} <= names(session.tools)


@needs_mcp
async def test_a_collision_with_a_built_in_name_is_reported_not_resolved(tmp_path):
    """Which of two same-named tools ran is what nobody can reconstruct afterwards.

    A server named `create` offering `event` would register as `create_event`,
    which a built-in tool already answers to. It is refused and said out loud.
    """
    config = write_config(tmp_path, echo_server("create"))
    settings = settings_for(tmp_path, config)

    async with open_session(settings, model=StubModel([])) as session:
        native = next(tool for tool in session.tools if tool.name == "create_event")
        assert "schedule" in (native.description or "").lower() or native.metadata is None

        borrowed = [
            tool
            for tool in session.tools
            if (tool.metadata or {}).get(mcp_client.MCP_SERVER_KEY) == "create"
        ]
        assert {tool.name for tool in borrowed} == {"create_echo", "create_word_count",
                                                    "create_explode"}


# --- Lifecycle --------------------------------------------------------------


@needs_mcp
async def test_closing_the_session_leaves_no_surviving_process(tmp_path):
    """Claim 4, which waku never asserted, and the one most likely to be false.

    On Windows an orphaned child does not die with its parent, and the failure
    presents as a machine slowly accumulating browsers rather than as an error
    anyone sees. The fixture writes its own pid so there is something to check.
    """
    pid_file = tmp_path / "server.pid"
    config = write_config(
        tmp_path,
        {"name": "echo", "command": sys.executable, "args": [str(FIXTURE), str(pid_file)]},
    )

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        assert "echo_echo" in names(session.tools)
        pid = int(pid_file.read_text(encoding="utf-8"))
        assert alive(pid), "the fixture should be running while the session is open"

    deadline = time.monotonic() + 10
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not alive(pid), f"process {pid} outlived the session that started it"


@needs_mcp
async def test_tools_are_known_before_the_first_turn(tmp_path):
    """The argument against connecting lazily, as a case.

    `build_graph` calls `bind_tools`, so a tool the model has not been told
    about cannot be requested. If these arrived after the graph was built they
    would be invisible to the model no matter how well they worked.
    """
    config = write_config(tmp_path, echo_server())
    model = StubModel([AIMessage(content="hi")])

    async with open_session(settings_for(tmp_path, config), model=model) as session:
        assert "echo_echo" in {tool.name for tool in model.bound_tools}
        assert session.mcp.states[0].connected


# --- The graph does not change ----------------------------------------------

# Recorded before a line of this change was written. The design says MCP makes
# `deps.tools` longer and does nothing else; this is what makes that a claim
# rather than an intention. If a file here had to change, the seam was in the
# wrong place and that would have been the finding rather than the feature.
GRAPH_BEFORE_PHASE_4 = {
    "__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "build.py": "067491a879079958f8ac67733f52c100ce705b1ddfbd6c7e0125c0a15087c546",
    "fanout.py": "6c6a8bfa6a34942b14e6449b1ddf7f1e29db8b4cec1612006b769222427f3bf4",
    "nodes.py": "d7e5bf14fb4bfca05278bf943f15af41bf7373528cd31c617eca45e21a7ff3a0",
    "state.py": "8e6e0f511b2fae2ecfb87506ec12a487e51091f7bb080b10c4afddecbfac86f8",
}


def test_the_graph_package_is_byte_identical():
    graph = Path(__file__).resolve().parents[2] / "miku" / "graph"
    found = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(graph.glob("*.py"))
    }
    assert found == GRAPH_BEFORE_PHASE_4


def test_the_graph_never_learns_what_mcp_is():
    """A contributed tool is looked up by name and awaited, like any other."""
    graph = Path(__file__).resolve().parents[2] / "miku" / "graph"
    for path in graph.glob("*.py"):
        assert "mcp" not in path.read_text(encoding="utf-8").lower()


# --- Configuration, parsed --------------------------------------------------


def test_a_malformed_file_is_reported_and_the_session_still_opens(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{ not json at all", encoding="utf-8")
    config = mcp_config.load_mcp_config(settings_for(tmp_path, path))

    assert config.servers == ()
    assert any("not valid JSON" in problem for problem in config.problems)
    assert config.connectable() == []


async def test_a_malformed_file_leaves_the_built_in_tools_registered(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{ not json at all", encoding="utf-8")

    async with open_session(settings_for(tmp_path, path), model=StubModel([])) as session:
        assert "create_event" in names(session.tools)
        assert session.mcp.tools == []


def test_an_unimplemented_transport_is_reported_naming_the_server(tmp_path):
    path = write_config(tmp_path, {"name": "remote", "command": "x", "transport": "http"})
    config = mcp_config.load_mcp_config(settings_for(tmp_path, path))

    assert config.servers == ()
    assert len(config.problems) == 1
    assert "remote" in config.problems[0]
    assert "http" in config.problems[0]


def test_a_server_with_no_command_is_refused_by_name(tmp_path):
    path = write_config(tmp_path, {"name": "empty", "command": "   "})
    problems = mcp_config.load_mcp_config(settings_for(tmp_path, path)).problems

    assert len(problems) == 1
    assert "empty" in problems[0]


def test_two_servers_with_one_name_keep_the_first(tmp_path):
    path = write_config(
        tmp_path,
        {"name": "twice", "command": "first"},
        {"name": "twice", "command": "second"},
    )
    config = mcp_config.load_mcp_config(settings_for(tmp_path, path))

    assert [server.command for server in config.servers] == ["first"]
    assert any("twice" in problem for problem in config.problems)


def test_a_bad_entry_does_not_cost_the_good_ones(tmp_path):
    path = write_config(
        tmp_path,
        {"name": "fine", "command": "ok"},
        {"command": "nameless"},
    )
    config = mcp_config.load_mcp_config(settings_for(tmp_path, path))

    assert [server.name for server in config.servers] == ["fine"]
    assert len(config.problems) == 1


def test_a_working_directory_survives_parsing(tmp_path):
    """Without `cwd` the servers this change was written for cannot start at all."""
    path = write_config(tmp_path, {"name": "s", "command": "py", "cwd": "D:\\my-mcp"})
    server = mcp_config.load_mcp_config(settings_for(tmp_path, path)).servers[0]

    assert server.cwd == "D:\\my-mcp"


def test_the_committed_example_file_parses(tmp_path):
    """The template is documentation, and documentation that does not parse is worse
    than none. It is also what the fixture case points at, so the two cannot drift."""
    example = Path(__file__).resolve().parents[2] / "mcp.example.json"
    servers, problems = mcp_config.read_server_file(example)

    assert problems == []
    assert [server.name for server in servers] == ["echo", "chrome", "employees"]
    assert servers[0].enabled and servers[0].args == ("evals/fixtures/mcp_echo_server.py",)
    assert servers[1].tools == ("navigate_page", "take_snapshot", "click")
    assert servers[2].cwd == "D:\\my-mcp"


def test_the_mcp_modules_read_no_environment():
    """The existing environment-free pin, extended.

    `config.py` decides where to look and whether to look. Reading a JSON file
    is not reading the environment, and a second module reaching for
    `os.environ` is how two answers to "what is configured?" start to diverge.

    Through `code_only`, and deliberately: the first version of this scan failed
    on the docstrings that explain the rule. A grep over raw source cannot tell
    a call from a sentence about a call, which this repo learned once already.
    """
    for module in (mcp_config, mcp_client):
        stripped = code_only(module)
        for forbidden in ("environ", "getenv", "load_dotenv"):
            assert forbidden not in stripped, f"{module.__name__} must not read {forbidden}"


# --- Inspection -------------------------------------------------------------


def test_server_state_is_reportable_with_no_session(tmp_path):
    """Connection state is unknown, not an error, and nothing is started to find out."""
    path = write_config(tmp_path, echo_server(), {"name": "off", "command": "x", "enabled": False})
    view = runtime_inspect.mcp_view(settings_for(tmp_path, path))

    assert view.enabled and view.present and view.active
    assert [server.name for server in view.servers] == ["echo", "off"]
    assert all(server.connected is None for server in view.servers)
    assert view.servers[1].enabled is False


def test_the_view_distinguishes_disabled_from_absent(tmp_path):
    path = write_config(tmp_path, echo_server())

    off = runtime_inspect.mcp_view(settings_for(tmp_path, path, enabled=False))
    assert off.present and not off.enabled and not off.active
    assert [server.name for server in off.servers] == ["echo"]

    absent = runtime_inspect.mcp_view(settings_for(tmp_path, None))
    assert absent.enabled and not absent.present and not absent.active
    assert absent.servers == []


@needs_mcp
async def test_live_state_comes_from_a_session_that_already_holds_it(tmp_path):
    config = write_config(tmp_path, echo_server())

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        view = runtime_inspect.mcp_view(session.settings, session.mcp.states)

    assert view.servers[0].connected is True
    assert view.servers[0].tool_count == 3
    assert view.servers[0].error == ""


@needs_mcp
async def test_tools_are_reported_with_distinguishable_sources(tmp_path):
    config = write_config(tmp_path, echo_server())

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        views = {view.name: view for view in runtime_inspect.tools_view(session.tools)}

    assert views["create_event"].source == runtime_inspect.NATIVE
    assert views["echo_echo"].source == "echo"
    # The description a borrowed tool is offered with is the server's own.
    assert views["echo_echo"].description


def test_inspecting_servers_starts_nothing(tmp_path, monkeypatch):
    """`inspect.py` is safe to call in the middle of a turn. Spawning a browser
    to answer "which servers are configured" would break that outright."""
    import subprocess

    def refuse(*args, **kwargs):
        raise AssertionError("inspection started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    path = write_config(tmp_path, echo_server(), {"name": "chrome", "command": "npx"})

    view = runtime_inspect.mcp_view(settings_for(tmp_path, path))
    assert len(view.servers) == 2


def test_the_inspection_module_still_reads_no_environment():
    """The existing pin, re-asserted now that `mcp_view` reads a file.

    Reading a configuration file is not reading the environment. This is the
    line that says so in a way that fails if someone blurs it.
    """
    stripped = code_only(runtime_inspect)

    for forbidden in ("environ", "getenv", "load_dotenv"):
        assert forbidden not in stripped


# --- Gateways ---------------------------------------------------------------


def test_the_terminal_reports_nothing_configured_as_a_sentence(tmp_path, capsys):
    """Exit 0, and a sentence. "Nothing is set up" is not a failure."""
    from miku.gateway.cli import print_mcp_report

    view = runtime_inspect.mcp_view(settings_for(tmp_path, None))
    print_mcp_report(view, [])

    printed = capsys.readouterr().out
    assert "no external tool servers are configured" in printed
    assert printed.isascii()


def test_the_terminal_distinguishes_disabled_from_absent(tmp_path, capsys):
    from miku.gateway.cli import print_mcp_report

    path = write_config(tmp_path, echo_server())
    view = runtime_inspect.mcp_view(settings_for(tmp_path, path, enabled=False))
    print_mcp_report(view, [])

    printed = capsys.readouterr().out
    assert "disabled" in printed
    assert "echo" in printed  # configured servers are still listed
    assert printed.isascii()


def test_the_terminal_reports_a_failure_with_its_reason(tmp_path, capsys):
    from miku.gateway.cli import print_mcp_report
    from miku.mcp.client import ServerState

    path = write_config(tmp_path, {"name": "broken", "command": "nope"})
    states = [ServerState("broken", True, "stdio", False, error="FileNotFoundError: no such file")]
    print_mcp_report(runtime_inspect.mcp_view(settings_for(tmp_path, path), states), [])

    printed = capsys.readouterr().out
    assert "FAILED" in printed
    assert "FileNotFoundError" in printed
    assert printed.isascii()


@needs_mcp
async def test_the_terminal_report_is_ascii_with_a_live_server(tmp_path, capsys, monkeypatch):
    """Windows consoles mangle anything else, and this output is for a terminal."""
    from miku.gateway import cli

    config = write_config(tmp_path, echo_server())
    settings = settings_for(tmp_path, config)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    assert await cli.show_mcp() == 0

    printed = capsys.readouterr().out
    assert printed.isascii()
    assert "echo_echo" in printed
    assert "3 tools" in printed


def test_the_terminal_reads_through_the_inspection_surface():
    """It opens the connector, as `miku threads` opens a checkpointer -- but the
    reading is `mcp_view`, not a second parse of the configuration file."""
    from miku.gateway import cli

    source = code_only(cli)
    assert "mcp_view" in source
    assert "load_mcp_config" not in source
    assert "read_server_file" not in source


@needs_mcp
async def test_the_cockpit_serves_server_state(tmp_path):
    """In-process, no port bound, as every other web case is."""
    pytest.importorskip("fastapi")
    import httpx

    from miku.gateway.web import create_app

    config = write_config(tmp_path, echo_server(), {"name": "off", "command": "x",
                                                    "enabled": False})

    async with open_session(settings_for(tmp_path, config), model=StubModel([])) as session:
        transport = httpx.ASGITransport(app=create_app(session=session))
        async with httpx.AsyncClient(transport=transport, base_url="http://cockpit") as client:
            body = (await client.get("/api/mcp")).json()
            tools = (await client.get("/api/tools")).json()

    assert body["enabled"] and body["present"]
    servers = {server["name"]: server for server in body["servers"]}
    assert servers["echo"]["connected"] is True
    assert servers["echo"]["tool_count"] == 3
    assert servers["off"]["enabled"] is False

    sources = {tool["name"]: tool["source"] for tool in tools}
    assert sources["create_event"] == "native"
    assert sources["echo_echo"] == "echo"


def test_the_cockpit_neither_reads_the_file_nor_contacts_a_server():
    """Rendering a page must not be able to launch a browser."""
    from miku.gateway import web

    source = code_only(web)
    assert "mcp_view" in source
    assert "load_mcp_config" not in source
    assert "open_mcp" not in source


def test_the_two_gateways_still_do_not_import_each_other():
    """This change adds a surface to both, which is the kind of change that
    breaks the edge the peer constraint rests on."""
    from miku.gateway import cli, web

    assert "gateway.web" not in code_only(cli)
    assert "gateway.cli" not in code_only(web)
