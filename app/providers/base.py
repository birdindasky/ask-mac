"""Provider adapter base class + shared types."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from ..security import get_provider_key


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass
class StreamChunk:
    """A single streaming token / fragment."""

    delta: str = ""
    done: bool = False
    error: Optional[str] = None


@dataclass
class HealthResult:
    ok: bool
    message: str
    detail: Optional[str] = None


class ProviderAdapter:
    """Adapter contract. Each provider implements `stream` and `health_check`."""

    kind: str  # registry key

    def __init__(self, instance: dict):
        # `instance` mirrors a provider entry from config.json
        self.instance = instance
        self.config = dict(instance.get("config") or {})
        self.id = instance.get("id")
        self.name = instance.get("name")
        # API key lives in Keychain keyed by provider id; allow callers to
        # override (e.g. settings page test-with-unsaved-key) by passing
        # config.api_key explicitly. Otherwise fall back to Keychain.
        if not self.config.get("api_key") and self.id:
            stored = get_provider_key(self.id)
            if stored:
                self.config["api_key"] = stored

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        raise NotImplementedError

    # Helpers
    @staticmethod
    def _split_system(messages: list[Message], system: Optional[str]) -> tuple[Optional[str], list[Message]]:
        sys_parts: list[str] = []
        rest: list[Message] = []
        if system:
            sys_parts.append(system)
        for m in messages:
            if m.role == "system":
                sys_parts.append(m.content)
            else:
                rest.append(m)
        sys_text = "\n\n".join([s for s in sys_parts if s]).strip()
        return (sys_text or None), rest
