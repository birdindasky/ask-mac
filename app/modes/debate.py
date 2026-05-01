"""Debate mode: two models, lead+critic or symmetric, multi-round."""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

from .. import db
from ..providers.base import Message
from ..providers.registry import make_adapter
from ..utils.attachments import inline_into_prompt, normalize_attachments


def _build_messages_for_role(
    topic: str,
    transcript: list[dict],
    *,
    speaker_role: str,
    other_label: str,
    self_label: str,
    sub_mode: str,
    self_stance: str | None = None,
) -> list[Message]:
    """Construct a per-speaker view of the debate so far."""
    if sub_mode == "lead_critic":
        if speaker_role == "lead":
            sys = (
                "你是 Lead 主辩,任务是提出并坚决论证你的立场。"
                "面对 Critic 的反驳要回应、修正,但不要轻易倒戈。用中文,有结构、有论据。"
            )
        else:
            sys = (
                "你是 Critic 反方,任务是找 Lead 立场的漏洞,提出反例与质疑。"
                "保持锐利但讲理,用中文,简明扼要。"
            )
    else:  # symmetric
        sys = (
            f"你是辩手 {self_label},与 {other_label} 就同一议题展开多轮辩论。"
            "你的目标是阐明并维护自己的立场,同时认真回应对方的论点。用中文,逻辑清晰。"
        )
    if self_stance:
        sys += f"\n\n你的预设立场:{self_stance}\n请围绕这个立场展开论证,即便对方观点听起来合理也不要轻易转向。"

    msgs: list[Message] = [Message(role="system", content=sys)]

    intro = f"议题:{topic}"
    msgs.append(Message(role="user", content=intro))
    for turn in transcript:
        if turn["speaker_role"] == speaker_role:
            msgs.append(Message(role="assistant", content=turn["content"]))
        else:
            other_intro = f"[{turn['label']}]: {turn['content']}"
            msgs.append(Message(role="user", content=other_intro))
    return msgs


async def _stream_turn(
    session_id: str,
    speaker_role: str,
    label: str,
    provider_instance: dict,
    model_id: str,
    messages: list[Message],
    cancel_event: asyncio.Event,
    web_sources: list[dict] | None = None,
):
    has_search = bool(web_sources)
    meta_init = {"speaker_role": speaker_role, "web_search": has_search}
    if has_search:
        meta_init["search_sources"] = web_sources
    placeholder = db.add_message(
        session_id,
        "assistant",
        "",
        speaker=label,
        provider_id=provider_instance.get("id"),
        model_id=model_id,
        meta=meta_init,
    )
    yield {
        "event": "assistant_start",
        "data": {
            "message_id": placeholder["id"],
            "speaker_role": speaker_role,
            "label": label,
            "model": model_id,
            "web_search": has_search,
            "search_sources": web_sources or [],
        },
    }

    adapter = make_adapter(provider_instance)
    accumulated = ""
    err: str | None = None
    async for chunk in adapter.stream(messages, model_id, cancel_event=cancel_event):
        if chunk.error:
            err = chunk.error
            yield {"event": "assistant_error", "data": {"message_id": placeholder["id"], "error": chunk.error}}
            break
        if chunk.delta:
            accumulated += chunk.delta
            yield {"event": "assistant_delta", "data": {"message_id": placeholder["id"], "delta": chunk.delta}}
        if chunk.done:
            break
        if cancel_event.is_set():
            break

    meta = {"speaker_role": speaker_role, "web_search": has_search}
    if has_search:
        meta["search_sources"] = web_sources
    if err:
        meta["error"] = err
    if cancel_event.is_set():
        meta["cancelled"] = True
    db.update_message(placeholder["id"], content=accumulated, meta=meta)
    yield {
        "event": "assistant_end",
        "data": {
            "message_id": placeholder["id"],
            "speaker_role": speaker_role,
            "label": label,
            "content": accumulated,
            "error": err,
            "cancelled": cancel_event.is_set(),
        },
    }


async def run_debate(
    session_id: str,
    topic: str,
    sub_mode: str,  # "lead_critic" | "symmetric"
    side_a: dict,  # {provider_instance, model_id, label}
    side_b: dict,
    rounds: int = 2,
    *,
    cancel_event: asyncio.Event,
    web_context: str | None = None,
    web_sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> AsyncIterator[dict]:
    # Attachments attach to the shared topic — both sides see the same intro,
    # so inlining into `topic` flows to both _build_messages_for_role calls.
    norm_attachments = normalize_attachments(attachments)
    persisted_topic = inline_into_prompt(topic, norm_attachments)
    user_meta = {"attachments": norm_attachments} if norm_attachments else None
    user_msg = db.add_message(session_id, "user", persisted_topic, meta=user_meta)
    yield {"event": "user_message", "data": user_msg}
    topic = persisted_topic  # downstream prompt builders see the inlined version

    transcript: list[dict] = []
    # Web context (if any) is folded into the system message for both sides.
    extra_system = web_context or ""
    has_search = bool(web_sources)
    a_role = "lead" if sub_mode == "lead_critic" else "a"
    b_role = "critic" if sub_mode == "lead_critic" else "b"
    a_label = side_a.get("label") or ("Lead" if sub_mode == "lead_critic" else "辩手 A")
    b_label = side_b.get("label") or ("Critic" if sub_mode == "lead_critic" else "辩手 B")

    for r in range(rounds):
        yield {"event": "round_start", "data": {"round": r + 1, "of": rounds}}
        if cancel_event.is_set():
            break
        # A speaks
        msgs_a = _build_messages_for_role(
            topic,
            transcript,
            speaker_role=a_role,
            other_label=b_label,
            self_label=a_label,
            sub_mode=sub_mode,
            self_stance=side_a.get("stance"),
        )
        if extra_system:
            msgs_a = [Message(role="system", content=extra_system)] + msgs_a
        a_text = ""
        async for ev in _stream_turn(
            session_id, a_role, a_label, side_a["provider_instance"], side_a["model_id"], msgs_a, cancel_event, web_sources=web_sources,
        ):
            if ev["event"] == "assistant_end":
                a_text = ev["data"]["content"]
            yield ev
        transcript.append({"speaker_role": a_role, "label": a_label, "content": a_text})
        if cancel_event.is_set():
            break

        # B speaks
        msgs_b = _build_messages_for_role(
            topic,
            transcript,
            speaker_role=b_role,
            other_label=a_label,
            self_label=b_label,
            sub_mode=sub_mode,
            self_stance=side_b.get("stance"),
        )
        if extra_system:
            msgs_b = [Message(role="system", content=extra_system)] + msgs_b
        b_text = ""
        async for ev in _stream_turn(
            session_id, b_role, b_label, side_b["provider_instance"], side_b["model_id"], msgs_b, cancel_event, web_sources=web_sources,
        ):
            if ev["event"] == "assistant_end":
                b_text = ev["data"]["content"]
            yield ev
        transcript.append({"speaker_role": b_role, "label": b_label, "content": b_text})
        if cancel_event.is_set():
            break

    yield {"event": "debate_end", "data": {"rounds_completed": len(transcript) // 2}}
