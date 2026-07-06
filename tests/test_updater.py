"""Self-updater tests: version compare, release parsing, and — most
importantly — the in-place swap script's success + failure(restore) paths,
exercised for real with stubbed `open`/`hdiutil` so nothing actually
launches or mounts."""
from __future__ import annotations
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from app import updater


# ---------------------------------------------------------------- versions

def test_parse_and_is_newer():
    assert updater.parse_version("v0.5.0") == (0, 5, 0)
    assert updater.parse_version("Ask 0.4") == (0, 4)
    assert updater.is_newer("0.5.0", "0.4.0")
    assert not updater.is_newer("0.4.0", "0.4.0")
    assert not updater.is_newer("0.4.0", "0.5.0")
    # semver, not string compare
    assert updater.is_newer("0.10.0", "0.9.0")
    assert updater.is_newer("1.0.0", "0.99.9")
    # tolerant of junk
    assert not updater.is_newer("garbage", "0.1.0")


def test_bash_quoting():
    assert updater._q("/Applications/Ask.app") == "'/Applications/Ask.app'"
    # single quote inside a path is escaped, not broken out of
    q = updater._q("/tmp/it's here/Ask.app")
    assert q.startswith("'") and q.endswith("'")
    # round-trips through bash as the literal string
    out = subprocess.run(["/bin/bash", "-c", f"printf %s {q}"], capture_output=True, text=True)
    assert out.stdout == "/tmp/it's here/Ask.app"


# ---------------------------------------------------------------- mountpoint

def test_first_mountpoint_plist():
    plist = """<?xml version="1.0"?><!DOCTYPE plist><plist version="1.0"><dict>
    <key>system-entities</key><array>
      <dict><key>content-hint</key><string>x</string></dict>
      <dict><key>mount-point</key><string>/Volumes/Ask 0.5.0</string></dict>
    </array></dict></plist>"""
    assert updater._first_mountpoint(plist) == "/Volumes/Ask 0.5.0"


def test_first_mountpoint_scrape_fallback():
    assert updater._first_mountpoint("junk /Volumes/Ask 0.5.0\nmore") == "/Volumes/Ask 0.5.0"


# ---------------------------------------------------------------- check

def test_check_for_update_parses_release(monkeypatch):
    import io, json

    rel = {
        "tag_name": "v0.9.9", "name": "Ask v0.9.9", "body": "# notes\n- thing",
        "published_at": "2026-07-06T00:00:00Z", "html_url": "https://h/r",
        "assets": [
            {"name": "notes.txt", "browser_download_url": "https://x/n.txt"},
            {"name": "Ask-0.9.9.dmg", "browser_download_url": "https://x/Ask-0.9.9.dmg"},
        ],
    }

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(json.dumps(rel).encode()))
    monkeypatch.setattr(updater.settings, "APP_VERSION", "0.4.0")

    r = updater.check_for_update()
    assert r["ok"] and r["latest"] == "0.9.9" and r["is_newer"]
    assert r["dmg_url"] == "https://x/Ask-0.9.9.dmg"
    assert "thing" in r["notes"]


def test_check_network_error_is_soft(monkeypatch):
    def _boom(*a, **k): raise OSError("no net")
    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    r = updater.check_for_update()
    assert r["ok"] is False and "失败" in r["error"]


def test_app_bundle_none_in_dev(monkeypatch):
    monkeypatch.setattr(updater.settings, "_is_packaged", lambda: False)
    assert updater._app_bundle_path() is None


# ---------------------------------------------------------------- swap script

def _stub_bin(tmp: Path) -> Path:
    """A PATH dir with harmless `open` and `hdiutil` stubs so the swap script
    can run to completion without launching anything or touching disk images."""
    b = tmp / "stubbin"
    b.mkdir()
    for name in ("open", "hdiutil"):
        f = b / name
        f.write_text("#!/bin/bash\nexit 0\n")
        f.chmod(0o755)
    return b


def _run_swap(script: str, stubbin: Path):
    env = dict(os.environ, PATH=f"{stubbin}:{os.environ['PATH']}")
    sh = stubbin.parent / "swap.sh"
    sh.write_text(script)
    sh.chmod(0o755)
    return subprocess.run(["/bin/bash", str(sh)], capture_output=True, text=True,
                          env=env, timeout=30)


def _mk_bundle(path: Path, mark: str):
    """A structurally-valid fake .app (has Contents/MacOS + a marker)."""
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "MARK").write_text(mark)


