"""App-wide paths and constants.

Two layouts depending on how the app boots:
  - Dev (`python run.py`): data lives in ~/.ask-dev/ (sibling of the source repo,
    isolated so you can iterate without touching production data).
  - .app bundle: data at ~/Library/Application Support/Ask/ per macOS HIG.
    Logs at ~/Library/Logs/Ask/.

Override either with MLC_DATA_DIR / MLC_LOG_DIR env vars (used by tests).
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

APP_NAME = "Ask"
BUNDLE_ID = "com.birdindasky.ask"
APP_VERSION = "0.2.0"


def _is_packaged() -> bool:
    """True when running inside a .app bundle (py2app sets this)."""
    return getattr(sys, "frozen", False) or os.environ.get("MLC_PACKAGED") == "1"


def _default_data_dir() -> Path:
    if _is_packaged():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".ask-dev"


def _default_log_dir() -> Path:
    if _is_packaged():
        return Path.home() / "Library" / "Logs" / APP_NAME
    return _default_data_dir() / "logs"


DATA_DIR = Path(os.environ.get("MLC_DATA_DIR", str(_default_data_dir())))
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = Path(os.environ.get("MLC_LOG_DIR", str(_default_log_dir())))
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "app.db"
WINDOW_STATE_FILE = DATA_DIR / "window.json"
LOG_FILE = LOG_DIR / "ask.log"

CLI_REQUEST_TIMEOUT_SEC = int(os.environ.get("MLC_CLI_TIMEOUT", "120"))
DEFAULT_HTTP_TIMEOUT_SEC = int(os.environ.get("MLC_HTTP_TIMEOUT", "120"))

# Keychain service identifier for SecKeychain entries.
KEYCHAIN_SERVICE = BUNDLE_ID

# Whether to fall back to JSON storage for keys when running outside .app
# (i.e. dev mode). In packaged mode keys MUST go through Keychain.
ALLOW_KEYCHAIN_FALLBACK = not _is_packaged()
