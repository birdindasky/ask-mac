"""OpenAI native API adapter (chat completions)."""
from __future__ import annotations
from .openai_compat import OpenAICompatAdapter


class OpenAIAPIAdapter(OpenAICompatAdapter):
    kind = "openai_api"

    @property
    def base_url(self) -> str:
        return (self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
