"""API-level end-to-end test through FastAPI TestClient."""
from __future__ import annotations
import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_data_dir):
    # Install mock kind before creating the app
    from app.providers import registry as reg
    from app.providers.base import HealthResult, ProviderAdapter, StreamChunk

    class Mock(ProviderAdapter):
        kind = "mock"

        async def stream(self, messages, model_id, *, cancel_event=None, system=None):
            last = next((m for m in reversed(messages) if m.role == "user"), None)
            for ch in f"OK[{model_id}]: {(last.content if last else '')}":
                yield StreamChunk(delta=ch)
            yield StreamChunk(done=True)

        async def health_check(self, model_id=None):
            return HealthResult(True, "mock ok")

    class MockHighConfidence(ProviderAdapter):
        kind = "mock_high_confidence"

        async def stream(self, messages, model_id, *, cancel_event=None, system=None):
            text = (
                "【当前判断】我倾向于同意这个方向。\n"
                "【把握度】9/10\n"
                "【支撑】(1) 证据稳定 (2) 代价可控\n"
                "【被对方修正】无\n"
                "【仍坚持】核心判断仍成立,因为收益明确。\n"
                "【需要对方回应】无"
            )
            if messages and "讨论结束" in messages[-1].content:
                text = (
                    "【共识】我们都同意:可以推进。\n"
                    "【主要分歧】我们没谈拢的:无\n"
                    "【建议给用户的下一步】先做小范围验证。"
                )
            for ch in text:
                yield StreamChunk(delta=ch)
            yield StreamChunk(done=True)

        async def health_check(self, model_id=None):
            return HealthResult(True, "mock high confidence ok")

    reg.REGISTRY["mock"] = Mock
    reg.REGISTRY["mock_high_confidence"] = MockHighConfidence

    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def _read_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        data_line = next((line for line in block.splitlines() if line.startswith("data: ")), None)
        if data_line:
            events.append(json.loads(data_line[len("data: "):]))
    return events


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_provider_create_and_chat(client):
    r = client.post("/api/providers", json={
        "name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]
    })
    assert r.status_code == 200, r.text
    pid = r.json()["provider"]["id"]

    r = client.post("/api/sessions", json={"title": "t", "mode": "chat"})
    sid = r.json()["session"]["id"]

    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"text": "hi", "provider_id": pid, "model_id": "m1"}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "event: assistant_delta" in body
    assert "event: assistant_end" in body
    assert "OK[m1]: hi" in body


def test_provider_redacts_api_key(client):
    client.post("/api/providers", json={
        "name": "Mocky", "kind": "mock", "config": {"api_key": "secret123"}, "models": []
    })
    r = client.get("/api/providers")
    p = r.json()["providers"][0]
    # api_key never round-trips through the JSON config; it lives in Keychain.
    assert "api_key" not in p["config"]
    assert p["configured"] is True
    # Direct check: secret was stored under the provider id.
    from app.security import get_provider_key
    assert get_provider_key(p["id"]) == "secret123"


def test_compare_endpoint(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "c", "mode": "compare"}).json()["session"]["id"]
    body = {
        "text": "ping",
        "tracks": [
            {"track_id": "left", "provider_id": pid, "model_id": "A"},
            {"track_id": "right", "provider_id": pid, "model_id": "B"},
        ],
    }
    with client.stream("POST", f"/api/sessions/{sid}/compare", json=body) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "OK[A]" in text and "OK[B]" in text


