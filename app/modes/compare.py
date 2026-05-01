"""Side-by-side comparison mode: same prompt, two models, parallel stream."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

from .. import db
from ..providers.base import Message, StreamChunk
from ..providers.registry import make_adapter
from ..utils.attachments import inline_into_prompt, normalize_attachments


def _history_pairs(session_id: str) -> dict[str, list[Message]]:
    """Build per-track histories: user turns are shared, assistant turns are split by 'speaker'."""
    rows = db.list_messages(session_id)
    tracks: dict[str, list[Message]] = {}

    def ensure(track: str) -> list[Message]:
        return tracks.setdefault(track, [])

    speakers_seen: set[str] = set()
    for row in rows:
        if row["role"] == "assistant":
            sp = row.get("speaker") or row["provider_id"] or "?"
            speakers_seen.add(sp)

    for row in rows:
        if row["role"] == "user":
            for sp in speakers_seen or {"_a", "_b"}:
                ensure(sp).append(Message(role="user", content=row["content"]))
        elif row["role"] == "assistant":
            sp = row.get("speaker") or row["provider_id"] or "?"
            if row["content"]:
                ensure(sp).append(Message(role="assistant", content=row["content"]))
    return tracks


async def _stream_one(
    track_id: str,
    provider_instance: dict,
    model_id: str,
    history: list[Message],
    placeholder_id: str,
    out_q: asyncio.Queue,
    cancel_event: asyncio.Event,
) -> tuple[str, str | None]:
    adapter = make_adapter(provider_instance)
    accumulated = ""
    err: str | None = None
    async for chunk in adapter.stream(history, model_id, cancel_event=cancel_event):
        if chunk.error:
            err = chunk.error
            await out_q.put({"event": "assistant_error", "data": {"track": track_id, "message_id": placeholder_id, "error": chunk.error}})
            break
        if chunk.delta:
            accumulated += chunk.delta
            await out_q.put({"event": "assistant_delta", "data": {"track": track_id, "message_id": placeholder_id, "delta": chunk.delta}})
        if chunk.done:
            break
        if cancel_event.is_set():
            break
    return accumulated, err


async def run_compare(
    session_id: str,
    user_text: str,
    tracks: list[dict],  # [{track_id, provider_instance, model_id}, ...]
    *,
    cancel_event: asyncio.Event,
    web_context: str | None = None,
    web_sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> AsyncIterator[dict]:
    norm_attachments = normalize_attachments(attachments)
    persisted_text = inline_into_prompt(user_text, norm_attachments)
    user_meta = {"attachments": norm_attachments} if norm_attachments else None
    user_msg = db.add_message(session_id, "user", persisted_text, meta=user_meta)
    yield {"event": "user_message", "data": user_msg}

    # Get history excluding any empty placeholders.
    base_history = [
        Message(role=row["role"], content=row["content"])
        for row in db.list_messages(session_id)
        if row["role"] in ("user", "assistant") and (row["role"] != "assistant" or row["content"])
    ]
    if web_context:
        base_history = [Message(role="system", content=web_context)] + base_history
    # base_history already includes the just-saved user turn at the end.

    has_search = bool(web_sources)
    placeholders: list[dict] = []
    for t in tracks:
        meta_init = {"track": t["track_id"], "web_search": has_search}
        if has_search:
            meta_init["search_sources"] = web_sources
        ph = db.add_message(
            session_id,
            "assistant",
            "",
            speaker=t["provider_instance"].get("name"),
            provider_id=t["provider_instance"].get("id"),
            model_id=t["model_id"],
            meta=meta_init,
        )
        placeholders.append(ph)
        yield {
            "event": "assistant_start",
            "data": {"track": t["track_id"], "message_id": ph["id"], "speaker": t["provider_instance"].get("name"), "model": t["model_id"], "web_search": has_search, "search_sources": web_sources or []},
        }

    out_q: asyncio.Queue = asyncio.Queue()

    async def _runner(track, ph):
        return await _stream_one(
            track["track_id"], track["provider_instance"], track["model_id"], base_history, ph["id"], out_q, cancel_event
        )

    tasks = [asyncio.create_task(_runner(t, p)) for t, p in zip(tracks, placeholders)]

    pending = set(tasks)
    while pending:
        get_task = asyncio.create_task(out_q.get())
        done, _ = await asyncio.wait(
            {get_task, *pending}, return_when=asyncio.FIRST_COMPLETED
        )
        if get_task in done:
            yield get_task.result()
        else:
            get_task.cancel()
        pending = {t for t in pending if not t.done()}

    while not out_q.empty():
        yield await out_q.get()

    for t, ph, task in zip(tracks, placeholders, tasks):
        accumulated, err = await task
        meta = {"track": t["track_id"], "web_search": has_search}
        if has_search:
            meta["search_sources"] = web_sources
        if err:
            meta["error"] = err
        if cancel_event.is_set():
            meta["cancelled"] = True
        db.update_message(ph["id"], content=accumulated, meta=meta)
        yield {
            "event": "assistant_end",
            "data": {
                "track": t["track_id"],
                "message_id": ph["id"],
                "content": accumulated,
                "error": err,
                "cancelled": cancel_event.is_set(),
            },
        }
