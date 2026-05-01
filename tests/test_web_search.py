"""Web search settings + integration tests (multi-backend)."""
from __future__ import annotations
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_data_dir, monkeypatch):
    from app.providers import registry as reg
    from app.providers.base import HealthResult, ProviderAdapter, StreamChunk

    captured: dict = {}

    class Mock(ProviderAdapter):
        kind = "mock"

        async def stream(self, messages, model_id, *, cancel_event=None, system=None):
            captured["system_messages"] = [m.content for m in messages if m.role == "system"]
            captured["last_user"] = next((m.content for m in reversed(messages) if m.role == "user"), None)
            for ch in f"OK[{model_id}]":
                yield StreamChunk(delta=ch)
            yield StreamChunk(done=True)

        async def health_check(self, model_id=None):
            return HealthResult(True, "mock ok")

    reg.REGISTRY["mock"] = Mock

    # Stub every search backend's .search() method to return predictable data
    # without hitting any network.
    from app.search import BACKENDS, SearchResult

    async def fake_search(self, query, *, api_key, max_results=5, depth="basic", timeout=25.0):
        if not api_key:
            return SearchResult(ok=False, query=query, error=f"{self.label} key 未配置", provider=self.name)
        return SearchResult(
            ok=True, query=query, provider=self.name,
            answer="模拟答案",
            results=[
                {"title": f"{self.label} 结果1", "url": "https://example.com/1", "content": "示例内容 A"},
                {"title": f"{self.label} 结果2", "url": "https://example.com/2", "content": "示例内容 B"},
            ],
            elapsed_ms=42,
        )

    for backend in BACKENDS.values():
        monkeypatch.setattr(type(backend), "search", fake_search)

    import app.main as main
    importlib.reload(main)
    c = TestClient(main.app)
    c._captured = captured  # type: ignore
    return c


def test_settings_default_lists_all_backends(client):
    r = client.get("/api/web-search")
    assert r.status_code == 200
    data = r.json()
    names = {p["name"] for p in data["providers"]}
    assert {"tavily", "exa", "brave", "serper", "jina", "bocha"} <= names
    assert all(p["configured"] is False for p in data["providers"])
    assert data["active"] == "tavily"
    assert data["default_on"] is False


def test_save_key_per_backend_and_redact(client):
    r = client.put("/api/web-search/keys/exa", json={"api_key": "exa-secret"})
    assert r.status_code == 200
    data = r.json()
    exa = next(p for p in data["providers"] if p["name"] == "exa")
    assert exa["configured"] is True
    # Other backends unchanged
    others = [p for p in data["providers"] if p["name"] != "exa"]
    assert all(p["configured"] is False for p in others)


def test_switch_active_backend(client):
    client.put("/api/web-search/keys/brave", json={"api_key": "brave-x"})
    r = client.put("/api/web-search", json={"active": "brave", "default_on": True})
    data = r.json()
    assert data["active"] == "brave"
    assert data["default_on"] is True


def test_unknown_backend_rejected(client):
    r = client.put("/api/web-search", json={"active": "fake-co"})
    assert r.status_code == 400


def test_clear_key(client):
    client.put("/api/web-search/keys/serper", json={"api_key": "k"})
    r = client.delete("/api/web-search/keys/serper")
    serper = next(p for p in r.json()["providers"] if p["name"] == "serper")
    assert serper["configured"] is False


def test_chat_uses_active_backend_for_context(client):
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    # Configure & activate Exa
    client.put("/api/web-search/keys/exa", json={"api_key": "exa-test"})
    client.put("/api/web-search", json={"active": "exa"})
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]

    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "今天新闻", "provider_id": pid, "model_id": "m1", "web_search": True}) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "event: search_start" in body
    assert "\"provider\": \"exa\"" in body
    assert "event: search_done" in body
    assert "OK[m1]" in body

    sys_msgs = client._captured.get("system_messages") or []
    assert any("Exa 结果1" in s for s in sys_msgs)
    assert any("联网搜索结果" in s for s in sys_msgs)


def test_chat_without_flag_skips_search(client):
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    client.put("/api/web-search/keys/tavily", json={"api_key": "t"})
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]

    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "hi", "provider_id": pid, "model_id": "m1"}) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "event: search_start" not in body


def test_active_backend_missing_key_yields_error(client):
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    # Active backend (default tavily) has no key; chat should still complete.
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]
    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "hi", "provider_id": pid, "model_id": "m1", "web_search": True}) as resp:
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "event: search_error" in body
    assert "OK[m1]" in body


def test_citation_rules_in_system_prompt(client):
    """The injected system context must teach the model the [n] / [推] grammar."""
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    client.put("/api/web-search/keys/tavily", json={"api_key": "tvly-x"})
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]

    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "今天天气", "provider_id": pid, "model_id": "m1", "web_search": True}) as resp:
        b"".join(resp.iter_bytes())
    sys_msgs = client._captured.get("system_messages") or []
    blob = "\n".join(sys_msgs)
    assert "[n]" in blob
    assert "[推]" in blob
    assert "严禁" in blob


def test_search_sources_persisted_to_message_meta(client):
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    client.put("/api/web-search/keys/tavily", json={"api_key": "tvly-x"})
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]

    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "今天天气", "provider_id": pid, "model_id": "m1", "web_search": True}) as resp:
        b"".join(resp.iter_bytes())

    # Reload session — sources must come back from DB.
    full = client.get(f"/api/sessions/{sid}").json()
    assistant = next(m for m in full["messages"] if m["role"] == "assistant")
    assert assistant["meta"].get("web_search") is True
    sources = assistant["meta"].get("search_sources") or []
    assert len(sources) == 2
    assert sources[0].get("url") == "https://example.com/1"


def test_guess_tag_instruction_injected_without_search(client):
    """Even when web_search=false, the model is told to use [推] for guesses."""
    pid = client.post("/api/providers", json={"name": "M", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]["id"]
    with client.stream("POST", f"/api/sessions/{sid}/chat",
                       json={"text": "hi", "provider_id": pid, "model_id": "m1"}) as resp:
        b"".join(resp.iter_bytes())
    sys_msgs = client._captured.get("system_messages") or []
    blob = "\n".join(sys_msgs)
    assert "[推]" in blob


def test_legacy_tavily_key_migrates(isolated_data_dir):
    """Old config with top-level tavily_api_key should migrate transparently."""
    from app import config_store
    from app.security import get_search_key
    import json
    config_store.CONFIG_FILE.write_text(
        json.dumps({"providers": [], "web_search": {"tavily_api_key": "legacy-key"}}),
        encoding="utf-8",
    )
    cfg = config_store.load()
    # Key now lives in Keychain (or fallback secrets.json), not in config.json.
    assert get_search_key("tavily") == "legacy-key"
    assert "tavily_api_key" not in cfg["web_search"]
    assert "api_key" not in cfg["web_search"]["providers"]["tavily"]
