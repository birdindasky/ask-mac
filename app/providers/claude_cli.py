"""Claude Code CLI adapter (subscription mode).

Spawns `claude --print --output-format stream-json --include-partial-messages
--tools "" --bare --no-session-persistence` per request, scrubbing every env
var that could push the CLI onto API billing.
"""
from __future__ import annotations
import asyncio
import json as _json
import os
import shutil
from pathlib import Path
from typing import AsyncIterator, Optional

from .base import HealthResult, Message, ProviderAdapter, StreamChunk
from .cli_detect import augmented_path, find_binary
from ..settings import CLI_REQUEST_TIMEOUT_SEC

# Vars that would force the CLI onto pay-per-token API billing instead of
# the user's subscription. CLAUDE_CODE_OAUTH_TOKEN is intentionally KEPT —
# it carries the subscription auth in some environments.
ENV_BLACKLIST_EXACT = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "AWS_BEARER_TOKEN_BEDROCK",
}
ENV_BLACKLIST_PREFIXES = (
    "ANTHROPIC_BEDROCK_",
    "ANTHROPIC_VERTEX_",
    "AWS_BEARER_TOKEN_BEDROCK",
)


def scrub_env(extra_block: tuple[str, ...] = ()) -> dict[str, str]:
    env = {}
    for k, v in os.environ.items():
        if k in ENV_BLACKLIST_EXACT:
            continue
        if k in extra_block:
            continue
        if any(k.startswith(p) for p in ENV_BLACKLIST_PREFIXES):
            continue
        env[k] = v
    # Node-based CLIs (`codex`, `claude` ships node-bundled but its tools may
    # shell out) need /opt/homebrew/bin etc on PATH or env shebangs fail with
    # "env: node: No such file or directory" inside .app bundles where py2app
    # strips PATH down to /usr/bin:/bin.
    env["PATH"] = augmented_path(env.get("PATH"))
    return env


def _walk_text(obj) -> str:
    """Pull any "text" string from nested deltas / content blocks."""
    if isinstance(obj, dict):
        if "text" in obj and isinstance(obj["text"], str):
            return obj["text"]
        for v in obj.values():
            t = _walk_text(v)
            if t:
                return t
    return ""


class ClaudeCLIAdapter(ProviderAdapter):
    kind = "claude_cli"

    def _binary(self) -> str:
        return self.config.get("cli_path") or find_binary("claude") or "claude"

    def _build_prompt(self, messages: list[Message], system: Optional[str]) -> str:
        sys_text, rest = self._split_system(messages, system)
        parts = []
        if sys_text:
            parts.append(f"[System]\n{sys_text}")
        for m in rest:
            label = "User" if m.role == "user" else "Assistant"
            parts.append(f"[{label}]\n{m.content}")
        parts.append("[Assistant]\n")
        return "\n\n".join(parts)

    async def stream(
        self,
        messages: list[Message],
        model_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        system: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        binary = self._binary()
        if not binary or not Path(binary).is_file():
            yield StreamChunk(error="claude CLI 未安装。请在终端运行 `brew install claude-code`,然后重启 Ask。", done=True)
            return

        prompt_text = self._build_prompt(messages, system)
        args = [
            binary,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--no-session-persistence",
            "--setting-sources",
            "user",
            "--tools",
            "",
        ]
        if model_id:
            args += ["--model", model_id]

        env = scrub_env()
        # Run from /tmp so claude doesn't auto-discover a CLAUDE.md from cwd.
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/tmp",
        )
        try:
            assert proc.stdin
            proc.stdin.write(prompt_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        emitted_so_far = ""

        async def _read_stderr() -> bytes:
            assert proc.stderr
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(_read_stderr())

        try:
            assert proc.stdout
            while True:
                if cancel_event and cancel_event.is_set():
                    proc.terminate()
                    yield StreamChunk(done=True)
                    return
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=CLI_REQUEST_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    proc.terminate()
                    yield StreamChunk(error="claude CLI 超时", done=True)
                    return
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    payload = _json.loads(line_str)
                except _json.JSONDecodeError:
                    continue

                # Strategy: collect text from assistant messages and stream_event content_block_deltas.
                etype = payload.get("type")
                if etype == "stream_event":
                    ev = payload.get("event") or {}
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                emitted_so_far += text
                                yield StreamChunk(delta=text)
                elif etype == "assistant":
                    msg = payload.get("message") or {}
                    content = msg.get("content") or []
                    full = "".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                    )
                    if full and len(full) > len(emitted_so_far) and full.startswith(emitted_so_far):
                        yield StreamChunk(delta=full[len(emitted_so_far):])
                        emitted_so_far = full
                elif etype == "result":
                    if payload.get("subtype") and payload.get("subtype") != "success":
                        err = payload.get("result") or payload.get("error") or "claude CLI 失败"
                        yield StreamChunk(error=str(err)[:500], done=True)
                        return

            await proc.wait()
            stderr_bytes = await stderr_task
            if proc.returncode and proc.returncode != 0 and not emitted_so_far:
                err = stderr_bytes.decode("utf-8", errors="replace")[:500] or f"claude exit {proc.returncode}"
                yield StreamChunk(error=err, done=True)
                return
            yield StreamChunk(done=True)
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

    async def health_check(self, model_id: Optional[str] = None) -> HealthResult:
        binary = self._binary()
        if not binary or not Path(binary).is_file():
            return HealthResult(False, "claude CLI 未安装(brew install claude-code 或重启 Ask)")
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=scrub_env(),
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                ver = out.decode("utf-8", errors="replace").strip()
                return HealthResult(True, f"CLI 可用: {ver[:80]}")
            return HealthResult(False, "CLI 返回非零", err.decode("utf-8", errors="replace")[:200])
        except (asyncio.TimeoutError, FileNotFoundError) as e:
            return HealthResult(False, "CLI 调用失败", str(e))
