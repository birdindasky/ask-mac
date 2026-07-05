"""Export-plan helper endpoints: project list + target-path validation.

The SSE pipeline endpoint itself lives in api/chat.py next to the other
streaming endpoints so it shares the one-stream-per-session guard.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..modes import export_plan

router = APIRouter(prefix="/api/export", tags=["export"])


class ValidateBody(BaseModel):
    path: str


@router.get("/projects")
async def get_projects():
    return {"projects": export_plan.list_projects()}


@router.post("/validate")
async def post_validate(body: ValidateBody):
    resolved, reason = export_plan.validate_project_path(body.path)
    if resolved is None:
        return {"ok": False, "reason": reason}
    return {"ok": True, "resolved": str(resolved)}
