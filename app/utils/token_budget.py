"""Token estimation + context-window lookup.

Goal: tell the UI how full the context is so it can show a progress bar
and prompt the user to summarize before bumping into model limits. We
don't try to be exact (different tokenizers per family) — a robust
upper-bound is good enough for "is this turn going to bust the limit?".
"""
from __future__ import annotations
import re

# Per-family ceilings as of 2026-04. Conservative — pick the smaller
# documented value when a model has multiple deployment options.
# Keys are matched against the model_id with substring rules; first hit wins.
CONTEXT_WINDOWS: list[tuple[str, int]] = [
    # Anthropic
    ("claude-opus-4-7", 1_000_000),
    ("claude-sonnet-4-6", 1_000_000),
    ("claude-haiku-4-5", 200_000),
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-haiku-4", 200_000),
    # OpenAI
    ("gpt-5.5", 1_000_000),
    ("gpt-5.4", 1_000_000),
    ("gpt-5", 256_000),
    ("gpt-4o", 128_000),
    # Gemini
    ("gemini-3.1-pro", 2_000_000),
    ("gemini-3-flash", 1_000_000),
    ("gemini-3.1-flash", 1_000_000),
    ("gemini-2.5-pro", 2_000_000),
    ("gemini-2.5-flash", 1_000_000),
    # DeepSeek
    ("deepseek-v4-pro", 1_000_000),
    ("deepseek-v4-flash", 1_000_000),
    ("deepseek-chat", 128_000),
    ("deepseek-reasoner", 128_000),
    # Zhipu / GLM
    ("glm-5", 200_000),
    ("glm-4.7", 200_000),
    ("glm-4.5", 128_000),
    # Qwen
    ("qwen3.6-max", 260_000),
    ("qwen3.6-plus", 260_000),
    ("qwen3.6-flash", 1_000_000),
    ("qwen-max", 32_000),
    ("qwen-plus", 32_000),
    # Moonshot Kimi
    ("kimi-k2.6", 256_000),
    ("kimi-k2.5", 200_000),
    ("moonshot-v1-128k", 128_000),
    ("moonshot-v1-32k", 32_000),
    ("moonshot-v1-8k", 8_000),
    # MiniMax
    ("MiniMax-M2.7", 1_000_000),
    ("MiniMax-M2.5", 1_000_000),
    ("MiniMax-M1", 1_000_000),
    ("MiniMax-Text-01", 4_000_000),
    # Doubao
    ("doubao-seed-1-8", 256_000),
    ("doubao-seed-1-6", 256_000),
    # Yi
    ("yi-lightning", 32_000),
    ("yi-large", 32_000),
    # Aggregator slugs
    ("anthropic/claude", 200_000),
    ("openai/gpt-5.5", 1_000_000),
    ("openai/gpt-5", 256_000),
    ("google/gemini-3", 1_000_000),
    ("deepseek/deepseek-v4", 1_000_000),
    ("moonshotai/kimi", 256_000),
    ("Qwen/Qwen", 32_000),
    ("meta-llama/Llama", 128_000),
    # Subscription CLI defaults — pick the conservative end.
    ("gpt-5-codex", 256_000),
    ("gpt-5.1-codex", 256_000),
    ("gpt-5.3-codex", 256_000),
    ("o3", 200_000),
]

DEFAULT_WINDOW = 32_000  # Unknown / new models — conservative.


def context_window_for(model_id: str) -> int:
    if not model_id:
        return DEFAULT_WINDOW
    mid = model_id.lower()
    for needle, size in CONTEXT_WINDOWS:
        if needle.lower() in mid:
            return size
    return DEFAULT_WINDOW


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic upper-bound estimator.

    For mixed CJK + English we use 1 token per CJK char + ~1.3 tokens per
    word. This overshoots compared to real BPE tokenizers (intentional —
    we'd rather warn early than blow past the limit).
    """
    if not text:
        return 0
    cjk_chars = sum(1 for ch in text if "一" <= ch <= "鿿")
    non_cjk = "".join(ch for ch in text if not ("一" <= ch <= "鿿"))
    word_count = len(_WORD_RE.findall(non_cjk))
    return cjk_chars + int(word_count * 1.3) + 4  # tiny per-message overhead


def estimate_messages(messages: list) -> int:
    """messages may be Message dataclass-like or dict rows from db."""
    total = 0
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # multi-modal: sum text blocks; image/file count as ~512 tokens each
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text":
                    total += estimate_tokens(blk.get("data") or blk.get("text") or "")
                elif blk.get("type") in ("image", "file"):
                    total += 512
    return total


def budget_summary(used: int, max_tokens: int) -> dict:
    pct = round(used / max_tokens * 100, 1) if max_tokens else 0
    return {
        "used_tokens": used,
        "max_tokens": max_tokens,
        "pct": pct,
        "warn": pct >= 90,
        "soft_warn": pct >= 70,
    }