def test_debate_endpoint(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "d", "mode": "debate"}).json()["session"]["id"]
    body = {
        "topic": "AI good?",
        "sub_mode": "lead_critic",
        "rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "Lead-1", "label": "Lead"},
        "side_b": {"provider_id": pid, "model_id": "Critic-1", "label": "Critic"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/debate", json=body) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "round_start" in text
    assert "OK[Lead-1]" in text and "OK[Critic-1]" in text


def test_discuss_emits_checkpoint_when_no_convergence(client):
    """Without 把握度 ≥ 8 from both sides, the rounds cap path emits a
    checkpoint (not consensus) and lets the user choose continue/finalize."""
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "discuss", "mode": "discuss"}).json()["session"]["id"]
    body = {
        "topic": "是否应该引入讨论模式?",
        "max_rounds": 2,
        "side_a": {"provider_id": pid, "model_id": "A-1", "label": "A 方"},
        "side_b": {"provider_id": pid, "model_id": "B-1", "label": "B 方"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss", json=body) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    events = _read_sse(text)
    end = next(ev for ev in events if ev["event"] == "discuss_end")
    assert end["data"]["converged"] is False
    assert end["data"]["stage"] == "checkpoint"
    assert any(ev["event"] == "assistant_start" and ev["data"].get("speaker_role") == "checkpoint" for ev in events)

    messages = client.get(f"/api/sessions/{sid}").json()["messages"]
    # Should have NOT emitted a final consensus turn yet — only the checkpoint.
    assert messages[-1]["meta"]["checkpoint"] is True
    assert messages[-1]["meta"].get("consensus") is not True


def test_discuss_continue_then_finalize(client):
    """User runs discuss → checkpoint → continue → checkpoint → finalize → consensus."""
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "discuss", "mode": "discuss"}).json()["session"]["id"]
    init = {
        "topic": "怎么决定?",
        "max_rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "A-1"},
        "side_b": {"provider_id": pid, "model_id": "B-1"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss", json=init) as resp:
        b"".join(resp.iter_bytes())

    # Continue once: another round, same low-confidence mock → another checkpoint.
    cont = {
        "extra_rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "A-1"},
        "side_b": {"provider_id": pid, "model_id": "B-1"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss/continue", json=cont) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    events = _read_sse(text)
    end = next(ev for ev in events if ev["event"] == "discuss_end")
    assert end["data"]["stage"] == "checkpoint"
    assert end["data"]["rounds_completed"] == 2  # 1 initial + 1 continue

    # Finalize: emit consensus from current transcript even without convergence.
    final = {"side_a": {"provider_id": pid, "model_id": "A-1"}}
    with client.stream("POST", f"/api/sessions/{sid}/discuss/finalize", json=final) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    events = _read_sse(text)
    assert any(ev["event"] == "assistant_start" and ev["data"].get("speaker_role") == "consensus" for ev in events)

    messages = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert messages[-1]["meta"]["consensus"] is True
    # Earlier checkpoints should still be in history with checkpoint=True.
    checkpoints = [m for m in messages if m.get("meta", {}).get("checkpoint")]
    assert len(checkpoints) >= 2  # one from initial, one from continue


def test_discuss_converged_still_emits_checkpoint(client):
    """Even when both sides hit 把握度 ≥ 8 in round 1, the user — not the
    server — decides whether to stop. Backend reports converged=True in
    the SSE end event but still emits a checkpoint, leaving consensus
    creation to the explicit /finalize call."""
    pid = client.post("/api/providers", json={"name": "Certain", "kind": "mock_high_confidence", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "discuss", "mode": "discuss"}).json()["session"]["id"]
    body = {
        "topic": "是否应该先做 MVP?",
        "max_rounds": 5,
        "side_a": {"provider_id": pid, "model_id": "A-1"},
        "side_b": {"provider_id": pid, "model_id": "B-1"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss", json=body) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    events = _read_sse(text)
    end = next(ev for ev in events if ev["event"] == "discuss_end")
    assert end["data"]["converged"] is True
    assert end["data"]["stage"] == "checkpoint"
    assert end["data"]["rounds_completed"] == 1

    messages = client.get(f"/api/sessions/{sid}").json()["messages"]
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 3
    assert [m["meta"]["speaker_role"] for m in assistant_messages] == ["a", "b", "checkpoint"]
    assert messages[-1]["meta"].get("consensus") is not True
    assert messages[-1]["meta"]["checkpoint"] is True


def test_discuss_no_stance_field_accepted(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "discuss", "mode": "discuss"}).json()["session"]["id"]
    body = {
        "topic": "是否保留 stance 字段?",
        "max_rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "A-1", "stance": "支持"},
        "side_b": {"provider_id": pid, "model_id": "B-1", "stance": "反对"},
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss", json=body) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())

    saved = client.get("/api/ui-prefs").json()["last_discuss"]
    assert "stance" not in saved["side_a"]
    assert "stance" not in saved["side_b"]


def test_session_delete(client):
    sid = client.post("/api/sessions", json={"title": "x", "mode": "chat"}).json()["session"]["id"]
    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 404


def test_budget_endpoint(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "b", "mode": "chat"}).json()["session"]["id"]
    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"text": "hi", "provider_id": pid, "model_id": "m1"}) as resp:
        b"".join(resp.iter_bytes())

    r = client.get(f"/api/sessions/{sid}/budget", params={"model_id": "claude-opus-4-7"})
    assert r.status_code == 200
    body = r.json()
    assert body["max_tokens"] == 1_000_000
    assert body["used_tokens"] > 0
    assert "pct" in body and "warn" in body and "soft_warn" in body


def test_regenerate_drops_last_assistant_and_reruns(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "r", "mode": "chat"}).json()["session"]["id"]
    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"text": "first", "provider_id": pid, "model_id": "m1"}) as resp:
        b"".join(resp.iter_bytes())
    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert len(msgs) == 2  # user + assistant

    with client.stream("POST", f"/api/sessions/{sid}/regenerate", json={"model_id": "m2", "provider_id": pid}) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    assert "OK[m2]: first" in text  # re-ran with the new model

    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert len(msgs) == 2
    assert msgs[1]["model_id"] == "m2"


