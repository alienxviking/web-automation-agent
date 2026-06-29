"""Central configuration.

Everything tunable lives here and is sourced from environment variables (loaded
from a local ``.env`` file). Nothing about the target page is hardcoded in the
agent logic — it all flows through this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load variables from a local .env file if present. Real environment variables
# always take precedence over the file.
load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None and raw.strip() else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of the agent's runtime settings."""

    api_key: str
    model: str
    target_url: str
    task: str
    form_name: str
    form_description: str
    headed: bool
    viewport_width: int
    viewport_height: int
    max_steps: int
    screenshot_dir: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            model=os.getenv("LLM_MODEL", "gemini-2.5-flash").strip(),
            target_url=os.getenv(
                "TARGET_URL",
                "https://ui.shadcn.com/docs/forms/react-hook-form",
            ).strip(),
            task=os.getenv(
                "TASK",
                'Locate the form on the page. Fill in the "Name" field and the '
                '"Description" field with realistic sample values, then stop.',
            ).strip(),
            form_name=os.getenv("FORM_NAME", "").strip(),
            form_description=os.getenv("FORM_DESCRIPTION", "").strip(),
            headed=_get_bool("HEADED", True),
            viewport_width=_get_int("VIEWPORT_WIDTH", 1280),
            viewport_height=_get_int("VIEWPORT_HEIGHT", 800),
            max_steps=_get_int("MAX_STEPS", 18),
            screenshot_dir=os.getenv("SCREENSHOT_DIR", "screenshots").strip(),
        )

    def validate(self) -> None:
        """Fail fast with a helpful message if something essential is missing."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add a "
                "free key from https://aistudio.google.com/apikey"
            )
        if not self.target_url:
            raise ValueError("TARGET_URL must not be empty.")
