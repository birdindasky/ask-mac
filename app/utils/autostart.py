"""Open-at-login toggle.

Implementation: write/remove a LaunchAgent plist at
~/Library/LaunchAgents/<bundle-id>.plist. We deliberately avoid the
SMAppService API (macOS 13+ but PyObjC binding is fiddly) and
osascript/System Events (requires Automation permission, prompts the user
on every call) — a LaunchAgent is the lowest-friction path that works
without any permission grant.

The plist points at /Applications/Ask.app/Contents/MacOS/Ask. If the user
has the .app installed elsewhere, autostart simply won't fire — that's
acceptable for v0.2.

Public API (mockable via filesystem in tests):
  enable_login_item()  -> writes plist
  disable_login_item() -> removes plist
  is_enabled()         -> bool, plist file exists
  plist_path()         -> Path, override target with MLC_LAUNCH_AGENT_PATH
  launch_path()        -> Path, override target with MLC_AUTOSTART_BINARY
"""
from __future__ import annotations
import os
import plistlib
from pathlib import Path

from .. import settings

# Default install location — what `make install` places it at.
_DEFAULT_BINARY = Path("/Applications/Ask.app/Contents/MacOS/Ask")


def plist_path() -> Path:
    """Where the LaunchAgent plist goes. MLC_LAUNCH_AGENT_PATH overrides
    for tests."""
    override = os.environ.get("MLC_LAUNCH_AGENT_PATH")
    if override:
        return Path(override)
    return Path.home() / "Library" / "LaunchAgents" / f"{settings.BUNDLE_ID}.plist"


def launch_path() -> Path:
    """Path to the executable launchd should run. MLC_AUTOSTART_BINARY
    overrides for tests."""
    override = os.environ.get("MLC_AUTOSTART_BINARY")
    if override:
        return Path(override)
    return _DEFAULT_BINARY


def is_enabled() -> bool:
    return plist_path().is_file()


def enable_login_item() -> None:
    """Write the LaunchAgent plist. Idempotent — overwrites if present."""
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": settings.BUNDLE_ID,
        "ProgramArguments": [str(launch_path())],
        # RunAtLoad fires once on login; KeepAlive=False because the user
        # may quit the app intentionally and we don't want it auto-restarted.
        "RunAtLoad": True,
        "KeepAlive": False,
        # ProcessType=Interactive so launchd treats it like a user-facing app
        # (no aggressive throttling).
        "ProcessType": "Interactive",
    }
    with open(target, "wb") as f:
        plistlib.dump(plist, f)


def disable_login_item() -> None:
    """Remove the LaunchAgent plist. Idempotent — silently no-op if absent."""
    target = plist_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
