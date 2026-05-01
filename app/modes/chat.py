"""Single-model chat mode."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

from .. import db
from ..providers.base import Message, StreamChunk
from ..providers.registry import make_adapter
from ..utils.attachments import inline_into_prompt, normalize_attachments


def _history(session_id: str) -> list[Message]:
    msgs: list[Message] = []
    for row in db.list_messages(session_id):
        if row["role"] in ("user", "assistant"):
            msgs.append(Message(role=row["role"], content=row["content"]))
    return msgs


async def run_chat(
    session_id: str,
    user_text: str,
    provider_instance: dict,
    model_id: str,
    *,
    cancel_event: asyncio.Event,
    web_context: str | None = None,
    web_sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE-ready dict events for a chat turn."""
    norm_attachments = normalize_attachments(attachments)
    # Persisted user content is the inlined text so the saved transcript is
    # self-contained; the original blocks live in meta.attachments for the UI
    # and any future vision-capable adapter that wants the raw bytes.
    persisted_text = inline_into_prompt(user_text, norm_attachments)
    user_meta = {"attachments": norm_attachments} if norm_attachments else None
    user_msg = db.add_message(session_id, "user", persisted_text, meta=user_meta)
    yield {"event": "user_message", "data": user_msg}

    has_search = bool(web_sources)
    initial_meta = {"web_search": has_search}
    if has_search:
        initial_meta["search_sources"] = web_sources
    placeholder = db.add_message(
        session_id,
        "assistant",
        "",
        speaker=provider_instance.get("name"),
        provider_id=provider_instance.get("id"),
        model_id=model_id,
        meta=initial_meta,
    )
    yield {"event": "assistant_start", "data": {"message_id": placeholder["id"], "speaker": provider_instance.get("name"), "model": model_id, "web_search": has_search, "search_sources": web_sources or []}}

    adapter = make_adapter(provider_instance)
    messages = _history(session_id)
    # Drop the empty placeholder we just created so it isn't fed back to the model.
    if messages and messages[-1].role == "assistant" and not messages[-1].content:
        messages = messages[:-1]
    if web_context:
        messages = [Message(role="system", content=web_context)] + messages

    accumulated = ""
    error_msg: str | None = None
    async for chunk in adapter.stream(messages, model_id, cancel_event=cancel_event):
        if chunk.error:
            error_msg = chunk.error
            yield {"event": "assistant_error", "data": {"message_id": placeholder["id"], "error": chunk.error}}
            break
        if chunk.delta:
            accumulated += chunk.delta
            yield {"event": "assistant_delta", "data": {"message_id": placeholder["id"], "delta": chunk.delta}}
        if chunk.done:
            break
        if cancel_event.is_set():
            break

    meta = {"web_search": has_search}
    if has_search:
        meta["search_sources"] = web_sources
    if error_msg:
        meta["error"] = error_msg
    if cancel_event.is_set():
        meta["cancelled"] = True
    db.update_message(placeholder["id"], content=accumulated, meta=meta)

    yield {
        "event": "assistant_end",
        "data": {"message_id": placeholder["id"], "content": accumulated, "error": error_msg, "cancelled": cancel_event.is_set()},
    }
