"""Exa (formerly Metaphor) — neural / semantic search."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://api.exa.ai/search"


class ExaBackend(SearchBackend):
    name = "exa"
    label = "Exa"
    signup_url = "https://exa.ai"
    hint = "神经搜索,适合找深度内容 / 论文 / 博客。免费额度 1000/月。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=25.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="Exa key 未配置", provider=self.name)
        body = {
            "query": query[:400],
            "numResults": max(1, min(int(max_results), 10)),
            "useAutoprompt": True,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 1500}},
        }
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(URL, headers=headers, json=body)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"Exa HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            results = []
            for x in (data.get("results") or []):
                results.append({
                    "title": x.get("title") or "(无标题)",
                    "url": x.get("url"),
                    "content": (x.get("text") or x.get("summary") or "").strip(),
                })
            return SearchResult(ok=True, query=query, provider=self.name, results=results, elapsed_ms=elapsed)
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"Exa 网络错误: {e}", provider=self.name)
