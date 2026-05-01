"""Tests for notifier + internal API endpoints.

We don't actually post a real notification to the OS — instead we patch
out the AppKit/UserNotifications surface so the code paths exercise but
the side effects are observable via mock state.
"""
from __future__ import annotations
import importlib

import pytest
from fastapi.testclient import TestClient


def test_notify_unavailable_returns_false(monkeypatch):
    import app.utils.notifier as n
    importlib.reload(n)
    monkeypatch.setattr(n, "_NOTIFY_AVAILABLE", False)
    assert n.notify("t", "b") is False


def test_notify_available_dispatches(monkeypatch):
    import app.utils.notifier as n
    importlib.reload(n)

    calls: list[dict] = []

    class _FakeContent:
        def __init__(self):
            self.title = None
            self.body = None

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setTitle_(self, t):
            self.title = t

        def setBody_(self, b):
            self.body = b

    class _FakeRequest:
        @classmethod
        def requestWithIdentifier_content_trigger_(cls, ident, content, trigger):
            return {"id": ident, "content": content}

    class _FakeCenter:
        @classmethod
        def currentNotificationCenter(cls):
            return cls()

        def requestAuthorizationWithOptions_completionHandler_(self, opts, cb):
            cb(True, None)

        def addNotificationRequest_withCompletionHandler_(self, req, cb):
            calls.append(req)
            cb(None)

    monkeypatch.setattr(n, "_NOTIFY_AVAILABLE", True)
    monkeypatch.setattr(n, "_AUTH_REQUESTED", False)
    monkeypatch.setattr(n, "UNMutableNotificationContent", _FakeContent, raising=False)
    monkeypatch.setattr(n, "UNNotificationRequest", _FakeRequest, raising=False)
    monkeypatch.setattr(n, "UNUserNotificationCenter", _FakeCenter, raising=False)
    monkeypatch.setattr(n, "UNAuthorizationOptionAlert", 1, raising=False)
    monkeypatch.setattr(n, "UNAuthorizationOptionSound", 2, raising=False)
    monkeypatch.setattr(n, "UNAuthorizationOptionBadge", 4, raising=False)

    assert n.notify("hello", "world", identifier="abc") is True
    assert len(calls) == 1
    assert calls[0]["id"] == "abc"
    assert calls[0]["content"].title == "hello"
    assert calls[0]["content"].body == "world"


def test_dock_badge_unavailable_returns_false(monkeypatch):
    import app.utils.dock_badge as d
    importlib.reload(d)
    monkeypatch.setattr(d, "_AVAILABLE", False)
    assert d.set_badge(True) is False


def test_dock_badge_dispatches(monkeypatch):
    import app.utils.dock_badge as d
    importlib.reload(d)

    pushed: list = []

    class _FakeQueue:
        @classmethod
        def mainQueue(cls):
            return cls()

        def addOperationWithBlock_(self, block):
            pushed.append(block)
            block()

    class _FakeTile:
        def __init__(self):
            self.label = None
            self.displayed = 0

        def setBadgeLabel_(self, label):
            self.label = label

        def display(self):
            self.displayed += 1

    fake_tile = _FakeTile()

    class _FakeApp:
        @staticmethod
        def dockTile():
            return fake_tile

    monkeypatch.setattr(d, "_AVAILABLE", True)
    monkeypatch.setattr(d, "NSOperationQueue", _FakeQueue, raising=False)
    monkeypatch.setattr(d, "NSApp", _FakeApp, raising=False)

    assert d.set_badge(True) is True
    assert fake_tile.label == "●"
    assert fake_tile.displayed == 1

    assert d.set_badge(False) is True
    assert fake_tile.label == ""


@pytest.fixture
def client():
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_endpoint_dock_badge(client, monkeypatch):
    seen = []
    import app.utils.dock_badge as d
    monkeypatch.setattr(d, "set_badge", lambda busy: (seen.append(busy) or True))
    r = client.post("/api/internal/dock-badge", json={"busy": True})
    assert r.status_code == 200
    assert r.json()["dispatched"] is True
    assert seen == [True]


def test_endpoint_notify(client, monkeypatch):
    seen = []
    import app.utils.notifier as n
    monkeypatch.setattr(n, "notify", lambda title, body: (seen.append((title, body)) or True))
    r = client.post("/api/internal/notify", json={"title": "T", "body": "B"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert seen == [("T", "B")]
