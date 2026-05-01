"""Data lifecycle endpoints — export, import, wipe.

Three concerns kept separate so the UI can offer them independently:
  - config: providers + ui prefs + web-search settings (NEVER includes keys)
  - sessions: SQLite session+message rows
  - wipe: drops all sessions/messages (config preserved)

Import replaces by default — caller can opt into merge for sessions only
(config replace is safer since it's a single document).
"""
from __future__ import annotations
import json as _json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config_store, db, settings
from ..utils import autostart

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ImportConfigBody(BaseModel):
    config: dict[str, Any]


class ImportSessionsBody(BaseModel):
    sessions: list[dict[str, Any]]
    merge: bool = False  # False = replace all, True = additive


class AutostartBody(BaseModel):
    enabled: bool


@router.get("/info")
async def info():
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "bundle_id": settings.BUNDLE_ID,
        "data_dir": str(settings.DATA_DIR),
        "log_dir": str(settings.LOG_DIR),
        "packaged": settings._is_packaged(),
    }


@router.get("/export/config")
async def export_config():
    """Return the full config doc (no keys — those live in Keychain)."""
    return {
        "exported_at": time.time(),
        "version": settings.APP_VERSION,
        "config": config_store.load(),
    }


@router.post("/import/config")
async def import_config(body: ImportConfigBody):
    """Replace the entire config doc. Migration normalizes whatever shape you send."""
    if not isinstance(body.config, dict):
        raise HTTPException(400, "config must be an object")
    cfg = config_store.save(body.config)
    return {"ok": True, "config": cfg}


@router.get("/export/sessions")
async def export_sessions():
    sessions = db.list_sessions()
    bundle = []
    for s in sessions:
        msgs = db.list_messages(s["id"])
        bundle.append({"session": s, "messages": msgs})
    return {
        "exported_at": time.time(),
        "version": settings.APP_VERSION,
        "sessions": bundle,
    }


@router.post("/import/sessions")
async def import_sessions(body: ImportSessionsBody):
    """Replace-or-merge session import. Each entry is {session, messages}."""
    if not body.merge:
        with db.tx() as c:
            c.execute("DELETE FROM messages")
            c.execute("DELETE FROM sessions")

    inserted = 0
    for entry in body.sessions:
        sess = entry.get("session") or {}
        msgs = entry.get("messages") or []
        if not sess.get("id") or not sess.get("title"):
            continue
        with db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO sessions (id, title, mode, created_at, updated_at, meta_json) VALUES (?,?,?,?,?,?)",
                (
                    sess["id"],
                    sess["title"],
                    sess.get("mode") or "chat",
                    float(sess.get("created_at") or time.time()),
                    float(sess.get("updated_at") or time.time()),
                    _json.dumps(sess.get("meta") or {}),
                ),
            )
            for m in msgs:
                if not m.get("id"):
                    continue
                c.execute(
                    "INSERT OR REPLACE INTO messages (id, session_id, role, speaker, provider_id, model_id, content, created_at, meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        m["id"],
                        sess["id"],
                        m.get("role") or "assistant",
                        m.get("speaker"),
                        m.get("provider_id"),
                        m.get("model_id"),
                        m.get("content") or "",
                        float(m.get("created_at") or time.time()),
                        _json.dumps(m.get("meta") or {}),
                    ),
                )
        inserted += 1
    return {"ok": True, "imported": inserted, "merge": body.merge}


@router.post("/wipe/sessions")
async def wipe_sessions():
    """Drop every session and message. Config is preserved."""
    with db.tx() as c:
        c.execute("DELETE FROM messages")
        cur = c.execute("DELETE FROM sessions")
        deleted = cur.rowcount
    return {"ok": True, "deleted": deleted}


@router.get("/autostart")
async def get_autostart():
    """Whether Ask is registered to launch at user login."""
    return {"enabled": autostart.is_enabled()}


@router.put("/autostart")
async def set_autostart(body: AutostartBody):
    """Enable or disable launch-at-login by writing/removing a LaunchAgent
    plist at ~/Library/LaunchAgents/<bundle-id>.plist."""
    try:
        if body.enabled:
            autostart.enable_login_item()
        else:
            autostart.disable_login_item()
    except OSError as e:
        raise HTTPException(500, f"autostart toggle failed: {e}")
    return {"ok": True, "enabled": autostart.is_enabled()}
