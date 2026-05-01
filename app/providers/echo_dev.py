"""Local-only echo adapter for self-acceptance / smoke tests.

Registered only when MLC_DEV_ECHO=1. NOT a production provider — it just
streams back a deterministic answer so the SSE pipeline can be exercised
without spending API credits.
"""
from __future__ import annotations
import asyncio
import os
from typing import AsyncIterator, Optional

from .base import HealthResult, Message, ProviderAdapter, StreamChunk


class EchoDevAdapter(ProviderAdapter):
    kind = "echo_dev"

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        topic = (last_user.content if last_user else "").strip() or "(空)"
        intro = f"[{model_id}] 你好,我是 echo 模型。你刚才说:\n\n> {topic}\n\n这是用来本地验证流式管线的占位回复。"
        for ch in intro:
            if cancel_event and cancel_event.is_set():
                yield StreamChunk(done=True)
                return
            yield StreamChunk(delta=ch)
            await asyncio.sleep(0.005)
        yield StreamChunk(done=True)

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        return HealthResult(True, "echo dev: 永远在线")


def maybe_register():
    if os.environ.get("MLC_DEV_ECHO") == "1":
        from . import registry

        registry.REGISTRY["echo_dev"] = EchoDevAdapter
