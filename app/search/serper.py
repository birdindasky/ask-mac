"""Serper — Google search via API, fast and cheap."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://google.serper.dev/search"


class SerperBackend(SearchBackend):
    name = "serper"
    label = "Serper.dev"
    signup_url = "https://serper.dev"
    hint = "Google 搜索结果(SerpAPI 替代),极快,免费 2500 次。带 answerBox。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=15.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="Serper key 未配置", provider=self.name)
        body = {
            "q": query[:400],
            "num": max(1, min(int(max_results), 10)),
        }
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(URL, headers=headers, json=body)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"Serper HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            answer = None
            ab = data.get("answerBox") or {}
            if ab:
                answer = ab.get("answer") or ab.get("snippet") or ab.get("title")
            results = []
            for x in (data.get("organic") or []):
                results.append({
                    "title": x.get("title") or "(无标题)",
                    "url": x.get("link"),
                    "content": (x.get("snippet") or "").strip(),
                })
            return SearchResult(ok=True, query=query, provider=self.name, answer=answer,
                                results=results, elapsed_ms=elapsed)
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"Serper 网络错误: {e}", provider=self.name)
