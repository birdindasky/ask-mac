"""SSE streaming endpoints for chat / compare / debate / discuss."""
from __future__ import annotations
import asyncio
import json as _json
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config_store, db
from ..modes import chat as chat_mode
from ..modes import compare as compare_mode
from ..modes import debate as debate_mode
from ..modes import discuss as discuss_mode
from ..search import GUESS_TAG_INSTRUCTION_ZH, format_for_prompt, get as get_search_backend
from ..security import get_search_key

router = APIRouter(prefix="/api", tags=["chat"])

# session_id → cancel event so we can stop in-flight streams.
_cancels: dict[str, asyncio.Event] = {}


class ChatBody(BaseModel):
    text: str
    provider_id: str
    model_id: str
    web_search: bool = False
    attachments: list[dict] | None = None


class CompareBody(BaseModel):
    text: str
    tracks: list[dict]  # [{provider_id, model_id, track_id?}, ...]
    web_search: bool = False
    attachments: list[dict] | None = None


class DebateBody(BaseModel):
    topic: str
    sub_mode: str = "lead_critic"  # or "symmetric"
    side_a: dict  # {provider_id, model_id, label?, stance?}
    side_b: dict
    rounds: int = 2
    web_search: bool = False
    attachments: list[dict] | None = None


class DiscussBody(BaseModel):
    topic: str
    side_a: dict  # {provider_id, model_id, label?}
    side_b: dict
    # Default 1: discuss one round, surface the checkpoint, let the user
    # decide whether to continue. Anything higher feels like running
    # without permission per user feedback 2026-04-29.
    max_rounds: int = 1
    web_search: bool = False
    attachments: list[dict] | None = None


class DiscussContinueBody(BaseModel):
    side_a: dict
    side_b: dict
    extra_rounds: int = 1
    web_search: bool = False
    attachments: list[dict] | None = None


class DiscussFinalizeBody(BaseModel):
    side_a: dict
    web_search: bool = False


class RegenerateBody(BaseModel):
    """Drop the trailing assistant message and re-run with same/new model."""
    provider_id: str | None = None
    model_id: str | None = None
    web_search: bool = False


def _claim_stream(sid: str) -> asyncio.Event:
    """Single-stream-per-session guard. 409 if a stream is already live."""
    if sid in _cancels:
        raise HTTPException(409, "session already has an active stream")
    cancel = asyncio.Event()
    _cancels[sid] = cancel
    return cancel


def _remember_pick(scope: str, payload: dict) -> None:
    """Best-effort persist last_chat_pick / last_compare_pair / last_debate."""
    key = {
        "chat": "last_chat_pick",
        "compare": "last_compare_pair",
        "debate": "last_debate",
        "discuss": "last_discuss",
    }.get(scope)
    if not key:
        return

    def _mut(cfg):
        cfg.setdefault("ui", {})[key] = payload
        return cfg

    try:
        config_store.update(_mut)
    except Exception:
        pass


def _provider(pid: str) -> dict:
    cfg = config_store.load()
    p = next((x for x in cfg["providers"] if x["id"] == pid), None)
    if not p:
        raise HTTPException(404, f"provider {pid} not found")
    if not p.get("enabled", True):
        raise HTTPException(400, f"provider {pid} is disabled")
    return p


async def _maybe_run_search(query: str, enabled: bool):
    """Dispatch to the user's active search backend.

    Returns (context_str, sources_list, events). When `enabled` is False or
    the backend has no key, the context is None but a guess-marking instruction
    is still injected so the model still tags its inferences with [推].
    """
    if not enabled:
        # No search; still inject the [推] marking instruction so guess-tagging
        # works even in plain chat.
        return GUESS_TAG_INSTRUCTION_ZH, [], []
    cfg = config_store.load().get("web_search", {})
    name = cfg.get("active") or "tavily"
    backend = get_search_backend(name)
    if backend is None:
        return GUESS_TAG_INSTRUCTION_ZH, [], [
            {"event": "search_error", "data": {"error": f"未知搜索后端: {name}"}},
        ]
    api_key = get_search_key(name)
    if not api_key:
        return GUESS_TAG_INSTRUCTION_ZH, [], [{
            "event": "search_error",
            "data": {"error": f"联网开关开了,但 {backend.label} 的 key 未配置(去设置里填,或换一家已配置的)"},
        }]
    events = [{"event": "search_start", "data": {"query": query[:200], "provider": name}}]
    res = await backend.search(
        query,
        api_key=api_key,
        max_results=int(cfg.get("max_results") or 5),
        depth=cfg.get("depth") or "basic",
    )
    if not res.ok:
        events.append({"event": "search_error", "data": {"error": res.error or "search failed", "provider": name}})
        return GUESS_TAG_INSTRUCTION_ZH, [], events
    sources = [{"title": r.get("title"), "url": r.get("url")} for r in res.results]
    events.append({
        "event": "search_done",
        "data": {
            "provider": name,
            "count": len(res.results),
            "elapsed_ms": res.elapsed_ms,
            "results": sources,
        },
    })
    return format_for_prompt(res), sources, events


