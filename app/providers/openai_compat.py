"""Generic OpenAI-compatible /v1/chat/completions adapter.

Used for: OpenAI itself, DeepSeek, GLM, Qwen, MiniMax, Moonshot, Ark, Yi,
SiliconFlow, OpenRouter, Together, Groq, OneAPI, custom self-hosted.
"""
from __future__ import annotations
import asyncio
import json as _json
from typing import AsyncIterator, Optional

import httpx

from .base import HealthResult, Message, ProviderAdapter, StreamChunk


class OpenAICompatAdapter(ProviderAdapter):
    kind = "openai_compat"

    @property
    def api_key(self) -> str:
        return (self.config.get("api_key") or "").strip()

    @property
    def base_url(self) -> str:
        url = (self.config.get("base_url") or "").rstrip("/")
        return url

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self.base_url:
            yield StreamChunk(error="base_url 未配置", done=True)
            return

        sys_text, rest = self._split_system(messages, system)
        api_messages: list[dict] = []
        if sys_text:
            api_messages.append({"role": "system", "content": sys_text})
        for m in rest:
            if not m.content:
                continue
            api_messages.append({"role": m.role, "content": m.content})

        body = {
            "model": model_id,
            "messages": api_messages,
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
                async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        yield StreamChunk(
                            error=f"HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')[:500]}",
                            done=True,
                        )
                        return
                    async for line in resp.aiter_lines():
                        if cancel_event and cancel_event.is_set():
                            yield StreamChunk(done=True)
                            return
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                if data == "[DONE]":
                                    yield StreamChunk(done=True)
                                    return
                                continue
                            try:
                                payload = _json.loads(data)
                            except _json.JSONDecodeError:
                                continue
                            choices = payload.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            text = delta.get("content")
                            if text:
                                yield StreamChunk(delta=text)
                            finish = choices[0].get("finish_reason")
                            if finish:
                                yield StreamChunk(done=True)
                                return
                    yield StreamChunk(done=True)
        except httpx.HTTPError as e:
            yield StreamChunk(error=f"网络错误: {e}", done=True)

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        if not self.base_url:
            return HealthResult(False, "缺少 base_url")
        if not self.api_key:
            return HealthResult(False, "缺少 API key")
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": model_id or "ping",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(url, headers=self._headers(), json=body)
            if r.status_code < 300:
                return HealthResult(True, "连接 OK")
            if r.status_code == 401:
                return HealthResult(False, "鉴权失败(401)", r.text[:300])
            if r.status_code == 404:
                return HealthResult(False, "404 — 路径或模型不对", r.text[:300])
            if r.status_code in (400, 422):
                # Some providers reject max_tokens=1 but the auth was accepted; treat as warning.
                txt = r.text or ""
                if "model" in txt.lower():
                    return HealthResult(False, "模型 id 可能不可用", txt[:300])
                return HealthResult(True, "鉴权通过(请求被拒,模型可用性请单独验证)", txt[:300])
            return HealthResult(False, f"HTTP {r.status_code}", r.text[:300])
        except httpx.HTTPError as e:
            return HealthResult(False, "网络错误", str(e))
