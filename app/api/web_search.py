"""Web search settings + multi-backend dispatch."""
from __future__ import annotations
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config_store
from ..search import descriptors, get as get_backend
from ..security import (
    delete_search_key,
    get_search_key,
    has_search_key,
    set_search_key,
)

router = APIRouter(prefix="/api/web-search", tags=["web-search"])


def _public_view(ws: dict) -> dict:
    descs = descriptors()
    out_providers = []
    for d in descs:
        out_providers.append({**d, "configured": has_search_key(d["name"])})
    return {
        "active": ws.get("active", "tavily"),
        "default_on": bool(ws.get("default_on", False)),
        "max_results": int(ws.get("max_results", 5)),
        "depth": ws.get("depth", "basic"),
        "providers": out_providers,
    }


class GlobalUpdate(BaseModel):
    active: str | None = None
    default_on: bool | None = None
    max_results: int | None = None
    depth: str | None = None


class KeyUpdate(BaseModel):
    api_key: str


@router.get("")
async def get_settings():
    cfg = config_store.load()
    return _public_view(cfg.get("web_search", {}))


@router.put("")
async def update_global(body: GlobalUpdate):
    def _mut(cfg):
        ws = cfg.setdefault("web_search", {})
        if body.active is not None:
            if get_backend(body.active) is None:
                raise HTTPException(400, f"unknown search backend: {body.active}")
            ws["active"] = body.active
        if body.default_on is not None:
            ws["default_on"] = bool(body.default_on)
        if body.max_results is not None:
            ws["max_results"] = max(1, min(int(body.max_results), 10))
        if body.depth is not None:
            ws["depth"] = "advanced" if body.depth == "advanced" else "basic"
        return cfg

    cfg = config_store.update(_mut)
    return _public_view(cfg.get("web_search", {}))


@router.put("/keys/{name}")
async def update_key(name: str, body: KeyUpdate):
    if get_backend(name) is None:
        raise HTTPException(404, f"unknown search backend: {name}")
    if body.api_key:
        # Blank value preserves existing keychain entry, matching the
        # provider edit form's "leave blank to keep" semantics.
        set_search_key(name, body.api_key)
    cfg = config_store.load()
    return _public_view(cfg.get("web_search", {}))


@router.delete("/keys/{name}")
async def clear_key(name: str):
    if get_backend(name) is None:
        raise HTTPException(404, f"unknown search backend: {name}")
    delete_search_key(name)
    cfg = config_store.load()
    return _public_view(cfg.get("web_search", {}))


class TestBody(BaseModel):
    name: str | None = None
    api_key: str | None = None


@router.post("/test")
async def test_backend(body: TestBody):
    cfg = config_store.load().get("web_search", {})
    name = body.name or cfg.get("active", "tavily")
    backend = get_backend(name)
    if backend is None:
        raise HTTPException(400, f"unknown search backend: {name}")
    api_key = (body.api_key or "").strip()
    if not api_key:
        api_key = get_search_key(name)
    if not api_key:
        raise HTTPException(400, f"{backend.label} 没有 key 也没传 api_key")
    res = await backend.health_check(api_key)
    return {
        "ok": res.ok,
        "provider": name,
        "message": "连通,可用" if res.ok else (res.error or "失败"),
        "elapsed_ms": res.elapsed_ms,
        "results_preview": [
            {"title": r.get("title"), "url": r.get("url")}
            for r in (res.results or [])[:3]
        ],
    }
