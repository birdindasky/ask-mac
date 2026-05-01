"""Wrap dist/Ask.app into a drag-to-Applications .dmg.

Run AFTER `python setup.py py2app` has produced the .app. Generates a
disk image with the .app on the left, a symlink to /Applications on the
right, and a transparent grid background — the canonical Mac install
flow even though we're not signing anything.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import dmgbuild

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_NAME = "Ask"
APP_PATH = DIST / f"{APP_NAME}.app"
DMG_PATH = DIST / f"{APP_NAME}-0.2.0.dmg"


def main() -> None:
    if not APP_PATH.exists():
        print(f"error: {APP_PATH} not found — run `make build` first", file=sys.stderr)
        raise SystemExit(2)

    if DMG_PATH.exists():
        DMG_PATH.unlink()

    settings = {
        "filename": str(DMG_PATH),
        "volume_name": f"{APP_NAME} 0.2.0",
        "format": "UDZO",
        "size": None,  # auto-fit
        "files": [str(APP_PATH)],
        "symlinks": {"Applications": "/Applications"},
        "icon_locations": {
            f"{APP_NAME}.app": (140, 200),
            "Applications": (440, 200),
        },
        "background": "builtin-arrow",
        "window_rect": ((200, 120), (600, 400)),
        "default_view": "icon-view",
        "show_icon_preview": True,
        "icon_size": 96,
        "show_status_bar": False,
        "show_tab_view": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "show_sidebar": False,
    }
    dmgbuild.build_dmg(
        filename=settings["filename"],
        volume_name=settings["volume_name"],
        settings=settings,
    )
    print(f"wrote {DMG_PATH}")


if __name__ == "__main__":
    main()
