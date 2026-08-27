"""Runtime configuration.

Everything that changes between runs lives here. DATA_DIR is the important
one: it is the whole input surface, so pointing at a different folder is all
it takes to run the workflow against different evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).parent
PROJECT_DIR = PACKAGE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""

    # claude-opus-5 for the reasoning passes. The reading pass is cheaper and
    # more mechanical, so it runs on a smaller model.
    model: str = "claude-opus-5"
    reader_model: str = "claude-sonnet-5"
    max_tokens: int = 4096

    data_dir: Path = PROJECT_DIR / "data"
    runs_dir: Path = PROJECT_DIR / "runs"

    # The quarter being drafted. Not derived from today's date: a director
    # submitting late is drafting last quarter, and guessing that wrong is
    # worse than asking.
    quarter: str = "2026-Q3"

    # How many times compose may be sent back over a repairable validation
    # failure before the problem goes to the director instead.
    max_repair_attempts: int = 2

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def objective_file(self) -> Path:
        return self.data_dir / "objective.md"

    @property
    def prior_update_file(self) -> Path:
        return self.data_dir / "prior_update.md"

    @property
    def understanding_dir(self) -> Path:
        """Cached document understanding, keyed by content hash."""
        return self.runs_dir / "understanding"

    @property
    def checkpoint_db(self) -> Path:
        return self.runs_dir / "checkpoints.db"

    def run_dir(self, thread_id: str) -> Path:
        return self.runs_dir / thread_id

    def resolved_api_key(self) -> str:
        """The key, in precedence order. Required — there is no offline mode."""
        return (
            runtime_api_key()
            or self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def require_api_key(self) -> str:
        key = self.resolved_api_key()
        if not key:
            raise MissingAPIKey(
                "ANTHROPIC_API_KEY is not set.\n"
                "Copy .env.example to .env and add a key, or export it:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return key


class MissingAPIKey(RuntimeError):
    """Raised when the workflow is asked to run without a key."""


# --- a key supplied through the interface ------------------------------------
# Held in this process and nowhere else. Not written to .env, not logged, not
# echoed back to the page, and gone when the server stops. A web form that
# persists a secret to disk is a bigger problem than the convenience is worth,
# and for a local single-user tool a process-lifetime key is enough.

_runtime_api_key: str = ""


def set_runtime_api_key(key: str) -> None:
    global _runtime_api_key
    _runtime_api_key = key.strip()


def clear_runtime_api_key() -> None:
    global _runtime_api_key
    _runtime_api_key = ""


def runtime_api_key() -> str:
    return _runtime_api_key


def mask(key: str) -> str:
    """Enough to recognise which key is loaded, not enough to use it."""
    if not key:
        return ""
    return f"{key[:7]}…{key[-4:]}" if len(key) > 15 else "…" + key[-4:]


settings = Settings()