def _sse(payload: dict) -> bytes:
    data = _json.dumps(payload, ensure_ascii=False)
    return f"event: {payload['event']}\ndata: {data}\n\n".encode("utf-8")


async def _wrap(generator: AsyncIterator[dict]) -> AsyncIterator[bytes]:
    try:
        async for ev in generator:
            yield _sse(ev)
    except Exception as e:  # pragma: no cover
        yield _sse({"event": "fatal", "data": {"error": str(e)}})
    finally:
        yield _sse({"event": "stream_end", "data": {}})


@router.post("/sessions/{sid}/chat")
async def post_chat(sid: str, body: ChatBody):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    provider = _provider(body.provider_id)
    cancel = _claim_stream(sid)
    _remember_pick("chat", {"provider_id": body.provider_id, "model_id": body.model_id})

    async def _gen():
        try:
            web_ctx, web_sources, search_events = await _maybe_run_search(body.text, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in chat_mode.run_chat(
                sid, body.text, provider, body.model_id,
                cancel_event=cancel, web_context=web_ctx, web_sources=web_sources,
                attachments=body.attachments,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


@router.post("/sessions/{sid}/compare")
async def post_compare(sid: str, body: CompareBody):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    if len(body.tracks) != 2:
        raise HTTPException(400, "compare mode requires exactly 2 tracks")
    tracks = []
    for i, t in enumerate(body.tracks):
        prov = _provider(t["provider_id"])
        tracks.append(
            {
                "track_id": t.get("track_id") or f"t{i+1}",
                "provider_instance": prov,
                "model_id": t["model_id"],
            }
        )
    cancel = _claim_stream(sid)
    _remember_pick("compare", [
        {"provider_id": t["provider_id"], "model_id": t["model_id"]} for t in body.tracks
    ])

    async def _gen():
        try:
            web_ctx, web_sources, search_events = await _maybe_run_search(body.text, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in compare_mode.run_compare(
                sid, body.text, tracks, cancel_event=cancel, web_context=web_ctx, web_sources=web_sources,
                attachments=body.attachments,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


@router.post("/sessions/{sid}/debate")
async def post_debate(sid: str, body: DebateBody):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    if body.sub_mode not in ("lead_critic", "symmetric"):
        raise HTTPException(400, "sub_mode must be lead_critic or symmetric")
    side_a = {
        "provider_instance": _provider(body.side_a["provider_id"]),
        "model_id": body.side_a["model_id"],
        "label": body.side_a.get("label"),
        "stance": (body.side_a.get("stance") or "").strip() or None,
    }
    side_b = {
        "provider_instance": _provider(body.side_b["provider_id"]),
        "model_id": body.side_b["model_id"],
        "label": body.side_b.get("label"),
        "stance": (body.side_b.get("stance") or "").strip() or None,
    }
    rounds = max(1, min(int(body.rounds or 2), 5))
    cancel = _claim_stream(sid)
    _remember_pick("debate", {
        "sub_mode": body.sub_mode,
        "rounds": rounds,
        "side_a": {k: side_a[k] for k in ("model_id", "label", "stance") if side_a.get(k) is not None} | {"provider_id": body.side_a["provider_id"]},
        "side_b": {k: side_b[k] for k in ("model_id", "label", "stance") if side_b.get(k) is not None} | {"provider_id": body.side_b["provider_id"]},
    })

    async def _gen():
        try:
            web_ctx, web_sources, search_events = await _maybe_run_search(body.topic, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in debate_mode.run_debate(
                sid, body.topic, body.sub_mode, side_a, side_b,
                rounds=rounds, cancel_event=cancel, web_context=web_ctx, web_sources=web_sources,
                attachments=body.attachments,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


@router.post("/sessions/{sid}/discuss")
async def post_discuss(sid: str, body: DiscussBody):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    side_a = {
        "provider_instance": _provider(body.side_a["provider_id"]),
        "model_id": body.side_a["model_id"],
        "label": body.side_a.get("label") or "A 方",
    }
    side_b = {
        "provider_instance": _provider(body.side_b["provider_id"]),
        "model_id": body.side_b["model_id"],
        "label": body.side_b.get("label") or "B 方",
    }
    max_rounds = max(1, min(int(body.max_rounds or 3), 5))
    cancel = _claim_stream(sid)
    _remember_pick("discuss", {
        "max_rounds": max_rounds,
        "side_a": {
            "provider_id": body.side_a["provider_id"],
            "model_id": side_a["model_id"],
            "label": side_a["label"],
        },
        "side_b": {
            "provider_id": body.side_b["provider_id"],
            "model_id": side_b["model_id"],
            "label": side_b["label"],
        },
    })

    async def _gen():
        try:
            web_ctx, web_sources, search_events = await _maybe_run_search(body.topic, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in discuss_mode.run_discuss(
                sid, body.topic, side_a, side_b,
                max_rounds=max_rounds, cancel_event=cancel, web_context=web_ctx, web_sources=web_sources,
                attachments=body.attachments,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


@router.post("/sessions/{sid}/discuss/continue")
async def post_discuss_continue(sid: str, body: DiscussContinueBody):
    """User clicked '继续' on a checkpoint — run another batch of rounds."""
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    side_a = {
        "provider_instance": _provider(body.side_a["provider_id"]),
        "model_id": body.side_a["model_id"],
        "label": body.side_a.get("label") or "A 方",
    }
    side_b = {
        "provider_instance": _provider(body.side_b["provider_id"]),
        "model_id": body.side_b["model_id"],
        "label": body.side_b.get("label") or "B 方",
    }
    extra_rounds = max(1, min(int(body.extra_rounds or 3), 5))
    cancel = _claim_stream(sid)

    async def _gen():
        try:
            # Continue uses the original topic for any new web search context.
            topic, _ = discuss_mode.rebuild_transcript(sid)
            web_ctx, web_sources, search_events = await _maybe_run_search(topic, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in discuss_mode.continue_discuss(
                sid, side_a, side_b,
                extra_rounds=extra_rounds, cancel_event=cancel,
                web_context=web_ctx, web_sources=web_sources,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


@router.post("/sessions/{sid}/discuss/finalize")
async def post_discuss_finalize(sid: str, body: DiscussFinalizeBody):
    """User clicked '到此为止' on a checkpoint — emit consensus from current transcript."""
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    side_a = {
        "provider_instance": _provider(body.side_a["provider_id"]),
        "model_id": body.side_a["model_id"],
        "label": body.side_a.get("label") or "A 方",
    }
    cancel = _claim_stream(sid)

    async def _gen():
        try:
            topic, _ = discuss_mode.rebuild_transcript(sid)
            web_ctx, web_sources, search_events = await _maybe_run_search(topic, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in discuss_mode.finalize_discuss(
                sid, side_a, cancel_event=cancel,
                web_context=web_ctx, web_sources=web_sources,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


class SummarizeBody(BaseModel):
    provider_id: str
    model_id: str


@router.post("/sessions/{sid}/summarize")
async def post_summarize(sid: str, body: SummarizeBody):
    """Compact a long conversation: ask the model to summarize, then atomically
    replace older history with a single synthetic [历史摘要] user message.

    The most recent assistant message survives so the UI flow stays coherent.
    Frontend calls this when budget hits the 90% mark and the user confirms.
    """
    sess = db.get_session(sid)
    if not sess:
        raise HTTPException(404, "session not found")
    msgs = db.list_messages(sid)
    if len(msgs) < 4:
        raise HTTPException(400, "not enough history to summarize")

    provider = _provider(body.provider_id)
    transcript_lines = []
    for m in msgs[:-1]:  # leave the very last message intact for context continuity
        role = "用户" if m["role"] == "user" else (m.get("speaker") or "助手")
        transcript_lines.append(f"[{role}] {m['content']}")
    transcript = "\n\n".join(transcript_lines)

    from ..providers.base import Message
    from ..providers.registry import make_adapter
    adapter = make_adapter(provider)
    prompt_messages = [
        Message(
            role="system",
            content=(
                "你是会话摘要助手。把下面这段对话压缩成结构化摘要,"
                "保留:议题、关键事实/数据、每方核心观点、达成共识/未决问题。"
                "用中文,不超过 600 字,直接输出摘要正文,不要寒暄。"
            ),
        ),
        Message(role="user", content=transcript),
    ]
    cancel = asyncio.Event()
    summary_parts: list[str] = []
    err: str | None = None
    async for chunk in adapter.stream(prompt_messages, body.model_id, cancel_event=cancel):
        if chunk.error:
            err = chunk.error
            break
        if chunk.delta:
            summary_parts.append(chunk.delta)
        if chunk.done:
            break
    if err:
        raise HTTPException(502, f"summarize failed: {err}")
    summary = "".join(summary_parts).strip()
    if not summary:
        raise HTTPException(502, "summarize produced empty output")

    last_msg = msgs[-1]
    # Replace history atomically: drop everything except the trailing message,
    # prepend a synthetic [历史摘要] user message so future turns have context.
    with db.tx() as c:
        c.execute(
            "DELETE FROM messages WHERE session_id=? AND id<>?",
            (sid, last_msg["id"]),
        )
    summary_msg = db.add_message(
        sid,
        "user",
        f"[历史摘要]\n{summary}",
        meta={"summary": True, "compacted_count": len(msgs) - 1},
    )
    # Push the summary above the surviving message by patching its created_at.
    with db.tx() as c:
        c.execute(
            "UPDATE messages SET created_at=? WHERE id=?",
            (last_msg["created_at"] - 0.001, summary_msg["id"]),
        )
    return {
        "ok": True,
        "summary": summary,
        "compacted_count": len(msgs) - 1,
        "messages": db.list_messages(sid),
    }


@router.post("/sessions/{sid}/cancel")
async def cancel_stream(sid: str):
    ev = _cancels.get(sid)
    if not ev:
        return {"ok": False, "message": "no active stream"}
    ev.set()
    return {"ok": True}


@router.post("/sessions/{sid}/regenerate")
async def post_regenerate(sid: str, body: RegenerateBody):
    """Drop trailing assistant turn(s) since the last user message and re-run.

    Same model by default; pass provider_id/model_id to swap. Only valid in
    chat mode — compare/debate use their own multi-track flow."""
    sess = db.get_session(sid)
    if not sess:
        raise HTTPException(404, "session not found")
    if sess["mode"] != "chat":
        raise HTTPException(400, "regenerate is only supported in chat mode")

    msgs = db.list_messages(sid)
    # Walk back from the end, deleting trailing assistant messages until we
    # hit a user message. The user message stays — it's the prompt to re-run.
    last_user = None
    for m in reversed(msgs):
        if m["role"] == "user":
            last_user = m
            break
        if m["role"] == "assistant":
            db.update_message(m["id"], content="", meta={"deleted": True})
            # Hard-delete so history rebuilt by chat_mode doesn't see the empty turn.
            with db.tx() as c:
                c.execute("DELETE FROM messages WHERE id=?", (m["id"],))
    if not last_user:
        raise HTTPException(400, "no user message to regenerate from")

    # Re-source the prompt from the last user message and remove it too —
    # run_chat re-inserts it fresh. This keeps the message ordering clean.
    user_text = last_user["content"]
    with db.tx() as c:
        c.execute("DELETE FROM messages WHERE id=?", (last_user["id"],))

    # If caller didn't specify provider/model, copy from the most recent
    # assistant message (the one we just deleted in the loop above).
    provider_id = body.provider_id
    model_id = body.model_id
    if not provider_id or not model_id:
        for m in reversed(msgs):
            if m["role"] == "assistant" and m.get("provider_id") and m.get("model_id"):
                provider_id = provider_id or m["provider_id"]
                model_id = model_id or m["model_id"]
                break
    if not provider_id or not model_id:
        raise HTTPException(400, "provider_id and model_id required (no prior assistant to copy from)")

    provider = _provider(provider_id)
    cancel = _claim_stream(sid)
    _remember_pick("chat", {"provider_id": provider_id, "model_id": model_id})

    async def _gen():
        try:
            web_ctx, web_sources, search_events = await _maybe_run_search(user_text, body.web_search)
            for ev in search_events:
                yield ev
            async for ev in chat_mode.run_chat(
                sid, user_text, provider, model_id,
                cancel_event=cancel, web_context=web_ctx, web_sources=web_sources,
            ):
                yield ev
        finally:
            _cancels.pop(sid, None)

    return StreamingResponse(_wrap(_gen()), media_type="text/event-stream")


class AdoptBody(BaseModel):
    message_id: str
    note: str | None = None


@router.post("/sessions/{sid}/adopt")
async def adopt_message(sid: str, body: AdoptBody):
    if not db.get_session(sid):
        raise HTTPException(404, "session not found")
    msg = db.get_message(body.message_id)
    if not msg or msg["session_id"] != sid:
        raise HTTPException(404, "message not found")
    meta = dict(msg.get("meta") or {})
    meta["adopted"] = True
    if body.note:
        meta["adopt_note"] = body.note
    db.update_message(body.message_id, meta=meta)
    db.touch_session(sid)
    return {"ok": True}
