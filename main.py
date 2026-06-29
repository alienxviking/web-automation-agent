"""Entry point for the Website Automation Agent.

Usage:
    python main.py

All behaviour is controlled through the .env file (see .env.example). Optional
command-line flags let you override the URL and task for a quick one-off run.
"""

from __future__ import annotations

import argparse
import sys

from agent import Agent
from config import Config
from logger import get_logger

log = get_logger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous website automation agent.")
    parser.add_argument("--url", help="Override the target URL for this run.")
    parser.add_argument("--task", help="Override the natural-language task for this run.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Force the browser to run headless (overrides HEADED).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = Config.load()
    except Exception as exc:  # noqa: BLE001
        log.error("Configuration error: %s", exc)
        return 2

    # Apply command-line overrides on top of the loaded config.
    overrides = {}
    if args.url:
        overrides["target_url"] = args.url
    if args.task:
        overrides["task"] = args.task
    if args.headless:
        overrides["headed"] = False
    if overrides:
        config = config.__class__(**{**config.__dict__, **overrides})

    try:
        config.validate()
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    log.info("Starting agent | model=%s | url=%s", config.model, config.target_url)

    try:
        success = Agent(config).run()
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level safety net
        log.error("Unexpected failure: %s", exc)
        return 1

    if success:
        log.info("✅ Task completed successfully.")
        return 0
    log.error("❌ Task did not complete successfully. See logs/screenshots for details.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
