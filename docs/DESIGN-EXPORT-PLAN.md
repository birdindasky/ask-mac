# Design: Export Plan (出计划) — v0.3.0

Status: approved 2026-07-05 · implemented on `feat/export-plan`

## Problem

Ask is a meeting room: models chat, compare, debate, and converge — but the
conclusion lives only in chat bubbles. Users copy text out by hand and paste
it into a coding agent (Claude Code, Codex CLI) to act on it. The handoff is
manual, lossy, and unstructured.

## Goal

One button turns a finished discussion into a **plan document written into a
real project folder**, plus a ready-to-paste **kickoff prompt** so a coding
agent can pick up the work immediately. Ask stays a thinking tool — it writes
exactly one markdown file per export and never touches anything else.

## Non-goals

- No general file-write tool for models during chat (rejected: uncontrolled).
- No git operations of any kind. Never commits, never edits repo config.
- No auto-launch of terminal / Claude Code (maybe later; clipboard prompt first).
- No project *reading*: the plan is distilled from the conversation only; the
  executing agent verifies against the real codebase (division of labor).

## UX flow

1. **Project tag (挂牌)** — a session may carry `meta.project_path`. Set from a
   chip in the session header: pick from `~/Projects/*` or type a path.
   Tagged sessions export straight to their project; untagged ones ask at
   export time.
2. **Export button (出计划)** — available in every mode once the session has
   assistant content. Opens a dialog: target project (prefilled from tag),
   optional title, and two model pickers — **writer (撰稿人)** and **reviewer
   (审稿人)** — free choice across all wired providers, last pick remembered
   (`ui.last_export`). First-run default: Claude writes, Codex reviews
   (heterogeneous vendors on subscription = free review pass).
3. **Pipeline, fully visible** — writer and reviewer turns stream into the
   session as labeled bubbles, exactly like debate turns. No background box.
4. **Verdict**
   - PASS → file written, success card in-session: absolute path + kickoff
     prompt + one-tap copy + reveal-in-Finder. System notification fires.
   - REJECTED (after max 2 rewrite cycles) → **nothing is written**. The
     reviewer's objections land in the session as a normal message and the
     user is pointed at the existing "continue discussion" machinery — the
     fix loop is *more discussion*, not a stamped defective file.

## Pipeline

```
transcript = all user/assistant turns with speaker labels
if len(transcript) > CHUNK_THRESHOLD: map-reduce pre-summary (writer model)

draft   = writer(transcript, template)          # visible turn
verdict = reviewer(transcript, draft)           # visible turn
while verdict == REJECT and rewrites < 2:
    draft   = writer(transcript, template, objections)
    verdict = reviewer(transcript, draft)
PASS   -> write file, post success message (meta.export_result)
REJECT -> post objections message, no file
```

Reviewer contract: first line `【裁决】通过` or `【裁决】退稿`, then numbered
issues. The reviewer checks the draft **against the transcript** for: dropped
conclusions, unresolved disagreements presented as settled, and content
invented beyond the discussion. Parse is tolerant (verdict line anywhere in
the first 5 lines; missing verdict = reject-safe).

In discuss mode, a rejection's objections are injected into the next
`/discuss/continue` batch as third-party review context, so the models answer
the reviewer's points directly. In chat mode the objection message is part of
history naturally. In compare/debate the message is visible and the user
steers.

## Plan document

Path: `<project>/docs/ASK-PLAN-YYYYMMDD-<slug>.md`. `docs/` is created if
missing. Existing files are never overwritten — collision appends `-2`, `-3`…
Slug from user title or writer-suggested topic, sanitized (no separators,
≤40 chars, CJK allowed).

Header banner (code-inserted, not model-written):

```
> ⚠️ 内部工作文件 (internal working file) — do NOT commit or publish.
> Source: Ask session "<title>" · mode <mode> · writer <model> · reviewer <model> · <timestamp>
```

Body sections (writer template, Chinese body / English code terms):
1. 背景与目标 2. 已定结论 (each with basis) 3. **未定分歧** (honest list of
what was NOT settled — protects the executor from treating open questions as
mandates) 4. 执行计划 (concrete steps) 5. 风险与边界 6. 验收标准
7. 来源 (session, participants). A pure-conclusion discussion is allowed to
state 执行计划: 无.

## Kickoff prompt (咒语)

Copied to clipboard on demand; uses the **absolute path** so it works from
any CWD:

```
读 /abs/path/docs/ASK-PLAN-….md，按计划开干。计划与实际代码冲突时以实际为准，
先报告再动手。这份文件是内部工作底稿，不要 commit 进任何仓库。
```

Works verbatim for both Claude Code and Codex CLI.

## Safety

- Target must resolve to an **existing directory strictly under `$HOME`**,
  and must not be `$HOME` itself, Ask's own data dir, or anything outside.
- The project path is re-validated **at write time** (the pipeline runs for
  minutes after the initial check), and `docs/` must resolve to the real
  `<project>/docs` — a symlinked `docs/` is refused, and filenames are built
  on the resolved base so a post-check swap can't redirect the write.
  (Both hardenings from the 2026-07-05 codex review.)
- Exactly one file is written per export. No overwrites, no deletes.
- Leak guard is soft by design (user decision 2026-07-05): banner + kickoff
  prompt both forbid committing; `.gitignore` is never touched.

## API surface

- `GET  /api/export/projects` — top-level non-hidden dirs of `~/Projects`.
- `POST /api/export/validate` — `{path}` → `{ok, resolved, reason?}`.
- `PUT  /api/sessions/{sid}/project` — `{path|null}`; server-side meta merge
  (plain `PUT /sessions/{sid}` replaces meta wholesale — do not use for this).
- `POST /api/sessions/{sid}/export-plan` — SSE; body `{project_path, title?,
  writer:{provider_id,model_id}, reviewer:{provider_id,model_id}}`. Shares the
  one-stream-per-session guard with chat. Events: standard `assistant_*`
  bubbles plus `export_status` / `export_done` / `export_rejected`.

## Limits

- CLI adapter timeout is per-output-gap (120 s), not total — long plans are
  fine while streaming; a model that stalls >120 s between chunks fails the
  export with a visible error, nothing written.
- Map-reduce pre-summary (very long debates) is the weakest-quality corner;
  acceptance tests target it explicitly.
