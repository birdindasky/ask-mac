"""Tavily — RAG-optimized AI search with built-in summarization."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://api.tavily.com/search"


class TavilyBackend(SearchBackend):
    name = "tavily"
    label = "Tavily"
    signup_url = "https://tavily.com"
    hint = "RAG 专用,免费 1000 次/月,自带摘要。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=25.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="Tavily key 未配置", provider=self.name)
        body = {
            "query": query[:400],
            "max_results": max(1, min(int(max_results), 10)),
            "search_depth": "advanced" if depth == "advanced" else "basic",
            "include_answer": True,
            "include_raw_content": False,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(URL, headers=headers, json=body)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"Tavily HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            return SearchResult(
                ok=True, query=query, provider=self.name,
                answer=data.get("answer"),
                results=[{"title": x.get("title"), "url": x.get("url"), "content": x.get("content")}
                         for x in (data.get("results") or [])],
                elapsed_ms=elapsed,
            )
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"Tavily 网络错误: {e}", provider=self.name)