def test_swap_success_replaces_bundle(tmp_path):
    apps = tmp_path / "Applications"; apps.mkdir()
    old = apps / "Ask.app"; _mk_bundle(old, "old")
    vol = tmp_path / "Volumes"; vol.mkdir()
    src = vol / "Ask.app"; _mk_bundle(src, "new")

    # pid 999999 almost certainly doesn't exist → wait loop exits immediately
    script = updater.build_swap_script(999999, old, src, vol, tmp_path / "u.log")
    res = _run_swap(script, _stub_bin(tmp_path))
    assert res.returncode == 0
    assert (old / "MARK").read_text() == "new", "bundle should now be the new app"
    assert not (apps / ".Ask.app.bak").exists(), "backup cleaned on success"
    assert not (apps / ".Ask.app.new").exists(), "staging cleaned"


def test_swap_failure_restores_backup(tmp_path):
    apps = tmp_path / "Applications"; apps.mkdir()
    old = apps / "Ask.app"; _mk_bundle(old, "old")
    vol = tmp_path / "Volumes"; vol.mkdir()
    missing_src = vol / "Ask.app"   # does NOT exist → ditto fails

    script = updater.build_swap_script(999999, old, missing_src, vol, tmp_path / "u.log")
    _run_swap(script, _stub_bin(tmp_path))
    assert (old / "MARK").read_text() == "old", "original app preserved on failure"


def test_swap_aborts_on_structurally_broken_new_app(tmp_path):
    # src exists but is NOT a valid bundle (no Contents/MacOS) → must abort
    # without touching the good old app.
    apps = tmp_path / "Applications"; apps.mkdir()
    old = apps / "Ask.app"; _mk_bundle(old, "old")
    vol = tmp_path / "Volumes"; vol.mkdir()
    bad = vol / "Ask.app"; bad.mkdir(); (bad / "junk").write_text("x")

    script = updater.build_swap_script(999999, old, bad, vol, tmp_path / "u.log")
    res = _run_swap(script, _stub_bin(tmp_path))
    assert res.returncode == 1
    assert (old / "MARK").read_text() == "old", "old app must survive a broken update"


def test_swap_recovers_leftover_backup(tmp_path):
    # Simulate a prior crashed run: app missing, but a good backup sits at .bak.
    apps = tmp_path / "Applications"; apps.mkdir()
    old = apps / "Ask.app"          # intentionally absent
    bak = apps / ".Ask.app.bak"; _mk_bundle(bak, "recovered")
    vol = tmp_path / "Volumes"; vol.mkdir()
    src = vol / "Ask.app"; _mk_bundle(src, "new")

    script = updater.build_swap_script(999999, old, src, vol, tmp_path / "u.log")
    _run_swap(script, _stub_bin(tmp_path))
    # Either way the app path must end up present (recovered then updated).
    assert old.exists(), "app must be restored/installed, never left missing"


def test_trusted_dmg_url():
    ok = "https://github.com/birdindasky/ask-mac/releases/download/v0.5.0/Ask-0.5.0.dmg"
    assert updater.is_trusted_dmg_url(ok)
    assert not updater.is_trusted_dmg_url("http://github.com/birdindasky/ask-mac/releases/x.dmg")  # not https
    assert not updater.is_trusted_dmg_url("https://evil.com/birdindasky/ask-mac/releases/x.dmg")   # wrong host
    assert not updater.is_trusted_dmg_url("https://github.com/someone/else/releases/x.dmg")        # wrong repo
    assert not updater.is_trusted_dmg_url("https://github.com.evil.com/birdindasky/ask-mac/releases/x.dmg")
    assert not updater.is_trusted_dmg_url("")


def test_download_rejects_truncated(monkeypatch, tmp_path):
    import io

    class _Resp(io.BytesIO):
        headers = {"Content-Length": "1000"}   # claims 1000 but body is short
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(b"only-a-few-bytes"))
    with pytest.raises(IOError):
        updater.download_dmg("https://x/y.dmg", tmp_path / "d.dmg")


def test_swap_script_has_safety_rails():
    s = updater.build_swap_script(123, Path("/Applications/Ask.app"),
                                  Path("/Volumes/Ask/Ask.app"), Path("/Volumes/Ask"),
                                  Path("/tmp/u.log"))
    assert "kill -9" in s                       # force-quit fallback
    assert "recovering leftover backup" in s    # crash recovery
    assert "Contents/MacOS" in s                # verifies the swapped bundle
    assert "restoring backup" in s              # restores on install failure
    assert "com.apple.quarantine" in s          # strips quarantine
    assert "open " in s                         # relaunches
