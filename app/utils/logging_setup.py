"""Rotating file logger for the app.

Writes to ~/Library/Logs/Ask/ask.log in packaged mode, ~/.ask-dev/logs/
in dev mode. Rotates daily, keeps 7 days. Mirrors output to stderr so
`python run.py` still shows live logs in the terminal.
"""
from __future__ import annotations
import logging
import logging.handlers
import sys

from .. import settings

_INITIALIZED = False


def init_logging(level: int = logging.INFO) -> None:
    """Idempotent — safe to call from main.py and mac_launcher.py."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.TimedRotatingFileHandler(
        settings.LOG_FILE,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(level)
    root.addHandler(stderr_handler)

    # Quiet noisy third-party loggers we don't care about.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _INITIALIZED = True
    logging.getLogger("ask").info("logging initialized → %s", settings.LOG_FILE)
