"""Dock-tile badge dot for in-progress streams.

The launcher and the FastAPI server live in the same Python process, so
we can import AppKit directly here and post the badge change onto the
main thread via NSOperationQueue. Wrapped in try/except so dev-mode
(where AppKit may not be importable) just no-ops.
"""
from __future__ import annotations
import logging

log = logging.getLogger("ask.dock_badge")

_AVAILABLE = False

try:
    from AppKit import NSApp  # type: ignore
    from Foundation import NSOperationQueue  # type: ignore

    _AVAILABLE = True
except Exception:  # pragma: no cover - dev mode without AppKit
    log.debug("AppKit not importable; dock badge disabled", exc_info=True)


def set_badge(busy: bool) -> bool:
    """Set or clear the Dock badge dot. Returns True if dispatched."""
    if not _AVAILABLE:
        return False
    label = "●" if busy else ""

    def _apply():
        try:
            tile = NSApp.dockTile()
            tile.setBadgeLabel_(label)
            tile.display()
        except Exception:
            log.exception("dock badge set failed")

    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(_apply)
        return True
    except Exception:
        log.exception("dock badge dispatch failed")
        return False
