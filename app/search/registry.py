"""Search backend registry + prompt formatter (backend-agnostic)."""
from __future__ import annotations
from typing import Optional

from .base import SearchBackend, SearchResult
from .bocha import BochaBackend
from .brave import BraveBackend
from .exa import ExaBackend
from .jina import JinaBackend
from .serper import SerperBackend
from .tavily import TavilyBackend

BACKENDS: dict[str, SearchBackend] = {
    "tavily": TavilyBackend(),
    "exa": ExaBackend(),
    "brave": BraveBackend(),
    "serper": SerperBackend(),
    "jina": JinaBackend(),
    "bocha": BochaBackend(),
}

ORDER = ["tavily", "exa", "brave", "serper", "jina", "bocha"]


def get(name: str) -> Optional[SearchBackend]:
    return BACKENDS.get(name)


def descriptors() -> list[dict]:
    """Public metadata about each backend (for UI rendering)."""
    return [
        {
            "name": b.name,
            "label": b.label,
            "signup_url": b.signup_url,
            "hint": b.hint,
            "fields": list(b.fields),
        }
        for b in (BACKENDS[k] for k in ORDER if k in BACKENDS)
    ]


def format_for_prompt(res: SearchResult, *, lang: str = "zh") -> str:
    """Render a SearchResult into a system-prompt block, regardless of source backend.

    The footer enforces a strict citation grammar:
      - Every factual claim ends with [n] pointing to the numbered source above,
        OR with [推] to mark inference / extrapolation / common knowledge.
      - The frontend renders [n] as a clickable pill and [推] as a faint badge.
    """
    if not res.ok or not res.results:
        return ""
    if lang == "zh":
        header = f"[联网搜索结果(来源 {res.provider or '?'})]"
        footer = (
            "回答规则(务必严格遵守,UI 会根据这些标记渲染):\n"
            "1. 每条事实陈述末尾必须带标记,二选一:\n"
            "   - [n] :该信息来自上方编号 n 的搜索结果(n 是 1 到 N 的整数)。\n"
            "   - [推]:该信息是你的推测、常识、或基于检索结果的外推,不是直接来自搜索。\n"
            "2. 同一句涉及多个来源时可写 [1][3]。\n"
            "3. 严禁:陈述事实却不带任何标记;把推测伪装成 [n];引用不存在的编号。\n"
            "4. 如果搜索结果不足以回答,先明说缺什么,再给出基于 [推] 的尝试性回答。\n"
            "5. 标记符号必须是英文方括号 [],不是中文【】。"
        )
    else:
        header = f"[Web search results from {res.provider or '?'}]"
        footer = (
            "Citation rules — every factual claim MUST end with one of:\n"
            "  [n]  — drawn from numbered source n above\n"
            "  [推] — your own inference / extrapolation / common knowledge (not from sources)\n"
            "Use [1][2] when multiple sources support a claim. Never bare-state facts."
        )

    parts = [header]
    if res.answer:
        parts.append(f"摘要: {res.answer}")
    for i, r in enumerate(res.results, 1):
        title = (r.get("title") or "(无标题)").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip().replace("\n", " ")
        if len(content) > 500:
            content = content[:500] + "…"
        parts.append(f"\n[{i}] {title}\n    URL: {url}\n    {content}")
    parts.append("")
    parts.append(footer)
    return "\n".join(parts)


GUESS_TAG_INSTRUCTION_ZH = (
    "[标注规则] 没有联网检索时,请对自己拿不准 / 推测 / 估算的陈述末尾加 [推] 标记;"
    "广泛公认的常识(如基础数学、物理常量、历史日期)不需要标记。严禁把推测当事实陈述。"
)
