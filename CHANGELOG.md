# Changelog

## 0.3.0 — 2026-07-05

### Added
- **Export Plan (出计划)**: distill a finished discussion into a plan document
  written into a project folder, ready for a coding agent to execute.
  - Sessions can be tagged with a project folder (📁 toolbar chip); tagged
    sessions export straight there, untagged ones ask at export time.
  - Writer + reviewer pipeline: any two wired models. The writer produces a
    7-section plan (background / settled conclusions / open disagreements /
    steps / risks / acceptance / source); the reviewer checks it against the
    transcript and can force up to 2 rewrites. All turns stream into the
    session as labeled bubbles.
  - Approved plans land at `<project>/docs/ASK-PLAN-YYYYMMDD-<topic>.md` —
    never overwrites, "do not commit" banner, absolute-path kickoff prompt
    with one-tap copy (works for Claude Code and Codex CLI).
  - Rejected plans write **nothing**: objections post into the session, and
    Discuss-mode "Continue" feeds them into the next rounds automatically.
  - Very long discussions are chunk-summarized before distillation.
  - Safety: targets must be existing directories inside `$HOME` (Ask's own
    data dir excluded); exactly one file per export; no git operations.
- Reveal-in-Finder for exported plan files (`POST /api/internal/reveal`).
- System notification when an export finishes.

### Changed
- `scripts/build_dmg.py` now reads the version from `app.settings` instead of
  hardcoding it.

## 0.2.0 — 2026-07-04

Initial public release: four modes (Chat / Compare / Debate / Discuss with
early convergence), 8 provider kinds incl. Claude CLI / Codex CLI
subscription mode, 6 web-search backends, Keychain-stored keys, SQLite +
FTS5 local persistence, EN/zh UI, native macOS shell (menu bar, Dock badge,
status item).
