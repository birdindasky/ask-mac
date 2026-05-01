"""Brave Search — independent index, generous free tier."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://api.search.brave.com/res/v1/web/search"


class BraveBackend(SearchBackend):
    name = "brave"
    label = "Brave Search"
    signup_url = "https://api.search.brave.com"
    hint = "Brave 独立索引,免费 2000 次/月。Web 搜索结果,无 AI 摘要。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=20.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="Brave key 未配置", provider=self.name)
        params = {"q": query[:400], "count": max(1, min(int(max_results), 10))}
        headers = {
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(URL, headers=headers, params=params)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"Brave HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            web = (data.get("web") or {}).get("results") or []
            results = []
            for x in web:
                results.append({
                    "title": x.get("title") or "(无标题)",
                    "url": x.get("url"),
                    "content": (x.get("description") or x.get("snippet") or "").strip(),
                })
            return SearchResult(ok=True, query=query, provider=self.name, results=results, elapsed_ms=elapsed)
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"Brave 网络错误: {e}", provider=self.name)
