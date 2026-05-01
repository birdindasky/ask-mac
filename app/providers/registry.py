"""Provider registry: kind → adapter class."""
from __future__ import annotations

from .anthropic_api import AnthropicAPIAdapter
from .base import ProviderAdapter
from .claude_cli import ClaudeCLIAdapter
from .codex_cli import CodexCLIAdapter
from .gemini_api import GeminiAPIAdapter
from .openai_api import OpenAIAPIAdapter
from .openai_compat import OpenAICompatAdapter

REGISTRY: dict[str, type[ProviderAdapter]] = {
    "anthropic_api": AnthropicAPIAdapter,
    "claude_cli": ClaudeCLIAdapter,
    "openai_api": OpenAIAPIAdapter,
    "codex_cli": CodexCLIAdapter,
    "gemini_api": GeminiAPIAdapter,
    "openai_compat": OpenAICompatAdapter,
}


def make_adapter(instance: dict) -> ProviderAdapter:
    kind = instance.get("kind")
    if kind not in REGISTRY:
        raise ValueError(f"Unknown provider kind: {kind}")
    return REGISTRY[kind](instance)
