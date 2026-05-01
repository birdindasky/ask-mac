"""Internal endpoints called by the frontend to drive native chrome.

Two side-effecting fire-and-forget hooks:
  - POST /api/internal/dock-badge {busy: bool}
        Toggles the Dock-tile badge dot. Frontend calls busy=true when a
        stream starts and busy=false when it ends.
  - POST /api/internal/notify {title, body}
        Posts a UNUserNotification. Frontend should only call this when
        the window is hidden so we don't double-notify the user.

Both endpoints succeed silently in dev mode where AppKit/UserNotifications
aren't wired up — the underlying utility modules degrade to no-op.
"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

from ..utils import dock_badge, notifier

router = APIRouter(prefix="/api/internal", tags=["internal"])


class DockBadgeBody(BaseModel):
    busy: bool


class NotifyBody(BaseModel):
    title: str
    body: str


@router.post("/dock-badge")
async def post_dock_badge(payload: DockBadgeBody):
    dispatched = dock_badge.set_badge(payload.busy)
    return {"ok": True, "dispatched": dispatched}


@router.post("/notify")
async def post_notify(payload: NotifyBody):
    delivered = notifier.notify(payload.title, payload.body)
    return {"ok": True, "delivered": delivered}
