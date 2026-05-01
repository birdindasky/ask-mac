"""macOS user notifications via UNUserNotificationCenter.

This is the modern API (replaces NSUserNotification deprecated in 11.0).
On first send we request authorization; the OS only prompts once, after
which the user's choice is sticky.

Everything is wrapped in try/except + a `_NOTIFY_AVAILABLE` flag so this
module is safe to import in dev mode (where the .app's bundle identifier
isn't registered with the notification center) and in tests (where
PyObjC may or may not be importable). Failures are logged and swallowed —
notifications are advisory, not critical-path.
"""
from __future__ import annotations
import logging
import uuid

log = logging.getLogger("ask.notifier")

_NOTIFY_AVAILABLE = False
_AUTH_REQUESTED = False

try:
    from UserNotifications import (  # type: ignore
        UNUserNotificationCenter,
        UNMutableNotificationContent,
        UNNotificationRequest,
        UNAuthorizationOptionAlert,
        UNAuthorizationOptionSound,
        UNAuthorizationOptionBadge,
    )

    _NOTIFY_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only outside macOS / dev
    log.debug("UserNotifications import failed; notifier disabled", exc_info=True)


def request_permission() -> None:
    """Ask the user for notification permission. First call shows the
    system prompt; subsequent calls hit a cached answer.

    We fire-and-forget the completion handler — the result lands in the
    OS authorization state which the next `notify()` call queries
    implicitly.
    """
    global _AUTH_REQUESTED
    if not _NOTIFY_AVAILABLE or _AUTH_REQUESTED:
        return
    try:
        center = UNUserNotificationCenter.currentNotificationCenter()
        opts = (
            UNAuthorizationOptionAlert
            | UNAuthorizationOptionSound
            | UNAuthorizationOptionBadge
        )

        def _completion(granted, error):
            if error is not None:
                log.warning("notification permission error: %s", error)
            else:
                log.info("notification permission granted=%s", bool(granted))

        center.requestAuthorizationWithOptions_completionHandler_(opts, _completion)
        _AUTH_REQUESTED = True
    except Exception:
        log.exception("requestAuthorization failed")


def notify(title: str, body: str, identifier: str | None = None) -> bool:
    """Post a notification. Returns True if dispatched, False if the
    platform binding isn't available.

    Errors are logged + swallowed: notifications are best-effort and we
    never want a missing notification to break the chat flow.
    """
    if not _NOTIFY_AVAILABLE:
        return False
    request_permission()
    try:
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(str(title))
        content.setBody_(str(body))
        ident = identifier or str(uuid.uuid4())
        # trigger=None → deliver immediately.
        request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
            ident, content, None
        )
        center = UNUserNotificationCenter.currentNotificationCenter()

        def _completion(error):
            if error is not None:
                log.warning("notification add error: %s", error)

        center.addNotificationRequest_withCompletionHandler_(request, _completion)
        return True
    except Exception:
        log.exception("notify failed")
        return False
