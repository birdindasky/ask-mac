"""Self-updater: check GitHub Releases, download the newer .dmg, and swap the
running .app in place.

Why this works without code-signing pain: the DMG is fetched with urllib
(not a browser), so macOS never stamps it with com.apple.quarantine — and a
non-quarantined app doesn't trip Gatekeeper. We strip the xattr anyway as a
belt-and-suspenders step.

The actual swap is done by a detached shell helper (see build_swap_script):
it waits for this process to exit, stages the new app next to the old one,
backs the old one up, moves the new one into place, and relaunches — restoring
the backup if anything fails, so a botched update never leaves a half-written
bundle behind. Everything here is a no-op / reports "dev build" when not
running from a packaged .app.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urlparse

from . import settings

GITHUB_REPO = "birdindasky/ask-mac"
_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_UA = {"User-Agent": f"Ask/{settings.APP_VERSION}", "Accept": "application/vnd.github+json"}


# ---------------------------------------------------------------- versions

def parse_version(text: str) -> tuple[int, ...]:
    """'v0.4.0' / '0.4.0' / 'Ask 0.4' → (0,4,0). Non-numeric tails are ignored."""
    m = re.search(r"(\d+(?:\.\d+)*)", text or "")
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


# ---------------------------------------------------------------- packaging

def _app_bundle_path() -> Optional[Path]:
    """Path to the running Ask.app, or None in dev mode.

    In a py2app bundle sys.executable is <App>.app/Contents/MacOS/<exe>, so the
    bundle is three parents up. We only accept a path that actually ends in
    `.app` to avoid ever pointing the swap at a dev checkout."""
    if not settings._is_packaged():
        return None
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


# ---------------------------------------------------------------- check

def check_for_update(timeout: float = 10.0) -> dict:
    """Query GitHub for the latest release. Returns a dict the API layer can
    hand straight to the frontend."""
    current = settings.APP_VERSION
    bundle = _app_bundle_path()
    base = {"ok": True, "packaged": bundle is not None, "current": current}
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(_LATEST_URL, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # network / rate-limit / parse
        return {**base, "ok": False, "error": f"检查更新失败:{e}"}

    tag = rel.get("tag_name") or rel.get("name") or ""
    latest = tag.lstrip("vV")
    dmg_url = None
    for asset in rel.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".dmg"):
            dmg_url = asset.get("browser_download_url")
            break
    return {
        **base,
        "latest": latest,
        "tag": tag,
        "is_newer": is_newer(latest, current),
        "notes": rel.get("body") or "",
        "published_at": rel.get("published_at"),
        "dmg_url": dmg_url,
        "html_url": rel.get("html_url"),
    }


# ---------------------------------------------------------------- download

def is_trusted_dmg_url(url: str) -> bool:
    """Only ever download from our own GitHub release. This is the anchor that
    keeps the (deliberately quarantine-free) install as trustworthy as the
    manual "download from the releases page" path — an attacker who reaches the
    local port still can't point us at their own binary."""
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    host_ok = host == "github.com" or host.endswith(".github.com") or host.endswith(".githubusercontent.com")
    return p.scheme == "https" and host_ok and f"/{GITHUB_REPO}/releases/" in (p.path or "")


def download_dmg(url: str, dest: Path, on_progress: Optional[Callable[[int, int], None]] = None,
                 timeout: float = 30.0) -> None:
    """Stream a DMG to `dest`. urllib download → no com.apple.quarantine.
    Verifies completeness against Content-Length so a truncated transfer is an
    error, not a silently-corrupt install."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    on_progress(got, total)
    if total and got < total:
        raise IOError(f"下载不完整({got}/{total} 字节)")


# ---------------------------------------------------------------- swap script

def build_swap_script(app_pid: int, app_path: Path, src_app: Path,
                      dmg_vol: Path, log_path: Path) -> str:
    """Detached bash that waits for us to quit, then atomically swaps the
    bundle with backup/restore, and relaunches. Kept dependency-free (ditto,
    mv, hdiutil, open, xattr are all system tools)."""
    parent = str(app_path.parent)
    return f"""#!/bin/bash
APP_PID={app_pid}
APP_PATH={_q(app_path)}
SRC_APP={_q(src_app)}
DMG_VOL={_q(dmg_vol)}
PARENT={_q(parent)}
exec >>{_q(log_path)} 2>&1
STAGE="$PARENT/.Ask.app.new"
BAK="$PARENT/.Ask.app.bak"

echo "[updater] $(date) waiting for pid $APP_PID"
kill "$APP_PID" 2>/dev/null
for i in $(seq 1 80); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.5; done
kill -9 "$APP_PID" 2>/dev/null   # force-quit if it ignored SIGTERM
sleep 0.5

# Crash recovery: a prior interrupted run may have left the app renamed to
# BAK. If the app is missing but a backup exists, restore it before touching
# anything — never rm the backup while the live app is absent.
if [ ! -d "$APP_PATH" ] && [ -d "$BAK" ]; then
  echo "[updater] recovering leftover backup"
  mv "$BAK" "$APP_PATH"
