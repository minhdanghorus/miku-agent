"""Configuration — every knob, read once from the environment.

Nothing else in miku reads os.environ. If you want to know what can be tuned,
this file and .env.example are the whole answer.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Runtime configuration, MIKU_-prefixed in the environment."""

    model_config = SettingsConfigDict(env_prefix="MIKU_", extra="ignore")

    # Which registered provider descriptor to use.
    provider: str = "greennode"

    # Per-role model overrides. Empty means "use the descriptor's default".
    model_main: str = ""
    model_fast: str = ""
    model_judge: str = ""
    model_select: str = ""
    model_embed: str = ""

    # The loop's hard stop. Reaching it ends the turn with a reply saying so.
    max_iterations: int = Field(default=8, ge=1)

    # How wide a fan-out goes. Clamped down to the number of distinct angles
    # available, so raising this alone does not buy more diversity.
    fanout_branches: int = Field(default=5, ge=1)

    # Every model request in a turn counts against this, the main loop's and any
    # delegated subgraph's alike. max_iterations bounds depth; this bounds
    # depth x breadth, which is the number that actually decides the bill.
    max_requests_per_turn: int = Field(default=24, ge=1)

    # The same idea for a consolidation run, which is not a turn and must not
    # spend a turn's allowance. Small on purpose: the pass is one request today,
    # and the headroom exists so that chunking a large fact set stays bounded
    # rather than becoming an unbounded loop over memory.
    max_requests_per_consolidation: int = Field(default=4, ge=1)

    # Request limits, applied to every model the adapter builds.
    request_timeout: float = Field(default=90.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    max_concurrency: int = Field(default=4, ge=1)

    # Where state.db and traces/ live.
    state_dir: Path = Path(".miku")

    # The external-tool connector, off unless both of these agree. The flag is
    # the brake for a config file that outlives the intention behind it: a
    # forgotten mcp.json would otherwise spawn processes on every session of a
    # repo someone came back to after three months.
    #
    # These two fields are all of MCP that reaches Settings. A server spec is a
    # nested thing of variable arity -- command, args, an environment map, a
    # working directory, a tool allowlist -- and flattening that into
    # MIKU_MCP_SERVER_0_ARGS_2 produces names nobody can read. The servers live
    # in a file, and miku/mcp/config.py is what reads it. That is not a hole in
    # this module's rule, which is about the environment: reading a JSON file is
    # not reading os.environ.
    mcp_enabled: bool = False

    # Defaulted into the gitignored state directory rather than the repo root,
    # because a server description carries the credentials that server starts
    # with, and the root is where an accidental `git add -f` finds it. The
    # tracked template is mcp.example.json.
    #
    # The literal default is what someone reading this file needs to see, but it
    # follows state_dir rather than being pinned to it: an eval that redirects
    # state_dir to a temporary directory would otherwise still be pointed at the
    # developer's real server list. See _mcp_config_follows_state_dir.
    mcp_config: Path = Path(".miku/mcp.json")

    # Whose facts the long-term store holds (its namespace).
    user_id: str = "local"

    @model_validator(mode="after")
    def _mcp_config_follows_state_dir(self) -> Settings:
        """Keep the config file inside the state directory unless asked otherwise.

        Only when `mcp_config` was not set explicitly. Someone who names a path
        means that path; someone who moves `state_dir` means the file moved with
        it, which is what every other thing in that directory does.
        """
        if "mcp_config" not in self.model_fields_set:
            object.__setattr__(self, "mcp_config", self.state_dir / "mcp.json")
        return self

    def model_override(self, role: str) -> str:
        """The configured override for a role, or "" if none."""
        return {
            "main": self.model_main,
            "fast": self.model_fast,
            "judge": self.model_judge,
            "select": self.model_select,
            "embed": self.model_embed,
        }.get(role, "")

    @property
    def db_path(self) -> Path:
        """The single SQLite file: thread state, facts, and events."""
        return self.state_dir / "state.db"

    @property
    def traces_dir(self) -> Path:
        return self.state_dir / "traces"

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)


def load_settings(**overrides: object) -> Settings:
    """Build settings, letting tests override any field directly."""
    return Settings(**overrides)  # type: ignore[arg-type]
