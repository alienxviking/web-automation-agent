"""Lightweight, colourful console logging shared across the project.

A single configured logger keeps the agent's decisions and the browser tool
actions easy to follow during a live demo, while also writing a plain-text log
file for later inspection.
"""

from __future__ import annotations

import logging
import os
import sys

_LOGGER_NAME = "agent"


class _ConsoleFormatter(logging.Formatter):
    """Adds simple ANSI colours so the trace is readable at a glance."""

    COLOURS = {
        logging.DEBUG: "\033[90m",     # grey
        logging.INFO: "\033[36m",      # cyan
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[41m",  # red background
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self.COLOURS.get(record.levelno, "")
        message = super().format(record)
        return f"{colour}{message}{self.RESET}"


def get_logger(log_dir: str = "logs") -> logging.Logger:
    """Return the shared logger, configuring handlers exactly once."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ConsoleFormatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
    logger.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(log_dir, "agent.log"), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(file_handler)
    except OSError:
        # If the log directory cannot be created we still keep console logging.
        pass

    return logger
