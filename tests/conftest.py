"""Shared test fixtures: isolate DATA_DIR per test run."""
from __future__ import annotations
import importlib
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path):
    """Force every test to use a fresh data dir + DB + config file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MLC_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MLC_LOG_DIR", str(data_dir / "logs"))
    # Reload settings + dependents so the new env var takes effect.
    import app.settings as st
    importlib.reload(st)
    import app.db as db
    importlib.reload(db)
    # Keychain wrapper looks up settings lazily, so it picks up the new
    # DATA_DIR automatically — no reload needed for app.security.*.
    import app.config_store as cs
    importlib.reload(cs)
    yield data_dir
    # nothing to cleanup — tmp_path is auto-removed
