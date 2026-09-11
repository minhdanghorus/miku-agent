"""What an external tool server is, and how one is described.

This module reads a JSON file. It does not read the environment -- `Settings`
already decided where to look and whether to look, and that is the whole of
MCP's presence there. The rule in `runtime/config.py` says nothing else reads
`os.environ`, and a file is not the environment; keeping the server list out of
`Settings` is what stops a nested, variable-arity structure being flattened into
names like MIKU_MCP_SERVER_0_ARGS_2.

It also connects to nothing. Parsing a description of a server and starting one
are separate, and only this half is safe to call from `inspect.py`, which is
pinned read-only and must be callable in the middle of a turn without launching
a browser.

Every problem found here is reported and carried, never raised. A server
description that is wrong costs that server; it does not cost the session. The
one thing the file cannot do is make Miku fail to start.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from miku.runtime.config import Settings

# The only transport with an implementation behind it. The field exists anyway,
# and defaults to this, so that adding Streamable HTTP later is an addition
# rather than a migration of every configuration file already written.
SUPPORTED_TRANSPORTS = ("stdio",)


@dataclass(frozen=True)
class MCPServerSpec:
    """One external server, exactly as the file described it.

    `tools` is an allowlist and `None` means "everything this server offers".
    It is an allowlist rather than a denylist on purpose: a denylist lets a
    server's next release add tools to the model's prompt without anyone
    deciding to.

    `cwd` is not a refinement. A server that lives in its own project, with its
    own interpreter and relative imports, cannot be started from this project's
    directory -- and that describes the servers this change was written for.
    """

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None
    transport: str = "stdio"
    enabled: bool = True
    tools: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MCPConfig:
    """The file's contents, plus what the two gates make of them.

    `servers` is everything the file described and parsed, whether or not the
    connector is on. That is deliberate: a gateway has to be able to say
    "disabled" and "nothing configured" as different sentences, and it cannot do
    that from an empty list.

    `connectable()` is the other question -- what should actually be started --
    and that one is empty whenever either gate is closed.
    """

    path: Path
    present: bool
    enabled: bool
    servers: tuple[MCPServerSpec, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        """Both gates open. Neither alone is enough."""
        return self.enabled and self.present

    def connectable(self) -> list[MCPServerSpec]:
        """The servers to start: none unless active, and none marked disabled."""
        if not self.active:
            return []
        return [server for server in self.servers if server.enabled]


def load_mcp_config(settings: Settings) -> MCPConfig:
    """Read the configured file, if there is one.

    Absence is never an error. Enabling the connector without writing a file is
    a state, not a misconfiguration, and this feature is not important enough to
    fail a startup over.

    The file is parsed even when the connector is disabled, and the servers it
    describes are carried. Only `connectable()` respects the gates. A gateway
    has to distinguish "nothing is configured" from "the connector is off", and
    an empty list cannot say which.
    """
    path = Path(settings.mcp_config)
    present = path.is_file()
    if not present:
        return MCPConfig(path=path, present=False, enabled=settings.mcp_enabled)

    servers, problems = read_server_file(path)
    return MCPConfig(
        path=path,
        present=True,
        enabled=settings.mcp_enabled,
        servers=tuple(servers),
        problems=tuple(problems),
    )


def read_server_file(path: Path) -> tuple[list[MCPServerSpec], list[str]]:
    """Parse one file into server descriptions and a list of what was wrong.

    Returns both halves rather than raising, because a session that opens with
    four built-in tools and a warning is better than one that does not open.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        return [], [f"{path}: could not be read ({error})"]
    except json.JSONDecodeError as error:
        return [], [f"{path}: is not valid JSON ({error})"]

    if not isinstance(raw, dict):
        return [], [f"{path}: expected an object with a 'servers' list"]

    entries = raw.get("servers")
    if entries is None:
        return [], [f"{path}: has no 'servers' key"]
    if not isinstance(entries, list):
        return [], [f"{path}: 'servers' must be a list"]

    servers: list[MCPServerSpec] = []
    problems: list[str] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries):
        spec, problem = _parse_server(entry, index, path)
        if problem is not None:
            problems.append(problem)
            continue
        if spec is None:
            continue
        if spec.name in seen:
            problems.append(f"{path}: two servers are named '{spec.name}'; the second is ignored")
            continue
        seen.add(spec.name)
        servers.append(spec)

    return servers, problems


def _parse_server(entry: object, index: int, path: Path) -> tuple[MCPServerSpec | None, str | None]:
    """One entry, validated.

    The label falls back to the position in the list, because an entry with no
    usable name still has to be identifiable in the sentence that reports it.
    """
    where = f"{path}: server #{index + 1}"
    if not isinstance(entry, dict):
        return None, f"{where} is not an object"

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, f"{where} has no name"
    name = name.strip()
    where = f"{path}: server '{name}'"

    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        return None, f"{where} has no command to run"

    transport = entry.get("transport", "stdio")
    if not isinstance(transport, str) or transport not in SUPPORTED_TRANSPORTS:
        return None, (
            f"{where} asks for transport '{transport}', which is not implemented; "
            f"supported: {', '.join(SUPPORTED_TRANSPORTS)}"
        )

    args, problem = _string_list(entry.get("args", []), f"{where}: 'args'")
    if problem is not None:
        return None, problem

    tools_raw = entry.get("tools")
    tools: tuple[str, ...] | None = None
    if tools_raw is not None:
        selected, problem = _string_list(tools_raw, f"{where}: 'tools'")
        if problem is not None:
            return None, problem
        tools = tuple(selected)

    env_raw = entry.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env_raw.items()
    ):
        return None, f"{where}: 'env' must be an object of strings"

    cwd = entry.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return None, f"{where}: 'cwd' must be a string"

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        return None, f"{where}: 'enabled' must be true or false"

    return (
        MCPServerSpec(
            name=name,
            command=command.strip(),
            args=tuple(args),
            env=dict(env_raw),
            cwd=cwd,
            transport=transport,
            enabled=enabled,
            tools=tools,
        ),
        None,
    )


def _string_list(value: object, label: str) -> tuple[list[str], str | None]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return [], f"{label} must be a list of strings"
    if not all(isinstance(item, str) for item in value):
        return [], f"{label} must be a list of strings"
    return [str(item) for item in value], None


def report(problems: Sequence[str], stream=None) -> None:
    """Say what was wrong, once, on stderr.

    The same shape the tracer uses for a trace-write failure: a warning a person
    can read, on the stream that is not the conversation.
    """
    for problem in problems:
        print(f"[miku] mcp: {problem}", file=stream or sys.stderr)
