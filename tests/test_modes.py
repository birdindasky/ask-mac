"""End-to-end mode tests with a mocked provider."""
from __future__ import annotations
import asyncio

from app import db
from app.modes import chat as chat_mode
from app.modes import compare as compare_mode
from app.modes import debate as debate_mode
from app.providers import registry as reg
from app.providers.base import HealthResult, ProviderAdapter, StreamChunk


class MockAdapter(ProviderAdapter):
    kind = "mock"

    async def stream(self, messages, model_id, *, cancel_event=None, system=None):
        # Echo last user content character by character with a fixed prefix.
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        text = f"[{model_id}] " + (last_user.content if last_user else "")
        for ch in text:
            yield StreamChunk(delta=ch)
        yield StreamChunk(done=True)

    async def health_check(self, model_id=None):
        return HealthResult(True, "mock ok")


def _install_mock():
    reg.REGISTRY["mock"] = MockAdapter


async def _drain(gen):
    out = []
    async for ev in gen:
        out.append(ev)
    return out


def test_chat_mode_streams_and_persists():
    _install_mock()
    s = db.create_session("t", "chat")
    provider = {"id": "p", "name": "Mocky", "kind": "mock", "config": {}}
    cancel = asyncio.Event()
    events = asyncio.run(_drain(chat_mode.run_chat(s["id"], "hello", provider, "mocky-1", cancel_event=cancel)))
    types = [e["event"] for e in events]
    assert types[0] == "user_message"
    assert "assistant_start" in types
    deltas = [e for e in events if e["event"] == "assistant_delta"]
    assert len(deltas) > 0
    end = next(e for e in events if e["event"] == "assistant_end")
    assert "[mocky-1]" in end["data"]["content"]

    msgs = db.list_messages(s["id"])
    assert len(msgs) == 2
    assert msgs[1]["role"] == "assistant"
    assert "[mocky-1]" in msgs[1]["content"]


def test_compare_mode_runs_two_tracks():
    _install_mock()
    s = db.create_session("t", "compare")
    provider = {"id": "p", "name": "Mocky", "kind": "mock", "config": {}}
    cancel = asyncio.Event()
    tracks = [
        {"track_id": "left", "provider_instance": provider, "model_id": "A"},
        {"track_id": "right", "provider_instance": provider, "model_id": "B"},
    ]
    events = asyncio.run(_drain(compare_mode.run_compare(s["id"], "hi", tracks, cancel_event=cancel)))
    starts = [e for e in events if e["event"] == "assistant_start"]
    assert {e["data"]["track"] for e in starts} == {"left", "right"}
    ends = [e for e in events if e["event"] == "assistant_end"]
    assert len(ends) == 2
    assert any("[A]" in e["data"]["content"] for e in ends)
    assert any("[B]" in e["data"]["content"] for e in ends)


def test_debate_mode_two_rounds():
    _install_mock()
    s = db.create_session("t", "debate")
    provider = {"id": "p", "name": "Mocky", "kind": "mock", "config": {}}
    cancel = asyncio.Event()
    side_a = {"provider_instance": provider, "model_id": "A", "label": "Lead"}
    side_b = {"provider_instance": provider, "model_id": "B", "label": "Critic"}
    events = asyncio.run(_drain(debate_mode.run_debate(s["id"], "AI 是否有害?", "lead_critic", side_a, side_b, rounds=2, cancel_event=cancel)))
    rounds = [e for e in events if e["event"] == "round_start"]
    assert len(rounds) == 2
    end = next(e for e in events if e["event"] == "debate_end")
    assert end["data"]["rounds_completed"] == 2