def test_admin_export_import_wipe(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "e", "mode": "chat"}).json()["session"]["id"]
    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"text": "hello", "provider_id": pid, "model_id": "m1"}) as resp:
        b"".join(resp.iter_bytes())

    cfg_dump = client.get("/api/admin/export/config").json()
    sess_dump = client.get("/api/admin/export/sessions").json()
    assert cfg_dump["config"]["version"] >= 2
    assert any(s["session"]["id"] == sid for s in sess_dump["sessions"])
    # Keys must NEVER be in the exported config doc.
    cfg_blob = json.dumps(cfg_dump)
    assert "secret" not in cfg_blob.lower() or "api_key" not in cfg_blob

    # Wipe sessions, confirm gone, then re-import and confirm present.
    r = client.post("/api/admin/wipe/sessions")
    assert r.json()["ok"] is True
    assert client.get("/api/sessions").json()["sessions"] == []

    r = client.post("/api/admin/import/sessions", json={"sessions": sess_dump["sessions"], "merge": False})
    assert r.json()["ok"] is True
    restored = client.get("/api/sessions").json()["sessions"]
    assert any(s["id"] == sid for s in restored)


def test_summarize_compacts_history(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "s", "mode": "chat"}).json()["session"]["id"]
    # Drive 3 turns so we have ≥4 messages to satisfy the summarize threshold.
    for prompt in ("first", "second", "third"):
        with client.stream("POST", f"/api/sessions/{sid}/chat", json={"text": prompt, "provider_id": pid, "model_id": "m1"}) as r:
            b"".join(r.iter_bytes())
    msgs_before = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert len(msgs_before) == 6  # 3 user + 3 assistant

    r = client.post(f"/api/sessions/{sid}/summarize", json={"provider_id": pid, "model_id": "summarizer"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compacted_count"] == 5  # everything except the trailing message
    assert body["summary"]  # mock streams something back

    msgs_after = client.get(f"/api/sessions/{sid}").json()["messages"]
    # Should be: synthetic [历史摘要] user message + the surviving last message.
    assert len(msgs_after) == 2
    assert "[历史摘要]" in msgs_after[0]["content"]
    assert msgs_after[0]["meta"].get("summary") is True


def test_chat_with_attachments_inlines_text_file(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "att", "mode": "chat"}).json()["session"]["id"]
    body = {
        "text": "Summarize this README",
        "provider_id": pid,
        "model_id": "m1",
        "attachments": [
            {"type": "file", "name": "README.md", "mime": "text/markdown", "data": "# Hello\n\n这是一份测试", "size": 30},
            {"type": "image", "name": "shot.png", "mime": "image/png", "data": "iVBORw0KGgo=", "size": 12},
        ],
    }
    with client.stream("POST", f"/api/sessions/{sid}/chat", json=body) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())

    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    user_msg = msgs[0]
    # Text file content is inlined into the persisted prompt so plain-text
    # adapters can see it; image stays in meta.attachments as a placeholder.
    assert "# Hello" in user_msg["content"]
    assert "[附件图片: shot.png" in user_msg["content"]
    saved = user_msg["meta"]["attachments"]
    assert len(saved) == 2
    assert saved[0]["type"] == "file" and saved[1]["type"] == "image"


