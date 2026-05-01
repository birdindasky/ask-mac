"""User-level config persistence.

JSON file at DATA_DIR/config.json — stores all non-secret metadata:
provider list (id/name/kind/models/template_key), web-search backend
catalog, UI preferences, app behavior toggles.

API keys are NEVER stored in this JSON. They live in the macOS Keychain
keyed by provider_id / search_backend_name (see app.security). Migration
on load lifts any legacy plain-text keys into the Keychain and scrubs
them from the JSON so they never round-trip back to disk.
"""
from __future__ import annotations
import json
import os
import tempfile
import threading
from typing import Any

from .security import set_provider_key, set_search_key
from .settings import CONFIG_FILE

_lock = threading.Lock()

_KNOWN_SEARCH_BACKENDS = ["tavily", "exa", "brave", "serper", "jina", "bocha"]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "ui": {
        "theme": "system",
        "locale": "system",
        "font_size": 16,  # px, 13-22; replaces legacy float font_scale
        "last_mode": "chat",
        "last_session_id": None,
        "status_bar": True,
        "notifications": True,
        "welcome_done": False,
        "last_chat_pick": None,
        "last_compare_pair": None,
        "last_debate": None,
        "last_discuss": None,
    },
    "providers": [],
    "web_search": {
        "active": "tavily",
        "default_on": False,
        "max_results": 5,
        "depth": "basic",
        "providers": {name: {} for name in _KNOWN_SEARCH_BACKENDS},
    },
}


def _load_raw() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_CONFIG))


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg.setdefault("version", 2)
    ui = cfg.setdefault("ui", {})
    ui.setdefault("theme", "system")
    ui.setdefault("locale", "system")
    # Migrate legacy float font_scale (1.0 baseline) → integer px font_size.
    legacy_scale = ui.pop("font_scale", None)
    if legacy_scale is not None and "font_size" not in ui:
        ui["font_size"] = max(13, min(22, int(round(float(legacy_scale) * 16))))
    ui.setdefault("font_size", 16)
    ui.setdefault("last_mode", "chat")
    ui.setdefault("last_session_id", None)
    ui.setdefault("status_bar", True)
    ui.setdefault("notifications", True)
    ui.setdefault("welcome_done", False)
    ui.setdefault("last_chat_pick", None)
    ui.setdefault("last_compare_pair", None)
    ui.setdefault("last_debate", None)
    ui.setdefault("last_discuss", None)
    # The pre-Ask theme default was "dark"; keep an existing user choice
    # but normalize unknown values.
    if ui["theme"] not in ("system", "dark", "light"):
        ui["theme"] = "system"
    if ui["locale"] not in ("system", "zh", "en"):
        ui["locale"] = "system"

    cfg.setdefault("providers", [])
    for p in cfg["providers"]:
        p.setdefault("enabled", True)
        p.setdefault("models", [])
        p.setdefault("config", {})
        # Migrate plain-text api_key into Keychain, scrub from JSON.
        legacy_key = p["config"].pop("api_key", None)
        if legacy_key:
            try:
                set_provider_key(p["id"], legacy_key)
            except Exception:
                # On migration failure keep the legacy key so nothing is lost.
                p["config"]["api_key"] = legacy_key

    ws = cfg.setdefault("web_search", {})
    # v1 had a single tavily_api_key field; absorb if still present.
    legacy_tavily = ws.pop("tavily_api_key", None)
    if legacy_tavily:
        try:
            set_search_key("tavily", legacy_tavily)
        except Exception:
            pass

    ws.setdefault("providers", {})
    for name in _KNOWN_SEARCH_BACKENDS:
        entry = ws["providers"].setdefault(name, {})
        # v1 stored api_key inline per backend; lift into Keychain.
        legacy_key = entry.pop("api_key", None)
        if legacy_key:
            try:
                set_search_key(name, legacy_key)
            except Exception:
                entry["api_key"] = legacy_key
    ws.setdefault("active", "tavily")
    if ws["active"] not in _KNOWN_SEARCH_BACKENDS:
        ws["active"] = "tavily"
    ws.setdefault("default_on", False)
    ws.setdefault("max_results", 5)
    ws.setdefault("depth", "basic")

    cfg["version"] = 2
    return cfg


def _persist(cfg: dict[str, Any]) -> None:
    """Write cfg atomically. Caller is responsible for holding the lock."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".cfg-", dir=str(CONFIG_FILE.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load() -> dict[str, Any]:
    with _lock:
        cfg = _migrate(_load_raw())
        # If the migration moved any keys, persist the cleaned JSON.
        _persist(cfg)
        return cfg


def save(cfg: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        cfg = _migrate(cfg)
        _persist(cfg)
        return cfg


def update(mutator) -> dict[str, Any]:
    """Atomic read-modify-write."""
    with _lock:
        cfg = _migrate(_load_raw())
        new_cfg = mutator(cfg) or cfg
        new_cfg = _migrate(new_cfg)
        _persist(new_cfg)
        return new_cfg
