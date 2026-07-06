"""Export-plan pipeline tests: path safety, transcript build, verdict parse,
and the full writer→reviewer loop with fake adapters (pass / reject / retry)."""
from __future__ import annotations
import asyncio
from pathlib import Path

import pytest

from app import db
from app.modes import export_plan
from app.providers.base import Message, StreamChunk


# ---------------------------------------------------------------- helpers

@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """Point Path.home() at tmp_path so path-safety tests never touch $HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _mk_project(home: Path, name: str = "proj") -> Path:
    p = home / "Projects" / name
    p.mkdir(parents=True)
    return p


class FakeAdapter:
    """Scripted adapter: routes on system prompt (writer/reviewer/chunker)."""

    def __init__(self, writer_texts: list[str], reviewer_texts: list[str],
                 chunk_text: str = "要点"):
        self.writer_texts = list(writer_texts)
        self.reviewer_texts = list(reviewer_texts)
        self.chunk_text = chunk_text
        self.calls: list[str] = []

    async def stream(self, messages: list[Message], model_id: str, *,
                     cancel_event=None, system=None):
        sys_prompt = next((m.content for m in messages if m.role == "system"), "")
        if "撰稿人" in sys_prompt:
            self.calls.append("writer")
            text = self.writer_texts.pop(0)
        elif "审稿人" in sys_prompt:
            self.calls.append("reviewer")
            text = self.reviewer_texts.pop(0)
        else:
            self.calls.append("chunk")
            text = self.chunk_text
        yield StreamChunk(delta=text)
        yield StreamChunk(done=True)


DRAFT = "# 计划\n## 1. 背景与目标\nx\n## 3. 未定分歧\n无\n"
PASS = "【裁决】通过"
REJECT = "【裁决】退稿\n1. 漏了结论 A"


def _run(session_id, project, adapter, title=None):
    async def _go():
        events = []
        async for ev in export_plan.run_export(
            session_id, str(project), title,
            {"provider_instance": {"id": "w", "kind": "fake"}, "model_id": "wm"},
            {"provider_instance": {"id": "r", "kind": "fake"}, "model_id": "rm"},
            cancel_event=asyncio.Event(),
        ):
            events.append(ev)
        return events
    return asyncio.run(_go())


@pytest.fixture
def session_with_content():
    sess = db.create_session("测试会", "discuss", {})
    db.add_message(sess["id"], "user", "讨论主题:要不要做 X")
    db.add_message(sess["id"], "assistant", "我认为要做 X", speaker="A 方",
                   meta={"speaker_role": "a"})
    db.add_message(sess["id"], "assistant", "同意,先做 X1", speaker="B 方",
                   meta={"speaker_role": "b"})
    return sess


# ---------------------------------------------------------------- validation

def test_validate_rejects_outside_home(fake_home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    p, reason = export_plan.validate_project_path(str(outside))
    assert p is None and reason


def test_validate_rejects_home_itself(fake_home):
    p, reason = export_plan.validate_project_path(str(fake_home))
    assert p is None and "家目录" in reason


def test_validate_rejects_missing_and_file(fake_home):
    assert export_plan.validate_project_path(str(fake_home / "nope"))[0] is None
    f = fake_home / "f.txt"
    f.write_text("x")
    assert export_plan.validate_project_path(str(f))[0] is None


def test_validate_rejects_data_dir(fake_home, monkeypatch):
    from app import settings as st
    data = fake_home / "askdata"
    data.mkdir()
    monkeypatch.setattr(st, "DATA_DIR", data)
    assert export_plan.validate_project_path(str(data))[0] is None
    sub = data / "sub"
    sub.mkdir()
    assert export_plan.validate_project_path(str(sub))[0] is None


def test_validate_accepts_project_dir(fake_home):
    proj = _mk_project(fake_home)
    p, reason = export_plan.validate_project_path(str(proj))
    assert p == proj.resolve() and reason is None


def test_validate_empty(fake_home):
    assert export_plan.validate_project_path("")[0] is None


# ---------------------------------------------------------------- filenames

def test_slugify():
    assert export_plan._slugify("记忆 模块/重构") == "记忆-模块重构"
    assert export_plan._slugify("../../etc") == "etc"
    assert export_plan._slugify("") == "plan"
    assert len(export_plan._slugify("很" * 100)) == 40


def test_target_file_never_overwrites(fake_home):
    from datetime import datetime
    proj = _mk_project(fake_home)
    when = datetime(2026, 7, 5)
    first = export_plan._target_file(proj, "x", when)
    first.write_text("occupied")
    second = export_plan._target_file(proj, "x", when)
    assert second != first
    assert second.name == "ASK-PLAN-20260705-x-2.md"


# ---------------------------------------------------------------- transcript

def test_transcript_labels_and_exclusions(session_with_content):
    sid = session_with_content["id"]
    db.add_message(sid, "assistant", "", speaker="空的")  # empty → skipped
    db.add_message(sid, "assistant", "旧草稿", speaker="撰稿人",
                   meta={"export_role": "writer"})  # excluded
    db.add_message(sid, "assistant", "退稿意见", speaker="审稿人",
                   meta={"export_role": "reviewer"})  # kept
    t = export_plan.build_source_transcript(sid)
    assert "[用户]: 讨论主题:要不要做 X" in t
    assert "[A 方]: 我认为要做 X" in t
    assert "旧草稿" not in t
    assert "退稿意见" in t


def test_split_chunks_boundaries():
    text = "\n\n".join(["a" * 10] * 10)
    chunks = export_plan._split_chunks(text, size=25)
    assert all(len(c) <= 25 for c in chunks)
    assert "".join(chunks).replace("\n\n", "") == "a" * 100


# ---------------------------------------------------------------- verdict

def test_parse_verdict():
    ok, issues = export_plan.parse_verdict("【裁决】通过\n很好")
    assert ok and issues == ""
    ok, issues = export_plan.parse_verdict(REJECT)
    assert not ok and "漏了结论 A" in issues
    ok, issues = export_plan.parse_verdict("我觉得还行")  # no verdict line
    assert not ok


def test_parse_verdict_anchored_to_first_lines():
    # A quoted historical verdict past line 5 must NOT count (codex finding:
    # unanchored search could flip a fresh REJECT into a silent PASS).
    echoed = "回顾历史:\n1\n2\n3\n4\n上一轮审稿人说过【裁决】通过\n【裁决】退稿\n1. 新问题"
    ok, _ = export_plan.parse_verdict(echoed)
    assert not ok, "verdict token buried past line 5 must be reject-safe"
    # Fresh verdict within the first 5 lines still parses, with issues after it.
    ok, issues = export_plan.parse_verdict("先说明一下。\n【裁决】退稿\n1. 问题甲")
    assert not ok and "问题甲" in issues
    ok, issues = export_plan.parse_verdict("\r\n【裁决】通过\r\n备注")
    assert ok and issues == ""


def test_parse_verdict_rule_echo_inside_window():
    # codex residual: a rule restatement INSIDE the 5-line window quotes both
    # tokens mid-line; the real verdict line must win, not the echo.
    echoed = "你回复的第一行必须是:【裁决】通过 或 【裁决】退稿。\n【裁决】退稿\n1. 真实问题"
    ok, issues = export_plan.parse_verdict(echoed)
    assert not ok and "真实问题" in issues
    # Mirror case: echo then a genuine PASS.
    ok, issues = export_plan.parse_verdict("规则是【裁决】退稿 或 【裁决】通过。\n【裁决】通过")
    assert ok and issues == ""
    # A verdict that never opens a line is treated as missing → reject-safe.
    ok, _ = export_plan.parse_verdict("我认为可以给出【裁决】通过 这样的评价")
    assert not ok


def test_target_file_rejects_docs_symlink(fake_home, tmp_path):
    # codex finding: docs/ as a symlink pointing outside $HOME let the write
    # escape the validated boundary. _target_file must refuse to follow it.
    from datetime import datetime
    proj = _mk_project(fake_home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (proj / "docs").symlink_to(outside)
    with pytest.raises(export_plan.ExportPathError):
        export_plan._target_file(proj, "x", datetime(2026, 7, 5))
    assert not list(outside.glob("*")), "nothing may land outside the project"


def test_export_pipeline_blocks_docs_symlink(fake_home, session_with_content, monkeypatch, tmp_path):
    # Full-pipeline version: PASS verdict but docs is a symlink → export_error,
    # no file anywhere.
    proj = _mk_project(fake_home)
    outside = tmp_path / "outside2"
    outside.mkdir()
    (proj / "docs").symlink_to(outside)
    adapter = FakeAdapter([DRAFT], [PASS])
    monkeypatch.setattr(export_plan, "make_adapter", lambda p: adapter)

    events = _run(session_with_content["id"], proj, adapter)
    assert not [e for e in events if e["event"] == "export_done"]
    errs = [e for e in events if e["event"] == "export_error"]
    assert errs and "docs" in errs[0]["data"]["error"]
    assert not list(outside.glob("*"))


# ---------------------------------------------------------------- pipeline

def test_export_pass_writes_file(fake_home, session_with_content, monkeypatch):
    proj = _mk_project(fake_home)
    adapter = FakeAdapter([DRAFT], [PASS])
    monkeypatch.setattr(export_plan, "make_adapter", lambda p: adapter)
    monkeypatch.setattr(export_plan.notifier, "notify", lambda *a, **k: True)

    events = _run(session_with_content["id"], proj, adapter)
    done = [e for e in events if e["event"] == "export_done"]
    assert len(done) == 1
    path = Path(done[0]["data"]["path"])
    assert path.exists() and path.parent == proj / "docs"
    body = path.read_text()
    assert "do NOT commit" in body          # banner
    assert "## 1. 背景与目标" in body        # draft body
    assert str(path) in done[0]["data"]["spell"]  # absolute path in spell
    assert adapter.calls == ["writer", "reviewer"]
    # Success message persisted with meta for reload rendering.
    msgs = db.list_messages(session_with_content["id"])
    result = [m for m in msgs if (m.get("meta") or {}).get("export_role") == "result"]
    assert result and result[-1]["meta"]["export_result"]["status"] == "done"


def test_export_reject_three_strikes_writes_nothing(fake_home, session_with_content, monkeypatch):
    proj = _mk_project(fake_home)
    adapter = FakeAdapter([DRAFT, DRAFT, DRAFT], [REJECT, REJECT, REJECT])
    monkeypatch.setattr(export_plan, "make_adapter", lambda p: adapter)

    events = _run(session_with_content["id"], proj, adapter)
    assert not [e for e in events if e["event"] == "export_done"]
    rejected = [e for e in events if e["event"] == "export_rejected"]
    assert len(rejected) == 1
    assert not list((proj / "docs").glob("*.md")) if (proj / "docs").exists() else True
    assert adapter.calls == ["writer", "reviewer"] * 3
    # Rejection is a persisted message → pending_review_note picks it up.
    note = export_plan.pending_review_note(session_with_content["id"])
    assert note and "漏了结论 A" in note


def test_export_reject_then_pass(fake_home, session_with_content, monkeypatch):
    proj = _mk_project(fake_home)
    adapter = FakeAdapter([DRAFT, DRAFT], [REJECT, PASS])
    monkeypatch.setattr(export_plan, "make_adapter", lambda p: adapter)
    monkeypatch.setattr(export_plan.notifier, "notify", lambda *a, **k: True)

    events = _run(session_with_content["id"], proj, adapter)
    assert [e for e in events if e["event"] == "export_done"]
    assert adapter.calls == ["writer", "reviewer", "writer", "reviewer"]


def test_export_empty_session_errors(fake_home):
    sess = db.create_session("空会", "chat", {})
    proj = _mk_project(fake_home)
    events = _run(sess["id"], proj, None)
    errs = [e for e in events if e["event"] == "export_error"]
    assert errs and "没有可提炼" in errs[0]["data"]["error"]


def test_export_long_transcript_chunks(fake_home, session_with_content, monkeypatch):
    sid = session_with_content["id"]
    db.add_message(sid, "assistant", "长" * 120_000, speaker="A 方",
                   meta={"speaker_role": "a"})
    proj = _mk_project(fake_home)
    adapter = FakeAdapter([DRAFT], [PASS])
    monkeypatch.setattr(export_plan, "make_adapter", lambda p: adapter)
    monkeypatch.setattr(export_plan.notifier, "notify", lambda *a, **k: True)

    events = _run(sid, proj, adapter)
    chunk_evs = [e for e in events if e["event"] == "export_status"
                 and e["data"].get("stage") == "chunking"]
    assert chunk_evs, "long transcript must trigger the map-reduce pre-summary"
    assert [e for e in events if e["event"] == "export_done"]
    assert adapter.calls[0] == "chunk"


def test_pending_review_note_cleared_by_new_rounds(session_with_content):
    sid = session_with_content["id"]
    db.add_message(sid, "assistant", "❌ 审稿未通过…意见如下", speaker="出计划",
                   meta={"export_role": "result", "export_result": {"status": "rejected"}})
    assert export_plan.pending_review_note(sid)
    # New a/b turn after the rejection → note no longer pending.
    db.add_message(sid, "assistant", "回应审稿", speaker="A 方",
                   meta={"speaker_role": "a"})
    assert export_plan.pending_review_note(sid) is None


# ---------------------------------------------------------------- API layer

@pytest.fixture
def client(isolated_data_dir):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_api_project_tag_merges_meta(client, fake_home):
    proj = _mk_project(fake_home, "tagme")
    sess = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]
    # Pre-existing unrelated meta key must survive the tag merge.
    client.put(f"/api/sessions/{sess['id']}", json={"meta": {"keep_me": 1}})
    r = client.put(f"/api/sessions/{sess['id']}/project", json={"path": str(proj)})
    assert r.status_code == 200
    meta = r.json()["session"]["meta"]
    assert meta["project_path"] == str(proj.resolve())
    assert meta["keep_me"] == 1
    # Clearing removes only the tag.
    meta = client.put(f"/api/sessions/{sess['id']}/project", json={"path": None}).json()["session"]["meta"]
    assert "project_path" not in meta and meta["keep_me"] == 1


def test_api_project_tag_rejects_bad_path(client, fake_home):
    sess = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]
    assert client.put(f"/api/sessions/{sess['id']}/project", json={"path": "/etc"}).status_code == 400


def test_api_export_plan_rejects_bad_path(client, fake_home):
    sess = client.post("/api/sessions", json={"title": "t", "mode": "chat"}).json()["session"]
    r = client.post(f"/api/sessions/{sess['id']}/export-plan", json={
        "project_path": "/etc",
        "writer": {"provider_id": "x", "model_id": "y"},
        "reviewer": {"provider_id": "x", "model_id": "y"},
    })
    assert r.status_code == 400


def test_api_export_validate(client, fake_home):
    proj = _mk_project(fake_home, "valid8")
    assert client.post("/api/export/validate", json={"path": str(proj)}).json()["ok"] is True
    assert client.post("/api/export/validate", json={"path": "/etc"}).json()["ok"] is False
