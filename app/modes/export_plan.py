"""Export-plan pipeline: distill a finished discussion into a plan document.

Flow (docs/DESIGN-EXPORT-PLAN.md):
  transcript -> writer drafts (visible turn) -> reviewer verdicts (visible turn)
  -> up to MAX_REWRITES rewrite cycles -> PASS: write ASK-PLAN-*.md into the
  target project's docs/ and post a success message; REJECT: post the
  objections, write nothing — the fix loop is more discussion, not a stamped
  defective file.

The written file is the ONLY filesystem side effect. Target paths are
validated to live strictly under $HOME and outside Ask's own data dir.
"""
from __future__ import annotations
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

from .. import db
from .. import settings
from ..providers.base import Message
from ..providers.registry import make_adapter
from ..utils import notifier

# Char thresholds sized for the smallest common context window (~128k tokens):
# CJK text runs ~1 token/char, so 100k chars leaves room for prompts + output.
CHUNK_THRESHOLD_CHARS = 100_000
CHUNK_SIZE_CHARS = 50_000
MAX_REWRITES = 2

WRITER_LABEL = "撰稿人"
REVIEWER_LABEL = "审稿人"
RESULT_LABEL = "出计划"

_VERDICT_PASS = "通过"
_VERDICT_REJECT = "退稿"
_VERDICT_RE = re.compile(r"【裁决】\s*(通过|退稿)")

_WRITER_SYSTEM = (
    "你是「撰稿人」。任务:把一场多模型讨论的完整记录,提炼成一份可以直接指挥"
    "编码 agent(如 Claude Code / Codex)开工的计划书。\n\n"
    "铁律:\n"
    "1. 只提炼讨论中真实出现的内容。禁止编造讨论之外的结论、数字、方案。\n"
    "2. 讨论中没谈拢的问题,必须如实放进「未定分歧」,绝不允许写成已定结论。\n"
    "3. 执行计划要具体到编码 agent 拿到就能动手:做什么、改哪里(如果讨论提到)、"
    "先后顺序。讨论没细到这个程度的,就写到讨论实际到达的深度,并在未定分歧里注明。\n"
    "4. 纯结论型讨论(没有可执行事项)时,「执行计划」一节写「无——本场为结论/调研型讨论」。\n"
    "5. 正文用中文;代码、命令、文件名、API 名保留英文。\n"
    "6. 直接输出 Markdown 正文,不要用代码块把整份文档包起来,不要写任何前言、后语、致意。\n\n"
    "结构(严格按此七节):\n"
    "# <标题>\n"
    "## 1. 背景与目标\n"
    "## 2. 已定结论\n"
    "(每条附依据:讨论中谁提出、为何成立)\n"
    "## 3. 未定分歧\n"
    "(每条写清分歧是什么、各方立场;若无写「无」)\n"
    "## 4. 执行计划\n"
    "(有序步骤,每步可验证)\n"
    "## 5. 风险与边界\n"
    "## 6. 验收标准\n"
    "## 7. 来源\n"
    "(一句话说明提炼自哪场会话、什么模式)"
)

_REVIEWER_SYSTEM = (
    "你是「审稿人」。一份计划书草稿声称提炼自一场多模型讨论。你的任务:对照讨论原文,"
    "判断这份草稿是否忠实、是否够格拿去指挥编码 agent 开工。\n\n"
    "逐项核查:\n"
    "1. 漏报:讨论中达成的重要结论,草稿漏了没有?\n"
    "2. 越权定案:讨论中没谈拢的分歧,草稿写成定论了没有?(最严重的错误)\n"
    "3. 无中生有:草稿是否夹带讨论中不存在的结论、数字、方案?\n"
    "4. 不可执行:执行步骤是否含糊到编码 agent 没法动手?\n\n"
    "裁决规则:\n"
    "- 你回复的第一行必须是:【裁决】通过 或 【裁决】退稿\n"
    "- 退稿时列编号意见,每条指出:问题类型(上述 1-4)、草稿中的位置、讨论原文依据。\n"
    "- 只有实质问题才退稿;文风、措辞、格式的小瑕疵不构成退稿理由。\n"
    "- 实质问题拿不准时倾向退稿:宁可再讨论一轮,不放走一份带病图纸。"
)

_CHUNK_SYSTEM = (
    "你是讨论记录压缩器。把下面这段多模型讨论记录压缩成要点清单,必须保留:"
    "各方结论、明确的分歧、关键论据、出现过的数字/文件名/命令/API 名。"
    "禁止新增内容。直接输出要点清单。"
)


# ---------------------------------------------------------------- validation

