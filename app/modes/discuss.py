"""Discuss mode: two neutral sides converge toward a shared answer."""
from __future__ import annotations
import asyncio
import re
from typing import AsyncIterator

from .. import db
from ..providers.base import Message
from ..providers.registry import make_adapter
from ..utils.attachments import inline_into_prompt, normalize_attachments

_CONFIDENCE_RE = re.compile(r"【把握度】\s*(\d+)\s*/\s*10")

_FINAL_PROMPT = (
    "讨论结束。基于前面所有轮次,请用 A 方身份输出最终共识总结,严格按以下四段:\n"
    "【概要】一句话最终结论(40 字以内,直接把答案给用户,例如:同意做 X,先用方案 A,3 周内验证 B)。\n"
    "【共识】我们都同意:...\n"
    "【主要分歧】我们没谈拢的:...(若无,写无)\n"
    "【建议给用户的下一步】...\n"
    "仅输出这四段,不要再加其他内容。"
)

_CHECKPOINT_PROMPT = (
    "讨论已经进行完一批轮次。请用 A 方身份输出当前进度,严格按以下四段:\n"
    "【概要】一句话总结进展(40 字以内,例如:已就 X / Y 达成一致,仍剩 Z 这一点要确认,建议先答 W 再继续)。\n"
    "【已统一】我们目前都同意的关键点:...(若无,写无)\n"
    "【仍有分歧】我们还没谈拢的:...\n"
    "【建议】继续讨论可能的方向 / 或者就此打住的理由(让用户判断):...\n"
    "仅输出这四段,不要再加其他内容。"
)


def _confidence(text: str) -> int | None:
    match = _CONFIDENCE_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1))


def _build_messages_for_side(
    topic: str, transcript: list[dict], *, speaker_role: str, self_label: str,
    other_label: str, web_context: str | None = None, final_turn: bool = False,
    checkpoint_turn: bool = False,
) -> list[Message]:
    sys = (
        f"你是{self_label},正在与{other_label}围绕同一问题进行平和、协作的讨论。"
        "目标是逼近最佳答案,不是赢。你可以明确改主意,也应该指出自己被对方修正的地方。"
        "每一轮只能输出以下 6 个 section,不要加其他标题或寒暄:\n"
        "【当前判断】<one-paragraph stance, may evolve across rounds>\n"
        "【把握度】<integer X>/10\n"
        "【支撑】(1) ... (2) ...\n"
        "【被对方修正】<which point you accepted from the other side, or 无>\n"
        "【仍坚持】<what you still hold, with reason, or 无>\n"
        "【需要对方回应】<what you want the other to address, or 无>"
    )
    if web_context:
        sys += f"\n\n可参考的联网资料:\n{web_context}"

    msgs: list[Message] = [Message(role="system", content=sys)]
    msgs.append(Message(role="user", content=f"讨论主题:{topic}"))
    for turn in transcript:
        if turn["speaker_role"] == speaker_role:
            msgs.append(Message(role="assistant", content=turn["content"]))
        else:
            msgs.append(Message(role="user", content=f"[{turn['label']}]: {turn['content']}"))
    if final_turn:
        msgs.append(Message(role="user", content=_FINAL_PROMPT))
    elif checkpoint_turn:
        msgs.append(Message(role="user", content=_CHECKPOINT_PROMPT))
    return msgs