def test_compare_accepts_attachments(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "c-att", "mode": "compare"}).json()["session"]["id"]
    body = {
        "text": "compare these",
        "tracks": [
            {"track_id": "left", "provider_id": pid, "model_id": "A"},
            {"track_id": "right", "provider_id": pid, "model_id": "B"},
        ],
        "attachments": [
            {"type": "file", "name": "notes.md", "mime": "text/markdown", "data": "# hello", "size": 7},
        ],
    }
    with client.stream("POST", f"/api/sessions/{sid}/compare", json=body) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())
    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    user_msg = msgs[0]
    assert "# hello" in user_msg["content"]
    assert user_msg["meta"]["attachments"][0]["name"] == "notes.md"


def test_debate_accepts_attachments(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "d-att", "mode": "debate"}).json()["session"]["id"]
    body = {
        "topic": "should we ship?",
        "sub_mode": "lead_critic",
        "rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "Lead-1", "label": "Lead"},
        "side_b": {"provider_id": pid, "model_id": "Critic-1", "label": "Critic"},
        "attachments": [
            {"type": "file", "name": "spec.md", "mime": "text/markdown", "data": "ship plan", "size": 9},
        ],
    }
    with client.stream("POST", f"/api/sessions/{sid}/debate", json=body) as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    # Both debate sides see the attachment-inlined topic via the per-side prompt.
    assert "ship plan" in text
    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert "ship plan" in msgs[0]["content"]
    assert msgs[0]["meta"]["attachments"][0]["name"] == "spec.md"


def test_discuss_accepts_attachments(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "ds-att", "mode": "discuss"}).json()["session"]["id"]
    body = {
        "topic": "怎么决定?",
        "max_rounds": 1,
        "side_a": {"provider_id": pid, "model_id": "A-1"},
        "side_b": {"provider_id": pid, "model_id": "B-1"},
        "attachments": [
            {"type": "file", "name": "ctx.md", "mime": "text/markdown", "data": "background context", "size": 18},
        ],
    }
    with client.stream("POST", f"/api/sessions/{sid}/discuss", json=body) as resp:
        assert resp.status_code == 200
        b"".join(resp.iter_bytes())
    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert "background context" in msgs[0]["content"]
    assert msgs[0]["meta"]["attachments"][0]["name"] == "ctx.md"


def test_fts_search_finds_message_across_sessions(client):
    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid_a = client.post("/api/sessions", json={"title": "session-A", "mode": "chat"}).json()["session"]["id"]
    sid_b = client.post("/api/sessions", json={"title": "session-B", "mode": "chat"}).json()["session"]["id"]
    # Stream two distinct prompts so FTS has ≥2 user-message hits to choose from.
    with client.stream("POST", f"/api/sessions/{sid_a}/chat", json={"text": "trigram 测试搜索 alpha", "provider_id": pid, "model_id": "m1"}) as r:
        b"".join(r.iter_bytes())
    with client.stream("POST", f"/api/sessions/{sid_b}/chat", json={"text": "完全无关的内容 beta", "provider_id": pid, "model_id": "m1"}) as r:
        b"".join(r.iter_bytes())

    # English substring
    r = client.get("/api/sessions/search/messages", params={"q": "trigram"})
    assert r.status_code == 200
    hits = r.json()["results"]
    assert any(h["session_id"] == sid_a and "trigram" in (h["snippet"] or "").lower() for h in hits)

    # CJK substring
    r = client.get("/api/sessions/search/messages", params={"q": "测试搜索"})
    hits = r.json()["results"]
    assert any(h["session_id"] == sid_a for h in hits)

    # Empty query is a no-op
    r = client.get("/api/sessions/search/messages", params={"q": "  "})
    assert r.json()["results"] == []