def list_projects() -> list[dict]:
    """Top-level non-hidden directories of ~/Projects (may not exist)."""
    root = Path.home() / "Projects"
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and not p.name.startswith("."):
            out.append({"name": p.name, "path": str(p)})
    return out


def validate_project_path(raw: str) -> tuple[Optional[Path], Optional[str]]:
    """Resolve and vet an export target. Returns (path, None) or (None, reason)."""
    raw = (raw or "").strip()
    if not raw:
        return None, "目标路径为空"
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None, "路径无法解析"
    home = Path.home().resolve()
    if not p.exists():
        return None, "目标文件夹不存在"
    if not p.is_dir():
        return None, "目标不是文件夹"
    if p == home:
        return None, "不能直接写到家目录,请选一个项目文件夹"
    if home not in p.parents:
        return None, "只允许写入你家目录下的文件夹"
    # Attribute lookup at call time so tests reloading app.settings see the
    # isolated data dir (import-time `from ..settings import DATA_DIR` would
    # pin the original value).
    data_dir = settings.DATA_DIR.resolve()
    if p == data_dir or data_dir in p.parents:
        return None, "不能写入 Ask 自己的数据目录"
    return p, None


def _slugify(text: str) -> str:
    """Filename-safe slug: keep word chars + CJK, spaces to '-', cap at 40."""
    text = (text or "").strip()
    text = re.sub(r"[\s]+", "-", text)
    text = re.sub(r"[^\w一-鿿-]", "", text)
    text = text.strip("-_")
    return text[:40] or "plan"


def _target_file(project: Path, slug: str, when: datetime) -> Path:
    docs = project / "docs"
    docs.mkdir(exist_ok=True)
    base = f"ASK-PLAN-{when:%Y%m%d}-{slug}"
    candidate = docs / f"{base}.md"
    i = 2
    while candidate.exists():
        candidate = docs / f"{base}-{i}.md"
        i += 1
    return candidate


# ---------------------------------------------------------------- transcript

def build_source_transcript(session_id: str) -> str:
    """All user/assistant turns as labeled lines. Prior export writer drafts
    and result cards are excluded (they would anchor the next draft); prior
    reviewer objections stay in — they drove the re-discussion."""
    lines: list[str] = []
    for m in db.list_messages(session_id):
        content = (m.get("content") or "").strip()
        if not content:
            continue
        meta = m.get("meta") or {}
        if meta.get("export_role") in ("writer", "result"):
            continue
        if m["role"] == "user":
            lines.append(f"[用户]: {content}")
        elif m["role"] == "assistant":
            label = m.get("speaker") or m.get("model_id") or "助手"
            lines.append(f"[{label}]: {content}")
    return "\n\n".join(lines)