async def _stream_turn(
    session_id: str, speaker_role: str, label: str, provider_instance: dict,
    model_id: str, messages: list[Message], cancel_event: asyncio.Event, *,
    web_sources: list[dict] | None = None, consensus: bool = False, checkpoint: bool = False,
):
    has_search = bool(web_sources)
    meta_init = {"speaker_role": speaker_role, "web_search": has_search}
    if consensus:
        meta_init["consensus"] = True
    if checkpoint:
        meta_init["checkpoint"] = True
    if has_search:
        meta_init["search_sources"] = web_sources
    placeholder = db.add_message(
        session_id, "assistant", "", speaker=label,
        provider_id=provider_instance.get("id"), model_id=model_id, meta=meta_init,
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
        if chunk.done or cancel_event.is_set():
            break

    meta = meta_init.copy()
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


def rebuild_transcript(session_id: str) -> tuple[str, list[dict]]:
    """Reconstruct the (topic, [{speaker_role,label,content}, ...]) tuple from
    persisted messages so /discuss/continue and /discuss/finalize can pick up
    where the prior batch of rounds left off.

    Topic = the first user message in the session (subsequent user messages
    are unrelated — they shouldn't exist in discuss mode anyway).
    Transcript only includes assistant messages with speaker_role in (a, b);
    consensus and checkpoint turns are skipped because they're not part of
    the model-to-model exchange.
    """
    msgs = db.list_messages(session_id)
    topic = next((m["content"] for m in msgs if m["role"] == "user"), "")
    transcript: list[dict] = []
    for m in msgs:
        if m["role"] != "assistant":
            continue
        meta = m.get("meta") or {}
        sr = meta.get("speaker_role")
        if sr in ("a", "b"):
            transcript.append({
                "speaker_role": sr,
                "label": m.get("speaker") or ("A 方" if sr == "a" else "B 方"),
                "content": m.get("content") or "",
            })
    return topic, transcript


async def _run_rounds(
    session_id: str, topic: str, transcript: list[dict],
    side_a: dict, side_b: dict, *, rounds: int, a_label: str, b_label: str,
    cancel_event: asyncio.Event, web_context: str | None, web_sources: list[dict] | None,
) -> AsyncIterator[dict]:
    """Yield SSE events for `rounds` exchanges, mutating `transcript` in place.

    Returns early via converged sentinel — caller checks the last entries' 把握度.
    """
    for r in range(rounds):
        yield {"event": "round_start", "data": {"round": r + 1, "of": rounds}}
        if cancel_event.is_set():
            return

        msgs_a = _build_messages_for_side(
            topic, transcript, speaker_role="a", self_label=a_label, other_label=b_label, web_context=web_context,
        )
        a_text = ""
        async for ev in _stream_turn(
            session_id, "a", a_label, side_a["provider_instance"], side_a["model_id"],
            msgs_a, cancel_event, web_sources=web_sources,
        ):
            if ev["event"] == "assistant_end":
                a_text = ev["data"]["content"]
            yield ev
        transcript.append({"speaker_role": "a", "label": a_label, "content": a_text})
        if cancel_event.is_set():
            return

        msgs_b = _build_messages_for_side(
            topic, transcript, speaker_role="b", self_label=b_label, other_label=a_label, web_context=web_context,
        )
        b_text = ""
        async for ev in _stream_turn(
            session_id, "b", b_label, side_b["provider_instance"], side_b["model_id"],
            msgs_b, cancel_event, web_sources=web_sources,
        ):
            if ev["event"] == "assistant_end":
                b_text = ev["data"]["content"]
            yield ev
        transcript.append({"speaker_role": "b", "label": b_label, "content": b_text})

        a_conf = _confidence(a_text)
        b_conf = _confidence(b_text)
        if a_conf is not None and b_conf is not None and a_conf >= 8 and b_conf >= 8:
            return  # caller detects convergence by re-checking last entries
        if cancel_event.is_set():
            return


def _has_converged(transcript: list[dict]) -> bool:
    """Both most-recent A and B turns report 把握度 ≥ 8."""
    if len(transcript) < 2:
        return False
    last_a = next((t for t in reversed(transcript) if t["speaker_role"] == "a"), None)
    last_b = next((t for t in reversed(transcript) if t["speaker_role"] == "b"), None)
    if not last_a or not last_b:
        return False
    a_conf = _confidence(last_a["content"])
    b_conf = _confidence(last_b["content"])
    return a_conf is not None and b_conf is not None and a_conf >= 8 and b_conf >= 8


async def _emit_consensus(
    session_id: str, topic: str, transcript: list[dict], side_a: dict, *,
    a_label: str, b_label: str, cancel_event: asyncio.Event,
    web_context: str | None, web_sources: list[dict] | None,
) -> AsyncIterator[dict]:
    msgs_final = _build_messages_for_side(
        topic, transcript, speaker_role="a", self_label=a_label, other_label=b_label,
        web_context=web_context, final_turn=True,
    )
    async for ev in _stream_turn(
        session_id, "consensus", a_label, side_a["provider_instance"], side_a["model_id"],
        msgs_final, cancel_event, web_sources=web_sources, consensus=True,
    ):
        yield ev


async def _emit_checkpoint(
    session_id: str, topic: str, transcript: list[dict], side_a: dict, *,
    a_label: str, b_label: str, cancel_event: asyncio.Event,
    web_context: str | None, web_sources: list[dict] | None,
) -> AsyncIterator[dict]:
    msgs_cp = _build_messages_for_side(
        topic, transcript, speaker_role="a", self_label=a_label, other_label=b_label,
        web_context=web_context, checkpoint_turn=True,
    )
    async for ev in _stream_turn(
        session_id, "checkpoint", a_label, side_a["provider_instance"], side_a["model_id"],
        msgs_cp, cancel_event, web_sources=web_sources, checkpoint=True,
    ):
        yield ev


async def run_discuss(
    session_id: str, topic: str, side_a: dict, side_b: dict, max_rounds: int = 3,
    *, cancel_event: asyncio.Event, web_context: str | None = None,
    web_sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Initial discuss run: persist topic, run rounds, emit either consensus
    (auto-converged) or checkpoint (rounds cap hit, awaiting user)."""
    # Attachments attach to the shared topic; both sides see them via the
    # `讨论主题:{topic}` line _build_messages_for_side emits.
    norm_attachments = normalize_attachments(attachments)
    persisted_topic = inline_into_prompt(topic, norm_attachments)
    user_meta = {"attachments": norm_attachments} if norm_attachments else None
    user_msg = db.add_message(session_id, "user", persisted_topic, meta=user_meta)
    yield {"event": "user_message", "data": user_msg}
    topic = persisted_topic

    rounds = max(1, min(int(max_rounds or 3), 5))
    a_label = side_a.get("label") or "A 方"
    b_label = side_b.get("label") or "B 方"
    transcript: list[dict] = []

    async for ev in _run_rounds(
        session_id, topic, transcript, side_a, side_b,
        rounds=rounds, a_label=a_label, b_label=b_label,
        cancel_event=cancel_event, web_context=web_context, web_sources=web_sources,
    ):
        yield ev

    if cancel_event.is_set():
        yield {"event": "discuss_end", "data": {"rounds_completed": len(transcript) // 2, "converged": False, "stage": "cancelled"}}
        return

    # Always emit a checkpoint — even if 把握度 已经都 ≥ 8 — so the user
    # decides whether to stop or keep going. converged: true is reported
    # in the SSE event so the UI can hint "可以收尾了" without forcing it.
    converged = _has_converged(transcript)
    async for ev in _emit_checkpoint(
        session_id, topic, transcript, side_a, a_label=a_label, b_label=b_label,
        cancel_event=cancel_event, web_context=web_context, web_sources=web_sources,
    ):
        yield ev
    yield {"event": "discuss_end", "data": {"rounds_completed": len(transcript) // 2, "converged": converged, "stage": "checkpoint"}}


async def continue_discuss(
    session_id: str, side_a: dict, side_b: dict, extra_rounds: int = 3,
    *, cancel_event: asyncio.Event, web_context: str | None = None,
    web_sources: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Resume after a checkpoint: rebuild transcript from db, run another
    extra_rounds, then ALWAYS emit another checkpoint. User is the only
    one who decides when to stop and write the consensus."""
    topic, transcript = rebuild_transcript(session_id)
    if not topic:
        yield {"event": "fatal", "data": {"error": "no prior discuss topic in this session"}}
        return

    rounds = max(1, min(int(extra_rounds or 3), 5))
    a_label = side_a.get("label") or "A 方"
    b_label = side_b.get("label") or "B 方"

    async for ev in _run_rounds(
        session_id, topic, transcript, side_a, side_b,
        rounds=rounds, a_label=a_label, b_label=b_label,
        cancel_event=cancel_event, web_context=web_context, web_sources=web_sources,
    ):
        yield ev

    if cancel_event.is_set():
        yield {"event": "discuss_end", "data": {"rounds_completed": len(transcript) // 2, "converged": False, "stage": "cancelled"}}
        return

    converged = _has_converged(transcript)
    async for ev in _emit_checkpoint(
        session_id, topic, transcript, side_a, a_label=a_label, b_label=b_label,
        cancel_event=cancel_event, web_context=web_context, web_sources=web_sources,
    ):
        yield ev
    yield {"event": "discuss_end", "data": {"rounds_completed": len(transcript) // 2, "converged": converged, "stage": "checkpoint"}}


async def finalize_discuss(
    session_id: str, side_a: dict,
    *, cancel_event: asyncio.Event, web_context: str | None = None,
    web_sources: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """User clicked "到此为止" at a checkpoint — emit the consensus turn
    using whatever transcript already exists, even if not auto-converged."""
    topic, transcript = rebuild_transcript(session_id)
    if not topic or not transcript:
        yield {"event": "fatal", "data": {"error": "no prior discuss to finalize"}}
        return

    a_label = side_a.get("label") or "A 方"
    b_label = side_a.get("label") or "B 方"  # only A speaks here; b_label is for the prompt template
    async for ev in _emit_consensus(
        session_id, topic, transcript, side_a, a_label=a_label, b_label=b_label,
        cancel_event=cancel_event, web_context=web_context, web_sources=web_sources,
    ):
        yield ev
    yield {"event": "discuss_end", "data": {"rounds_completed": len(transcript) // 2, "converged": False, "stage": "consensus"}}
