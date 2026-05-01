from __future__ import annotations
import json

from app import config_store


def test_default_config_when_absent():
    cfg = config_store.load()
    assert cfg["version"] == 2
    assert cfg["ui"]["theme"] == "system"
    assert cfg["providers"] == []
    # Web search backends list is seeded but unconfigured by default.
    assert "tavily" in cfg["web_search"]["providers"]


def test_save_then_load_roundtrip():
    cfg = config_store.load()
    cfg["providers"].append({"id": "p1", "name": "x", "kind": "openai_api", "config": {"api_key": "sk"}, "models": ["m"]})
    config_store.save(cfg)
    loaded = config_store.load()
    assert loaded["providers"][0]["id"] == "p1"
    assert loaded["providers"][0]["enabled"] is True
    # api_key was lifted into the keychain (or fallback) and scrubbed from JSON.
    assert "api_key" not in loaded["providers"][0]["config"]
    from app.security import get_provider_key
    assert get_provider_key("p1") == "sk"


def test_migrate_legacy_blob():
    # Simulate an old v1 config missing fields.
    raw = {"providers": [{"id": "x", "name": "n", "kind": "openai_api"}]}
    config_store.CONFIG_FILE.write_text(json.dumps(raw), encoding="utf-8")
    cfg = config_store.load()
    assert cfg["version"] == 2
    assert cfg["ui"]["theme"] == "system"
    p = cfg["providers"][0]
    assert p["enabled"] is True
    assert p["models"] == []
    assert p["config"] == {}


def test_atomic_update_preserves_other_keys():
    config_store.update(lambda c: {**c, "ui": {**c["ui"], "theme": "light"}})
    assert config_store.load()["ui"]["theme"] == "light"


def test_legacy_v1_keys_lift_into_keychain():
    """v1 config with api_key inline should migrate keys into Keychain."""
    raw = {
        "version": 1,
        "providers": [
            {"id": "old", "name": "Old", "kind": "openai_api",
             "config": {"api_key": "sk-legacy", "base_url": "https://x"},
             "models": ["m"]}
        ],
        "web_search": {
            "tavily_api_key": "tvly-legacy-root",
            "providers": {"exa": {"api_key": "exa-legacy"}},
        },
    }
    config_store.CONFIG_FILE.write_text(json.dumps(raw), encoding="utf-8")
    cfg = config_store.load()
    from app.security import get_provider_key, get_search_key

    assert "api_key" not in cfg["providers"][0]["config"]
    assert get_provider_key("old") == "sk-legacy"
    assert "tavily_api_key" not in cfg["web_search"]
    assert get_search_key("tavily") == "tvly-legacy-root"
    assert get_search_key("exa") == "exa-legacy"
    assert "api_key" not in cfg["web_search"]["providers"]["exa"]
