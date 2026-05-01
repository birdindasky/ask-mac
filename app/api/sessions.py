"""Session and message REST endpoints."""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionIn(BaseModel):
    title: str | None = None
    mode: str = "chat"
    meta: dict[str, Any] = {}


class SessionUpdate(BaseModel):
    title: str | None = None
    mode: str | None = None
    meta: dict[str, Any] | None = None


@router.get("")
async def list_sessions(q: str | None = None):
    return {"sessions": db.list_sessions(q)}


@router.get("/search/messages")
async def search_messages(q: str, limit: int = 30):
    """Full-text search across every message in every session (FTS5 trigram)."""
    return {"results": db.search_messages(q, limit=limit), "query": q}


@router.post("")
async def create_session(body: SessionIn):
    title = (body.title or "").strip() or "新会话"
    sess = db.create_session(title, body.mode, body.meta)
    return {"session": sess}


@router.get("/{sid}")
async def get_session(sid: str):
    sess = db.get_session(sid)
    if not sess:
        raise HTTPException(404, "session not found")
    return {"session": sess, "messages": db.list_messages(sid)}


@router.put("/{sid}")
async def update_session(sid: str, body: SessionUpdate):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    sess = db.update_session(sid, title=body.title, mode=body.mode, meta=body.meta)
    return {"session": sess}


@router.delete("/{sid}")
async def delete_session(sid: str):
    if not db.delete_session(sid):
        raise HTTPException(404, "session not found")
    return {"ok": True}
