from __future__ import annotations

from app.providers.registry import REGISTRY, make_adapter


def test_all_kinds_resolvable():
    assert {"anthropic_api", "claude_cli", "openai_api", "codex_cli", "gemini_api", "openai_compat"} <= set(REGISTRY)


def test_make_adapter_unknown():
    import pytest

    with pytest.raises(ValueError):
        make_adapter({"kind": "noooo"})


def test_openai_compat_health_no_baseurl():
    import asyncio

    a = make_adapter({"id": "x", "name": "n", "kind": "openai_compat", "config": {}})
    res = asyncio.run(a.health_check("any"))
    assert res.ok is False
    assert "base_url" in res.message


def test_anthropic_health_no_key():
    import asyncio

    a = make_adapter({"id": "x", "name": "n", "kind": "anthropic_api", "config": {}})
    res = asyncio.run(a.health_check())
    assert res.ok is False
    assert "key" in res.message.lower()


def test_cli_scrub_env_strips_anthropic_keys(monkeypatch):
    from app.providers.claude_cli import scrub_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked")
    monkeypatch.setenv("OPENAI_API_KEY", "leaked2")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil")
    monkeypatch.setenv("HOME", "/tmp")
    env = scrub_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env.get("HOME") == "/tmp"
