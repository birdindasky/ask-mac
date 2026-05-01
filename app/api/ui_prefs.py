"""UI preferences endpoint."""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config_store

router = APIRouter(prefix="/api/ui-prefs", tags=["ui"])


class PrefsBody(BaseModel):
    theme: str | None = None  # 'dark' | 'light' | 'system'
    locale: str | None = None  # 'system' | 'zh' | 'en'
    last_mode: str | None = None
    last_session_id: str | None = None
    font_size: int | None = None  # base px, 13-22 — controls .md-body sizing
    welcome_done: bool | None = None


@router.get("")
async def get_prefs():
    return config_store.load().get("ui", {})


@router.put("")
async def update_prefs(body: PrefsBody):
    def _mut(cfg):
        ui = cfg.setdefault("ui", {})
        if body.theme is not None:
            ui["theme"] = body.theme
        if body.locale is not None:
            if body.locale in ("system", "zh", "en"):
                ui["locale"] = body.locale
        if body.last_mode is not None:
            ui["last_mode"] = body.last_mode
        if body.last_session_id is not None:
            ui["last_session_id"] = body.last_session_id
        if body.font_size is not None:
            ui["font_size"] = max(13, min(22, int(body.font_size)))
        if body.welcome_done is not None:
            ui["welcome_done"] = bool(body.welcome_done)
        return cfg

    cfg = config_store.update(_mut)
    return cfg.get("ui", {})
