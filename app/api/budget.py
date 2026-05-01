"""Token budget endpoint — drives the UI progress bar."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..utils.token_budget import budget_summary, context_window_for, estimate_messages

router = APIRouter(prefix="/api", tags=["budget"])


@router.get("/sessions/{sid}/budget")
async def get_budget(sid: str, model_id: str | None = None):
    sess = db.get_session(sid)
    if not sess:
        raise HTTPException(404, "session not found")
    msgs = db.list_messages(sid)
    used = estimate_messages(msgs)
    # Caller passes the currently-selected model so we use its window.
    # Falls back to the latest assistant message's model if not specified.
    if not model_id:
        for m in reversed(msgs):
            if m.get("model_id"):
                model_id = m["model_id"]
                break
    max_tokens = context_window_for(model_id or "")
    summary = budget_summary(used, max_tokens)
    summary["model_id"] = model_id or ""
    return summary
