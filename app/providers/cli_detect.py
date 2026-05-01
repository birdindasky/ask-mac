"""Find `claude` / `codex` binaries even when PATH is empty (.app bundles).

py2app launches the .app with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin),
so shutil.which() inside the bundle will miss /opt/homebrew/bin and friends.
We scan the canonical locations Mac users install Node-based CLIs into.
"""
from __future__ import annotations
import os
import shutil
import threading
from pathlib import Path

CANDIDATE_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    str(Path.home() / ".local/bin"),
    str(Path.home() / ".npm-global/bin"),
    str(Path.home() / ".volta/bin"),
    str(Path.home() / ".asdf/shims"),
    str(Path.home() / "n/bin"),
]

_cache: dict[str, str | None] = {}
_lock = threading.Lock()


def find_binary(name: str, *, refresh: bool = False) -> str | None:
    """Return absolute path to `name` or None.

    Result is cached for the process lifetime; pass refresh=True to re-scan
    after the user installs the CLI without restarting Ask.
    """
    if not refresh and name in _cache:
        return _cache[name]
    with _lock:
        if not refresh and name in _cache:
            return _cache[name]
        # 1. Honor an explicit absolute path the user might have configured.
        # 2. PATH (works in dev mode, mostly empty in .app).
        # 3. Canonical directories.
        result: str | None = shutil.which(name)
        if not result:
            for d in CANDIDATE_DIRS:
                p = Path(d) / name
                if p.is_file() and os.access(p, os.X_OK):
                    result = str(p)
                    break
        _cache[name] = result
        return result


def is_available(name: str, *, refresh: bool = False) -> bool:
    return find_binary(name, refresh=refresh) is not None


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def augmented_path(existing: str | None = None) -> str:
    """Build a PATH that includes every CANDIDATE_DIR that actually exists.

    Required because Node-based CLIs like `codex` have a `#!/usr/bin/env node`
    shebang — even when we hand them an absolute path, their child env still
    needs `node` (and possibly `npx`, `npm`) on PATH or you get
    "env: node: No such file or directory". py2app launches the .app with
    PATH=/usr/bin:/bin:/usr/sbin:/sbin so we have to put Homebrew/nvm/volta
    back in front.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for d in CANDIDATE_DIRS:
        if d in seen:
            continue
        if Path(d).is_dir():
            parts.append(d)
            seen.add(d)
    if existing:
        for d in existing.split(":"):
            if d and d not in seen:
                parts.append(d)
                seen.add(d)
    return ":".join(parts)
