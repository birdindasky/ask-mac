"""博查 Bocha AI Search — 中文优化的 AI 搜索接口."""
from __future__ import annotations
import time

import httpx

from .base import SearchBackend, SearchResult

URL = "https://api.bochaai.com/v1/web-search"


class BochaBackend(SearchBackend):
    name = "bocha"
    label = "博查 Bocha"
    signup_url = "https://bochaai.com"
    hint = "国内 AI 搜索,中文场景优化,带 summary 字段。需要从博查官网申请 key。"

    async def search(self, query, *, api_key, max_results=5, depth="basic", timeout=25.0) -> SearchResult:
        if not api_key:
            return SearchResult(ok=False, query=query, error="博查 key 未配置", provider=self.name)
        body = {
            "query": query[:400],
            "freshness": "noLimit",
            "summary": True,
            "count": max(1, min(int(max_results), 10)),
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(URL, headers=headers, json=body)
            elapsed = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                return SearchResult(ok=False, query=query, elapsed_ms=elapsed,
                                    error=f"博查 HTTP {r.status_code}: {r.text[:200]}", provider=self.name)
            data = r.json()
            payload = data.get("data") or {}
            web = (payload.get("webPages") or {}).get("value") or []
            results = []
            for x in web:
                results.append({
                    "title": x.get("name") or x.get("title") or "(无标题)",
                    "url": x.get("url"),
                    "content": (x.get("summary") or x.get("snippet") or "").strip(),
                })
            return SearchResult(ok=True, query=query, provider=self.name, results=results, elapsed_ms=elapsed)
        except httpx.HTTPError as e:
            return SearchResult(ok=False, query=query, error=f"博查 网络错误: {e}", provider=self.name)
