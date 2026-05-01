"""Google Gemini API adapter (Generative Language API, streamGenerateContent)."""
from __future__ import annotations
import asyncio
import json as _json
from typing import AsyncIterator, Optional

import httpx

from .base import HealthResult, Message, ProviderAdapter, StreamChunk


class GeminiAPIAdapter(ProviderAdapter):
    kind = "gemini_api"

    @property
    def api_key(self) -> str:
        return (self.config.get("api_key") or "").strip()

    @property
    def base_url(self) -> str:
        return (
            self.config.get("base_url") or "https://generativelanguage.googleapis.com"
        ).rstrip("/")

    def _build_contents(self, messages: list[Message], system: Optional[str]):
        sys_text, rest = self._split_system(messages, system)
        contents = []
        for m in rest:
            if not m.content:
                continue
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        sys_block = None
        if sys_text:
            sys_block = {"parts": [{"text": sys_text}]}
        return contents, sys_block

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self.api_key:
            yield StreamChunk(error="Gemini API key 未配置", done=True)
            return

        contents, sys_block = self._build_contents(messages, system)
        body: dict = {"contents": contents}
        if sys_block:
            body["systemInstruction"] = sys_block

        url = (
            f"{self.base_url}/v1beta/models/{model_id}:streamGenerateContent"
            f"?alt=sse&key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        yield StreamChunk(
                            error=f"Gemini HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')[:500]}",
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
                            if not data:
                                continue
                            try:
                                payload = _json.loads(data)
                            except _json.JSONDecodeError:
                                continue
                            for cand in payload.get("candidates", []) or []:
                                content = cand.get("content") or {}
                                for part in content.get("parts", []) or []:
                                    text = part.get("text")
                                    if text:
                                        yield StreamChunk(delta=text)
                                if cand.get("finishReason"):
                                    yield StreamChunk(done=True)
                                    return
                    yield StreamChunk(done=True)
        except httpx.HTTPError as e:
            yield StreamChunk(error=f"Gemini 网络错误: {e}", done=True)

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        if not self.api_key:
            return HealthResult(False, "缺少 API key")
        url = (
            f"{self.base_url}/v1beta/models/{model_id or 'gemini-1.5-flash'}:generateContent"
            f"?key={self.api_key}"
        )
        body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(url, json=body)
            if r.status_code < 300:
                return HealthResult(True, "连接 OK")
            if r.status_code == 400:
                return HealthResult(False, "请求被拒(400),通常是 API key 或模型 id 不对", r.text[:300])
            if r.status_code == 403:
                return HealthResult(False, "API key 无权限(403)", r.text[:300])
            if r.status_code == 404:
                return HealthResult(False, "模型不存在(404)", r.text[:300])
            return HealthResult(False, f"HTTP {r.status_code}", r.text[:300])
        except httpx.HTTPError as e:
            return HealthResult(False, "网络错误", str(e))
