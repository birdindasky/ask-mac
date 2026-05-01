"""Jina AI Search — LLM-friendly, returns clean markdown content."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://s.jina.ai/"


class JinaBackend(SearchBackend):
    name = "jina"
    label = "Jina Search"
    signup_url = "https://jina.ai/reader"
    hint = "Jina Search,自带 reader 转 markdown,LLM 友好。免费 1M tokens/月。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=30.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="Jina key 未配置", provider=self.name)
        params = {"q": query[:400]}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "X-Respond-With": "no-content" if depth != "advanced" else "",
            "X-Return-Format": "markdown",
        }
        # Drop empty headers (httpx complains)
        headers = {k: v for k, v in headers.items() if v}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(URL, headers=headers, params=params)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"Jina HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            entries = data.get("data") or []
            results = []
            for x in entries[: max(1, min(int(max_results), 10))]:
                results.append({
                    "title": x.get("title") or "(无标题)",
                    "url": x.get("url"),
                    "content": (x.get("content") or x.get("description") or "").strip(),
                })
            return SearchResult(ok=True, query=query, provider=self.name, results=results, elapsed_ms=elapsed)
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"Jina 网络错误: {e}", provider=self.name)
