"""Provider CRUD + health check + template listing."""
from __future__ import annotations
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config_store
from ..providers.registry import REGISTRY, make_adapter
from ..providers.templates import CATEGORY_LABELS, TEMPLATES, get_template
from ..security import (
    delete_provider_key,
    get_provider_key,
    has_provider_key,
    set_provider_key,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str
    template_key: str | None = None
    kind: str
    enabled: bool = True
    models: list[str] = []
    config: dict[str, Any] = {}


class ProviderUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    models: list[str] | None = None
    config: dict[str, Any] | None = None


def _redact(p: dict) -> dict:
    """Public view of a provider — keys live in Keychain, never echoed back."""
    cfg = dict(p.get("config") or {})
    cfg.pop("api_key", None)  # defense-in-depth: should never be present
    return {**p, "config": cfg, "configured": has_provider_key(p["id"])}


@router.get("/templates")
async def list_templates():
    return {
        "templates": TEMPLATES,
        "category_labels": CATEGORY_LABELS,
        "kinds": list(REGISTRY.keys()),
    }


@router.get("")
async def list_providers():
    cfg = config_store.load()
    return {"providers": [_redact(p) for p in cfg.get("providers", [])]}


@router.post("")
async def create_provider(body: ProviderIn):
    if body.kind not in REGISTRY:
        raise HTTPException(400, f"unknown kind: {body.kind}")

    pid = uuid.uuid4().hex
    cfg_payload = dict(body.config or {})
    api_key = cfg_payload.pop("api_key", None)  # never persist into JSON

    instance = {
        "id": pid,
        "name": body.name.strip() or "未命名",
        "template_key": body.template_key,
        "kind": body.kind,
        "enabled": body.enabled,
        "models": [m for m in (body.models or []) if m.strip()],
        "config": cfg_payload,
    }

    def _mut(cfg):
        cfg["providers"].append(instance)
        return cfg

    config_store.update(_mut)
    if api_key:
        set_provider_key(pid, api_key)
    return {"provider": _redact(instance)}


@router.put("/{pid}")
async def update_provider(pid: str, body: ProviderUpdate):
    pending_key: str | None = None

    def _mut(cfg):
        nonlocal pending_key
        for p in cfg["providers"]:
            if p["id"] == pid:
                if body.name is not None:
                    p["name"] = body.name.strip() or p["name"]
                if body.enabled is not None:
                    p["enabled"] = body.enabled
                if body.models is not None:
                    p["models"] = [m for m in body.models if m.strip()]
                if body.config is not None:
                    new_cfg = dict(p.get("config") or {})
                    for k, v in body.config.items():
                        if k == "api_key":
                            # Blank submission preserves existing keychain entry.
                            if v:
                                pending_key = v
                            continue
                        new_cfg[k] = v
                    p["config"] = new_cfg
                return cfg
        raise HTTPException(404, "provider not found")

    cfg = config_store.update(_mut)
    if pending_key:
        set_provider_key(pid, pending_key)
    inst = next((p for p in cfg["providers"] if p["id"] == pid), None)
    return {"provider": _redact(inst) if inst else None}


@router.delete("/{pid}")
async def delete_provider(pid: str):
    found = {"v": False}

    def _mut(cfg):
        before = len(cfg["providers"])
        cfg["providers"] = [p for p in cfg["providers"] if p["id"] != pid]
        found["v"] = len(cfg["providers"]) != before
        return cfg

    config_store.update(_mut)
    if not found["v"]:
        raise HTTPException(404, "provider not found")
    delete_provider_key(pid)
    return {"ok": True}


class TestBody(BaseModel):
    template_key: str | None = None
    kind: str | None = None
    config: dict[str, Any] | None = None
    model_id: str | None = None
    pid: str | None = None  # if testing an existing one, send id only


@router.post("/test")
async def test_provider(body: TestBody):
    instance: dict | None = None
    if body.pid:
        cfg = config_store.load()
        instance = next((p for p in cfg["providers"] if p["id"] == body.pid), None)
        if not instance:
            raise HTTPException(404, "provider not found")
        merged = dict(instance.get("config") or {})
        # Stored key (if any) is loaded by the adapter automatically; here we
        # only inject a candidate from the form so users can test before save.
        if body.config:
            for k, v in body.config.items():
                if k == "api_key" and (v == "" or v is None):
                    continue
                merged[k] = v
        instance = {**instance, "config": merged}
    else:
        kind = body.kind
        cfg_payload = dict(body.config or {})
        tmpl = get_template(body.template_key) if body.template_key else None
        if tmpl:
            kind = kind or tmpl["kind"]
            base_cfg = dict(tmpl.get("config") or {})
            base_cfg.update(cfg_payload)
            cfg_payload = base_cfg
        if not kind or kind not in REGISTRY:
            raise HTTPException(400, f"unknown kind: {kind}")
        instance = {"id": "tmp", "name": "test", "kind": kind, "config": cfg_payload}

    adapter = make_adapter(instance)
    res = await adapter.health_check(body.model_id)
    return {"ok": res.ok, "message": res.message, "detail": res.detail}
