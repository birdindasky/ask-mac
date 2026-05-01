"""Search backend protocol + shared types."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchResult:
    ok: bool
    query: str
    answer: Optional[str] = None
    results: list[dict] = None  # type: ignore
    elapsed_ms: int = 0
    error: Optional[str] = None
    provider: str = ""

    def __post_init__(self):
        if self.results is None:
            self.results = []


class SearchBackend:
    """Each backend normalizes its API response to a SearchResult."""

    name: str = ""
    label: str = ""
    signup_url: str = ""
    hint: str = ""
    fields: list[str] = ["api_key"]

    async def search(
        self,
        query: str,
        *,
        api_key: str,
        max_results: int = 5,
        depth: str = "basic",
        timeout: float = 25.0,
    ) -> SearchResult:
        raise NotImplementedError

    async def health_check(self, api_key: str) -> SearchResult:
        return await self.search("hello", api_key=api_key, max_results=1, timeout=15)