fi
rm -rf "$STAGE"

echo "[updater] staging"
if ! ditto "$SRC_APP" "$STAGE" || [ ! -d "$STAGE/Contents/MacOS" ]; then
  echo "[updater] staged app missing/broken, aborting (old app untouched)"
  rm -rf "$STAGE"; hdiutil detach "$DMG_VOL" 2>/dev/null; open "$APP_PATH"; exit 1
fi
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null

echo "[updater] backing up current"
rm -rf "$BAK"
if [ -d "$APP_PATH" ]; then
  if ! mv "$APP_PATH" "$BAK"; then
    echo "[updater] backup failed, aborting (old app intact)"
    rm -rf "$STAGE"; hdiutil detach "$DMG_VOL" 2>/dev/null; open "$APP_PATH"; exit 1
  fi
fi

# APP_PATH is guaranteed gone now → mv won't nest inside a surviving dir.
echo "[updater] installing"
if mv "$STAGE" "$APP_PATH" && [ -d "$APP_PATH/Contents/MacOS" ]; then
  echo "[updater] ok"
  rm -rf "$BAK"
else
  echo "[updater] install failed, restoring backup"
  rm -rf "$APP_PATH"
  [ -d "$BAK" ] && mv "$BAK" "$APP_PATH"
fi
hdiutil detach "$DMG_VOL" 2>/dev/null
echo "[updater] relaunching"
open "$APP_PATH"
"""


def _q(p) -> str:
    """Single-quote a path for bash."""
    s = str(p)
    return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- orchestrate

def perform_update() -> Iterator[dict]:
    """Generator yielding progress events; the last real step spawns the
    detached swapper and asks the app to quit. Caller streams these as SSE.

    The download URL is resolved SERVER-SIDE from the current GitHub release —
    never taken from the caller — so a stray local POST can't point the updater
    at attacker-hosted content."""
    bundle = _app_bundle_path()
    if bundle is None:
        yield {"stage": "error", "error": "开发模式不支持自更新(仅打包版可用)"}
        return
    info = check_for_update()
    if not info.get("ok"):
        yield {"stage": "error", "error": info.get("error") or "检查更新失败"}
        return
    if not info.get("is_newer"):
        yield {"stage": "error", "error": "已是最新版,无需更新"}
        return
    dmg_url = info.get("dmg_url")
    if not dmg_url or not is_trusted_dmg_url(dmg_url):
        yield {"stage": "error", "error": "找不到可信的安装包下载地址"}
        return

    workdir = Path(tempfile.mkdtemp(prefix="ask-update-"))
    dmg = workdir / "Ask-update.dmg"
    try:
        yield {"stage": "downloading", "pct": 0}
        last = [-1]

        def _p(got: int, total: int):
            pct = int(got * 100 / total) if total else 0
            if pct != last[0]:
                last[0] = pct
        download_dmg(dmg_url, dmg, on_progress=_p)
        yield {"stage": "downloading", "pct": 100}
    except Exception as e:
        yield {"stage": "error", "error": f"下载失败:{e}"}
        return

    # Mount
    import subprocess
    try:
        yield {"stage": "mounting"}
        out = subprocess.run(
            ["/usr/bin/hdiutil", "attach", str(dmg), "-nobrowse", "-noverify", "-plist"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            yield {"stage": "error", "error": f"挂载失败:{out.stderr[:200]}"}
            return
        vol = _first_mountpoint(out.stdout)
        if not vol:
            yield {"stage": "error", "error": "找不到挂载卷"}
            return
        src_app = next(iter(Path(vol).glob("*.app")), None)
        if src_app is None:
            yield {"stage": "error", "error": "安装包里没有 .app"}
            subprocess.run(["/usr/bin/hdiutil", "detach", vol], capture_output=True)
            return
    except Exception as e:
        yield {"stage": "error", "error": f"挂载出错:{e}"}
        return

    # Spawn detached swapper + ask app to quit.
    try:
        yield {"stage": "installing"}
        log_path = settings.LOG_DIR / "updater.log"
        script = build_swap_script(os.getpid(), bundle, src_app, Path(vol), log_path)
        sh = workdir / "swap.sh"
        sh.write_text(script)
        sh.chmod(0o755)
        subprocess.Popen(
            ["/bin/bash", str(sh)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        yield {"stage": "relaunching"}
    except Exception as e:
        yield {"stage": "error", "error": f"启动更新脚本失败:{e}"}
        return


def _first_mountpoint(plist_stdout: str) -> Optional[str]:
    """Pull the mount-point out of `hdiutil attach -plist` output."""
    import plistlib
    try:
        data = plistlib.loads(plist_stdout.encode("utf-8"))
    except Exception:
        # Fallback: scrape a /Volumes/... path.
        m = re.search(r"(/Volumes/[^\n<]+)", plist_stdout)
        return m.group(1).strip() if m else None
    for ent in data.get("system-entities", []):
        mp = ent.get("mount-point")
        if mp:
            return mp
    return None