def _split_chunks(text: str, size: int = CHUNK_SIZE_CHARS) -> list[str]:
    """Split on turn boundaries, keeping each chunk under `size` chars."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in text.split("\n\n"):
        block_len = len(block) + 2
        if current and current_len + block_len > size:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(block)
        current_len += block_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------- verdict

def parse_verdict(text: str) -> tuple[bool, str]:
    """Returns (passed, issues). Missing verdict line is reject-safe."""
    match = _VERDICT_RE.search(text or "")
    if not match:
        return False, (text or "").strip() or "审稿人未给出【裁决】行,按退稿处理。"
    passed = match.group(1) == _VERDICT_PASS
    issues = (text[match.end():] if not passed else "").strip()
    return passed, issues


def pending_review_note(session_id: str) -> Optional[str]:
    """Objections from a final export rejection that postdates the last a/b
    discuss turn — /discuss/continue feeds this back into the next rounds."""
    last_reject: Optional[dict] = None
    last_ab_at = 0.0
    for m in db.list_messages(session_id):
        meta = m.get("meta") or {}
        if meta.get("speaker_role") in ("a", "b"):
            last_ab_at = m.get("created_at") or 0.0
        result = meta.get("export_result") or {}
        if result.get("status") == "rejected":
            last_reject = m
    if last_reject and (last_reject.get("created_at") or 0.0) > last_ab_at:
        content = (last_reject.get("content") or "").strip()
        return content or None
    return None


# ---------------------------------------------------------------- documents

def _strip_md_fence(text: str) -> str:
    """Unwrap a draft the model wrapped in one big ``` fence."""
    t = (text or "").strip()
    m = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", t, flags=re.DOTALL)
    return m.group(1).strip() if m else t


def _banner(session: dict, writer_model: str, reviewer_model: str, when: datetime) -> str:
    title = session.get("title") or "未命名会话"
    mode = session.get("mode") or "chat"
    return (
        "> ⚠️ 内部工作文件 (internal working file) — do NOT commit or publish.\n"
        f"> Source: Ask session \"{title}\" · mode {mode} · writer {writer_model}"
        f" · reviewer {reviewer_model} · {when:%Y-%m-%d %H:%M}\n"
    )


def build_spell(path: Path) -> str:
    return (
        f"读 {path}，按计划开干。计划与实际代码冲突时以实际为准，先报告再动手。"
        "这份文件是内部工作底稿，不要 commit 进任何仓库。"
    )


# ---------------------------------------------------------------- streaming

async def _stream_visible_turn(
    session_id: str, role_key: str, label: str, provider_instance: dict,
    model_id: str, messages: list[Message], cancel_event: asyncio.Event,
) -> AsyncIterator[dict]:
    """One visible bubble (writer or reviewer turn). Mirrors discuss._stream_turn."""
    meta_init = {"export_role": role_key}
    placeholder = db.add_message(
        session_id, "assistant", "", speaker=label,
        provider_id=provider_instance.get("id"), model_id=model_id, meta=meta_init,
    )
    yield {
        "event": "assistant_start",
        "data": {
            "message_id": placeholder["id"], "label": label, "speaker": label,
            "model": model_id, "export_role": role_key,
        },
    }
    adapter = make_adapter(provider_instance)
    accumulated = ""
    err: str | None = None
    async for chunk in adapter.stream(messages, model_id, cancel_event=cancel_event):
        if chunk.error:
            err = chunk.error
            yield {"event": "assistant_error", "data": {"message_id": placeholder["id"], "error": err}}
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
            "message_id": placeholder["id"], "label": label, "content": accumulated,
            "error": err, "cancelled": cancel_event.is_set(), "export_role": role_key,
        },
    }


async def _collect(provider_instance: dict, model_id: str, messages: list[Message],
                   cancel_event: asyncio.Event) -> tuple[str, Optional[str]]:
    """Non-visible model call (chunk pre-summaries). Returns (text, error)."""
    adapter = make_adapter(provider_instance)
    accumulated = ""
    async for chunk in adapter.stream(messages, model_id, cancel_event=cancel_event):
        if chunk.error:
            return accumulated, chunk.error
        if chunk.delta:
            accumulated += chunk.delta
        if chunk.done or cancel_event.is_set():
            break
    return accumulated, None


def _writer_messages(session: dict, transcript: str, prev_draft: Optional[str],
                     objections: Optional[str]) -> list[Message]:
    title = session.get("title") or "未命名会话"
    mode = session.get("mode") or "chat"
    parts = [f"会话标题:{title}\n模式:{mode}\n\n=== 讨论原文 ===\n{transcript}"]
    if prev_draft and objections:
        parts.append(
            "=== 你上一版草稿 ===\n" + prev_draft +
            "\n\n=== 审稿人退稿意见(必须逐条解决) ===\n" + objections +
            "\n\n请重写完整计划书(全文输出,不是补丁)。"
        )
    else:
        parts.append("请输出计划书全文。")
    return [
        Message(role="system", content=_WRITER_SYSTEM),
        Message(role="user", content="\n\n".join(parts)),
    ]


def _reviewer_messages(transcript: str, draft: str) -> list[Message]:
    return [
        Message(role="system", content=_REVIEWER_SYSTEM),
        Message(role="user", content=(
            f"=== 讨论原文 ===\n{transcript}\n\n=== 计划书草稿 ===\n{draft}\n\n请裁决。"
        )),
    ]


# ---------------------------------------------------------------- pipeline

async def run_export(
    session_id: str,
    project_path: str,
    title: Optional[str],
    writer: dict,      # {"provider_instance": dict, "model_id": str}
    reviewer: dict,
    cancel_event: asyncio.Event,
) -> AsyncIterator[dict]:
    session = db.get_session(session_id)
    if not session:
        yield {"event": "export_error", "data": {"error": "session not found"}}
        return
    project, reason = validate_project_path(project_path)
    if project is None:
        yield {"event": "export_error", "data": {"error": reason}}
        return

    transcript = build_source_transcript(session_id)
    if not transcript.strip():
        yield {"event": "export_error", "data": {"error": "会话里还没有可提炼的内容"}}
        return

    # Very long discussions: map-reduce pre-summary with the writer model.
    if len(transcript) > CHUNK_THRESHOLD_CHARS:
        chunks = _split_chunks(transcript)
        summaries: list[str] = []
        for i, chunk in enumerate(chunks):
            yield {"event": "export_status", "data": {"stage": "chunking", "current": i + 1, "total": len(chunks)}}
            summary, err = await _collect(
                writer["provider_instance"], writer["model_id"],
                [Message(role="system", content=_CHUNK_SYSTEM), Message(role="user", content=chunk)],
                cancel_event,
            )
            if err:
                yield {"event": "export_error", "data": {"error": f"讨论记录压缩失败: {err}"}}
                return
            if cancel_event.is_set():
                return
            summaries.append(f"[第 {i + 1}/{len(chunks)} 段要点]\n{summary.strip()}")
        transcript = (
            "(原讨论过长,以下为分段压缩后的要点记录)\n\n" + "\n\n".join(summaries)
        )

    draft: Optional[str] = None
    objections: Optional[str] = None
    passed = False
    for attempt in range(1, MAX_REWRITES + 2):  # initial draft + MAX_REWRITES rewrites
        yield {"event": "export_status", "data": {"stage": "draft", "attempt": attempt}}
        new_draft = ""
        turn_err = None
        async for ev in _stream_visible_turn(
            session_id, "writer", WRITER_LABEL,
            writer["provider_instance"], writer["model_id"],
            _writer_messages(session, transcript, draft, objections), cancel_event,
        ):
            if ev["event"] == "assistant_end":
                new_draft = ev["data"]["content"]
                turn_err = ev["data"]["error"]
            yield ev
        if cancel_event.is_set():
            return
        if turn_err or not new_draft.strip():
            yield {"event": "export_error", "data": {"error": turn_err or "撰稿人没有产出内容"}}
            return
        draft = _strip_md_fence(new_draft)

        yield {"event": "export_status", "data": {"stage": "review", "attempt": attempt}}
        verdict_text = ""
        turn_err = None
        async for ev in _stream_visible_turn(
            session_id, "reviewer", REVIEWER_LABEL,
            reviewer["provider_instance"], reviewer["model_id"],
            _reviewer_messages(transcript, draft), cancel_event,
        ):
            if ev["event"] == "assistant_end":
                verdict_text = ev["data"]["content"]
                turn_err = ev["data"]["error"]
            yield ev
        if cancel_event.is_set():
            return
        if turn_err or not verdict_text.strip():
            yield {"event": "export_error", "data": {"error": turn_err or "审稿人没有产出内容"}}
            return
        passed, issues = parse_verdict(verdict_text)
        if passed:
            break
        objections = issues or verdict_text

    if not passed:
        content = (
            f"❌ 审稿未通过({MAX_REWRITES + 1} 稿仍有实质问题),本次不落盘。\n\n"
            f"审稿人最终意见:\n\n{objections}\n\n"
            "建议:带着这些意见继续讨论,谈拢后再出一次计划。"
        )
        msg = db.add_message(
            session_id, "assistant", content, speaker=RESULT_LABEL,
            meta={"export_role": "result", "export_result": {"status": "rejected"}},
        )
        yield {"event": "assistant_start", "data": {"message_id": msg["id"], "label": RESULT_LABEL, "speaker": RESULT_LABEL, "export_role": "result"}}
        yield {"event": "assistant_end", "data": {"message_id": msg["id"], "label": RESULT_LABEL, "content": content, "error": None, "cancelled": False, "export_role": "result"}}
        yield {"event": "export_rejected", "data": {"issues": objections, "message_id": msg["id"]}}
        return

    when = datetime.now()
    slug = _slugify(title or session.get("title") or "plan")
    target = _target_file(project, slug, when)
    body = _banner(session, writer["model_id"], reviewer["model_id"], when) + "\n" + (draft or "") + "\n"
    try:
        with open(target, "x", encoding="utf-8") as f:
            f.write(body)
    except OSError as e:
        yield {"event": "export_error", "data": {"error": f"写文件失败: {e}"}}
        return

    spell = build_spell(target)
    content = (
        f"✅ 计划书已写入:\n\n`{target}`\n\n"
        f"开工咒语(复制到 Claude Code / Codex 即可开干):\n\n> {spell}"
    )
    msg = db.add_message(
        session_id, "assistant", content, speaker=RESULT_LABEL,
        meta={
            "export_role": "result",
            "export_result": {"status": "done", "path": str(target), "spell": spell},
        },
    )
    yield {"event": "assistant_start", "data": {"message_id": msg["id"], "label": RESULT_LABEL, "speaker": RESULT_LABEL, "export_role": "result"}}
    yield {"event": "assistant_end", "data": {"message_id": msg["id"], "label": RESULT_LABEL, "content": content, "error": None, "cancelled": False, "export_role": "result"}}
    yield {"event": "export_done", "data": {"path": str(target), "spell": spell, "message_id": msg["id"]}}
    notifier.notify("出计划完成", f"{target.name} 已写入 {project.name}")
