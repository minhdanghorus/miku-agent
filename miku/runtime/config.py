"""Configuration — every knob, read once from the environment.

Nothing else in miku reads os.environ. If you want to know what can be tuned,
this file and .env.example are the whole answer.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
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

    # Request limits, applied to every model the adapter builds.
    request_timeout: float = Field(default=90.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    max_concurrency: int = Field(default=4, ge=1)

    # Where state.db and traces/ live.
    state_dir: Path = Path(".miku")

    # Whose facts the long-term store holds (its namespace).
    user_id: str = "local"

    def model_override(self, role: str) -> str:
        """The configured override for a role, or "" if none."""
        return {
            "main": self.model_main,
            "fast": self.model_fast,
            "judge": self.model_judge,
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
