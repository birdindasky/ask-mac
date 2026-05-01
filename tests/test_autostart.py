"""Test autostart helpers — uses MLC_LAUNCH_AGENT_PATH override so the
real ~/Library/LaunchAgents stays untouched."""
from __future__ import annotations
import importlib
import plistlib

import pytest


@pytest.fixture
def autostart(monkeypatch, tmp_path):
    plist = tmp_path / "com.example.ask.plist"
    binary = tmp_path / "Ask"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MLC_LAUNCH_AGENT_PATH", str(plist))
    monkeypatch.setenv("MLC_AUTOSTART_BINARY", str(binary))
    import app.utils.autostart as a
    importlib.reload(a)
    return a, plist


def test_enable_writes_plist(autostart):
    a, plist = autostart
    assert not a.is_enabled()
    a.enable_login_item()
    assert a.is_enabled()
    assert plist.is_file()
    with open(plist, "rb") as f:
        data = plistlib.load(f)
    assert data["Label"]
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"]


def test_disable_removes_plist(autostart):
    a, plist = autostart
    a.enable_login_item()
    assert plist.is_file()
    a.disable_login_item()
    assert not plist.is_file()
    # Idempotent: second call doesn't raise.
    a.disable_login_item()


def test_enable_overwrites_existing(autostart):
    a, plist = autostart
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text("not a plist")
    a.enable_login_item()
    with open(plist, "rb") as f:
        data = plistlib.load(f)
    assert data["RunAtLoad"] is True


def test_endpoint_get_and_put(client_with_autostart):
    client = client_with_autostart
    r = client.get("/api/admin/autostart")
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    r = client.put("/api/admin/autostart", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["enabled"] is True

    r = client.get("/api/admin/autostart")
    assert r.json()["enabled"] is True

    r = client.put("/api/admin/autostart", json={"enabled": False})
    assert r.json()["enabled"] is False


@pytest.fixture
def client_with_autostart(monkeypatch, tmp_path):
    plist = tmp_path / "agent.plist"
    binary = tmp_path / "Ask-bin"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MLC_LAUNCH_AGENT_PATH", str(plist))
    monkeypatch.setenv("MLC_AUTOSTART_BINARY", str(binary))
    import app.utils.autostart as a
    importlib.reload(a)
    import app.api.admin as admin_mod
    importlib.reload(admin_mod)
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    return TestClient(main.app)
