"""Anthropic native API adapter."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Optional

import httpx

from .base import HealthResult, Message, ProviderAdapter, StreamChunk


class AnthropicAPIAdapter(ProviderAdapter):
    kind = "anthropic_api"

    @property
    def api_key(self) -> str:
        return (self.config.get("api_key") or "").strip()

    @property
    def base_url(self) -> str:
        return (self.config.get("base_url") or "https://api.anthropic.com").rstrip("/")

    @property
    def version(self) -> str:
        return self.config.get("anthropic_version") or "2023-06-01"

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self.api_key:
            yield StreamChunk(error="Anthropic API key 未配置", done=True)
            return

        sys_text, rest = self._split_system(messages, system)
        api_messages = [
            {"role": ("assistant" if m.role == "assistant" else "user"), "content": m.content}
            for m in rest
            if m.content
        ]
        body = {
            "model": model_id,
            "max_tokens": 4096,
            "stream": True,
            "messages": api_messages,
        }
        if sys_text:
            body["system"] = sys_text

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        yield StreamChunk(
                            error=f"Anthropic HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')[:500]}",
                            done=True,
                        )
                        return
                    event_name = ""
                    async for line in resp.aiter_lines():
                        if cancel_event and cancel_event.is_set():
                            yield StreamChunk(done=True)
                            return
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip()
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if not data:
                                continue
                            import json as _json

                            try:
                                payload = _json.loads(data)
                            except _json.JSONDecodeError:
                                continue
                            etype = payload.get("type") or event_name
                            if etype == "content_block_delta":
                                delta = payload.get("delta") or {}
                                text = delta.get("text") or ""
                                if text:
                                    yield StreamChunk(delta=text)
                            elif etype == "message_stop":
                                yield StreamChunk(done=True)
                                return
                            elif etype == "error":
                                err = payload.get("error", {}).get("message") or "unknown error"
                                yield StreamChunk(error=f"Anthropic error: {err}", done=True)
                                return
                    yield StreamChunk(done=True)
        except httpx.HTTPError as e:
            yield StreamChunk(error=f"Anthropic 网络错误: {e}", done=True)

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        if not self.api_key:
            return HealthResult(False, "缺少 API key")
        url = f"{self.base_url}/v1/messages"
        body = {
            "model": model_id or "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(url, headers=headers, json=body)
            if r.status_code < 300:
                return HealthResult(True, "连接 OK")
            if r.status_code == 401:
                return HealthResult(False, "API key 被拒绝(401)", r.text[:300])
            if r.status_code == 404:
                return HealthResult(False, "模型不存在(404)", r.text[:300])
            return HealthResult(False, f"HTTP {r.status_code}", r.text[:300])
        except httpx.HTTPError as e:
            return HealthResult(False, "网络错误", str(e))