def test_concurrency_guard_returns_409(client):
    """A second stream against the same session while one is live → 409."""
    import asyncio
    from app.api import chat as chat_mod

    pid = client.post("/api/providers", json={"name": "Mocky", "kind": "mock", "config": {}, "models": ["m1"]}).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "g", "mode": "chat"}).json()["session"]["id"]

    # Manually inject a sentinel cancel-event so the guard sees an in-flight stream.
    chat_mod._cancels[sid] = asyncio.Event()
    try:
        r = client.post(f"/api/sessions/{sid}/chat", json={"text": "x", "provider_id": pid, "model_id": "m1"})
        assert r.status_code == 409
    finally:
        chat_mod._cancels.pop(sid, None)


def test_cancel_stream_marks_message_cancelled(client):
    """Stream → cancel → assert SSE ends with stream_end AND DB row has meta.cancelled=true."""
    import asyncio
    import threading
    from app.providers import registry as reg
    from app.providers.base import HealthResult, ProviderAdapter, StreamChunk

    class SlowMock(ProviderAdapter):
        kind = "slow_mock"

        async def stream(self, messages, model_id, *, cancel_event=None, system=None):
            # why: each chunk awaits the cancel event with a short timeout so
            # the test's POST /cancel can interrupt mid-stream.
            for ch in "this stream is intentionally slow":
                if cancel_event and cancel_event.is_set():
                    return
                if cancel_event:
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=0.05)
                        return  # cancel fired during the wait
                    except asyncio.TimeoutError:
                        pass
                yield StreamChunk(delta=ch)
            yield StreamChunk(done=True)

        async def health_check(self, model_id=None):
            return HealthResult(True, "slow mock ok")

    reg.REGISTRY["slow_mock"] = SlowMock

    pid = client.post(
        "/api/providers",
        json={"name": "Slowy", "kind": "slow_mock", "config": {}, "models": ["m1"]},
    ).json()["provider"]["id"]
    sid = client.post("/api/sessions", json={"title": "cancel", "mode": "chat"}).json()["session"]["id"]

    text_holder: dict = {}

    def _drive_stream():
        with client.stream(
            "POST", f"/api/sessions/{sid}/chat",
            json={"text": "go", "provider_id": pid, "model_id": "m1"},
        ) as resp:
            text_holder["text"] = b"".join(resp.iter_bytes()).decode("utf-8")

    t = threading.Thread(target=_drive_stream)
    t.start()

    # Wait for the stream to register its cancel event before we POST cancel.
    from app.api import chat as chat_mod
    import time as _time
    for _ in range(100):
        if sid in chat_mod._cancels:
            break
        _time.sleep(0.01)
    r = client.post(f"/api/sessions/{sid}/cancel")
    assert r.json()["ok"] is True
    t.join(timeout=5)
    assert not t.is_alive(), "stream did not stop after cancel"

    body = text_holder["text"]
    assert "event: stream_end" in body, "stream_end frame not emitted"

    msgs = client.get(f"/api/sessions/{sid}").json()["messages"]
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert assistant["meta"].get("cancelled") is True


def test_debate_stance_woven_into_system_prompt():
    """Stance text shows up in the per-side system message."""
    from app.modes.debate import _build_messages_for_role

    msgs = _build_messages_for_role(
        "AI 是否正在取代程序员",
        [],
        speaker_role="lead",
        other_label="Critic",
        self_label="Lead",
        sub_mode="lead_critic",
        self_stance="AI 不会取代程序员,只会扩展我们的能力。",
    )
    sys_text = msgs[0].content
    assert "AI 不会取代" in sys_text
    assert "预设立场" in sys_text
